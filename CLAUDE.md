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

### Pre-commit Hooks

Pre-commit hooks automatically check code quality before commits. They match GitHub Actions CI checks.

```bash
# Install hooks (one-time setup - should be done after cloning)
uv run pre-commit install
uv run pre-commit install --hook-type pre-push

# Run manually on all files
uv run pre-commit run --all-files
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

**Note:** If pre-commit hooks are installed, steps 2-3 will run automatically on commit/push. You can run them manually with `uv run pre-commit run --all-files`.

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
- Use the meta tag with attributes `name="viewport" content="width=device-width, initial-scale=1.0"`
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

## Podcast Development Patterns

### Overview

The podcast generation feature (`podcast_generator.py`) creates audio podcasts from RSS articles using Claude AI for script generation and AWS Polly for text-to-speech synthesis.

### Key Components

#### Script Generation with Claude

- Use Claude 3.5 Haiku for cost-effective script generation
- Token limits: 4,000 tokens (lower than email processing to control costs)
- Prompt must specify clear speaker labels ("Marco:", "John:")
- Scripts should be 5-10 minutes in length

```python
@pydantic.validate_call(validate_return=True)
def generate_script(articles: List[Dict[str, Any]]) -> Optional[str]:
    # Generate conversational podcast script
    # Returns script with speaker labels or None on error
```

#### Speaker Parsing

Parse scripts to identify speaker segments and their text:

```python
@pydantic.validate_call(validate_return=True)
def parse_speaker_segments(script: str) -> List[Tuple[str, str]]:
    """
    Extract (speaker, text) tuples from script.
    Handles multi-line segments without speaker labels.
    """
    # Implementation splits on "Marco:" and "John:" labels
    # Returns list of (speaker_name, dialogue_text) tuples
```

#### Text Chunking for Polly

AWS Polly Neural voices have a 3,000 character limit per request. Handle this with chunking:

```python
@pydantic.validate_call(validate_return=True)
def chunk_text(text: str, max_chars: int = POLLY_NEURAL_CHAR_LIMIT) -> List[str]:
    """
    Split text at sentence boundaries to stay under char limit.
    Never splits mid-sentence.
    """
    # Use regex to split on sentence endings: . ! ?
    # Preserve separators
    # Combine sentences until reaching max_chars
```

**Critical**: Always chunk text before passing to Polly to avoid runtime failures.

#### Voice Synthesis with Multiple Voices

Synthesize audio with distinct voices for each speaker:

```python
# Voice constants
POLLY_NEURAL_CHAR_LIMIT = 3000
MARCO_VOICE = "Matthew"  # US English male, conversational
JOHN_VOICE = "Joey"      # US English male, analytical

@pydantic.validate_call(validate_return=True)
def synthesize_speech(script: str) -> Optional[bytes]:
    """
    1. Parse script into speaker segments
    2. For each segment:
       - Choose voice based on speaker
       - Chunk text if needed
       - Synthesize each chunk
    3. Concatenate all audio chunks
    """
    # Returns combined MP3 audio as bytes
```

#### RSS Feed Generation

Generate standard podcast RSS 2.0 feed with iTunes tags:

```python
@pydantic.validate_call(validate_return=True)
def update_podcast_feed(
    bucket: str,
    audio_url: str,
    title: str,
    description: str,
    pub_date: str
) -> bool:
    """
    Create or update podcast RSS feed.
    - Read existing feed from S3 (if exists)
    - Add new episode at top
    - Keep last 10 episodes
    - Generate XML with iTunes tags
    - Upload to S3 at podcasts/feed.xml
    """
```

### Error Handling

#### Graceful Degradation

Always handle errors gracefully and log with context:

```python
try:
    message = client.messages.create(...)
    return message.content[0].text
except (anthropic.APIError, anthropic.APIConnectionError, RuntimeError, ValueError) as e:
    logger.error("Error generating script with Claude: %s", e)
    return None
```

#### Polly Error Handling

```python
try:
    response = polly.synthesize_speech(
        Text=chunk,
        OutputFormat="mp3",
        VoiceId=voice_id,
        Engine="neural"
    )
    audio_chunks.append(response["AudioStream"].read())
except ClientError as e:
    logger.error("Error synthesizing speech chunk: %s", e)
    return None
```

### Testing Patterns

#### Testing Speaker Parsing

```python
def test_parse_speaker_segments():
    script = """Marco: Welcome to the show!
John: Thanks Marco. Let's dive in."""

    segments = parse_speaker_segments(script)

    assert len(segments) == 2
    assert segments[0] == ("Marco", "Welcome to the show!")
    assert segments[1] == ("John", "Thanks Marco. Let's dive in.")
```

#### Testing Text Chunking

```python
def test_chunk_text_long():
    long_text = "This is sentence one. " * 200  # ~4400 chars

    chunks = chunk_text(long_text, max_chars=3000)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 3000
```

#### Testing Voice Switching

```python
@patch('podcast_generator.boto3.client')
def test_synthesize_speech_with_voice_switching(mock_boto3):
    mock_polly = MagicMock()
    mock_boto3.return_value = mock_polly
    mock_polly.synthesize_speech.return_value = {
        "AudioStream": MagicMock(read=lambda: b"audio chunk")
    }

    script = "Marco: Hello!\nJohn: Hi there!"
    audio = synthesize_speech(script)

    # Should call synthesize_speech twice (once per speaker)
    assert mock_polly.synthesize_speech.call_count == 2

    # Verify different voices used
    calls = mock_polly.synthesize_speech.call_args_list
    voices = [call[1]['VoiceId'] for call in calls]
    assert MARCO_VOICE in voices
    assert JOHN_VOICE in voices
```

#### Testing RSS Feed Generation

```python
@patch('podcast_generator.boto3.client')
def test_update_podcast_feed_new(mock_boto3):
    mock_s3 = MagicMock()
    mock_boto3.return_value = mock_s3

    # Simulate no existing feed
    class NoSuchKey(Exception):
        pass
    mock_s3.exceptions.NoSuchKey = NoSuchKey
    mock_s3.get_object.side_effect = NoSuchKey("Not found")

    success = update_podcast_feed(
        bucket="test-bucket",
        audio_url="https://example.com/audio.mp3",
        title="Test Episode",
        description="Test Description",
        pub_date="2025-01-01T12:00:00"
    )

    assert success
    # Verify RSS feed uploaded to correct location
    assert mock_s3.put_object.call_args[1]['Key'] == 'podcasts/feed.xml'
```

### Cost Optimization

#### Token Management

- Use Claude 3.5 Haiku (most cost-effective model)
- Set conservative token limits (4,000 for podcasts vs 100,000 for email)
- Limit script length to 5-10 minutes to control Polly costs

#### Voice Selection

```python
# Neural voices (high quality, higher cost)
MARCO_VOICE = "Matthew"  # $16/M chars
JOHN_VOICE = "Joey"

# Alternative: Standard voices (lower quality, 75% cheaper)
# MARCO_VOICE = "Matthew" with Engine="standard"  # $4/M chars
```

#### Monitoring Costs

Log key metrics for cost tracking:

```python
logger.info("Script generated successfully (length: %d chars)", len(script))
logger.info("Found %d speaker segments", len(segments))
logger.info("Synthesizing %d chunks for %s", len(text_chunks), speaker)
logger.info("Audio synthesized successfully (%d bytes)", len(audio_data))
```

### Common Pitfalls

1. **Forgetting to chunk text**: Always chunk before calling Polly or you'll hit the 3000-char limit
2. **Not handling speaker labels**: Ensure Claude prompt explicitly requests "Marco:" and "John:" labels
3. **Missing newlines in test scripts**: Parser looks for line breaks between speakers
4. **Catching too broad exceptions**: Use specific exception types (ClientError, APIError, etc.)
5. **Not preserving sentence boundaries**: Split on sentence endings, not arbitrary character counts

### Integration with Existing System

The podcast generator reuses functions from `email_articles.py`:

```python
from .email_articles import get_feed_file, filter_items, get_last_run, set_last_run

def generate_podcast(event, context):
    # Reuse existing RSS feed retrieval
    run_date = get_last_run(last_run_param)
    rss_file = get_feed_file(bucket, rss_key)
    filtered_items = filter_items(rss_file, run_date)

    # Podcast-specific processing
    script = generate_script(filtered_items)
    audio = synthesize_speech(script)

    # Store and update feed
    upload_to_s3(bucket, s3_key, audio, "audio/mpeg")
    update_podcast_feed(bucket, audio_url, title, description, pub_date)

    # Update state using shared function
    set_last_run(last_run_param)
```

### Future Enhancements

Consider these patterns for future improvements:

1. **SSML Support**: Use Speech Synthesis Markup Language for more natural pauses, emphasis
2. **Audio Post-Processing**: Add intro/outro music, normalize volume levels
3. **CDN Integration**: Use CloudFront for podcast audio delivery to reduce bandwidth costs
4. **Compression**: Compress MP3 files to reduce storage and bandwidth costs
5. **Local Testing CLI**: Create `cli_podcast_generator.py` similar to `cli_article_processor.py`
