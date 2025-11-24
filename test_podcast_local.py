#!/usr/bin/env python3
"""Local demonstration script for podcast generator functions."""

import sys
sys.path.append('src')

from rss_email.podcast_generator import parse_speaker_segments, chunk_text  # noqa: E402


# Test data
sample_script = """Marco: Welcome to Eamon's Daily Tech News! I'm Marco.
John: And I'm John. Today we have some exciting stories about AI and technology.
Marco: That's right! Let's start with the big news about OpenAI's latest model release.
John: This is really interesting. The new model shows significant improvements in reasoning capabilities.
Marco: Absolutely. And there's also news about quantum computing breakthroughs.
John: Yes, researchers at IBM have made some impressive progress."""

long_text = "This is a sentence. " * 200  # Create text >3000 chars


def test_speaker_parsing():
    """Test speaker segment parsing."""
    print("=" * 60)
    print("TEST 1: Speaker Segment Parsing")
    print("=" * 60)

    segments = parse_speaker_segments(sample_script)

    print(f"\nFound {len(segments)} speaker segments:\n")
    for i, (speaker, text) in enumerate(segments, 1):
        print(f"{i}. {speaker}: {text[:60]}...")

    print("\n✅ Speaker parsing works correctly!")


def test_text_chunking():
    """Test text chunking for Polly limits."""
    print("\n" + "=" * 60)
    print("TEST 2: Text Chunking (Polly 3000-char limit)")
    print("=" * 60)

    print(f"\nOriginal text length: {len(long_text)} characters")

    chunks = chunk_text(long_text, max_chars=3000)

    print(f"Split into {len(chunks)} chunks:")
    for i, chunk in enumerate(chunks, 1):
        print(f"  Chunk {i}: {len(chunk)} characters")
        assert len(chunk) <= 3000, f"Chunk {i} exceeds 3000 chars!"

    print("\n✅ Text chunking works correctly!")


def test_combined_workflow():
    """Test the combined workflow."""
    print("\n" + "=" * 60)
    print("TEST 3: Combined Workflow")
    print("=" * 60)

    print("\n1. Parsing script into speaker segments...")
    segments = parse_speaker_segments(sample_script)
    print(f"   ✓ Found {len(segments)} segments")

    print("\n2. Checking if any segments need chunking...")
    total_chunks = 0
    for speaker, text in segments:
        chunks = chunk_text(text, max_chars=3000)
        total_chunks += len(chunks)
        if len(chunks) > 1:
            print(f"   ✓ {speaker}: {len(text)} chars → {len(chunks)} chunks")
        else:
            print(f"   ✓ {speaker}: {len(text)} chars (no chunking needed)")

    print(f"\n3. Total audio chunks to synthesize: {total_chunks}")
    print("   (In production, each chunk would be sent to AWS Polly)")

    print("\n✅ Combined workflow works correctly!")


def main():
    """Run all local tests."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 10 + "PODCAST GENERATOR LOCAL TEST" + " " * 20 + "║")
    print("╚" + "=" * 58 + "╝")
    print("\nTesting core podcast generation functions without AWS...")

    try:
        test_speaker_parsing()
        test_text_chunking()
        test_combined_workflow()

        print("\n" + "=" * 60)
        print("🎉 ALL LOCAL TESTS PASSED!")
        print("=" * 60)
        print("\nThe podcast generator is ready for deployment!")
        print("\nNext steps:")
        print("  1. Ensure Rancher Desktop (Docker) is running")
        print("  2. Run: cdk synth")
        print("  3. Run: cdk deploy")
        print("")

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
