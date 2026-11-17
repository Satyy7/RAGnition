from ragnition.core.providers.cache import CallCache, make_cache_key


def test_key_ignores_dict_order():
    assert make_cache_key("chat", "groq", "m", {"a": 1, "b": 2}) == make_cache_key("chat", "groq", "m", {"b": 2, "a": 1})


def test_key_changes_with_model_and_payload():
    base = make_cache_key("chat", "groq", "m1", {"q": "x"})
    assert base != make_cache_key("chat", "groq", "m2", {"q": "x"})
    assert base != make_cache_key("chat", "groq", "m1", {"q": "y"})
    assert base != make_cache_key("judge", "groq", "m1", {"q": "x"})


def test_roundtrip_persists_across_reopen(tmp_path):
    path = tmp_path / "cache.sqlite3"
    cache = CallCache(path)
    key = make_cache_key("chat", "groq", "m", {"q": "x"})
    assert cache.get(key) is None
    cache.put(key, namespace="chat", provider="groq", model="m", response_json='{"text":"hi"}')
    cache.close()

    reopened = CallCache(path)
    assert reopened.get(key) == '{"text":"hi"}'
    assert reopened.stats() == {"chat": 1}
    reopened.close()
