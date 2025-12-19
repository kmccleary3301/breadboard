"""Smoke test for Anthropic streaming via provider runtime."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def load_env(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    try:
        for raw in dotenv_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


def run_smoke(model_id: str, prompt: str, max_tokens: int, temperature: float) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from agentic_coder_prototype.provider_routing import provider_router
    from agentic_coder_prototype.provider_runtime import (
        ProviderRuntimeContext,
        ProviderRuntimeError,
        provider_registry,
    )

    class _Session:
        def get_provider_metadata(self, key: str, default=None):
            return default

    descriptor, model = provider_router.get_runtime_descriptor(model_id)
    runtime = provider_registry.create_runtime(descriptor)
    client_cfg = provider_router.create_client_config(model_id)

    api_key = client_cfg.get("api_key")
    if not api_key:
        raise SystemExit(f"Missing API key environment variable: {descriptor.api_key_env}")

    client = runtime.create_client(
        api_key,
        base_url=client_cfg.get("base_url"),
        default_headers=client_cfg.get("default_headers"),
    )

    context = ProviderRuntimeContext(
        session_state=_Session(),
        agent_config={
            "provider_tools": {
                "anthropic": {
                    "max_output_tokens": max_tokens,
                    "temperature": temperature,
                }
            }
        },
        stream=True,
    )

    try:
        result = runtime.invoke(
            client=client,
            model=model,
            messages=[{"role": "user", "content": prompt}],
            tools=None,
            stream=True,
            context=context,
        )
    except ProviderRuntimeError as exc:
        raise SystemExit(f"Anthropic smoke test failed: {exc}") from exc

    print("Model:", result.model)
    print("Usage:", result.usage)
    if result.messages:
        print("Response:\n", (result.messages[0].content or "").strip())
    if result.reasoning_summaries:
        print("Reasoning summary:", result.reasoning_summaries[0])


def main() -> None:
    parser = argparse.ArgumentParser(description="Anthropic Responses smoke test")
    parser.add_argument(
        "--model",
        default="anthropic/claude-sonnet-4-20250514",
        help="Model identifier (default: anthropic/claude-sonnet-4-20250514)",
    )
    parser.add_argument(
        "--prompt",
        default="Reply with a short haiku about modular coding agents.",
        help="Prompt to send",
    )
    parser.add_argument("--max-tokens", type=int, default=64, help="max output tokens")
    parser.add_argument("--temperature", type=float, default=0.2, help="sampling temperature")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    load_env(repo_root / ".env")
    load_env(Path.cwd() / ".env")

    run_smoke(args.model, args.prompt, args.max_tokens, args.temperature)


if __name__ == "__main__":
    main()

