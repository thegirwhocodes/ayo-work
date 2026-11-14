import pytest
import struct
from app.voice.audio import (
    pcm16_to_mulaw,
    mulaw_to_pcm16,
    Downsample24kTo8k,
    FrameChunker,
    rms,
)


def test_mulaw_pcm16_roundtrip():
    # Generate 16-bit PCM samples
    samples = [0, 1000, -1000, 16000, -16000, 32000, -32000]
    pcm_bytes = struct.pack(f"<{len(samples)}h", *samples)

    # Encode to mu-law
    mulaw = pcm16_to_mulaw(pcm_bytes)
    assert len(mulaw) == len(samples)

    # Decode back to PCM16
    decoded_pcm = mulaw_to_pcm16(mulaw)
    assert len(decoded_pcm) == len(pcm_bytes)

    # mu-law is lossy compression (8-bit log), verify reasonable correlation
    decoded_samples = struct.unpack(f"<{len(samples)}h", decoded_pcm)
    for orig, dec in zip(samples, decoded_samples):
        if orig == 0:
            assert abs(dec) <= 32
        else:
            rel_err = abs(orig - dec) / abs(orig)
            assert rel_err < 0.15  # within 15% error margin for 8-bit log companding


def test_downsample_24k_to_8k():
    resampler = Downsample24kTo8k()

    # 24kHz audio: 300 samples (600 bytes) -> should decimate to 100 samples (200 bytes) at 8kHz
    samples_24k = [1000] * 300
    pcm24k_bytes = struct.pack(f"<{len(samples_24k)}h", *samples_24k)

    pcm8k_bytes = resampler.resample(pcm24k_bytes)
    assert len(pcm8k_bytes) == 200

    # Average of constant 1000 should be 1000
    samples_8k = struct.unpack(f"<{len(pcm8k_bytes)//2}h", pcm8k_bytes)
    assert all(s == 1000 for s in samples_8k)


def test_frame_chunker():
    chunker = FrameChunker(frame_bytes=160)

    # Add 350 bytes: should produce two 160-byte frames (320 bytes total), 30 bytes remaining in buffer
    data = b"\x01" * 350
    frames = chunker.add_samples(data)

    assert len(frames) == 2
    assert len(frames[0]) == 160
    assert len(frames[1]) == 160

    # Add another 130 bytes (30 + 130 = 160 bytes) -> produces 1 frame
    data2 = b"\x02" * 130
    frames2 = chunker.add_samples(data2)
    assert len(frames2) == 1
    assert len(frames2[0]) == 160


def test_rms():
    # Silence has 0 RMS
    silence = struct.pack("<4h", 0, 0, 0, 0)
    assert rms(silence) == 0.0

    # Constant amplitude
    signal = struct.pack("<4h", 1000, -1000, 1000, -1000)
    assert int(rms(signal)) == 1000
