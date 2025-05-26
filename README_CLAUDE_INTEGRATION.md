# Claude Integration for RSS Email

This document describes the Claude AI integration that has been added to the RSS Email system to provide intelligent categorization, summarization, and grouping of RSS articles.

## Overview

The RSS Email system now integrates with Anthropic's Claude API to:
- Categorize articles into meaningful topics (Technology, Business, Science, etc.)
- Generate concise 2-3 sentence summaries of each article
- Group related articles together
- Prioritize tech-related content over entertainment/lifestyle content
- Preserve all articles (no articles are lost in the process)

## Architecture Changes

### New Files
1. **`src/rss_email/article_processor.py`** - Handles all Claude API interactions
2. **`src/rss_email/email_body_enhanced.html`** - Enhanced email template with categorized layout

### Modified Files
1. **`lib/rss_lambda_stack.ts`** - Added environment variables and permissions for Claude integration
2. **`src/rss_email/email_articles.py`** - Modified to use Claude processing with fallback
3. **`pyproject.toml`** - Added `anthropic` dependency

## Configuration

### Environment Variables

The following environment variables are configured in the CDK stack:

- `ANTHROPIC_API_KEY_PARAMETER`: Name of the AWS Parameter Store parameter containing the API key (default: `rss-email-anthropic-api-key`)
- `CLAUDE_MODEL`: The Claude model to use (default: `claude-3-5-haiku-latest`)
- `CLAUDE_MAX_TOKENS`: Maximum tokens per request (default: `100000`)
- `CLAUDE_MAX_REQUESTS`: Maximum API requests per Lambda execution (default: `5`)
- `CLAUDE_ENABLED`: Feature flag to enable/disable Claude processing (default: `true`)

### AWS Parameter Store

Before deploying, you need to create a parameter in AWS Parameter Store:

```bash
aws ssm put-parameter \
    --name "rss-email-anthropic-api-key" \
    --value "your-anthropic-api-key" \
    --type "SecureString" \
    --description "Anthropic API key for RSS email categorization"
```

## Features

### Intelligent Categorization

Articles are categorized into the following categories (in priority order):
1. Technology, AI/ML, Cybersecurity, Programming, Science
2. Business, Politics, Health, Environment
3. Entertainment, Gaming, Cycling, Media/TV/Film
4. Other

### Article Summaries

Each article receives a 2-3 sentence summary that captures the key information while being concise enough for quick scanning.

### Related Article Grouping

Similar articles covering the same topic or event are identified and grouped together, reducing redundancy in the email.

### Enhanced Email Template

The new email template features:
- Category-based sections with distinct styling
- Collapsible article descriptions (show more/less functionality)
- Related article indicators
- Metadata showing generation time and article counts
- Responsive design for mobile devices

## Error Handling and Fallback

The system includes robust error handling:
- If Claude API fails, the system falls back to the original HTML generation
- All errors are logged to CloudWatch
- API usage is monitored and limited to prevent excessive costs

## Monitoring

The integration logs the following metrics to CloudWatch:
- API usage (tokens consumed)
- Processing time
- Success/failure rates
- Fallback activations

## Cost Considerations

- The system uses Claude 3 Sonnet for cost efficiency
- Token usage is limited per request and per Lambda execution
- Only new articles since the last run are processed

## Testing

### Comprehensive Test Suite

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

### Manual Testing

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

### Testing Without AWS

For local testing without AWS credentials, you can:

1. Set the API key directly:
```bash
export ANTHROPIC_API_KEY="your-api-key"
```

2. Modify the test script to use the direct API key instead of Parameter Store

## Deployment

1. Ensure the Anthropic API key is stored in Parameter Store
2. Deploy the CDK stack:
```bash
npm run cdk deploy
```

The Lambda function timeout has been increased from 30s to 60s to accommodate Claude API calls.

## Disabling Claude Integration

To disable Claude integration and revert to the original behavior:
1. Set the `CLAUDE_ENABLED` environment variable to `false` in the Lambda configuration
2. Or remove the Anthropic API key from Parameter Store

The system will automatically fall back to the original HTML generation.
