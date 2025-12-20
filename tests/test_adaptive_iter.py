from adaptive_iter import encode_adaptive_iterable, decode_adaptive_iterable, ADAPTIVE_PREFIX_ITERABLE, ADAPTIVE_PREFIX_NON_ITERABLE


def test_encode_iterable():
    src = ["a", "b", "c"]
    gen = encode_adaptive_iterable(src)
    items = list(gen)
    assert items[0] == ADAPTIVE_PREFIX_ITERABLE
    assert items[1:] == src


def test_encode_non_iterable():
    obj = {"k": 1}
    gen = encode_adaptive_iterable(obj)
    items = list(gen)
    assert items[0] == ADAPTIVE_PREFIX_NON_ITERABLE
    # second item is payload; decode to verify roundtrip
    is_iter, val = decode_adaptive_iterable(items)
    assert is_iter is False
    assert val == obj


def test_decode_iterable_roundtrip():
    src = ["x", "y"]
    is_iter, it = decode_adaptive_iterable(encode_adaptive_iterable(src))
    assert is_iter is True
    assert list(it) == src


