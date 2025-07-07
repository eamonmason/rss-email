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
- **models.py**: Shared Pydantic models for consistent data validation across the application
- **lib/rss_lambda_stack.ts**: Main CDK infrastructure stack defining all AWS resources
- **cli_article_processor.py**: CLI tool for testing article processing with Claude API locally
- **compression_utils.py**: Utilities for compressing/decompressing article data for S3 storage
- **json_repair.py**: JSON repair utilities for handling malformed API responses
- **json_utils.py**: JSON extraction and validation utilities with Pydantic integration

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

## Code Standards and Best Practices

### Pydantic Usage
This project uses Pydantic extensively for data validation and consistency. Follow these guidelines:

#### Shared Models (`src/rss_email/models.py`)
- **RSSItem**: Use for all RSS article data across the application
- **FeedConfig/FeedList**: Use for RSS feed configuration validation
- **ApplicationSettings**: Use for centralized configuration management
- **ClaudeResponse**: Use for Claude API response validation

#### Function Validation
Apply `@pydantic.validate_call` decorator to functions that:
- Accept complex data structures (Dict, List, custom types)
- Handle external data (API responses, file parsing)
- Perform data transformation or validation
- Have multiple parameters that benefit from type checking

```python
@pydantic.validate_call(validate_return=True)
def process_articles(articles: List[RSSItem]) -> str:
    # Function implementation
    pass

# For functions with non-standard types (like ElementTree.Element)
@pydantic.validate_call(config={"arbitrary_types_allowed": True})
def parse_xml(element: ElementTree.Element) -> Dict[str, str]:
    # Function implementation
    pass
```

#### Backward Compatibility
- Always include fallback imports for Pydantic models
- Maintain compatibility when models aren't available
- Use try/except blocks for model instantiation with fallbacks

```python
try:
    from .models import RSSItem
except ImportError:
    RSSItem = None

# In functions
if RSSItem is not None:
    try:
        item = RSSItem(**data)
    except Exception:
        # Fallback to raw dict
        item = data
else:
    item = data
```

### HTML Email Development

#### Mobile-First Design
All HTML email templates must be mobile-friendly:

- **Base font size**: 16px minimum (18px+ on mobile)
- **Touch targets**: Minimum 44px for interactive elements
- **Line height**: 1.6+ for better readability
- **Responsive breakpoints**: 768px (tablet), 480px (mobile)

#### Template Structure
- Use `<meta name="viewport" content="width=device-width, initial-scale=1.0">`
- Combine CSS media queries with inline styles for email client compatibility
- Maintain table-based layouts for better email client support

#### Font Size Guidelines
```css
/* Desktop */
body { font-size: 16px; }
h1 { font-size: 1.8em; }
.article-title { font-size: 1.15em; }
.article-summary { font-size: 1em; }

/* Mobile (768px and below) */
body { font-size: 18px; }
.article-title { font-size: 1.1em; }

/* Small mobile (480px and below) */
body { font-size: 20px; }
```

#### Email Client Compatibility
- Test with table-based layouts
- Use inline styles for critical styling
- Provide fallbacks for CSS features
- Avoid JavaScript and complex CSS animations

### Error Handling Patterns

#### RSS Processing
- Always validate RSS date formats before parsing
- Skip malformed items with appropriate logging
- Provide meaningful error messages for debugging

```python
try:
    published_date = time.mktime(
        datetime.strptime(str(item_dict["pubDate"]), "%a, %d %b %Y %H:%M:%S %Z").timetuple()
    )
except ValueError as e:
    logger.warning("Failed to parse pubDate '%s': %s", item_dict["pubDate"], str(e))
    continue
```

#### API Integration
- Always include fallback behavior for external API failures
- Log errors with sufficient context for debugging
- Use rate limiting and token management for API calls

### Documentation Updates
When making significant changes:
1. Update relevant README sections
2. Update CLAUDE.md with new patterns or learnings
3. Add inline code comments for complex logic
4. Update function docstrings with parameter and return type information

### Testing Requirements
- Maintain pylint score of 9.9+ 
- Ensure PEP 8 compliance with flake8
- Write tests for new Pydantic models and validation
- Test mobile HTML rendering across different screen sizes
- Test backward compatibility scenarios

## Common Development Patterns

### Model Validation with Fallbacks
When updating existing code to use Pydantic models:

```python
# Pattern for optional model usage
@pydantic.validate_call(validate_return=True)
def process_data(input_data: str) -> Union[List[RSSItem], List[Dict]]:
    parsed_items = []
    for item_data in raw_data:
        if RSSItem is not None:
            try:
                parsed_items.append(RSSItem(**item_data))
            except Exception as e:
                logger.warning("Model validation failed: %s", str(e))
                parsed_items.append(item_data)  # Fallback to raw dict
        else:
            parsed_items.append(item_data)
    return parsed_items
```

### Configuration Management
Use ApplicationSettings for centralized config with environment variable support:

```python
# Prefer centralized settings over scattered os.environ calls
settings = ApplicationSettings(
    bucket=os.environ["BUCKET"],
    key=os.environ["KEY"],
    email=EmailSettings(
        source_email_address=os.environ["SOURCE_EMAIL_ADDRESS"],
        to_email_address=os.environ["TO_EMAIL_ADDRESS"]
    )
)
```

### HTML Template Updates
When modifying email templates:

1. **Always test mobile rendering** - Use browser dev tools to simulate mobile
2. **Maintain email client compatibility** - Test with major email clients
3. **Update both CSS and inline styles** - Ensure compatibility across clients
4. **Use progressive enhancement** - Start with basic layout, enhance with CSS

### File Organization
- **models.py**: All Pydantic models and shared data structures
- **{module}_articles.py**: Core business logic with Pydantic validation  
- **{module}.html**: Email templates with mobile-first responsive design
- **json_utils.py**: JSON parsing utilities with validation

### Import Patterns
Consistent import structure for better maintainability:

```python
# Standard library imports
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

# Third-party imports
import pydantic
from pydantic import BaseModel, Field

# Local imports with fallbacks
try:
    from .models import RSSItem, FeedList
except ImportError:
    RSSItem = None
    FeedList = None
```