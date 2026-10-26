# RSS Emailer

<img src="assets/logo.png" alt="RSS Emailer Logo" width="33%">

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

### Pre-commit Hooks

The project uses pre-commit hooks to automatically check code quality before commits. These hooks match the CI checks and help catch issues early.

```bash
# Install pre-commit hooks (one-time setup)
uv run pre-commit install
uv run pre-commit install --hook-type pre-push

# Run hooks manually on all files
uv run pre-commit run --all-files

# Run hooks on staged files only
uv run pre-commit run
```

**Pre-commit checks include:**

- File quality checks (trailing whitespace, end-of-file, YAML/JSON validation)
- Flake8 linting (PEP 8 compliance)
- Pylint code quality (9.9+ score required)
- Unit tests (on pre-push only)
- CDK synth validation (on pre-push only, for CDK infrastructure changes)

The hooks will automatically fix some issues (trailing whitespace, end-of-file). For other issues, you'll need to fix them manually before committing.

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
- **retrieve_articles.py**: Lambda function that fetches RSS feeds and stores aggregated data in S3
- **email_articles.py**: Lambda function that processes stored articles and sends formatted emails via SES (triggered by Step Functions)
- **generate_podcast.py**, **podcast_script.py**, **podcast_tts.py**, **podcast_generator.py**: Single-narrator podcast built from the RSS Brief (Claude script, Phonon speech, feed publishing)
- **article_processor.py**: Claude AI integration for intelligent article categorization and summarization
- **lib/rss_lambda_stack.ts**: Main CDK infrastructure stack defining all AWS resources including Step Functions workflow
- **cli_article_processor.py**: CLI tool for testing article processing with Claude API locally
- **compression_utils.py**: Utilities for compressing/decompressing article data for S3 storage
- **json_repair.py**: JSON repair utilities for handling malformed API responses

### Data Flow

**Every 3 hours:**
1. **RSSGenerationFunction** fetches articles from configured RSS feeds and stores aggregated data in S3

**Daily at 7:30 AM (weekdays):**
1. **Step Functions workflow** orchestrates the daily newsletter process:
   - **Step 1**: Retrieve articles from S3
   - **Step 2 (Email branch)**: Submit batch → Poll for completion → Generate and send the digest and RSS Brief
   - **Step 3 (Podcast)**: Narrate the brief with Claude and Phonon, then update the podcast feed
2. **Message Batches API** provides 50% cost savings for the email digest's asynchronous batch processing
3. Error handling and logging via SNS and CloudWatch with automated alerts

### AWS Services Used
- **Step Functions**: Orchestrates the daily workflow: email digest and brief, then the podcast
- **Lambda**: Serverless execution for individual workflow steps
- **S3**: Storage for RSS data, configuration, and podcast audio files
- **SES**: Email sending and receiving
- **SNS**: Error notifications
- **CloudWatch**: Logging and monitoring
- **EventBridge**: Scheduled triggers for workflows
- **ECR / Lambda container image**: Hosts the Phonon text-to-speech podcast function
- **Parameter Store**: Environment configuration
- **CloudFront**: Content delivery for podcast RSS feed

### Scheduling

The system runs on the following schedule (all times in UTC):

- **RSS Retrieval**: Every 3 hours (`0 */3 * * *`) - Fetches latest articles from configured RSS feeds and stores in S3
- **Daily Newsletter Workflow**: 7:30 AM, Monday-Friday (`30 7 * * 2-6`) - Triggers Step Functions workflow that:
  - Retrieves aggregated articles from S3
  - Processes the email digest using Message Batches API and sends it with the brief
  - Publishes the podcast episode narrating the brief

**Manual Triggering:**

You can manually trigger workflows using the AWS CLI:

```bash
# Trigger RSS retrieval
aws lambda invoke --function-name <RSSGenerationFunction-name> output.json

# Trigger the daily newsletter workflow (Step Functions)
aws stepfunctions start-execution \
  --state-machine-arn <DailyRSSNewsletterWorkflow-arn> \
  --input '{"trigger":"manual"}' \
  output.json
```

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

#### Mastodon source
An optional `mastodon` block adds link posts from your Mastodon home timeline as articles (the
shared page is the article, the post is its discussion link):
```json
{"mastodon": {"base_url": "https://mastodon.social", "max_articles": 15}}
```
Create the access token (`read:statuses` scope) as an SSM SecureString named
`rss-email-mastodon-token`. Without the block or the token the source is skipped.

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
- `CLAUDE_MODEL`: The Claude model to use (default: `claude-haiku-4-5`)
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

- The system uses Claude Haiku 4.5 for cost efficiency
- Token usage is limited per request and per Lambda execution
- Only new articles since the last run are processed

### Disabling Claude Integration

To disable Claude integration and revert to the original behavior:
1. Set the `CLAUDE_ENABLED` environment variable to `false` in the Lambda configuration
2. Or remove the Anthropic API key from Parameter Store

The system will automatically fall back to the original HTML generation.

## Podcast Generation

The podcast is a single-narrator, Economist-style audio edition of the daily
**RSS Brief**. It does not re-summarise the raw feed: the brief has already chosen
what matters, so the narration is written from the brief's "Read these" list, themes,
cross-cutting signals and personal items.

### How it works

1. `retrieve_and_send_email` sends the brief and, on success, stores a flattened
   narration input (titles, sources, why-it-matters; no ids or URLs) at
   `podcast/narration-input.json`.
2. After the email branch, the `GeneratePodcast` Lambda (`generate_podcast.py`):
   - skips cleanly if there is no brief for today or today's episode already exists;
   - asks Claude (`PODCAST_CLAUDE_MODEL`, default `claude-sonnet-5-5`) to write a
     1,100-1,400 word narration (`podcast_script.py`), reusing the brief's fidelity rules;
   - voices it with [Gradium Phonon](https://gradium.ai/blog/gradium-releases-phonon)
     through the `ptts` package (`podcast_tts.py`) and encodes it as 24 kHz mono MP3;
   - uploads `podcasts/episodes/rss-brief-{date}.mp3`, updates `podcasts/feed.xml` and
     invalidates the CloudFront cache (`podcast_generator.py`).

### Speech model

Phonon is a small (31M parameter) CPU-friendly text-to-speech model that needs no
PyTorch. Only the public Apache-2.0 `gradium/phonon-31m-en` weights are used; the 40M
models are gated and non-commercial. The Lambda is a container image
(`docker/podcast/Dockerfile`) with the weights baked in at build time, so there is no
runtime download. Pick the voice with `PODCAST_VOICE` (default `Hugh`; `Toby` and `Harper` are also baked into the image). To change it,
set `PODCAST_VOICE` at deploy time or edit the default in `lib/rss_lambda_stack.ts`.

Try voices locally without AWS or Anthropic credentials:

```bash
uv run python generate_test_podcast.py Hugh sample.mp3
```

### Podcast RSS feed

- Standard podcast RSS 2.0 feed with iTunes tags, the 10 most recent episodes
- Served through CloudFront; the domain is in SSM parameter `rss-podcast-cloudfront-domain`
- Add the feed URL to a podcast app (Apple Podcasts: Library, Edit, Add a Show by URL)

### Configuration

Set in `lib/rss_lambda_stack.ts` on the `GeneratePodcast` Lambda:

- `BUCKET`, `ANTHROPIC_API_KEY_PARAMETER`
- `PODCAST_CLAUDE_MODEL`, `PODCAST_VOICE`
- `PODCAST_CLOUDFRONT_DISTRIBUTION_ID`, `PODCAST_CLOUDFRONT_DOMAIN_PARAMETER`

### Troubleshooting

- **No episode:** check the `GeneratePodcast` logs. "No brief for ..." means the brief was
  disabled, empty or failed that day.
- **Feed not updating:** check S3 write access to `podcasts/feed.xml` and the CloudFront
  invalidation permission.
- **Voice or pacing:** adjust `PODCAST_VOICE` or the prompt in `podcast_script.py`.

### Disabling the podcast

Remove the `Generate Podcast` task from the workflow in `lib/rss_lambda_stack.ts` and redeploy.

## Deployment

Deployment is continuous: every push to `main` runs the
[`Deploy` workflow](.github/workflows/deploy.yml), which runs the lint and test suite
and then deploys the CloudFormation stack `cd-RSSEmailStack`.

The workflow authenticates to AWS with GitHub OIDC — there are no long-lived AWS keys
in GitHub. Deployment config is read from Parameter Store at deploy time, so Parameter
Store remains the single source of truth and GitHub holds no copies of it.

### CDK Operations

```bash
# Synthesize the application stack
npx cdk synth

# View deployment differences against the deployed stack
npx cdk diff RSSEmailStack

# Deploy manually (normally left to GitHub Actions)
npx cdk deploy RSSEmailStack
```

The construct id is `RSSEmailStack` but the deployed CloudFormation stack is named
`cd-RSSEmailStack`, retained from when deployment ran through a CDK Pipelines stage
named `cd`. Use the construct id on the command line; the CLI resolves it to the
deployed stack.

Note that `cdk deploy` requires Docker to build the Lambda layer. Set `CDK_DOCKER=false`
to fall back to local `pip` bundling — fine for `synth`/`diff`, but it produces
macOS/ARM wheels, so never use it for a real deploy.

### One-time Deployment Setup

#### 1. Parameter Store values

Put the following in Parameter Store, with appropriate values (as described above with
the `.env` file):

- `rss-email-AWS_ACCOUNT_ID`
- `rss-email-AWS_REGION`
- `rss-email-EMAIL_RECIPIENTS`
- `rss-email-SOURCE_DOMAIN`
- `rss-email-SOURCE_EMAIL_ADDRESS`
- `rss-email-TO_EMAIL_ADDRESS`

#### 2. The GitHub Actions deploy role

`lib/github_actions_role_stack.ts` defines the IAM role that GitHub Actions assumes via
OIDC. It is scoped to `refs/heads/main` of this repository, and grants only the ability
to assume the CDK bootstrap roles and read the parameters above.

It imports the account's existing `token.actions.githubusercontent.com` OIDC provider
rather than creating one — if the account has no provider yet, create it first (see the
[AWS OIDC documentation](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html)).

Deploy it once, by hand:

```sh
npx cdk deploy --app "npx tsx bin/github-oidc-cdk.ts"
```

#### 3. GitHub repository variables

Under Settings → Secrets and variables → Actions → Variables, set:

- `AWS_DEPLOY_ROLE_ARN` — the `DeployRoleArn` output from the stack above
- `AWS_REGION` — the deployment region, e.g. `eu-west-1`

#### 4. Feed configuration

Once the first deploy has completed successfully, upload the `feed_urls.json` file to
the new S3 bucket, for example:

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
export CLAUDE_MODEL="claude-haiku-4-5"
```

2. Run the email generation:
```bash
python src/rss_email/email_articles.py bucket-name articles.json --local-file test-articles.json
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
export CLAUDE_MODEL=claude-haiku-4-5
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

📥 Downloading articles JSON from S3...
   ✅ Downloaded articles JSON to: downloaded_articles.json
   📊 File size: 158188 characters
   📰 Found 126 articles

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
   ✅ Cleaned up downloaded articles JSON: downloaded_articles.json
```

#### Output Files

The test generates:
- `functional_test_output.html` - The generated email HTML (can be opened in a browser)
- `downloaded_articles.json` - Temporarily downloaded articles JSON (cleaned up automatically)

### Configuration Options

You can modify the test configuration by editing these variables in the script:

```python
class FunctionalWorkflowTest:
    def __init__(self):
        self.ssm_parameter_name = "rss-email-lastrun"
        self.s3_bucket = "cd-rssemailstack-rssbucket91adb797-1ds7r89g7wdoo"
        self.s3_key = "articles.json"
        self.output_file = "functional_test_output.html"
        self.downloaded_rss_file = "downloaded_articles.json"
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
rm -f functional_test_output.html downloaded_articles.json

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
