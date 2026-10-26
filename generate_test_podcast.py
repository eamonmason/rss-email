#!/usr/bin/env python3
"""Synthesise a sample narration locally with Phonon to check voice and pacing.

Usage: uv run python generate_test_podcast.py [VOICE] [OUTPUT.mp3]
No AWS or Anthropic credentials needed; weights download from Hugging Face on first run.
"""

import os
import sys

sys.path.append("src")

# pylint: disable=wrong-import-position
from rss_email.podcast_tts import DEFAULT_VOICE, synthesize_speech  # noqa: E402

SAMPLE_SCRIPT = """Good morning. It is Tuesday the twenty-seventh of October, and this is the briefing.

The main story is a quiet one. Markets barely moved, but the more interesting change lay in how companies \
pay for artificial intelligence, as several large customers began to cap their spending on model providers.

Elsewhere, a long-running security flaw in a popular open-source library was finally fixed, \
and maintainers are urging users to update at once.

One to watch: whether the spending caps spread beyond the biggest buyers. That is the briefing."""


def main() -> None:
    """Write the sample narration to an MP3 using the chosen voice."""
    voice = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_VOICE
    output = sys.argv[2] if len(sys.argv) > 2 else "test_podcast.mp3"
    os.environ["PODCAST_VOICE"] = voice
    audio = synthesize_speech(SAMPLE_SCRIPT)
    if audio is None:
        sys.exit("Synthesis failed")
    with open(output, "wb") as handle:
        handle.write(audio)
    print(f"Wrote {output} ({len(audio)} bytes) with voice {voice}")


if __name__ == "__main__":
    main()
