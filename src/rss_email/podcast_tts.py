"""Single-narrator speech synthesis with Gradium Phonon (``ptts``), encoded to MP3.

Only the Apache-2.0 ``gradium/phonon-31m-en`` weights are used; the 40M models
are gated and CC BY-NC. ``ptts`` needs no PyTorch and runs on CPU.
"""

import logging
import os
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
DEFAULT_VOICE = "Hugh"
MAX_CHUNK_CHARS = 400
PARAGRAPH_PAUSE_SECONDS = 0.5
SENTENCE_PAUSE_SECONDS = 0.15
MP3_BITRATE_KBPS = 64

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_paragraphs(script: str) -> List[str]:
    """Split a script into non-empty paragraphs on blank lines."""
    return [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]


def chunk_paragraph(paragraph: str, max_chars: int = MAX_CHUNK_CHARS) -> List[str]:
    """Group a paragraph's sentences into chunks of at most ``max_chars``.

    Never splits mid-sentence; a single over-long sentence becomes its own chunk.
    """
    chunks: List[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(" ".join(paragraph.split())):
        if current and len(current) + 1 + len(sentence) > max_chars:
            chunks.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def encode_mp3(samples, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Encode a mono float32 numpy array in [-1, 1] as MP3."""
    import numpy as np  # pylint: disable=import-outside-toplevel
    import lameenc  # pylint: disable=import-outside-toplevel

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_BITRATE_KBPS)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_channels(1)
    encoder.set_quality(2)
    return bytes(encoder.encode(pcm.tobytes()) + encoder.flush())


def synthesize_speech(script: str) -> Optional[bytes]:
    """Read ``script`` aloud in one voice and return MP3 bytes, or ``None`` on error.

    The voice comes from ``PODCAST_VOICE``. Paragraphs are separated by a longer
    pause than sentence chunks so the delivery has some shape.
    """
    paragraphs = split_paragraphs(script)
    if not paragraphs:
        logger.warning("Empty narration script; nothing to synthesise")
        return None

    try:
        import numpy as np  # pylint: disable=import-outside-toplevel
        import ptts  # pylint: disable=import-outside-toplevel

        tts = ptts.TTS(lang="en")
        voice = os.environ.get("PODCAST_VOICE", DEFAULT_VOICE)
        rate = int(getattr(tts, "sample_rate", SAMPLE_RATE))
        sentence_gap = np.zeros(int(rate * SENTENCE_PAUSE_SECONDS), dtype=np.float32)
        paragraph_gap = np.zeros(int(rate * PARAGRAPH_PAUSE_SECONDS), dtype=np.float32)

        pieces = []
        for paragraph in paragraphs:
            chunks = chunk_paragraph(paragraph)
            logger.info("Synthesising paragraph of %d chunks", len(chunks))
            for chunk in chunks:
                pieces.append(np.asarray(tts.synth(chunk, voice=voice), dtype=np.float32))
                pieces.append(sentence_gap)
            pieces[-1] = paragraph_gap
        audio = np.concatenate(pieces)
        mp3 = encode_mp3(audio, rate)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Error synthesising speech with Phonon: %s", exc, exc_info=True)
        return None

    logger.info("Audio synthesised: %.1f seconds, %d bytes", len(audio) / rate, len(mp3))
    return mp3
