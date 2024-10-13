#!/usr/bin/env python3
"""Lambda function to convert an RSS XML file in S3 to an email, and send it."""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from importlib.resources import files
from urllib.error import HTTPError
from xml.etree import ElementTree

import boto3
from botocore.exceptions import ClientError
from bs4 import BeautifulSoup

CHARSET = "UTF-8"
DAYS_OF_NEWS = 3
EMAIL_SUBJECT = 'Daily News'

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def get_description_body(html):
    """Return the body of the description, without any iframes."""
    if html is None:
        return ""
    parsed_html = BeautifulSoup(html, features="html.parser")
    for s in parsed_html.select('iframe'):
        s.decompose()

    stripped_html = parsed_html
    if parsed_html.find('html'):
        if parsed_html.body:
            stripped_html = parsed_html.body.text

    if len(stripped_html) > 20:
        child_offset = 0
        for child in stripped_html.findChildren():
            if child_offset > 20:
                child.decompose()
            child_offset += 1
    return stripped_html


def get_last_run(parameter_name):
    """Get the last run timestamp from parameter store."""
    try:
        ssm = boto3.client('ssm')
        parameter = ssm.get_parameter(Name=parameter_name)
        return datetime.strptime(parameter['Parameter']['Value'], "%Y-%m-%dT%H:%M:%S.%f")
    except ClientError as e:
        logger.warning(e)
        logger.warning("Error retrieving parameter from parameter store, retrieving default days.")
        return (datetime.today() - timedelta(days=DAYS_OF_NEWS))


def set_last_run(parameter_name):
    """Set the last run timestamp in parameter store."""
    current_timestamp = datetime.now().isoformat()
    ssm = boto3.client('ssm')
    ssm.put_parameter(
        Name=parameter_name,
        Value=current_timestamp,
        Type="String",
        Overwrite=True,
        Description="The last run timestamp of the RSS email I send out")


def add_attribute_to_dict(item, name, target_dict):
    """Add an attribute to a dictionary."""
    tmp_attribute = item.find(name)
    if tmp_attribute is not None:
        target_dict[name] = tmp_attribute.text


def read_s3_file(bucket_name, s3_key):
    """Read a file from S3."""
    s3 = boto3.client('s3')
    s3_response = s3.get_object(Bucket=bucket_name, Key=s3_key)
    file_content = s3_response.get('Body').read().decode('utf-8')
    return file_content


def generate_html(last_run_date, s3_bucket, s3_prefix):
    """Generate the HTML for the email."""
    try:
        rss_file = read_s3_file(s3_bucket, s3_prefix)
    except HTTPError as e:
        logger.error(f"Error retrieving RSS file: {s3_bucket}/{s3_prefix}".format(e))
        return "Internal error retrieving RSS file."

    all_items = []
    logger.debug("Retrieved RSS file. Last run date: %s", last_run_date)
    for item in ElementTree.fromstring(rss_file).findall('.//item'):
        item_dict = {}
        for name in ['title', 'link', 'description', 'pubDate']:
            add_attribute_to_dict(item, name, item_dict)

        published_date = time.mktime(
            datetime.strptime(
                str(item_dict["pubDate"]),
                "%a, %d %b %Y %H:%M:%S %Z").timetuple())
        item_dict["sortDate"] = published_date
        if datetime.fromtimestamp(published_date) > last_run_date:
            all_items.append(item_dict)

    list_output = u""

    previous_day = ""
    for item in sorted(all_items, key=lambda k: k['sortDate'], reverse=True):
        day = item['pubDate'][:3]
        if day != previous_day:
            list_output += f"<p><b>{day}</b></p>\n"
            previous_day = day        
        if len(item['description']) > 1000:
            item['description'] = item['description'][:1000] + "..." 
        list_output += f"""
            <div class="tooltip">
            <a href="{item['link']}">{item['title']}</a>
            <span class="tooltiptext">{item['pubDate']}</span>
            </div>\n
            <section class="longdescription">{item['description']}</section>\n"""

    html = files("rss_email").joinpath("email_body.html").read_text()
    return html.format(subject=EMAIL_SUBJECT, articles=list_output)


def is_valid_email(event_dict, valid_emails):
    """Check if the email address is valid."""
    if ("Records" in event_dict
        and len(event_dict["Records"]) > 0
        and "Sns" in event_dict["Records"][0]):
        ses_notification = event_dict["Records"][0]["Sns"]["Message"]
        json_ses = json.loads(ses_notification)
        if json_ses["mail"]["source"].lower() not in [email.lower() for email in valid_emails]:
            logger.warning("Invalid email address: %s", json_ses["mail"]["source"])
            return False

    return True


def send_email(event, context): # pylint: disable=unused-argument
    """Send the email."""
    logger.debug("Event body: %s", event)

    bucket = os.environ["BUCKET"]
    key = os.environ["KEY"]
    source_email_address = os.environ["SOURCE_EMAIL_ADDRESS"]
    to_email_address = os.environ["TO_EMAIL_ADDRESS"]
    parameter_name = os.environ["LAST_RUN_PARAMETER"]
    if not is_valid_email(event, [to_email_address]):
        return
    run_date = get_last_run(parameter_name)

    body = generate_html(run_date, bucket, key)

    # Create a new SES resource
    client = boto3.client('ses')

    # Try to send the email.
    try:
        # Provide the contents of the email.
        response = client.send_email(
            Destination={
                'ToAddresses': [
                    to_email_address,
                ],
            },
            Message={
                'Body': {
                    'Html': {
                        'Charset': CHARSET,
                        'Data': body,
                    },
                    'Text': {
                        'Charset': CHARSET,
                        'Data': body,
                    },
                },
                'Subject': {
                    'Charset': CHARSET,
                    'Data': EMAIL_SUBJECT,
                },
            },
            Source=source_email_address,
        )
    except ClientError as e:
        logger.warning("got an error: %s", e.response)
    else:
        logger.debug("Email sent! Message ID: %s", response['MessageId'])
        set_last_run(parameter_name)


def main():
    """Main function when invoked from command line and not lambda."""
    parser = argparse.ArgumentParser(
        description='Creates a HTML version of RSS for email delivery.')
    parser.add_argument(
        'rss_host', type=str, help='XML RSS S3 bucket, e.g. myfeedbucket')
    parser.add_argument(
        'rss_prefix', type=str, help='XML RSS file, e.g. rss.xml')
    args = parser.parse_args()
    run_date = datetime.today() - timedelta(days=DAYS_OF_NEWS)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    logger.setLevel(logging.DEBUG)
    logger.info(generate_html(run_date, args.rss_host, args.rss_prefix))

if __name__ == "__main__":
    main()
