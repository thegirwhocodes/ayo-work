"""Collect voiced telephone frames and resample them for ECAPA-TDNN."""

import numpy as np
import webrtcvad
from scipy.signal import resample_poly


class SpeechCollector:
    def __init__(self, seconds: int = 5):
        self.detector = webrtcvad.Vad(2)
        self.pending = bytearray()
        self.voiced = bytearray()
        self.target_bytes = seconds * 8000 * 2

    def add(self, pcm: bytes) -> bool:
        self.pending.extend(pcm)
        while len(self.pending) >= 320 and len(self.voiced) < self.target_bytes:
            frame = bytes(self.pending[:320])
            del self.pending[:320]
            if self.detector.is_speech(frame, 8000):
                self.voiced.extend(frame)
        return len(self.voiced) >= self.target_bytes

    def audio_16khz(self) -> bytes:
        if len(self.voiced) < self.target_bytes:
            raise ValueError("not enough speech for verification")
        samples = np.frombuffer(self.voiced, dtype="<i2").astype(np.float32)
        converted = resample_poly(samples, 2, 1)
        return np.clip(np.rint(converted), -32768, 32767).astype("<i2").tobytes()
