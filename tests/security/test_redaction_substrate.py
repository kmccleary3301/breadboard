"""C-G0c: unit tests for the central redaction substrate."""

from __future__ import annotations

import contextvars

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
        assert redaction.is_secret_key("X-Authorization")
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
            assert redaction.contains_registered_secret_text(f"prefix-{secret}-suffix")
            assert not redaction.contains_registered_secret_text("description")
        assert redaction.iter_registered_secret_values() == ()
        assert not redaction.contains_registered_secret_mapping_key(payload)
        assert not redaction.contains_registered_secret_text(f"prefix-{secret}-suffix")

    def test_registered_secret_scrubs_mapping_key_and_problem_path(self):
        secret = "mapping-key-canary-value"
        with redaction.secret_value_scope(secret):
            scrubbed, problems = redaction.scrub_structure(
                {"outer": [{f"prefix-{secret}-suffix": "description"}]}
            )
        safe_key = f"prefix-{redaction.REDACTED}-suffix"
        assert scrubbed == {"outer": [{safe_key: "description"}]}
        assert problems == [
            redaction.RedactionProblem(
                "secret_value",
                f"$.outer[0].{safe_key}",
                "secret value scrubbed from mapping key",
            )
        ]
        assert secret not in repr(problems)
        assert redaction.iter_registered_secret_values() == ()

    def test_registered_numeric_secret_scrubs_json_scalar(self):
        secret = "4827"
        with redaction.secret_value_scope(secret):
            scrubbed, problems = redaction.scrub_structure({"nested": [int(secret)]})
        assert scrubbed == {"nested": [redaction.REDACTED]}
        assert problems == [
            redaction.RedactionProblem(
                "secret_value",
                "$.nested[0]",
                "secret value scrubbed from scalar",
            )
        ]
        assert redaction.iter_registered_secret_values() == ()

    def test_short_numeric_secret_is_exact_for_scalars_and_fail_closed_for_text(self):
        with redaction.secret_value_scope("42", allow_short=True):
            scrubbed, _ = redaction.scrub_structure({"credential": 42, "status": 429})
            assert scrubbed == {
                "credential": redaction.REDACTED,
                "status": 429,
            }
            assert (
                redaction.scrub_text("request failed with status 429")
                == redaction.REDACTED
            )

    def test_credential_material_extracts_nested_and_encoded_secrets(self):
        assert redaction.credential_secret_values(
            {
                "api_key": "primary-secret",
                "headers": {
                    "Authorization": "Bearer authorization-secret",
                    "x-api-key": "header-secret",
                    "Cookie": "sid=cookie-secret",
                    "Content-Type": "application/json",
                },
                "base_url": (
                    "https://url-user:url-password@example.test/v1?api_key=query-secret"
                ),
                "routing": {
                    "nested": {"refresh_token": "routing-secret"},
                    "region": "us-east",
                },
            }
        ) == (
            "primary-secret",
            "Bearer authorization-secret",
            "authorization-secret",
            "header-secret",
            "sid=cookie-secret",
            "cookie-secret",
            "application/json",
            "url-user",
            "url-password",
            "query-secret",
            "routing-secret",
        )
        assert redaction.credential_secret_values(
            {"headers": {"x-api-key": "abc"}}
        ) == ("abc",)
        assert redaction.credential_secret_values(
            {"headers": {"X-Custom": "custom-header-secret"}}
        ) == ("custom-header-secret",)

    def test_credential_material_decodes_header_and_numeric_components(self):
        basic_credential = "YmFzaWMtdXNlcjpiYXNpYy1wYXNzd29yZA=="
        assert redaction.credential_secret_values(
            {
                "headers": {
                    "X-Authorization": "Bearer prefixed-secret",
                    "XAuthorization": "Bearer compact-secret",
                    "Proxy-Authentication": f"Basic {basic_credential}",
                    "Cookie": 'sid="cookie%2Dencoded"',
                    "Set-Cookie": ('session_token="set%2Dcookie"; Path=/; HttpOnly'),
                    "Content-Type": "application/json",
                },
                "routing": {
                    "access_token": 4827,
                    "session_token": True,
                },
            }
        ) == (
            "Bearer prefixed-secret",
            "prefixed-secret",
            "Bearer compact-secret",
            "compact-secret",
            f"Basic {basic_credential}",
            basic_credential,
            "basic-user:basic-password",
            "basic-user",
            "basic-password",
            'sid="cookie%2Dencoded"',
            "cookie%2Dencoded",
            "cookie-encoded",
            'session_token="set%2Dcookie"; Path=/; HttpOnly',
            "set%2Dcookie",
            "set-cookie",
            "application/json",
            "4827",
            "4827.0",
        )
        assert redaction.credential_secret_values(
            {"routing": {"access_token": 123}}
        ) == ("123", "123.0")

    def test_credential_material_keeps_raw_and_decoded_url_credentials(self):
        assert redaction.credential_secret_values(
            {
                "base_url": (
                    "https://url%2Duser:url%2Dpassword@example.test/v1"
                    "?api_key=query%2Dsecret"
                )
            }
        ) == (
            "url%2Duser",
            "url-user",
            "url%2Dpassword",
            "url-password",
            "query%2Dsecret",
            "query-secret",
        )

    def test_short_and_non_string_values_ignored(self):
        with redaction.secret_value_scope("abc", None, 12345):
            assert redaction.iter_registered_secret_values() == ()
        assert redaction.iter_registered_secret_values() == ()

    def test_credential_scope_registers_short_values(self):
        with redaction.secret_value_scope("abc", allow_short=True):
            assert redaction.iter_registered_secret_values() == ("abc",)
            assert redaction.scrub_text("echo abc") == (f"echo {redaction.REDACTED}")
        assert redaction.iter_registered_secret_values() == ()

    def test_short_secret_redacts_text_and_preserves_identity_substrings(self):
        with redaction.secret_value_scope("a", allow_short=True):
            assert (
                redaction.contains_registered_secret_text("provider call failed a")
                is True
            )
            assert (
                redaction.scrub_text("provider call failed a")
                == redaction.REDACTED
            )
            scrubbed, _ = redaction.scrub_structure(
                {"label": "a", "message": "provider call failed a"},
                identity_mapping_keys=True,
            )
            assert scrubbed == {
                "label": redaction.REDACTED,
                "message": redaction.REDACTED,
            }

        with redaction.secret_value_scope("ant", allow_short=True):
            assert redaction.contains_registered_secret_identity("anthropic") is False
            assert redaction.contains_registered_secret_text("anthropic") is True

    def test_exception_scrubbing_fails_closed_for_embedded_short_secret(self):
        error = RuntimeError("provider call failed a")
        error.details = {"message": "nested provider failure a"}
        error.add_note("provider note a")

        with redaction.secret_value_scope("a", allow_short=True):
            redaction.scrub_exception_in_place(error)

        assert str(error) == redaction.REDACTED
        assert error.details == {"message": redaction.REDACTED}
        assert error.__notes__ == [redaction.REDACTED]


    def test_exception_control_fields_cannot_restore_embedded_short_secret(self):
        error = RuntimeError("provider call failed")
        error.details = {
            "classification": "a-rate-limited",
            "status_code": 429,
        }

        with redaction.secret_value_scope("a", allow_short=True):
            redaction.scrub_exception_in_place(error)

        assert error.details == {
            "classification": redaction.REDACTED,
            "status_code": 429,
        }

    def test_scopes_are_isolated_between_operation_contexts(self):
        outer_secret = "outer-operation-secret"
        inner_secret = "inner-operation-secret"

        def isolated_operation():
            assert redaction.iter_registered_secret_values() == ()
            with redaction.secret_value_scope(inner_secret):
                return redaction.iter_registered_secret_values()

        with redaction.secret_value_scope(outer_secret):
            assert contextvars.Context().run(isolated_operation) == (inner_secret,)
            assert redaction.iter_registered_secret_values() == (outer_secret,)
        assert redaction.iter_registered_secret_values() == ()

    def test_overlapping_scopes_are_reference_counted(self):
        secret = "dup-canary-value"
        with redaction.secret_value_scope(secret):
            with redaction.secret_value_scope(secret):
                assert redaction.iter_registered_secret_values() == (secret,)
            assert redaction.iter_registered_secret_values() == (secret,)
        assert redaction.iter_registered_secret_values() == ()

    def test_overlapping_secret_values_scrub_longest_first(self):
        shorter = "overlap-secret"
        longer = "overlap-secret-suffix"
        with redaction.secret_value_scope(shorter, longer):
            assert redaction.iter_registered_secret_values() == (longer, shorter)
            assert redaction.scrub_text(longer) == redaction.REDACTED
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
