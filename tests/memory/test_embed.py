"""Embed helpers: NullEmbedder determinism, cosine, float16 roundtrip."""

import numpy as np

from tracking.memory.embed import NullEmbedder, cosine, from_blob, to_blob


def test_null_embedder_deterministic():
    e = NullEmbedder()
    a = e.embed("hello world")
    b = e.embed("hello world")
    assert np.array_equal(a, b)
    assert a.dtype == np.float32
    assert a.shape == (e.dim,)


def test_null_embedder_zero_mean():
    e = NullEmbedder()
    v = e.embed("some text")
    assert abs(float(v.mean())) < 1e-3


def test_cosine_matches_numpy_reference():
    rng = np.random.default_rng(42)
    a = rng.standard_normal(64).astype(np.float32)
    b = rng.standard_normal(64).astype(np.float32)
    expected = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
    assert abs(cosine(a, b) - expected) < 1e-6


def test_cosine_handles_zero_vector():
    z = np.zeros(8, dtype=np.float32)
    o = np.ones(8, dtype=np.float32)
    assert cosine(z, o) == 0.0
    assert cosine(o, z) == 0.0
    assert cosine(z, z) == 0.0


def test_float16_roundtrip_preserves_cosine_within_tolerance():
    rng = np.random.default_rng(7)
    a = rng.standard_normal(512).astype(np.float32)
    b = rng.standard_normal(512).astype(np.float32)
    a16 = from_blob(to_blob(a))
    b16 = from_blob(to_blob(b))
    c_full = cosine(a, b)
    c_16 = cosine(a16, b16)
    assert abs(c_full - c_16) < 1e-3


def test_to_blob_size_is_2_bytes_per_dim():
    v = np.zeros(512, dtype=np.float32)
    blob = to_blob(v)
    assert len(blob) == 512 * 2  # float16
