# RSS Emailer

![Lint and Unit Testing](https://github.com/eamonmason/rss-email/actions/workflows/lint_and_test.yml/badge.svg)

Sends an aggregated bunch of feeds to you via email.

## Prepare for deployment

### Environment Variables

Create a `.env` file in the base directory of the project, for your custom settings:

```sh
SOURCE_DOMAIN="mydomain.com"
SOURCE_EMAIL_ADDRESS="rss@mydomain"
TO_EMAIL_ADDRESS="me@someemailprovider.com"
EMAIL_RECIPIENTS="morerssplease@onedomain.com,sendmerss@twodomain.com"
```

### CDK Pre-requisites

Deployment uses AWS CDK, so install the necessary Node modules:

```sh
npm install aws-cdk-lib @types/node dotenv
```

If you have not run CDK in the target account and region, then bootstrap the CDK deploy by running:

```sh
cdk bootstrap aws://<your-account-id>/<target-region>
```

### Setting up Email

You need a domain for sending and (possibly) receiving email. I use route53, but any domain hosting will work as long as you have the ability to edit domain records for SES.

#### Sending Emails

SES needs a verified domain for sending emails from a trusted domain, and a verified email address for whoever you send the email to. See: [https://eu-west-1.console.aws.amazon.com/ses/home?region=eu-west-1#/verified-identities/create](https://eu-west-1.console.aws.amazon.com/ses/home?region=eu-west-1#/verified-identities/create) for setup instructions.

If your domain is registered in route53 then this happens automatically in a few minutes.

#### Receiving Emails

Create an MX record in your domain that points to the appropriate inbound SMTP relay for the region that the application is deployed in. See [https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html](https://docs.aws.amazon.com/ses/latest/dg/receiving-email-mx-record.html)

## Deploying

Deploy the stack with CDK by running:

```sh
cdk deploy
```

## Post-deployment

To receive email correctly, post deployment the SES Active Rule Set has to be enabled. Go to "Email receiving", select the RSSRuleSet resource and click "Set as Active".

## Building locally

```poetry install```

## Running locally

To retrieve the last 3 days of articles from a set of sources to the console (does not store in S3):

```poetry run python src/rss_email/retrieve_articles.py <feed_url_json_file>```

and to retrieve the articles from a file in S3, and format an email, but not actually send it:

```poetry run python src/rss_email/email_articles.py```

## Deploying the Pipeline

Add a GitHub personal token to AWS Secrets Manager, for the github repo, called `github-token`.

Put the following environment variables in parameter store, with appropriate values (as described above with the `.env` file):

- `rss-email-AWS_ACCOUNT_ID`
- `rss-email-AWS_REGION`
- `rss-email-EMAIL_RECIPIENTS`
- `rss-email-SOURCE_DOMAIN`
- `rss-email-SOURCE_EMAIL_ADDRESS`
- `rss-email-TO_EMAIL_ADDRESS`

Deploy the pipeline itself.

```sh
cdk deploy --app "npx ts-node bin/pipeline-cdk.ts"
```

Once the deploy has completed successfully, upload the `feed_urls.json` file to the new S3 bucket.

See [https://docs.aws.amazon.com/cdk/v2/guide/cdk_pipeline.html#cdk_pipeline_security](https://docs.aws.amazon.com/cdk/v2/guide/cdk_pipeline.html#cdk_pipeline_security) for more info.
