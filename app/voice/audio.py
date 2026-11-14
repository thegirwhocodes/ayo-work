"""Telephone audio primitives.

Phone networks carry 8 kHz mono audio. Asterisk AudioSocket hands us signed
16-bit little-endian PCM ("slin16") at 8 kHz; Twilio Media Streams hands us
G.711 mu-law at 8 kHz. Gemini Live accepts 8 kHz PCM input but always returns
24 kHz PCM, so the return path needs a 3:1 downsample.

Everything here is dependency-free (no audioop — it was removed in Python 3.13).
"""

from __future__ import annotations

import array
import sys

SAMPLE_WIDTH = 2  # bytes per 16-bit sample
PHONE_RATE = 8000
GEMINI_OUTPUT_RATE = 24000
FRAME_MS = 20
FRAME_BYTES = PHONE_RATE * SAMPLE_WIDTH * FRAME_MS // 1000  # 320 bytes = 20 ms @ 8 kHz

_LITTLE_ENDIAN = sys.byteorder == "little"


def _to_samples(pcm: bytes) -> array.array:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % SAMPLE_WIDTH)])
    if not _LITTLE_ENDIAN:
        samples.byteswap()
    return samples


def _from_samples(samples: array.array) -> bytes:
    if not _LITTLE_ENDIAN:
        samples = array.array("h", samples)
        samples.byteswap()
    return samples.tobytes()


# --------------------------------------------------------------------------
# G.711 mu-law (ITU-T G.711). Lookup tables built once at import.
# --------------------------------------------------------------------------
_MULAW_BIAS = 0x84
_MULAW_CLIP = 32635


def _mulaw_decode_byte(value: int) -> int:
    value = ~value & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    magnitude = ((mantissa << 3) + _MULAW_BIAS) << exponent
    magnitude -= _MULAW_BIAS
    return -magnitude if sign else magnitude


def _mulaw_encode_sample(sample: int) -> int:
    sign = 0x80 if sample < 0 else 0x00
    magnitude = min(abs(sample), _MULAW_CLIP) + _MULAW_BIAS
    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (magnitude & mask):
        exponent -= 1
        mask >>= 1
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return ~(sign | (exponent << 4) | mantissa) & 0xFF


_MULAW_DECODE_TABLE = array.array("h", (_mulaw_decode_byte(i) for i in range(256)))
_MULAW_ENCODE_TABLE = bytes(_mulaw_encode_sample(s) for s in range(-32768, 32768))


def mulaw_to_pcm16(data: bytes) -> bytes:
    """Decode G.711 mu-law bytes to 16-bit little-endian PCM."""
    table = _MULAW_DECODE_TABLE
    return _from_samples(array.array("h", (table[b] for b in data)))


def pcm16_to_mulaw(pcm: bytes) -> bytes:
    """Encode 16-bit little-endian PCM to G.711 mu-law bytes."""
    table = _MULAW_ENCODE_TABLE
    return bytes(table[s + 32768] for s in _to_samples(pcm))


# --------------------------------------------------------------------------
# Resampling
# --------------------------------------------------------------------------
class Downsample24kTo8k:
    """Stateful 3:1 decimator for Gemini's 24 kHz output.

    Averages each group of three samples (a cheap low-pass that stops the
    worst aliasing) and keeps leftover samples between WebSocket chunks so
    no audio is dropped at chunk boundaries.
    """

    def __init__(self) -> None:
        self._remainder = array.array("h")

    def feed(self, pcm_24k: bytes) -> bytes:
        samples = self._remainder + _to_samples(pcm_24k)
        usable = len(samples) - (len(samples) % 3)
        self._remainder = samples[usable:]
        out = array.array(
            "h",
            ((samples[i] + samples[i + 1] + samples[i + 2]) // 3 for i in range(0, usable, 3)),
        )
        return _from_samples(out)

    def resample(self, pcm_24k: bytes) -> bytes:
        return self.feed(pcm_24k)

    def reset(self) -> None:
        self._remainder = array.array("h")


class FrameChunker:
    """Buffers arbitrary-length PCM and yields fixed 20 ms phone frames."""

    def __init__(self, frame_bytes: int = FRAME_BYTES) -> None:
        self.frame_bytes = frame_bytes
        self._buf = bytearray()

    def feed(self, pcm: bytes) -> list[bytes]:
        self._buf.extend(pcm)
        frames = []
        while len(self._buf) >= self.frame_bytes:
            frames.append(bytes(self._buf[: self.frame_bytes]))
            del self._buf[: self.frame_bytes]
        return frames

    def add_samples(self, pcm: bytes) -> list[bytes]:
        return self.feed(pcm)

    def clear(self) -> None:
        self._buf.clear()


def rms(pcm: bytes) -> float:
    """Root-mean-square loudness of a PCM chunk (0 for silence)."""
    samples = _to_samples(pcm)
    if not samples:
        return 0.0
    return (sum(s * s for s in samples) / len(samples)) ** 0.5

