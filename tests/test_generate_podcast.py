"""Tests for the podcast Lambda handler (generate_podcast.py)."""
# pylint: disable=redefined-outer-name

from datetime import datetime
from unittest.mock import patch

import pytest

from rss_email import generate_podcast

TODAY = datetime.now().strftime("%Y-%m-%d")
INPUT = {"date": TODAY, "must_read": [{"title": "Lead story", "source": "Reuters", "why": "x"}]}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BUCKET", "bucket")
    monkeypatch.setenv("PODCAST_CLOUDFRONT_DOMAIN_PARAMETER", "domain-param")
    monkeypatch.setenv("PODCAST_CLOUDFRONT_DISTRIBUTION_ID", "DIST")


@pytest.fixture
def collaborators():
    with patch.object(generate_podcast, "load_narration_input", return_value=INPUT) as load, \
         patch.object(generate_podcast, "_episode_exists", return_value=False) as exists, \
         patch.object(generate_podcast, "get_anthropic_api_key", return_value="k"), \
         patch.object(generate_podcast.anthropic, "Anthropic"), \
         patch.object(generate_podcast, "generate_narration", return_value="Script.") as narrate, \
         patch.object(generate_podcast, "synthesize_speech", return_value=b"mp3") as tts, \
         patch.object(generate_podcast, "upload_to_s3", return_value=True) as upload, \
         patch.object(generate_podcast, "get_cloudfront_domain", return_value="cdn.example") as domain, \
         patch.object(generate_podcast, "update_podcast_feed", return_value=True) as feed:
        yield {"load": load, "exists": exists, "narrate": narrate, "tts": tts,
               "upload": upload, "domain": domain, "feed": feed}


def test_success_publishes_episode(env, collaborators):
    result = generate_podcast.lambda_handler({}, None)

    assert result["audio_generated"] is True
    assert result["audio_url"] == f"https://cdn.example/podcasts/episodes/rss-brief-{TODAY}.mp3"
    feed_kwargs = collaborators["feed"].call_args.kwargs
    assert feed_kwargs["title"] == f"RSS Brief — {TODAY}"
    assert "Lead story" in feed_kwargs["description"]
    assert feed_kwargs["audio_size"] == 3 and feed_kwargs["distribution_id"] == "DIST"


@pytest.mark.parametrize("stale", [None, {"date": "2000-01-01"}])
def test_skips_without_todays_brief(env, collaborators, stale):
    collaborators["load"].return_value = stale

    assert generate_podcast.lambda_handler({}, None) == {"status": "skipped", "audio_generated": False}
    collaborators["narrate"].assert_not_called()


def test_skips_when_episode_already_published(env, collaborators):
    collaborators["exists"].return_value = True

    assert generate_podcast.lambda_handler({}, None)["audio_generated"] is False
    collaborators["narrate"].assert_not_called()


@pytest.mark.parametrize("stage,message", [
    ("narrate", "narration"), ("tts", "synthesise"), ("upload", "upload"), ("feed", "feed"),
])
def test_stage_failure_raises(env, collaborators, stage, message):
    collaborators[stage].return_value = None if stage in ("narrate", "tts") else False

    with pytest.raises(RuntimeError, match=message):
        generate_podcast.lambda_handler({}, None)
