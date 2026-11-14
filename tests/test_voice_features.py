import math

import pytest

from harness.tools import compare_voice_features, extract_voice_features


def test_cosine_similarity_for_same_and_different_directions():
    assert compare_voice_features([1.0, 2.0], [2.0, 4.0]) == pytest.approx(1.0)
    assert compare_voice_features([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


@pytest.mark.parametrize("reference,candidate", [([], []), ([1.0], [1.0, 2.0]), ([0.0], [1.0]), ([math.nan], [1.0])])
def test_invalid_embeddings_are_rejected(reference, candidate):
    with pytest.raises(ValueError):
        compare_voice_features(reference, candidate)


@pytest.mark.parametrize("audio", [b"", b"x"])
def test_invalid_pcm_is_rejected(audio):
    with pytest.raises(ValueError):
        extract_voice_features(audio)
