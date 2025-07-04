# Functional Test Documentation

## Overview

The `test_functional_workflow.py` script provides a comprehensive end-to-end test of the RSS email workflow. This test simulates the complete production workflow locally without sending actual emails.

## What the Test Does

1. **Sets SSM Parameter**: Updates the `rss-email-lastrun` parameter to 24 hours ago
2. **Downloads RSS Data**: Fetches the current RSS articles from the production S3 bucket
3. **Generates Email**: Runs the complete email generation workflow including Claude processing (if configured)
4. **Outputs HTML**: Writes the result to a local HTML file instead of sending an email
5. **Cleanup**: Restores the original SSM parameter and cleans up temporary files

## Prerequisites

### AWS Credentials
You need AWS credentials configured for the target account. This can be done via:
- AWS CLI profile: `aws configure`
- Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
- IAM instance role (if running on EC2)

### Required AWS Permissions
The test requires the following AWS permissions:
- `ssm:GetParameter` - Read SSM parameters
- `ssm:PutParameter` - Update SSM parameters  
- `ssm:DeleteParameter` - Clean up test parameters
- `s3:GetObject` - Download RSS files from S3

### Environment Variables
Set the AWS region:
```bash
export AWS_DEFAULT_REGION=us-east-1  # or your target region
```

### Optional: Claude Configuration

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

## Running the Test

### Basic Usage

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

### Expected Output
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

## Output Files

The test generates:
- `functional_test_output.html` - The generated email HTML (can be opened in a browser)
- `downloaded_rss.xml` - Temporarily downloaded RSS file (cleaned up automatically)

## Configuration Options

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

## Troubleshooting

### Common Issues

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

### Debugging

Enable detailed logging by setting:
```bash
export PYTHONPATH=src
python -c "
import logging
logging.basicConfig(level=logging.DEBUG)
exec(open('test_functional_workflow.py').read())
"
```

### Manual Cleanup

If the test fails and doesn't clean up properly:

```bash
# Remove test files
rm -f functional_test_output.html downloaded_rss.xml

# Reset SSM parameter (replace with your original value)
aws ssm put-parameter --name "rss-email-lastrun" --value "2025-07-02T08:30:33.811000" --type String --overwrite
```

## Integration with CI/CD

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

## Comparison with Unit Tests

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