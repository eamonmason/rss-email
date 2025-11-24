import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '../src'))

from rss_email import podcast_generator  # noqa: E402


class TestPodcastGenerator(unittest.TestCase):
    def setUp(self):
        self.mock_env = {
            "BUCKET": "test-bucket",
            "KEY": "rss.xml",
            "PODCAST_LAST_RUN_PARAMETER": "test-param",
            "ANTHROPIC_API_KEY_PARAMETER": "test-key-param",
            "CLAUDE_MODEL": "claude-3-test",
            "CLAUDE_MAX_TOKENS": "100"
        }
        self.env_patcher = patch.dict(os.environ, self.mock_env)
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()

    @patch('rss_email.podcast_generator.boto3.client')
    @patch('rss_email.podcast_generator.anthropic.Anthropic')
    def test_generate_script(self, mock_anthropic, mock_boto3):
        # Mock SSM
        mock_ssm = MagicMock()
        mock_boto3.return_value = mock_ssm
        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "fake-key"}}

        # Mock Anthropic
        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text="Podcast Script")]
        mock_client.messages.create.return_value = mock_message

        articles = [{"title": "Test Article", "description": "Test Description"}]
        script = podcast_generator.generate_script(articles)

        self.assertEqual(script, "Podcast Script")
        mock_client.messages.create.assert_called_once()

    def test_parse_speaker_segments(self):
        script = """Marco: Welcome to the show!
John: Thanks Marco. Let's dive into today's news.
Marco: First up, we have a story about AI developments.
John: That's really interesting."""

        segments = podcast_generator.parse_speaker_segments(script)

        self.assertEqual(len(segments), 4)
        self.assertEqual(segments[0], ("Marco", "Welcome to the show!"))
        self.assertEqual(segments[1], ("John", "Thanks Marco. Let's dive into today's news."))
        self.assertEqual(segments[2], ("Marco", "First up, we have a story about AI developments."))
        self.assertEqual(segments[3], ("John", "That's really interesting."))

    def test_parse_speaker_segments_multiline(self):
        script = """Marco: This is a longer segment
that spans multiple lines
without a speaker label.
John: Now I'm talking."""

        segments = podcast_generator.parse_speaker_segments(script)

        self.assertEqual(len(segments), 2)
        self.assertIn("This is a longer segment that spans multiple lines", segments[0][1])
        self.assertEqual(segments[1][0], "John")

    def test_chunk_text_short(self):
        short_text = "This is a short text."
        chunks = podcast_generator.chunk_text(short_text, max_chars=100)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], short_text)

    def test_chunk_text_long(self):
        # Create text longer than 3000 chars
        long_text = "This is sentence one. " * 200  # ~4400 chars

        chunks = podcast_generator.chunk_text(long_text, max_chars=3000)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 3000)

    @patch('rss_email.podcast_generator.boto3.client')
    def test_synthesize_speech_with_voice_switching(self, mock_boto3):
        mock_polly = MagicMock()
        mock_boto3.return_value = mock_polly
        mock_polly.synthesize_speech.return_value = {
            "AudioStream": MagicMock(read=lambda: b"audio chunk")
        }

        script = "Marco: Hello!\nJohn: Hi there!"
        audio = podcast_generator.synthesize_speech(script)

        # Should have called synthesize_speech twice (once for Marco, once for John)
        self.assertEqual(mock_polly.synthesize_speech.call_count, 2)

        # Check that different voices were used
        calls = mock_polly.synthesize_speech.call_args_list
        voices_used = [call_args[1]['VoiceId'] for call_args in calls]
        self.assertIn(podcast_generator.MARCO_VOICE, voices_used)
        self.assertIn(podcast_generator.JOHN_VOICE, voices_used)

        # Audio should be concatenated
        self.assertEqual(audio, b"audio chunkaudio chunk")

    @patch('rss_email.podcast_generator.boto3.client')
    def test_synthesize_speech_long_segment(self, mock_boto3):
        """Test that long segments are chunked and synthesized properly."""
        mock_polly = MagicMock()
        mock_boto3.return_value = mock_polly
        mock_polly.synthesize_speech.return_value = {
            "AudioStream": MagicMock(read=lambda: b"audio chunk")
        }

        # Create a script with a segment longer than 3000 chars
        long_segment = "This is a sentence. " * 200  # ~4000 chars
        script = f"Marco: {long_segment}"

        audio = podcast_generator.synthesize_speech(script)

        # Should have been called multiple times due to chunking
        self.assertGreater(mock_polly.synthesize_speech.call_count, 1)
        self.assertIsNotNone(audio)

    @patch('rss_email.podcast_generator.boto3.client')
    def test_upload_to_s3(self, mock_boto3):
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3

        success = podcast_generator.upload_to_s3("bucket", "key", b"data", "audio/mpeg")
        self.assertTrue(success)
        mock_s3.put_object.assert_called_once()

    @patch('rss_email.podcast_generator.boto3.client')
    def test_update_podcast_feed_new(self, mock_boto3):
        """Test creating a new podcast feed."""
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3

        # Create a proper exception class
        class NoSuchKey(Exception):
            pass

        mock_s3.exceptions.NoSuchKey = NoSuchKey

        # Simulate no existing feed
        mock_s3.get_object.side_effect = NoSuchKey("Not found")

        success = podcast_generator.update_podcast_feed(
            bucket="test-bucket",
            audio_url="https://example.com/audio.mp3",
            title="Test Episode",
            description="Test Description",
            pub_date="2025-01-01T12:00:00"
        )

        self.assertTrue(success)
        mock_s3.put_object.assert_called_once()

        # Verify RSS feed was uploaded
        call_args = mock_s3.put_object.call_args
        self.assertEqual(call_args[1]['Key'], 'podcasts/feed.xml')
        self.assertEqual(call_args[1]['ContentType'], 'application/rss+xml')

        # Verify feed contains essential elements
        feed_body = call_args[1]['Body'].decode('utf-8')
        self.assertIn("Test Episode", feed_body)
        self.assertIn("https://example.com/audio.mp3", feed_body)
        self.assertIn("Eamon's Daily Tech News", feed_body)

    @patch('rss_email.podcast_generator.boto3.client')
    def test_update_podcast_feed_existing(self, mock_boto3):
        """Test updating an existing podcast feed."""
        mock_s3 = MagicMock()
        mock_boto3.return_value = mock_s3

        # Simulate existing feed with one episode
        existing_feed = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Eamon's Daily Tech News</title>
    <item>
      <title>Old Episode</title>
      <guid>https://example.com/old.mp3</guid>
    </item>
  </channel>
</rss>"""

        mock_response = MagicMock()
        mock_response['Body'].read.return_value = existing_feed.encode('utf-8')
        mock_s3.get_object.return_value = mock_response

        success = podcast_generator.update_podcast_feed(
            bucket="test-bucket",
            audio_url="https://example.com/new.mp3",
            title="New Episode",
            description="New Description",
            pub_date="2025-01-02T12:00:00"
        )

        self.assertTrue(success)

        # Verify feed contains both old and new episodes
        feed_body = mock_s3.put_object.call_args[1]['Body'].decode('utf-8')
        self.assertIn("New Episode", feed_body)
        self.assertIn("Old Episode", feed_body)

    @patch('rss_email.podcast_generator.boto3.client')
    @patch('rss_email.podcast_generator.anthropic.Anthropic')
    def test_generate_script_api_error(self, mock_anthropic, mock_boto3):
        """Test graceful handling of Claude API errors."""
        mock_ssm = MagicMock()
        mock_boto3.return_value = mock_ssm
        mock_ssm.get_parameter.return_value = {"Parameter": {"Value": "fake-key"}}

        mock_client = MagicMock()
        mock_anthropic.return_value = mock_client
        # Simulate APIError from anthropic library
        mock_client.messages.create.side_effect = RuntimeError("API Error")

        articles = [{"title": "Test", "description": "Test"}]
        script = podcast_generator.generate_script(articles)

        self.assertIsNone(script)

    @patch('rss_email.podcast_generator.boto3.client')
    def test_synthesize_speech_no_segments(self, mock_boto3):
        """Test handling of empty or malformed scripts."""
        audio = podcast_generator.synthesize_speech("")

        self.assertIsNone(audio)

    @patch('rss_email.podcast_generator.get_last_run')
    @patch('rss_email.podcast_generator.get_feed_file')
    @patch('rss_email.podcast_generator.filter_items')
    @patch('rss_email.podcast_generator.generate_script')
    @patch('rss_email.podcast_generator.synthesize_speech')
    @patch('rss_email.podcast_generator.upload_to_s3')
    @patch('rss_email.podcast_generator.update_podcast_feed')
    @patch('rss_email.podcast_generator.set_last_run')
    @patch('rss_email.podcast_generator.get_cloudfront_domain')
    def test_generate_podcast_flow(  # pylint: disable=too-many-positional-arguments
        self, mock_get_cloudfront_domain, mock_set_last_run, mock_update_feed, mock_upload,
        mock_synthesize, mock_generate_script, mock_filter,
        mock_get_feed, mock_get_last_run
    ):
        """Test the complete podcast generation flow."""
        mock_filter.return_value = [{"title": "Article 1"}]
        mock_generate_script.return_value = "Script"
        mock_synthesize.return_value = b"Audio"
        mock_upload.return_value = True
        mock_get_cloudfront_domain.return_value = "d123456.cloudfront.net"

        podcast_generator.generate_podcast({}, None)

        mock_generate_script.assert_called_once()
        mock_synthesize.assert_called_once()
        mock_upload.assert_called_once()
        mock_update_feed.assert_called_once()
        mock_set_last_run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
