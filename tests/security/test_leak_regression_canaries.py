"""C-G0d/C-G0e: per-path closure tests plus canary leak-regression suite."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from breadboard_engine.security import redaction

CANARY_ATTACH_KEY = "canary-attach-key-4f9e7c21ab"
CANARY_HEADER_TOKEN = "canary-header-token-8d2b6e90cd"
CANARY_RAW_HEADER = "canary-rawheader-1a5c3f77ee"
CANARY_LOG_VALUE = "canary-logvalue-6e0d9b42aa"
CANARY_TEXT_VALUE = "sk-canarytextvalue123456"


def _attach_via_broker(
    tmp_path: Path,
    provider_id: str,
    *,
    api_key: str,
    headers: dict[str, str] | None = None,
):
    from breadboard_engine.provider_broker import ProviderBroker, SQLiteCredentialStore

    broker = ProviderBroker(SQLiteCredentialStore(tmp_path / f"{provider_id}.sqlite3"))
    return broker.putApiKey(
        {
            "provider_id": provider_id,
            "account_label": f"canary-{provider_id}",
            "api_key": api_key,
            "headers": headers or {},
            "base_url": "https://example.invalid" if provider_id == "openai" else None,
        }
    )


@pytest.fixture(autouse=True)
def _clean_registry():
    redaction.clear_registered_secret_values()
    yield
    redaction.clear_registered_secret_values()


def _assert_tree_clean(root: Path, *canaries: str) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for canary in canaries:
            assert canary not in content, f"canary leaked into {path}"


class TestPath1BrokerCredentialBoundary:
    """Leak path 1: broker credentials never need process environment projection."""

    def test_attach_registers_secrets_and_public_scrub_is_env_independent(
        self, tmp_path: Path, monkeypatch
    ):
        from breadboard_engine.api.public.models import scrub_public

        monkeypatch.setitem(os.environ, "OPENAI_API_KEY", "preexisting")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_HEADERS_JSON", "{}")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_BASE_URL", "")
        _attach_via_broker(
            tmp_path,
            "openai",
            api_key=CANARY_ATTACH_KEY,
            headers={"chatgpt-account-id": CANARY_HEADER_TOKEN},
        )
        assert CANARY_HEADER_TOKEN in redaction.iter_registered_secret_values()
        scrubbed = scrub_public(
            {"detail": f"boom {CANARY_ATTACH_KEY} and {CANARY_HEADER_TOKEN}"}
        )
        assert CANARY_ATTACH_KEY not in json.dumps(scrubbed)
        assert CANARY_HEADER_TOKEN not in json.dumps(scrubbed)

    def test_non_openai_material_still_registered(self, tmp_path: Path):
        _attach_via_broker(tmp_path, "anthropic", api_key="anthropic-canary-key-77aa")
        assert "anthropic-canary-key-77aa" in redaction.iter_registered_secret_values()


class TestPath2RawHeaders:
    """Leak path: rate-limit parser persisted nearly all raw headers."""

    def test_raw_headers_sanitized_at_source(self):
        from breadboard_engine.limits.parse_headers import parse_rate_limit_headers

        snapshot = parse_rate_limit_headers(
            {
                "x-ratelimit-limit-requests": "100",
                "x-ratelimit-remaining-requests": "99",
                "authorization": f"Bearer {CANARY_RAW_HEADER}",
                "x-api-key": CANARY_RAW_HEADER,
                "cookie": f"session={CANARY_RAW_HEADER}",
            },
            provider="openai",
        )
        assert snapshot is not None
        assert snapshot["buckets"]
        assert CANARY_RAW_HEADER not in json.dumps(snapshot)
        assert snapshot["raw_headers"]["x-ratelimit-remaining-requests"] == "99"


class TestPath3RunLoggerKeylists:
    """Leak path: run logger recognized only four key spellings."""

    def _logger(self, tmp_path: Path):
        from breadboard_engine.run_logging.run_logger import LoggerV2Manager

        manager = LoggerV2Manager({"logging": {"root_dir": str(tmp_path / "logs")}})
        manager.start_run("canary-session")
        return manager

    def test_wide_key_coverage_in_json(self, tmp_path):
        manager = self._logger(tmp_path)
        manager.write_json(
            "meta/canary.json",
            {
                "access_token": CANARY_LOG_VALUE,
                "refresh_token": CANARY_LOG_VALUE,
                "cookie": CANARY_LOG_VALUE,
                "id_token": CANARY_LOG_VALUE,
                "session_access_token": CANARY_LOG_VALUE,
                "kept": "fine",
            },
        )
        manager.append_jsonl("events/canary.jsonl", {"x-api-key": CANARY_LOG_VALUE})
        _assert_tree_clean(Path(manager.run_dir), CANARY_LOG_VALUE)

    def test_text_writers_no_longer_bypass_redaction(self, tmp_path):
        redaction.register_secret_value(CANARY_LOG_VALUE)
        manager = self._logger(tmp_path)
        manager.write_text("notes/trace.txt", f"exception with {CANARY_LOG_VALUE}")
        manager.append_text("notes/trace.log", f"pattern {CANARY_TEXT_VALUE} leak\n")
        _assert_tree_clean(Path(manager.run_dir), CANARY_LOG_VALUE, CANARY_TEXT_VALUE)


class TestPath4RecorderAndProviderDump:
    """Leak paths: API recorder and provider dump had drifting deny-lists."""

    def test_api_recorder_wide_coverage(self):
        from breadboard_engine.run_logging.api_recorder import _redact_payload

        sanitized = _redact_payload(
            {"headers": {"x-goog-api-key": CANARY_LOG_VALUE}, "body": "ok"}
        )
        assert CANARY_LOG_VALUE not in json.dumps(sanitized)
        assert sanitized["body"] == "ok"

    def test_provider_dump_wide_coverage(self):
        from breadboard_engine.logging.provider_dump import _scrub

        sanitized = _scrub(
            {"set-cookie": CANARY_LOG_VALUE, "detail": f"Bearer {CANARY_RAW_HEADER}"}
        )
        assert CANARY_LOG_VALUE not in json.dumps(sanitized)
        assert CANARY_RAW_HEADER not in json.dumps(sanitized)


class TestCanarySweep:
    """C-G0e: one combined run injecting canaries through every historic path."""

    def test_end_to_end_zero_occurrences(self, tmp_path, monkeypatch):
        from breadboard_engine.api.public.models import scrub_public
        from breadboard_engine.limits.parse_headers import parse_rate_limit_headers
        from breadboard_engine.run_logging.api_recorder import APIRequestRecorder
        from breadboard_engine.run_logging.run_logger import LoggerV2Manager

        monkeypatch.setitem(os.environ, "OPENAI_API_KEY", "preexisting")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_HEADERS_JSON", "{}")
        monkeypatch.setitem(os.environ, "BREADBOARD_OPENAI_AUTH_BASE_URL", "")
        canaries = (
            CANARY_ATTACH_KEY,
            CANARY_HEADER_TOKEN,
            CANARY_RAW_HEADER,
            CANARY_LOG_VALUE,
            CANARY_TEXT_VALUE,
        )

        _attach_via_broker(
            tmp_path,
            "openai",
            api_key=CANARY_ATTACH_KEY,
            headers={"chatgpt-account-id": CANARY_HEADER_TOKEN},
        )

        manager = LoggerV2Manager({"logging": {"root_dir": str(tmp_path / "logs")}})
        manager.start_run("canary-e2e")
        recorder = APIRequestRecorder(manager)
        snapshot = parse_rate_limit_headers(
            {
                "x-ratelimit-limit-requests": "10",
                "x-api-key": CANARY_RAW_HEADER,
                "retry-after": "1",
            },
            provider="openai",
        )
        manager.write_json("events/limits.json", snapshot)
        manager.append_jsonl(
            "events/stream.jsonl",
            {"access_token": CANARY_LOG_VALUE, "note": CANARY_ATTACH_KEY},
        )
        manager.write_text("raw/error.txt", f"trace {CANARY_HEADER_TOKEN} {CANARY_TEXT_VALUE}")
        recorder.save_request(1, {"headers": {"authorization": CANARY_RAW_HEADER}})
        recorder.save_response(1, {"cookie": CANARY_LOG_VALUE})

        value_scrub_canaries = (CANARY_ATTACH_KEY, CANARY_HEADER_TOKEN, CANARY_TEXT_VALUE)
        public = scrub_public({"blob": " ".join(canaries)})
        assert not any(canary in json.dumps(public) for canary in value_scrub_canaries)
        _assert_tree_clean(Path(manager.run_dir), *canaries)
