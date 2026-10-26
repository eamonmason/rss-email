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
# Synthesize the application stack
npx cdk synth

# View deployment differences against the deployed stack
npx cdk diff RSSEmailStack

# Deploy manually (normally left to GitHub Actions)
npx cdk deploy RSSEmailStack

# Deploy the one-time GitHub Actions OIDC role stack
npx cdk deploy --app "npx tsx bin/github-oidc-cdk.ts"
```

The construct id is `RSSEmailStack`, but the deployed CloudFormation stack is named
`cd-RSSEmailStack` (pinned via `stackName` in `bin/cdk.ts`, retained from the
now-removed CDK Pipelines stage named `cd`). Use the construct id on the command line.

`cdk deploy` requires Docker for the Lambda layer. `CDK_DOCKER=false` falls back to
local `pip` bundling — acceptable for `synth`/`diff`, never for a real deploy.

## Architecture

### Core Components
- **retrieve_articles.py**: Key orchestrating Lambda function that fetches RSS feeds and stores aggregated data in S3
- **email_articles.py**: Key orchestrating Lambda function that processes stored articles and sends formatted emails via SES
- **article_processor.py**: Claude AI integration for intelligent article categorization and summarization
- **models.py**: Shared Pydantic models for consistent data validation across the application
- **lib/rss_lambda_stack.ts**: Main CDK infrastructure stack defining all AWS resources
- **cli_article_processor.py**: CLI tool for testing article processing with Claude API locally
- **brief_generator.py**: Synthesises the companion "RSS Brief" email ("Read these" list, themes, signal strength, cross-cutting signals) from a day's categorised articles via a single streamed Claude Opus 5.5 call (`claude-opus-5-5`; `effort` set in `brief_config.json` - `high`, because at `medium` the model only sometimes counts a societal story from a low-ranked source as major and drops it under the theme caps; server-side refusal fallback enabled). Opus 5.5 always thinks, so read response content by block type and leave `max_tokens` headroom for thinking. Bulk categorisation and grouping stay on `claude-haiku-4-5`; the podcast narration uses `claude-sonnet-5-5`
- **brief_prompt.py**: Prompt text and editorial rules (selection, length caps, fidelity) for the RSS Brief synthesis call
- **url_utils.py**: Credential-param stripping (via `w3lib`) and link normalisation shared by ingest and the brief
- **brief_memory.py**: Persists a rolling window of recent RSS Brief days to S3 so the synthesis prompt can avoid repeating stories and frame multi-day stories as developments — see "Brief Memory" below
- **podcast_script.py / podcast_tts.py / generate_podcast.py / podcast_generator.py**: Single-narrator podcast narrating the day's RSS Brief (Claude script, Phonon speech, feed publishing) - see "Podcast Development Patterns"
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

### Brief Memory

The RSS Brief (not the daily digest) remembers recent days so it can avoid
repeating stories and frame multi-day stories as developments instead of new
news. `retrieve_and_send_email._maybe_send_brief` loads a rolling window of
prior days from a single S3 object (`brief_memory.load_memory`), renders it
into a compact "previously covered" text block
(`brief_memory.render_previous_context`), and passes it into the synthesis
prompt as `previous_context` (`brief_generator.build_prompt`). After a
successful send, today's themes are reduced to `{category, theme,
signal_strength, tldr, articles}` records and written back
(`brief_memory.build_day_record` / `append_and_prune` / `save_memory`),
pruning anything older than the window. This is intentionally not a vector
store or managed memory service — at one run/day and roughly a hundred
articles/day, a small rolling JSON object is simpler and cheaper, and Claude
does the semantic matching in-prompt rather than via embeddings.

- `BRIEF_MEMORY_KEY` (default `brief-memory/memory.json`): S3 key for the
  memory object, in the same bucket as everything else (`RSS_BUCKET`).
- `BRIEF_MEMORY_DAYS` (default `14`): rolling window size in days.

A memory load/save failure is swallowed and logged — it never blocks the
digest or the brief.

Memory also drives a deterministic repeat filter: each day records the links
of the articles it featured (theme citations and the must-read list), and
`brief_memory.seen_links` hands them to `build_synthesis_input`, which drops
today's articles whose `url_utils.normalise_link` key (scheme, host and path,
with no query or fragment) matches. A real development usually has a new URL,
so it still gets through.

### Brief Shape & Filtering

The brief is meant to be read in a couple of minutes, not skimmed for an hour:

- **"Read these"** (`must_read`) leads the email: 5-8 articles across all
  categories, each with one sentence on why it's worth opening.
- **Slim categories**: 1-3 themes per category, at most 12 in total, 1-2
  sentence tldrs, no per-category verdict line. Empty categories are omitted.
- **Caps are config, and enforced twice**: `brief_config.json` keys
  `must_read_min`/`must_read_max`, `max_themes_per_category`,
  `max_total_themes` and `cross_cutting_max` are written into the prompt, and
  `brief_generator._enforce_caps` truncates the parsed result, so prompt drift
  can't bring back a long brief.
- **Each article is listed once**: the renderer skips an article already
  listed under an earlier theme or in Personal. The must-read list may overlap
  with the themes.
- **Sponsored content never reaches the prompt**: `is_sponsored` drops
  `/sponsored/`, `/partner-content/`, `/paid-post/` and `/brandvoice/` URLs,
  plus titles starting "Sponsored"/"Advertorial"/"Paid post".
- **Fidelity rules** (`brief_prompt.FIDELITY_RULE`): don't claim more than
  the source does, keep "agreed/announced" distinct from "completed", use
  `TODAY` to avoid calling past events upcoming, never cite coverage the
  reader can't see, and only give `relevance_to_reader` when it names a
  concrete decision or action.

Prompt text lives in `brief_prompt.py`; `brief_generator.py` builds and
renders it.

### URL Credential Stripping

Some paid feeds put a personal access token in every article link.
`url_utils.strip_credential_params` removes credential-like query parameters
(`access_token`, `token`, `api_key`, `sig`, … matched case-insensitively)
using `w3lib.url.url_query_cleaner`. It runs at ingest
(`retrieve_articles.get_feed`), so the token never reaches S3, the digest,
the brief or the podcast. It runs again in `build_synthesis_input` to cover
data stored before this change. A URL without such parameters is returned
byte-for-byte unchanged.

### Discussion Links

Feeds from discussion sites carry two URLs - the article and the thread - and
the brief renders both, as `[Source] Title · discussion`.
`retrieve_articles.extract_discussion` normalises the two feed shapes we
actually poll:

- **RSS 2.0 `<comments>`** (Hacker News via hnrss, Lobsters): `<link>` is
  already the article, `<comments>` is the thread. Straight read.
- **Reddit's Atom feeds** have no `<comments>`. Their `<link>` *is* the thread,
  and the article URL only appears as the `[link]` anchor in the post body, so
  the two are swapped: the article becomes the primary link and the thread
  becomes `comments`. Self-posts (where `[link]` points back at the thread)
  are left alone - one link already covers both.

Everything else falls through unchanged. Slashdot has no separate article URL
at all, so there is nothing to split.

Two invariants worth keeping:

- **Discussion URLs never enter the Claude prompt.** Like article URLs, they
  travel out-of-band in `brief_generator.build_article_index` and are
  reattached at render time by id. `build_prompt` sends only id/source/title/
  summary.
- **A group's discussion link comes from any member, not just the primary**
  (`article_processor.first_comments`). The grouper decides which feed lands
  first, and a mainstream copy of a story usually beats the Hacker News one,
  so reading `comments` off `members[0]` alone drops most threads.

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

### Mastodon Source
An optional top-level `mastodon` block adds link posts from a Mastodon home timeline as articles
(the shared page is the article, the post is its discussion link). It is separate from `feeds` so
it is never fetched as RSS:
```json
{"mastodon": {"base_url": "https://mastodon.social", "max_articles": 15}}
```
`max_articles` is kept per 24-hour window; `enabled: false` switches it off. The access token
(`read:statuses` scope) is an SSM SecureString named `rss-email-mastodon-token`, created by hand.
A missing block or token disables the source quietly; an API failure logs a warning and never
blocks the RSS feeds. See `src/rss_email/mastodon_source.py`.

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

Deployment is continuous via GitHub Actions: every push to `main` runs
`.github/workflows/deploy.yml`, which gates on the lint/test suite (called as a reusable
workflow) and then deploys `cd-RSSEmailStack`. AWS access uses GitHub OIDC — there are
no long-lived AWS keys in GitHub.

### Prerequisites

- Domain configured in SES for sending emails
- CDK bootstrapped in target AWS account/region: `cdk bootstrap aws://<account-id>/<region>`
- The OIDC deploy role deployed once from `bin/github-oidc-cdk.ts`
- GitHub repository variables `AWS_DEPLOY_ROLE_ARN` and `AWS_REGION` set

### Deployment Parameters

The deploy workflow reads these from AWS Parameter Store at deploy time (with the
`rss-email-` prefix) and exports them as environment variables for `bin/cdk.ts`.
Parameter Store is the single source of truth; do not duplicate them into GitHub.

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

The podcast is a single-narrator, Economist-style audio edition of the **RSS Brief**, not a
second summary of the raw feed. The brief has already done the editorial selection, so the
podcast narrates it. There is no two-host dialogue, no speaker labels and no Polly.

Flow: `retrieve_and_send_email._maybe_send_brief` sends the brief, then saves a flattened
narration input to `podcast/narration-input.json` (`podcast_script.build_narration_input`,
best-effort, no article ids or URLs). The Step Functions workflow runs email first, then the
`GeneratePodcast` Lambda (`generate_podcast.py`), which reads that input and:

1. skips unless the input is dated today and `podcasts/episodes/rss-brief-{date}.mp3` does
   not already exist (so manual re-triggers don't duplicate an episode);
2. writes the narration with one direct Claude call (`podcast_script.generate_narration`,
   model `PODCAST_CLAUDE_MODEL`, default `claude-sonnet-5-5`; read text blocks by type);
3. synthesises MP3 (`podcast_tts.synthesize_speech`);
4. uploads, updates `podcasts/feed.xml` and invalidates CloudFront (`podcast_generator.py`).

Failures raise so the stage's catch publishes an SNS alert; "no brief today" is a clean skip.

### Narration prompt

`podcast_script.NARRATION_PROMPT` follows the `brief_prompt.py` conventions: placeholders are
substituted with `str.replace`, and `brief_prompt.FIDELITY_RULE` is reused so the audio makes
no stronger claims than the brief, and `AI_WRITING_RULE` (after Wikipedia's "Signs of AI
writing") steers away from machine-prose tells: inflated significance, promotional words,
`-ing` tails, vague attributions, "not just X but Y", reflexive triplets, summary endings.
After generation `clean_script` turns em dashes into commas and `find_ai_tells` logs a
warning when stock phrases slip through (narrow on purpose; it does not rewrite or retry).
`podcast_tts.normalise_for_speech` rewrites "A.I." to "AI", which Phonon says without a
pause. The prompt asks for plain TTS-safe prose (no markdown,
URLs, brackets or speaker labels), British spelling, and 1,100-1,400 words.

### Speech synthesis (Gradium Phonon)

- `podcast_tts.py` uses the `ptts` package (no PyTorch, CPU-only) with the public Apache-2.0
  `gradium/phonon-31m-en` weights. **Do not switch to the 40M models: they are gated and CC BY-NC.**
- Text is split into paragraphs, then sentence chunks (`chunk_paragraph`, never mid-sentence).
  Chunks are joined with short pauses and encoded to 24 kHz mono MP3 via `lameenc`.
- Voice is `PODCAST_VOICE` (default `Hugh`; `Toby` and `Harper` are baked into the image; set `PODCAST_VOICE` at deploy time or edit `lib/rss_lambda_stack.ts` - voices not listed in the Dockerfile's `PODCAST_VOICES` fail offline). Try voices with
  `uv run python generate_test_podcast.py <voice> out.mp3`.
- `ptts`, `lameenc` and `numpy` live in the `podcast` dependency group (included in `dev`), so
  they stay out of the shared Lambda layer. The Lambda is a container image
  (`docker/podcast/Dockerfile`) with the model weights baked in (`HF_HOME`, then
  `HF_HUB_OFFLINE=1`), so there is no runtime download.
- `synthesize_speech(script) -> Optional[bytes]` is the seam: swapping the engine (for example
  to Polly Long-form) means replacing that one function.

### RSS feed and CloudFront

`update_podcast_feed` keeps the 10 most recent episodes and writes the RSS 2.0 `<enclosure>`
with `url`, `length` (bytes, required by podcast apps) and `type`. It invalidates
`/podcasts/feed.xml` when a distribution id is supplied, so apps see new episodes immediately.
The Lambda role needs `cloudfront:CreateInvalidation` and S3 write access to `podcasts/*`.

### Testing

Tests mock `ptts` (fake module in `sys.modules`), Anthropic and boto3. See
`tests/test_podcast_script.py`, `tests/test_podcast_tts.py`, `tests/test_generate_podcast.py`
and `tests/test_podcast_feed.py`.
