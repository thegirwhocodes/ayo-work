"""Speaker feature extraction for telephone calls."""

from __future__ import annotations

import array
import sys
import threading

__all__ = ["extract_voice_features"]

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
