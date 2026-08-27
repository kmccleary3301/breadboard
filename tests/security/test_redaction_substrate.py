"""C-G0c: unit tests for the central redaction substrate."""

from __future__ import annotations

import pytest

from breadboard_engine.security import redaction


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
        assert redaction.REDACTED in redaction.scrub_text(
            "key sk-proj-abcdef123456 end"
        )

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
    def test_scoped_value_scrubbed_then_released(self):
        secret = "hunter2-canary-value"
        with redaction.secret_value_scope(secret):
            assert secret not in redaction.scrub_text(f"prefix {secret} suffix")
            assert redaction.iter_registered_secret_values() == (secret,)
        assert redaction.iter_registered_secret_values() == ()

    def test_scoped_value_detected_in_nested_mapping_key(self):
        secret = "mapping-key-canary-value"
        payload = {"outer": [{f"prefix-{secret}-suffix": "description"}]}
        with redaction.secret_value_scope(secret):
            assert redaction.contains_registered_secret_mapping_key(payload)
            assert not redaction.contains_registered_secret_mapping_key(
                {"outer": [{"description": secret}]}
            )
            assert redaction.contains_registered_secret_text(
                f"prefix-{secret}-suffix"
            )
            assert not redaction.contains_registered_secret_text("description")
        assert redaction.iter_registered_secret_values() == ()
        assert not redaction.contains_registered_secret_mapping_key(payload)
        assert not redaction.contains_registered_secret_text(
            f"prefix-{secret}-suffix"
        )

    def test_short_and_non_string_values_ignored(self):
        with redaction.secret_value_scope("abc", None, 12345):
            assert redaction.iter_registered_secret_values() == ()
        assert redaction.iter_registered_secret_values() == ()

    def test_overlapping_scopes_are_reference_counted(self):
        secret = "dup-canary-value"
        with redaction.secret_value_scope(secret):
            with redaction.secret_value_scope(secret):
                assert redaction.iter_registered_secret_values() == (secret,)
            assert redaction.iter_registered_secret_values() == (secret,)
        assert redaction.iter_registered_secret_values() == ()

    def test_exception_fields_are_scrubbed_before_scope_release(self):
        secret = "exception-canary-value"
        error = RuntimeError(f"failed with {secret}")
        error.details = {"debug": secret, "authorization": secret}
        error.add_note(f"note {secret}")

        with redaction.secret_value_scope(secret):
            assert redaction.scrub_exception_in_place(error) is error

        assert secret not in str(error)
        assert secret not in repr(error.details)
        assert secret not in repr(error.__notes__)
        assert redaction.iter_registered_secret_values() == ()


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

    def test_auth_secrets_redact_without_destroying_generic_state_or_code(self):
        payload = {
            "state": "running",
            "code": "flow_unavailable",
            "authorization_code": "auth-code-canary",
            "user_code": "ABCD-EFGH",
        }

        scrubbed, _problems = redaction.scrub_structure(payload)

        assert scrubbed["state"] == "running"
        assert scrubbed["code"] == "flow_unavailable"
        assert scrubbed["authorization_code"] == redaction.REDACTED
        assert scrubbed["user_code"] == redaction.REDACTED

    def test_auth_url_redacts_secret_query_keys_and_drops_fragment(self):
        query_secret = "opaque-query-canary"
        fragment_secret = "opaque-fragment-canary"
        cleaned = redaction.scrub_auth_url(
            "https://auth.example.test/authorize"
            f"?client_id=public-client&access_token={query_secret}&state=one-time"
            f"#access_token={fragment_secret}"
        )

        assert "client_id=public-client" in cleaned
        assert query_secret not in cleaned
        assert fragment_secret not in cleaned
        assert "access_token=%2A%2A%2AREDACTED%2A%2A%2A" in cleaned
        assert "state=%2A%2A%2AREDACTED%2A%2A%2A" in cleaned
        assert "#" not in cleaned

    def test_provider_auth_runtime_is_removed_recursively(self):
        payload = {
            "provider_auth_runtime": {"openai": {"api_key": "top-canary"}},
            "wrapper": {
                "provider_auth_runtime.openai.api_key": "nested-canary",
                "safe": True,
            },
            "items": [{"provider_auth_runtime": {"token": "list-canary"}}],
            "providers.provider_auth_runtime.openai.api_key": "prefixed-canary",
            "providers": {
                "provider_auth_runtime.openai.api_key": "nested-prefixed-canary"
            },
        }

        assert redaction.contains_provider_auth_runtime(payload)
        stripped = redaction.strip_provider_auth_runtime(payload)
        assert stripped == {
            "wrapper": {"safe": True},
            "items": [{}],
            "providers": {},
        }
        assert not redaction.contains_provider_auth_runtime(stripped)
