# RSS Emailer

![Lint and Unit Testing](https://github.com/eamonmason/rss-email/actions/workflows/lint_and_test.yml/badge.svg)

RSS Email is an AWS Lambda-based serverless application that aggregates RSS feeds, processes articles using Claude AI, and sends curated daily email newsletters. The architecture uses event-driven AWS services with Infrastructure as Code via CDK.

## Quick Start

### Setup and Dependencies

```bash
# Install Python dependencies
uv sync

# Install Node.js dependencies for CDK
npm install
```

### Testing

```bash
# Run all unit tests
uv run python -m pytest tests

# Run specific test file
uv run python -m pytest tests/test_specific_module.py
```

### Linting and Code Quality

```bash
# Run pylint (must score 9.9+ to pass CI)
uv run pylint --fail-under=9.9 $(git ls-files '*.py')

# Run flake8 linting (enforces PEP 8 standards)
uv run flake8

# Run both linting tools together
uv run pylint --fail-under=9.9 $(git ls-files '*.py') && uv run flake8
```

### Local Development

```bash
# Test RSS retrieval locally (outputs to console, doesn't store in S3)
uv run python src/rss_email/retrieve_articles.py <feed_url_json_file>

# Test email formatting locally (doesn't actually send email)
uv run python src/rss_email/email_articles.py

# Test all feeds in feed_urls.json for connectivity
uv run python tests/test_all_feeds.py

# Run CLI article processor for testing Claude integration
uv run python src/cli_article_processor.py
```

## Architecture

### Core Components
- **retrieve_articles.py**: Key orchestrating Lambda function that fetches RSS feeds and stores aggregated data in S3
- **email_articles.py**: Key orchestrating Lambda function that processes stored articles and sends formatted emails via SES  
- **article_processor.py**: Claude AI integration for intelligent article categorization and summarization
- **lib/rss_lambda_stack.ts**: Main CDK infrastructure stack defining all AWS resources
- **cli_article_processor.py**: CLI tool for testing article processing with Claude API locally
- **compression_utils.py**: Utilities for compressing/decompressing article data for S3 storage
- **json_repair.py**: JSON repair utilities for handling malformed API responses

### Data Flow
1. **retrieve_articles.py** orchestrates RSS feed processing: fetches articles from feeds configured in `feed_urls.json`, creates aggregated file of recent articles, and stores in S3
2. **email_articles.py** orchestrates email delivery: retrieves aggregated articles from S3, processes them through Claude API for categorization (Technology, AI/ML, Cybersecurity, etc.), formats into HTML email, and sends via SES
3. Error handling and logging via SNS and CloudWatch with automated alerts

### AWS Services Used
- **Lambda**: Serverless execution
- **S3**: Storage for RSS data and configuration  
- **SES**: Email sending and receiving
- **SNS**: Error notifications
- **CloudWatch**: Logging and monitoring
- **Parameter Store**: Environment configuration

## Configuration

### Environment Variables

Create a `.env` file in the base directory of the project, for your custom settings:

```sh
SOURCE_DOMAIN="mydomain.com"
SOURCE_EMAIL_ADDRESS="rss@mydomain"
TO_EMAIL_ADDRESS="me@someemailprovider.com"
EMAIL_RECIPIENTS="morerssplease@onedomain.com,sendmerss@twodomain.com"
```

### Required Environment Variables
- `SOURCE_DOMAIN`: Email sending domain (must be verified in SES)
- `SOURCE_EMAIL_ADDRESS`: From email address
- `TO_EMAIL_ADDRESS`: Primary recipient email
- `EMAIL_RECIPIENTS`: Comma-separated list of recipient emails
- `AWS_ACCOUNT_ID` & `AWS_REGION`: AWS deployment configuration

### Feed Configuration
RSS sources are configured in `feed_urls.json` with this structure:
```json
{
  "feeds": [
    {
      "name": "Feed Name",  
      "url": "https://example.com/feed.xml"
    }
  ]
}
```

### CDK Pre-requisites

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

## Claude Integration

The RSS Email system integrates with Anthropic's Claude API to provide intelligent categorization, summarization, and grouping of RSS articles.

### Features

#### Intelligent Categorization

Articles are categorized into the following categories (in priority order):
1. Technology, AI/ML, Cybersecurity, Programming, Science
2. Business, Politics, Health, Environment
3. Entertainment, Gaming, Cycling, Media/TV/Film
4. Other

#### Article Summaries

Each article receives a 2-3 sentence summary that captures the key information while being concise enough for quick scanning.

#### Related Article Grouping

Similar articles covering the same topic or event are identified and grouped together, reducing redundancy in the email.

#### Enhanced Email Template

The enhanced email template features:
- Category-based sections with distinct styling
- Collapsible article descriptions (show more/less functionality)
- Related article indicators
- Metadata showing generation time and article counts
- Responsive design for mobile devices

### Configuration

#### Environment Variables

The following environment variables are configured in the CDK stack:

- `ANTHROPIC_API_KEY_PARAMETER`: Name of the AWS Parameter Store parameter containing the API key (default: `rss-email-anthropic-api-key`)
- `CLAUDE_MODEL`: The Claude model to use (default: `claude-3-5-haiku-latest`)
- `CLAUDE_MAX_TOKENS`: Maximum tokens per request (default: `100000`)
- `CLAUDE_MAX_REQUESTS`: Maximum API requests per Lambda execution (default: `5`)
- `CLAUDE_ENABLED`: Feature flag to enable/disable Claude processing (default: `true`)

#### AWS Parameter Store

Before deploying, you need to create a parameter in AWS Parameter Store:

```bash
aws ssm put-parameter \
    --name "rss-email-anthropic-api-key" \
    --value "your-anthropic-api-key" \
    --type "SecureString" \
    --description "Anthropic API key for RSS email categorization"
```

### Error Handling and Fallback

The system includes robust error handling:
- If Claude API fails, the system falls back to the original HTML generation
- All errors are logged to CloudWatch
- API usage is monitored and limited to prevent excessive costs

### Monitoring

The integration logs the following metrics to CloudWatch:
- API usage (tokens consumed)
- Processing time
- Success/failure rates
- Fallback activations

### Cost Considerations

- The system uses Claude 3 Haiku for cost efficiency
- Token usage is limited per request and per Lambda execution
- Only new articles since the last run are processed

### Disabling Claude Integration

To disable Claude integration and revert to the original behavior:
1. Set the `CLAUDE_ENABLED` environment variable to `false` in the Lambda configuration
2. Or remove the Anthropic API key from Parameter Store

The system will automatically fall back to the original HTML generation.

## Deployment

### Deploy the Stack

Deploy the stack with CDK by running:

```sh
cdk deploy
```

### CDK Operations

```bash
# Synthesize CDK stack
npx cdk synth

# Deploy main application stack
cdk deploy

# Deploy pipeline stack
cdk deploy --app "npx ts-node bin/pipeline-cdk.ts"

# View deployment differences
cdk diff
```

### Pipeline Deployment

Add a GitHub personal token to AWS Secrets Manager, for the github repo, called `github-token`.

Put the following environment variables in parameter store, with appropriate values (as described above with the `.env` file):

- `rss-email-AWS_ACCOUNT_ID`
- `rss-email-AWS_REGION`
- `rss-email-EMAIL_RECIPIENTS`
- `rss-email-SOURCE_DOMAIN`
- `rss-email-SOURCE_EMAIL_ADDRESS`
- `rss-email-TO_EMAIL_ADDRESS`

Deploy the pipeline itself:

```sh
cdk deploy --app "npx ts-node bin/pipeline-cdk.ts"
```

Once the deploy has completed successfully, upload the `feed_urls.json` file to the new S3 bucket, for example:

```json
{
    "feeds": [
        {
            "name": "Krebs on Security",
            "url": "https://krebsonsecurity.com/feed/"
        },        
        {
            "name": "The Register",
            "url": "http://www.theregister.co.uk/data_centre/cloud/headlines.atom"
        }
    ]
}
```

See [https://docs.aws.amazon.com/cdk/v2/guide/cdk_pipeline.html#cdk_pipeline_security](https://docs.aws.amazon.com/cdk/v2/guide/cdk_pipeline.html#cdk_pipeline_security) for more info.

#### Pipeline Parameters
Store these values in AWS Parameter Store with `rss-email-` prefix:
- `AWS_ACCOUNT_ID`, `AWS_REGION`
- `EMAIL_RECIPIENTS`, `SOURCE_DOMAIN`, `SOURCE_EMAIL_ADDRESS`, `TO_EMAIL_ADDRESS`

## Post-deployment

To receive email correctly, post deployment the SES Active Rule Set has to be enabled. Go to "Email receiving", select the RSSRuleSet resource and click "Set as Active".

Post-deployment, the SES Rule Set must be manually activated in the AWS console.

## Testing

### Unit Tests

Unit tests in `/tests/` directory cover all core modules:
- Tests use `moto` for AWS service mocking
- Integration tests validate RSS feed processing and email formatting
- CI runs tests against Python 3.13
- Pylint enforces code quality with 9.9+ score requirement
- All Python code must conform to PEP 8 standards (enforced by flake8)

### Claude Integration Testing

#### Comprehensive Test Suite

A comprehensive test script `test_article_processor.py` is provided to test all aspects of the Claude integration:

```bash
# Run all tests
python test_article_processor.py

# Run specific tests
python test_article_processor.py rate_limiter  # Test rate limiting
python test_article_processor.py tokens        # Test token estimation
python test_article_processor.py prompt        # Test prompt creation
python test_article_processor.py api_key       # Test API key retrieval
python test_article_processor.py claude        # Test Claude processing
python test_article_processor.py fallback      # Test fallback behavior
```

The test suite includes:
- **Rate Limiter Tests**: Verifies token and request limiting
- **Token Estimation Tests**: Validates token counting logic
- **Prompt Creation Tests**: Ensures prompts are correctly formatted
- **API Key Retrieval Tests**: Tests Parameter Store integration
- **Claude Processing Tests**: Full integration test with Claude API
- **Fallback Tests**: Verifies graceful degradation

#### Manual Testing

To test the email generation locally:

1. Set up environment variables:
```bash
export ANTHROPIC_API_KEY_PARAMETER="rss-email-anthropic-api-key"
export CLAUDE_ENABLED="true"
export CLAUDE_MODEL="claude-3-5-sonnet-20241022"
```

2. Run the email generation:
```bash
python src/rss_email/email_articles.py bucket-name rss.xml --local-file test-rss.xml
```

#### Testing Without AWS

For local testing without AWS credentials, you can:

1. Set the API key directly:
```bash
export ANTHROPIC_API_KEY="your-api-key"
```

2. Modify the test script to use the direct API key instead of Parameter Store

## Functional Testing

### Overview

The `test_functional_workflow.py` script provides a comprehensive end-to-end test of the RSS email workflow. This test simulates the complete production workflow locally without sending actual emails.

### What the Test Does

1. **Sets SSM Parameter**: Updates the `rss-email-lastrun` parameter to 24 hours ago
2. **Downloads RSS Data**: Fetches the current RSS articles from the production S3 bucket
3. **Generates Email**: Runs the complete email generation workflow including Claude processing (if configured)
4. **Outputs HTML**: Writes the result to a local HTML file instead of sending an email
5. **Cleanup**: Restores the original SSM parameter and cleans up temporary files

### Prerequisites

#### AWS Credentials
You need AWS credentials configured for the target account. This can be done via:
- AWS CLI profile: `aws configure`
- Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- IAM instance role (if running on EC2)

#### Required AWS Permissions
The test requires the following AWS permissions:
- `ssm:GetParameter` - Read SSM parameters
- `ssm:PutParameter` - Update SSM parameters  
- `ssm:DeleteParameter` - Clean up test parameters
- `s3:GetObject` - Download RSS files from S3

#### Environment Variables
Set the AWS region:
```bash
export AWS_DEFAULT_REGION=us-east-1  # or your target region
```

#### Optional: Claude Configuration

**Method 1: Using .env file (Recommended for local testing)**
Create a `.env` file in the project root:
```bash
# Copy the example file and edit it
cp .env.example .env
# Edit .env with your actual API key
```

**Method 2: Environment variables**
```bash
export CLAUDE_ENABLED=true
export ANTHROPIC_API_KEY=your-api-key-here
export CLAUDE_MODEL=claude-3-5-haiku-20241022
```

**Method 3: AWS Parameter Store (Production)**
```bash
export ANTHROPIC_API_KEY_PARAMETER=rss-email-anthropic-api-key
# Store the actual API key in AWS Parameter Store under this name
```

### Running the Test

#### Basic Usage

**Quick Test (Recommended)**
```bash
# Fast mode with limited articles for quicker Claude testing
FAST_MODE=true uv run python test_functional_workflow.py

# Or set environment variables in .env file:
# FAST_MODE=true
# MAX_TEST_ARTICLES=10
uv run python test_functional_workflow.py
```

**Full Test**
```bash
# Process all articles (may take 2-3 minutes with Claude)
uv run python test_functional_workflow.py

# Or run directly (requires dependencies installed)
python test_functional_workflow.py
```

#### Expected Output
The test provides detailed progress information:

```
🔍 Checking environment...
   ✅ AWS_DEFAULT_REGION is set
   ⚠️  AWS_ACCESS_KEY_ID not set (using AWS profile or instance role)

🤖 Claude configuration:
   ✅ CLAUDE_ENABLED: true
   ✅ ANTHROPIC_API_KEY: ********...key1234

🚀 Starting functional workflow test...
============================================================
🔧 Setting up test environment...
   📝 Saved original SSM parameter: 2025-07-02T10:30:33.811000
   ✅ Updated SSM parameter with test timestamp: 2025-07-02T10:30:33.811000

📥 Downloading RSS file from S3...
   ✅ Downloaded RSS file to: downloaded_rss.xml
   📊 File size: 158188 characters
   📰 Found 126 articles in RSS feed

🔄 Running email generation workflow...
   📅 Last run date: 2025-07-02 10:30:33.811000
   🕐 Hours since last run: 24.0
   ✅ Generated HTML email content
   📄 Output written to: functional_test_output.html
   📊 HTML size: 43656 characters
   🔍 Analyzing generated content:
      ✅ Claude-enhanced categorization detected
      📰 Estimated article count: 68
      ✅ Contains email subject
      ✅ Contains article links
      ✅ Substantial content generated

============================================================
✅ Functional test completed successfully!
📄 Check the output file: functional_test_output.html
📊 Output file size: 43910 bytes
🌐 Open in browser: file:///full/path/to/functional_test_output.html

🧹 Cleaning up test environment...
   ✅ Restored original SSM parameter: 2025-07-02T08:30:33.811000
   ✅ Cleaned up downloaded RSS file: downloaded_rss.xml
```

#### Output Files

The test generates:
- `functional_test_output.html` - The generated email HTML (can be opened in a browser)
- `downloaded_rss.xml` - Temporarily downloaded RSS file (cleaned up automatically)

### Configuration Options

You can modify the test configuration by editing these variables in the script:

```python
class FunctionalWorkflowTest:
    def __init__(self):
        self.ssm_parameter_name = "rss-email-lastrun"
        self.s3_bucket = "cd-rssemailstack-rssbucket91adb797-1ds7r89g7wdoo"
        self.s3_key = "rss.xml"
        self.output_file = "functional_test_output.html"
        self.downloaded_rss_file = "downloaded_rss.xml"
```

### Troubleshooting

#### Common Issues

**AWS Credentials Error**
```
NoCredentialsError: Unable to locate credentials
```
Solution: Configure AWS credentials using `aws configure` or set environment variables.

**Permission Denied**
```
AccessDenied: User: arn:aws:iam::123456789012:user/test is not authorized
```
Solution: Ensure your AWS user/role has the required permissions listed above.

**S3 Bucket Not Found**
```
NoSuchBucket: The specified bucket does not exist
```
Solution: Verify the S3 bucket name in the script matches your deployment.

**SSM Parameter Issues**
```
ParameterNotFound: Parameter rss-email-lastrun not found
```
This is normal - the test will create the parameter if it doesn't exist.

#### Debugging

Enable detailed logging by setting:
```bash
export PYTHONPATH=src
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
exec(open('test_functional_workflow.py').read())
"
```

#### Manual Cleanup

If the test fails and doesn't clean up properly:

```bash
# Remove test files
rm -f functional_test_output.html downloaded_rss.xml

# Reset SSM parameter (replace with your original value)
aws ssm put-parameter --name "rss-email-lastrun" --value "2025-07-02T08:30:33.811000" --type String --overwrite
```

### Integration with CI/CD

The test can be integrated into CI/CD pipelines:

```bash
# Exit code 0 on success, 1 on failure
uv run python test_functional_workflow.py
if [ $? -eq 0 ]; then
    echo "Functional test passed"
else
    echo "Functional test failed"
    exit 1
fi
```

### Comparison with Unit Tests

| Feature | Unit Tests | Functional Test |
|---------|------------|-----------------|
| Scope | Individual functions | End-to-end workflow |
| Dependencies | Mocked | Real AWS services |
| Speed | Fast | Slower |
| Reliability | High | Dependent on AWS |
| Coverage | Code paths | User scenarios |

Use this functional test to:
- Verify deployment health
- Test configuration changes
- Validate AWS permissions
- Debug production issues
- Demonstrate the complete workflow

For development and quick feedback, continue using the unit tests in the `tests/` directory.

## Development Workflow

When making changes to Python code, always follow this workflow:

1. **Make your changes** to the Python code
2. **Run unit tests** to ensure functionality: `uv run python -m pytest tests`
3. **Run linting** to ensure code quality: `uv run pylint --fail-under=9.9 $(git ls-files '*.py') && uv run flake8`
4. **Update documentation** if the changes affect public APIs or functionality
5. **Ensure PEP compliance** - flake8 will catch most PEP 8 violations automatically

## Testing Strategy

- Unit tests in `/tests/` directory cover all core modules
- Tests use `moto` for AWS service mocking
- Integration tests validate RSS feed processing and email formatting
- CI runs tests against Python 3.13
- Pylint enforces code quality with 9.9+ score requirement
- All Python code must conform to PEP 8 standards (enforced by flake8)