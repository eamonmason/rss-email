#!/usr/bin/env python3
"""
Functional test that runs the complete RSS email workflow locally.

This test:
1. Sets the SSM parameter 'rss-email-lastrun' to 24 hours ago
2. Downloads the current RSS articles file from S3
3. Runs the complete email generation workflow
4. Outputs the result to a local HTML file instead of sending email

Requirements:
- AWS credentials configured for the target account
- Proper environment variables set (see CLAUDE.md)
"""

import os
import sys
import xml.etree.ElementTree as ET  # noqa: N817
from datetime import datetime, timedelta
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

# Load environment variables from .env file
try:
    from dotenv import load_dotenv

    # Look for .env file in current directory
    env_file = Path(".env")
    if env_file.exists():
        load_dotenv(env_file)
        print(f"📁 Loaded environment variables from {env_file}")
except ImportError:
    print("⚠️  python-dotenv not installed. Install with: uv add python-dotenv")
    print("You can set environment variables manually if needed.")


from rss_email.email_articles import generate_html, get_last_run  # noqa: E402


class FunctionalWorkflowTest:
    """Functional test for the complete RSS email workflow."""

    def __init__(self):
        """Initialize the test with required configurations."""
        self.ssm_client = boto3.client("ssm")
        self.s3_client = boto3.client("s3")

        # Configuration
        self.ssm_parameter_name = "rss-email-lastrun"
        self.s3_bucket = "cd-rssemailstack-rssbucket91adb797-1ds7r89g7wdoo"
        self.s3_key = "rss.xml"
        self.output_file = "functional_test_output.html"
        self.downloaded_rss_file = "downloaded_rss.xml"

        # Test configuration options
        self.fast_mode = os.environ.get("FAST_MODE", "false").lower() == "true"
        self.max_test_articles = int(os.environ.get("MAX_TEST_ARTICLES", "15"))  # Limit for faster testing

        # Store original SSM parameter value for cleanup
        self.original_parameter_value = None

        # Setup Claude environment variables if needed
        self._setup_claude_environment()

    def _setup_claude_environment(self):
        """Setup Claude environment variables for testing."""
        # If ANTHROPIC_API_KEY is set but ANTHROPIC_API_KEY_PARAMETER is not,
        # we can use the direct API key approach
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        api_key_param = os.environ.get("ANTHROPIC_API_KEY_PARAMETER")

        # Configure test mode settings
        if self.fast_mode:
            print("   ⚡ Fast mode enabled - limiting articles for quicker testing")
            print(f"   📊 Max articles for testing: {self.max_test_articles}")

        if api_key and not api_key_param:
            # For testing, we'll create a mock parameter name
            # The article processor will fall back to direct env var if parameter fails
            print("   🔧 Using direct ANTHROPIC_API_KEY for testing")
        elif api_key_param:
            print("   🔧 Using Parameter Store for API key")
        elif not api_key and not api_key_param:
            print("   ⚠️  No Claude API key configuration found")
            print("   💡 Set ANTHROPIC_API_KEY in .env file for Claude features")

    def setup_test_environment(self):
        """Set up the test environment with required configurations."""
        print("🔧 Setting up test environment...")

        # Save original SSM parameter value
        try:
            response = self.ssm_client.get_parameter(Name=self.ssm_parameter_name)
            self.original_parameter_value = response["Parameter"]["Value"]
            print("   📝 Saved original SSM parameter value")
        except ClientError as error:
            if error.response["Error"]["Code"] == "ParameterNotFound":
                print("   ⚠️  SSM parameter doesn't exist, will create new one")
                self.original_parameter_value = None
            else:
                raise

        # Set SSM parameter to 24 hours ago
        test_timestamp = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.%f")
        try:
            if self.original_parameter_value is None:
                # Create new parameter
                self.ssm_client.put_parameter(
                    Name=self.ssm_parameter_name,
                    Value=test_timestamp,
                    Type="String",
                    Description="Last run timestamp for RSS email processing (functional test)"
                )
                print(f"   ✅ Created SSM parameter with test timestamp: {test_timestamp}")
            else:
                # Update existing parameter
                self.ssm_client.put_parameter(
                    Name=self.ssm_parameter_name,
                    Value=test_timestamp,
                    Type="String",
                    Overwrite=True
                )
                print(f"   ✅ Updated SSM parameter with test timestamp: {test_timestamp}")
        except ClientError as error:
            print(f"   ❌ Failed to set SSM parameter: {error}")
            raise

    def download_rss_file(self):
        """Download the current RSS file from S3."""
        print("📥 Downloading RSS file from S3...")

        try:
            response = self.s3_client.get_object(Bucket=self.s3_bucket, Key=self.s3_key)
            rss_content = response["Body"].read().decode("utf-8")

            # Write to local file
            with open(self.downloaded_rss_file, "w", encoding="utf-8") as file:
                file.write(rss_content)

            print(f"   ✅ Downloaded RSS file to: {self.downloaded_rss_file}")
            print(f"   📊 File size: {len(rss_content)} characters")

            # Show some basic stats about the RSS content
            try:
                root = ET.fromstring(rss_content)
                items = root.findall(".//item")
                print(f"   📰 Found {len(items)} articles in RSS feed")
            except ET.ParseError as error:
                print(f"   ⚠️  RSS parsing error (content may be compressed): {error}")

        except ClientError as error:
            print(f"   ❌ Failed to download RSS file: {error}")
            raise

    def run_email_generation(self):
        """Run the email generation workflow."""
        print("🔄 Running email generation workflow...")

        try:
            # Get the last run date from SSM
            last_run_date = get_last_run(self.ssm_parameter_name)
            print(f"   📅 Last run date: {last_run_date}")
            hours_since = (datetime.now() - last_run_date).total_seconds() / 3600
            print(f"   🕐 Hours since last run: {hours_since:.1f}")

            # Optionally limit RSS file for faster testing
            test_rss_file = self.downloaded_rss_file
            if self.fast_mode:
                test_rss_file = self._create_limited_rss_file()

            # Generate HTML using the downloaded RSS file
            print("   🤖 Starting Claude processing (this may take 1-3 minutes for large feeds)...")
            html_content = generate_html(
                last_run_date=last_run_date,
                s3_bucket=self.s3_bucket,
                s3_prefix=self.s3_key,
                local_file=test_rss_file
            )

            # Write HTML to output file
            with open(self.output_file, "w", encoding="utf-8") as file:
                file.write(html_content)

            print("   ✅ Generated HTML email content")
            print(f"   📄 Output written to: {self.output_file}")
            print(f"   📊 HTML size: {len(html_content)} characters")

            # Analyze the content
            self._analyze_html_content(html_content)

        except Exception as exc:
            print(f"   ❌ Email generation failed: {exc}")
            print("   💡 This might be normal if Claude is not configured")
            raise

    def _analyze_html_content(self, html_content):
        """Analyze the generated HTML content and provide insights."""
        print("   🔍 Analyzing generated content:")

        # Check for Claude-enhanced content
        claude_indicators = ["AI/ML", "Cybersecurity", "Technology"]
        has_claude_content = any(indicator in html_content for indicator in claude_indicators)

        if has_claude_content:
            print("      ✅ Claude-enhanced categorization detected")
        else:
            print("      📝 Standard format (no Claude categorization)")

        # Count articles
        article_count = html_content.count('<h3 style="margin: 0 0 8px 0;">')
        if article_count == 0:
            # Try alternative counting method
            article_count = html_content.count('<a href="http')

        print(f"      📰 Estimated article count: {article_count}")

        # Check for various sections
        content_checks = [
            ("Daily News" in html_content, "Contains email subject"),
            ("href=" in html_content, "Contains article links"),
            (len(html_content) > 1000, "Substantial content generated")
        ]

        for check_passed, description in content_checks:
            status = "✅" if check_passed else "⚠️ "
            print(f"      {status} {description}")

    def _create_limited_rss_file(self):
        """Create a limited RSS file for faster testing."""

        limited_file = "limited_rss.xml"

        try:
            # Parse the original RSS file
            with open(self.downloaded_rss_file, "r", encoding="utf-8") as file:
                rss_content = file.read()

            root = ET.fromstring(rss_content)
            items = root.findall(".//item")

            print(f"   ⚡ Limiting RSS from {len(items)} to {self.max_test_articles} articles for faster testing")

            # Keep only the first N items
            channel = root.find(".//channel")
            if channel is not None:
                # Remove existing items
                for item in items:
                    channel.remove(item)

                # Add back only the first N items
                for item in items[:self.max_test_articles]:
                    channel.append(item)

            # Write the limited RSS file
            limited_content = ET.tostring(root, encoding="unicode")
            with open(limited_file, "w", encoding="utf-8") as file:
                file.write(limited_content)

            print(f"   📝 Created limited RSS file: {limited_file}")
            return limited_file

        except (ET.ParseError, OSError, ValueError) as exc:
            print(f"   ⚠️  Failed to create limited RSS file: {exc}")
            print("   🔄 Falling back to original RSS file")
            return self.downloaded_rss_file

    def cleanup(self):
        """Clean up test environment."""
        print("🧹 Cleaning up test environment...")

        try:
            # Restore original SSM parameter value
            if self.original_parameter_value is not None:
                self.ssm_client.put_parameter(
                    Name=self.ssm_parameter_name,
                    Value=self.original_parameter_value,
                    Type="String",
                    Overwrite=True
                )
                print(f"   ✅ Restored original SSM parameter: {self.original_parameter_value}")
            else:
                # Delete the parameter we created
                self.ssm_client.delete_parameter(Name=self.ssm_parameter_name)
                print("   ✅ Deleted test SSM parameter")

        except ClientError as error:
            print(f"   ⚠️  Failed to restore SSM parameter: {error}")

        # Clean up downloaded files (optional)
        files_to_clean = [self.downloaded_rss_file, "limited_rss.xml"]
        for file_path in files_to_clean:
            try:
                if Path(file_path).exists():
                    os.remove(file_path)
                    print(f"   ✅ Cleaned up file: {file_path}")
            except OSError as error:
                print(f"   ⚠️  Failed to clean up {file_path}: {error}")

    def run_full_test(self):
        """Run the complete functional test."""
        print("🚀 Starting functional workflow test...")
        print("=" * 60)

        try:
            # Step 1: Setup
            self.setup_test_environment()
            print()

            # Step 2: Download RSS
            self.download_rss_file()
            print()

            # Step 3: Generate email
            self.run_email_generation()
            print()

            print("=" * 60)
            print("✅ Functional test completed successfully!")
            print(f"📄 Check the output file: {self.output_file}")

            # Show final output info
            if Path(self.output_file).exists():
                file_size = Path(self.output_file).stat().st_size
                print(f"📊 Output file size: {file_size} bytes")
                print(f"🌐 Open in browser: file://{Path(self.output_file).absolute()}")

        except Exception as exc:
            print("=" * 60)
            print(f"❌ Functional test failed: {exc}")
            print("🔍 Check your AWS credentials and environment variables")
            raise
        finally:
            print()
            self.cleanup()


def main():
    """Main function to run the functional test."""
    # Check for required environment variables
    required_env_vars = {
        "AWS_DEFAULT_REGION": "AWS region",
        "AWS_ACCESS_KEY_ID": "AWS access key (or use AWS profile)",
        "AWS_SECRET_ACCESS_KEY": "AWS secret key (or use AWS profile)"
    }

    print("🔍 Checking environment...")
    missing_vars = []
    for var, description in required_env_vars.items():
        if var not in os.environ:
            # AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY might not be needed if using AWS profile
            if var.startswith("AWS_ACCESS") or var.startswith("AWS_SECRET"):
                print(f"   ⚠️  {var} not set (using AWS profile or instance role)")
            else:
                missing_vars.append(f"{var} ({description})")
        else:
            print(f"   ✅ {var} is set")

    if missing_vars:
        print("❌ Missing required environment variables:")
        for var in missing_vars:
            print(f"   - {var}")
        print("\nPlease set these variables or configure AWS credentials.")
        return False

    # Optional environment variables for Claude
    claude_vars = [
        "CLAUDE_ENABLED",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY_PARAMETER",
        "CLAUDE_MODEL"
    ]
    # Test configuration options
    test_config_vars = ["FAST_MODE", "MAX_TEST_ARTICLES"]
    print("\n⚡ Test configuration:")
    for var in test_config_vars:
        if var in os.environ:
            print(f"   ✅ {var}: {os.environ[var]}")
        else:
            default_values = {"FAST_MODE": "false", "MAX_TEST_ARTICLES": "15"}
            print(f"   📝 {var}: {default_values.get(var, 'not set')} (default)")

    print("\n🤖 Claude configuration:")
    for var in claude_vars:
        if var in os.environ:
            print(f"   ✅ {var}: [set]")
        else:
            print(f"   ⚠️  {var}: not set")

    # Show usage tip for faster testing
    if not os.environ.get("FAST_MODE"):
        print("\n💡 Tip: For faster testing with Claude, set FAST_MODE=true")
        print("   This limits the test to 15 articles instead of processing the full feed")
        print("   Example: FAST_MODE=true uv run python test_functional_workflow.py")

    print()

    # Run the test
    test = FunctionalWorkflowTest()
    try:
        test.run_full_test()
        return True
    except KeyboardInterrupt:
        print("\n⚠️  Test interrupted by user")
        return False
    except (ClientError, ValueError, OSError) as exc:
        print(f"\n💥 Test failed with error: {exc}")
        return False


if __name__ == "__main__":
    SUCCESS = main()
    sys.exit(0 if SUCCESS else 1)
