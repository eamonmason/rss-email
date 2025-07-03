# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RSS Email is an AWS Lambda-based serverless application that aggregates RSS feeds, processes articles using Claude AI, and sends curated daily email newsletters. The architecture uses event-driven AWS services with Infrastructure as Code via CDK.

## Development Commands

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

## Testing Strategy

- Unit tests in `/tests/` directory cover all core modules
- Tests use `moto` for AWS service mocking
- Integration tests validate RSS feed processing and email formatting
- CI runs tests against Python 3.13
- Pylint enforces code quality with 9.9+ score requirement
- All Python code must conform to PEP 8 standards (enforced by flake8)

## Development Workflow

When making changes to Python code, always follow this workflow:

1. **Make your changes** to the Python code
2. **Run unit tests** to ensure functionality: `uv run python -m pytest tests`
3. **Run linting** to ensure code quality: `uv run pylint --fail-under=9.9 $(git ls-files '*.py') && uv run flake8`
4. **Update documentation** if the changes affect public APIs or functionality
5. **Ensure PEP compliance** - flake8 will catch most PEP 8 violations automatically

## Deployment

### Prerequisites
- Domain configured in SES for sending emails
- CDK bootstrapped in target AWS account/region: `cdk bootstrap aws://<account-id>/<region>`
- For pipeline deployment: GitHub token stored in AWS Secrets Manager as `github-token`

### Pipeline Parameters
Store these values in AWS Parameter Store with `rss-email-` prefix:
- `AWS_ACCOUNT_ID`, `AWS_REGION`
- `EMAIL_RECIPIENTS`, `SOURCE_DOMAIN`, `SOURCE_EMAIL_ADDRESS`, `TO_EMAIL_ADDRESS`

Post-deployment, the SES Rule Set must be manually activated in the AWS console.