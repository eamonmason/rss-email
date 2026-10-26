"""Tests for single-narrator Phonon synthesis (podcast_tts.py)."""

import sys
from unittest.mock import MagicMock

import numpy as np
import pytest

from rss_email.podcast_tts import (
    SAMPLE_RATE,
    chunk_paragraph,
    encode_mp3,
    split_paragraphs,
    synthesize_speech,
)


@pytest.fixture
def fake_ptts(monkeypatch):
    """Install a fake ``ptts`` whose synth returns 0.1s of a tone per call."""
    tts = MagicMock()
    tts.sample_rate = SAMPLE_RATE
    tts.synth.side_effect = lambda text, voice=None: np.full(SAMPLE_RATE // 10, 0.1, dtype=np.float32)
    module = MagicMock()
    module.TTS.return_value = tts
    monkeypatch.setitem(sys.modules, "ptts", module)
    return tts


def test_split_paragraphs_ignores_blank_runs():
    assert split_paragraphs("One.\n\n\nTwo.\n  \nThree.") == ["One.", "Two.", "Three."]


def test_chunk_paragraph_never_splits_sentences():
    sentences = ["This is sentence number %d." % i for i in range(30)]
    chunks = chunk_paragraph(" ".join(sentences), max_chars=100)

    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks) == " ".join(sentences)


def test_chunk_paragraph_keeps_overlong_sentence_whole():
    long_sentence = "word " * 100 + "end."

    assert chunk_paragraph(long_sentence, max_chars=50) == [long_sentence.strip()]


def test_encode_mp3_produces_mp3_frames():
    mp3 = encode_mp3(np.zeros(SAMPLE_RATE, dtype=np.float32))

    assert mp3[:1] == b"\xff" and len(mp3) > 100


def test_synthesize_speech_uses_configured_voice_and_returns_mp3(fake_ptts, monkeypatch):
    monkeypatch.setenv("PODCAST_VOICE", "Toby")

    mp3 = synthesize_speech("First sentence. Second sentence.\n\nNew paragraph.")

    assert mp3 and mp3[:1] == b"\xff"
    assert all(call.kwargs["voice"] == "Toby" for call in fake_ptts.synth.call_args_list)
    assert [call.args[0] for call in fake_ptts.synth.call_args_list] == [
        "First sentence. Second sentence.",
        "New paragraph.",
    ]


def test_synthesize_speech_empty_script_returns_none(fake_ptts):
    assert synthesize_speech("  \n\n ") is None
    fake_ptts.synth.assert_not_called()


def test_synthesize_speech_synth_error_returns_none(fake_ptts):
    fake_ptts.synth.side_effect = RuntimeError("boom")

    assert synthesize_speech("Hello there.") is None
