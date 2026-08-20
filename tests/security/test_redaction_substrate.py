"""C-G0c: unit tests for the central redaction substrate."""

from __future__ import annotations

import pytest

from agentic_coder_prototype.security import redaction


@pytest.fixture(autouse=True)
def _clean_registry():
    redaction.clear_registered_secret_values()
    yield
    redaction.clear_registered_secret_values()


class TestSecretKeyRegistry:
    def test_exact_names_case_and_dash_insensitive(self):
        assert redaction.is_secret_key("api_key")
        assert redaction.is_secret_key("API-KEY")
        assert redaction.is_secret_key("X-Api-Key")
        assert redaction.is_secret_key("Authorization")
        assert redaction.is_secret_key("Set-Cookie")
        assert redaction.is_secret_key("access_token")

    def test_suffix_rule(self):
        assert redaction.is_secret_key("session_access_token")
        assert redaction.is_secret_key("gh_password")
        assert redaction.is_secret_key("service-api-key")

    def test_reference_fields_are_not_secrets(self):
        assert not redaction.is_secret_key("secret_ref")
        assert not redaction.is_secret_key("token_type")
        assert not redaction.is_secret_key("provider")
        assert not redaction.is_secret_key("")


class TestValuePatterns:
    def test_openai_style_key(self):
        assert redaction.REDACTED in redaction.scrub_text("key sk-proj-abcdef123456 end")

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N"
        assert jwt not in redaction.scrub_text(f"token {jwt} tail")

    def test_bearer(self):
        assert "abcdefghij0123456789" not in redaction.scrub_text(
            "Bearer abcdefghij0123456789"
        )

    def test_plain_text_untouched(self):
        text = "requests remaining 42, reset in 6ms"
        assert redaction.scrub_text(text) == text


class TestRegisteredValues:
    def test_registered_value_scrubbed_everywhere(self):
        redaction.register_secret_value("hunter2-canary-value")
        assert "hunter2-canary-value" not in redaction.scrub_text(
            "prefix hunter2-canary-value suffix"
        )

    def test_short_and_non_string_values_ignored(self):
        redaction.register_secret_value("abc")
        redaction.register_secret_value(None)
        redaction.register_secret_value(12345)
        assert redaction.iter_registered_secret_values() == ()

    def test_idempotent(self):
        redaction.register_secret_value("dup-canary-value")
        redaction.register_secret_value("dup-canary-value")
        assert redaction.iter_registered_secret_values() == ("dup-canary-value",)


class TestScrubStructure:
    def test_secret_keys_redacted_with_typed_problems(self):
        payload = {"api_key": "raw", "nested": [{"Authorization": "Bearer x", "ok": 1}]}
        scrubbed, problems = redaction.scrub_structure(payload)
        assert scrubbed["api_key"] == redaction.REDACTED
        assert scrubbed["nested"][0]["Authorization"] == redaction.REDACTED
        assert scrubbed["nested"][0]["ok"] == 1
        codes = {p.code for p in problems}
        assert codes == {"secret_key"}
        assert all(isinstance(p, redaction.RedactionProblem) for p in problems)

    def test_value_problem_paths(self):
        scrubbed, problems = redaction.scrub_structure({"log": "sk-abcdef123456789"})
        assert scrubbed["log"] == redaction.REDACTED
        assert problems == [
            redaction.RedactionProblem(
                "secret_value", "$.log", "secret value scrubbed from text"
            )
        ]
        assert "sk-" not in problems[0].detail

    def test_idempotent_and_never_raises(self):
        payload = {"api_key": "x", "weird": object(), "t": ("sk-abcdef123456789",)}
        once, _ = redaction.scrub_structure(payload)
        twice, problems_second = redaction.scrub_structure(once)
        assert twice == once
        assert [p for p in problems_second if p.code == "secret_value"] == []

    def test_scrub_headers_keeps_rate_limit_data(self):
        headers = {
            "x-ratelimit-remaining-requests": "99",
            "x-api-key": "raw-secret",
            "Cookie": "session=abc",
        }
        scrubbed = redaction.scrub_headers(headers)
        assert scrubbed["x-ratelimit-remaining-requests"] == "99"
        assert scrubbed["x-api-key"] == redaction.REDACTED
        assert scrubbed["Cookie"] == redaction.REDACTED
