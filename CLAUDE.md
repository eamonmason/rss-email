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

# Run flake8 linting
uv run flake8
```

### Local Development
```bash
# Test RSS retrieval locally (outputs to console, doesn't store in S3)
uv run python src/rss_email/retrieve_articles.py <feed_url_json_file>

# Test email formatting locally (doesn't actually send email)
uv run python src/rss_email/email_articles.py
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
- **retrieve_articles.py**: Lambda function that fetches RSS feeds and stores aggregated data in S3
- **email_articles.py**: Lambda function that processes stored articles and sends formatted emails via SES  
- **article_processor.py**: Claude AI integration for intelligent article categorization and summarization
- **lib/rss_lambda_stack.ts**: Main CDK infrastructure stack defining all AWS resources

### Data Flow
1. Scheduled Lambda retrieves articles from RSS feeds configured in `feed_urls.json`
2. Articles are processed through Claude API for categorization (Technology, AI/ML, Cybersecurity, etc.)
3. Processed articles are stored in S3 
4. Email Lambda formats articles into HTML email and sends via SES
5. Error handling and logging via SNS and CloudWatch

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