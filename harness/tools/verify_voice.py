"""Deterministic speaker enrollment and comparison for the agent harness."""

from __future__ import annotations

import array
import asyncio
import math
import sys
import threading
from dataclasses import dataclass
from typing import Callable, Protocol

MODEL_SOURCE = "speechbrain/spkrec-ecapa-voxceleb"
model = None
model_lock = threading.Lock()


def get_speaker_model():
    """Load the CPU model once per process, on the first request."""
    global model
    with model_lock:
        if model is None:
            from speechbrain.inference.speaker import EncoderClassifier

            model = EncoderClassifier.from_hparams(source=MODEL_SOURCE)
        return model


def extract_voice_features(audio_data: bytes) -> list[float]:
    """Extract a speaker embedding from 16 kHz, mono, signed PCM16-LE audio.

    The caller must resample the audio before calling this function. The returned
    list is a speaker embedding, not a transcription. This synchronous, CPU-bound
    function should run in a worker rather than an async audio-handling loop.
    The first call downloads and loads the pretrained model if not already cached.
    """
    if not isinstance(audio_data, bytes):
        raise TypeError("audio_data must be PCM16 bytes")
    if not audio_data or len(audio_data) % 2:
        raise ValueError("audio_data must contain complete, non-empty PCM16 samples")

    import torch

    samples = array.array("h")
    samples.frombytes(audio_data)
    if sys.byteorder != "little":
        samples.byteswap()
    waveform = torch.frombuffer(samples, dtype=torch.int16).to(torch.float32).div_(32768.0).unsqueeze(0)

    model = get_speaker_model()
    with torch.inference_mode():
        embedding = model.encode_batch(waveform)
    return embedding.detach().cpu().reshape(-1).tolist()


def compare_voice_features(reference: list[float], candidate: list[float]) -> float:
    """Return cosine similarity for two compatible, nonzero embeddings."""
    if not reference or len(reference) != len(candidate):
        raise ValueError("embeddings must be non-empty and have equal dimensions")
    if not all(math.isfinite(value) for value in (*reference, *candidate)):
        raise ValueError("embeddings must contain finite values")
    reference_norm = math.sqrt(sum(value * value for value in reference))
    candidate_norm = math.sqrt(sum(value * value for value in candidate))
    if not reference_norm or not candidate_norm:
        raise ValueError("embeddings must be nonzero")
    return sum(a * b for a, b in zip(reference, candidate)) / (reference_norm * candidate_norm)



class VoiceStore(Protocol):
    def get(self, caller_id: str) -> list[float] | None: ...

    def enroll(self, caller_id: str, embedding: list[float]) -> bool:
        """Store only if absent; return whether this call enrolled it."""
        ...

    def delete(self, caller_id: str) -> bool:
        """Forget a stored voiceprint (reset); return whether one existed."""
        ...


@dataclass(frozen=True)
class VoiceVerificationResult:
    status: str
    similarity: float | None = None


class SpeakerVerifier:
    def __init__(
        self,
        store: VoiceStore,
        threshold: float,
        extractor: Callable[[bytes], list[float]] = extract_voice_features,
    ):
        if not -1 <= threshold <= 1:
            raise ValueError("threshold must be between -1 and 1")
        self.store = store
        self.threshold = threshold
        self.extractor = extractor

    def verify(self, caller_id: str, audio_data: bytes) -> VoiceVerificationResult:
        """Enroll once or compare with the existing embedding for this caller.

        The caller is responsible for consent and selecting adequate 16 kHz
        speech. Enrollment does not establish the caller's identity.
        """
        if not caller_id:
            raise ValueError("caller_id is required")
        reference = self.store.get(caller_id)
        embedding = self.extractor(audio_data)
        # Validate before either comparison or enrollment.
        compare_voice_features(embedding, embedding)
        if reference is None:
            if self.store.enroll(caller_id, embedding):
                return VoiceVerificationResult("enrolled")
            # Another call enrolled this identity concurrently.
            reference = self.store.get(caller_id)
            if reference is None:
                raise RuntimeError("voice enrollment could not be read")
        similarity = compare_voice_features(reference, embedding)
        return VoiceVerificationResult(
            "matched" if similarity >= self.threshold else "not_matched", similarity
        )


class VoiceVerificationTool:
    """Bind a single call's audio and capture the deterministic tool result."""

    def __init__(self, verifier: SpeakerVerifier, caller_id: str, audio_data: bytes):
        self.verifier = verifier
        self.caller_id = caller_id
        self.audio_data = audio_data
        self.result: VoiceVerificationResult | None = None
        self.lock = asyncio.Lock()

    async def verify_speaker(self) -> str:
        """Enroll or compare the caller's voice using the supplied call audio."""
        async with self.lock:
            if self.result is None:
                self.result = await asyncio.to_thread(
                    self.verifier.verify, self.caller_id, self.audio_data
                )
            return self.result.status
