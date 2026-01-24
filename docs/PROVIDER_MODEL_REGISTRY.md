# Provider Model Registry (Draft)

This document describes the optional **model registry** file used to override
provider settings, API keys, and model routing without code changes.

## Enable

- `BREADBOARD_MODEL_REGISTRY=/path/to/models.json`
- (alias) `BREADBOARD_MODEL_REGISTRY_PATH=/path/to/models.json`

## Minimal JSON Schema (Draft)

```json
{
  "default_provider": "openai",
  "providers": {
    "openai": {
      "api_key_env": "OPENAI_API_KEY",
      "base_url": "https://api.openai.com/v1",
      "default_headers": {
        "X-Title": "Breadboard"
      }
    }
  },
  "models": [
    {
      "id": "gpt-4.1",
      "provider": "openai",
      "model_id": "gpt-4.1"
    },
    {
      "id": "fast",
      "route": "openrouter/openai/gpt-5-nano",
      "supports_native_tools": false
    }
  ],
  "auth": {
    "env": {
      "openai": "OPENAI_API_KEY"
    },
    "keys": {
      "openai": "sk-..."
    },
    "files": [
      "~/.config/breadboard/keys.json"
    ]
  }
}
```

### Notes
- `models` may be a list (recommended) or a map keyed by model id.
- Each model entry may declare:
  - `id` (required)
  - `provider` / `provider_id`
  - `model_id` (override)
  - `route` (full provider route string)
  - `aliases` (list of alias ids)
  - `supports_native_tools` (override)
- `providers` entries override per-provider settings such as:
  - `api_key_env`, `base_url`, `default_headers`, `runtime_id`, `default_api_variant`,
    `supports_native_tools`, `supports_streaming`, `supports_reasoning_traces`, `supports_cache_control`

## Auth Resolution Order
1. `auth.keys` inline map in the registry.
2. `auth.env` map (env var lookup per provider).
3. `BREADBOARD_API_KEYS_PATH` file(s) (JSON map), in order.
4. Provider’s default `api_key_env`.

## Status
Draft — evolves with provider routing.
