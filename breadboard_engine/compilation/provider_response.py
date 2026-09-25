"""Compiler-owned admission for the lossless native-response consumer.

The compiler emits the policy as semantic provider data.  This module binds one
verified compiled model to one episode-scoped OpenAI Chat profile and the
observation/context identities supplied by the episode resolver.  It does not
instantiate a client or provide a transport fallback.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from breadboard_engine.compilation.contracts import (
    bytes_sha256,
    canonical_sha256,
    require_sha256,
)
from breadboard_engine.provider.profiles import OpenAICompletionsProviderProfile

NATIVE_RESPONSE_POLICY_SCHEMA_VERSION: Final = "bb.provider_native_response_policy.v1"
NATIVE_RESPONSE_CONSUMER_ID: Final = "breadboard.provider.recording.v1"
MINI_RESPONSE_CONSUMER_ID: Final = "breadboard.mini-swe-agent.v2.4.6"
PI_RESPONSE_CONSUMER_ID: Final = "breadboard.pi-coding-agent.v0.73.1"
OMP_RESPONSE_CONSUMER_ID: Final = "breadboard.oh-my-pi.v18.1.17"
OPENHANDS_RESPONSE_CONSUMER_ID: Final = "breadboard.openhands-sdk.v1.47.0"
HERMES_RESPONSE_CONSUMER_ID: Final = "breadboard.hermes-agent.v2026.9.11"
NATIVE_CHAT_RESPONSE_TARGETS: Final = MappingProxyType({
    OPENHANDS_RESPONSE_CONSUMER_ID: "openhands-sdk@1.47.0",
    HERMES_RESPONSE_CONSUMER_ID: "hermes-agent@2026.9.11",
})
OPENCLAW_RESPONSE_CONSUMER_ID: Final = "breadboard.openclaw.native-chat.v1"
NATIVE_RESPONSE_BINDING_SCHEMA_VERSION: Final = "bb.provider_native_response_binding.v1"
MAX_NATIVE_RESPONSE_BYTES: Final = 16 * 1024 * 1024
MAX_NATIVE_STREAM_FRAGMENTS: Final = 65_536


_NATIVE_RESPONSE_CONSUMER_MODES: Final = {
    NATIVE_RESPONSE_CONSUMER_ID: frozenset({"non_streaming", "streaming"}),
    MINI_RESPONSE_CONSUMER_ID: frozenset({"non_streaming"}),
    PI_RESPONSE_CONSUMER_ID: frozenset({"streaming"}),
    OMP_RESPONSE_CONSUMER_ID: frozenset({"streaming"}),
    OPENHANDS_RESPONSE_CONSUMER_ID: frozenset({"non_streaming"}),
    HERMES_RESPONSE_CONSUMER_ID: frozenset({"non_streaming"}),
    OPENCLAW_RESPONSE_CONSUMER_ID: frozenset({"streaming"}),
}


def native_response_consumer_modes(consumer_id: str) -> frozenset[str]:
    """Return the admitted request modes for one registered consumer."""
    try:
        return _NATIVE_RESPONSE_CONSUMER_MODES[consumer_id]
    except KeyError:
        raise NativeResponseBindingError(
            "response_policy.consumer_id is unsupported"
        ) from None


def is_native_response_consumer_registered(consumer_id: str) -> bool:
    return consumer_id in _NATIVE_RESPONSE_CONSUMER_MODES

_POLICY_FIELDS: Final = frozenset(
    {
        "schema_version",
        "consumer_id",
        "provider_profile_digest",
        "max_response_bytes",
        "max_stream_fragments",
    }
)


class NativeResponseBindingError(ValueError):
    """A native-response policy or binding is not admissible."""


def _require_text(value: Any, field_name: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise NativeResponseBindingError(f"{field_name} must be non-empty text")
    return value


def _require_positive_bounded_int(value: Any, field_name: str, maximum: int) -> int:
    if type(value) is not int or isinstance(value, bool) or not 0 < value <= maximum:
        raise NativeResponseBindingError(
            f"{field_name} must be a positive integer no greater than {maximum}"
        )
    return value


def profile_identity_digest(profile: OpenAICompletionsProviderProfile) -> str:
    """Return the standard digest reference for a profile's secret-free identity."""

    if not isinstance(profile, OpenAICompletionsProviderProfile):
        raise NativeResponseBindingError("profile must be OpenAICompletionsProviderProfile")
    return "sha256:" + hashlib.sha256(
        profile.identity_json().encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeResponsePolicy:
    """Closed limits and consumer identity for one compiled native response path."""

    schema_version: str
    consumer_id: str
    provider_profile_digest: str
    max_response_bytes: int
    max_stream_fragments: int

    def __post_init__(self) -> None:
        if self.schema_version != NATIVE_RESPONSE_POLICY_SCHEMA_VERSION:
            raise NativeResponseBindingError(
                "response_policy.schema_version is unsupported"
            )
        if not is_native_response_consumer_registered(self.consumer_id):
            raise NativeResponseBindingError(
                "response_policy.consumer_id is unsupported"
            )
        require_sha256(self.provider_profile_digest, "provider_profile_digest")
        _require_positive_bounded_int(
            self.max_response_bytes, "max_response_bytes", MAX_NATIVE_RESPONSE_BYTES
        )
        _require_positive_bounded_int(
            self.max_stream_fragments,
            "max_stream_fragments",
            MAX_NATIVE_STREAM_FRAGMENTS,
        )

    @classmethod
    def from_dict(cls, value: Any) -> NativeResponsePolicy:
        if not isinstance(value, Mapping):
            raise NativeResponseBindingError("response_policy must be an object")
        unknown = set(value) - _POLICY_FIELDS
        if unknown:
            raise NativeResponseBindingError(
                "response_policy contains unsupported fields: "
                + ", ".join(sorted(str(item) for item in unknown))
            )
        missing = _POLICY_FIELDS - set(value)
        if missing:
            raise NativeResponseBindingError(
                "response_policy is missing required fields: "
                + ", ".join(sorted(missing))
            )
        return cls(
            schema_version=value["schema_version"],
            consumer_id=value["consumer_id"],
            provider_profile_digest=value["provider_profile_digest"],
            max_response_bytes=value["max_response_bytes"],
            max_stream_fragments=value["max_stream_fragments"],
        )

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "consumer_id": self.consumer_id,
            "provider_profile_digest": self.provider_profile_digest,
            "max_response_bytes": self.max_response_bytes,
            "max_stream_fragments": self.max_stream_fragments,
        }



_BINDING_SEAL = object()

@dataclass(frozen=True, slots=True, init=False)
class CompiledNativeResponseBinding:
    """An admitted policy joined to one compiled model and episode context."""

    policy: NativeResponsePolicy
    episode_id: str
    effective_plan_digest: str
    capability_observation_digest: str
    authority_model_id: str
    compiled_manifest_digest: str
    compiled_model_digest: str

    def __init__(
        self,
        *,
        policy: NativeResponsePolicy,
        episode_id: str,
        effective_plan_digest: str,
        capability_observation_digest: str,
        authority_model_id: str,
        compiled_manifest_digest: str,
        compiled_model_digest: str,
        _seal: object | None = None,
    ) -> None:
        if _seal is not _BINDING_SEAL:
            raise NativeResponseBindingError(
                "CompiledNativeResponseBinding must come from admission"
            )
        object.__setattr__(self, "policy", policy)
        object.__setattr__(self, "episode_id", episode_id)
        object.__setattr__(self, "effective_plan_digest", effective_plan_digest)
        object.__setattr__(
            self, "capability_observation_digest", capability_observation_digest
        )
        object.__setattr__(self, "authority_model_id", authority_model_id)
        object.__setattr__(self, "compiled_manifest_digest", compiled_manifest_digest)
        object.__setattr__(self, "compiled_model_digest", compiled_model_digest)
        self.__post_init__()

    @classmethod
    def _from_admitted(
        cls,
        *,
        policy: NativeResponsePolicy,
        episode_id: str,
        effective_plan_digest: str,
        capability_observation_digest: str,
        authority_model_id: str,
        compiled_manifest_digest: str,
        compiled_model_digest: str,
    ) -> CompiledNativeResponseBinding:
        return cls(
            policy=policy,
            episode_id=episode_id,
            effective_plan_digest=effective_plan_digest,
            capability_observation_digest=capability_observation_digest,
            authority_model_id=authority_model_id,
            compiled_manifest_digest=compiled_manifest_digest,
            compiled_model_digest=compiled_model_digest,
            _seal=_BINDING_SEAL,
        )

    def __post_init__(self) -> None:
        if not isinstance(self.policy, NativeResponsePolicy):
            raise NativeResponseBindingError("binding.policy is invalid")
        _require_text(self.episode_id, "episode_id")
        require_sha256(self.effective_plan_digest, "effective_plan_digest")
        require_sha256(
            self.capability_observation_digest, "capability_observation_digest"
        )
        _require_text(self.authority_model_id, "authority_model_id")
        require_sha256(self.compiled_manifest_digest, "compiled_manifest_digest")
        require_sha256(self.compiled_model_digest, "compiled_model_digest")

    def identity_dict(self) -> dict[str, Any]:
        return {
            "schema_version": NATIVE_RESPONSE_BINDING_SCHEMA_VERSION,
            "policy": self.policy.identity_dict(),
            "episode_id": self.episode_id,
            "effective_plan_digest": self.effective_plan_digest,
            "capability_observation_digest": self.capability_observation_digest,
            "authority_model_id": self.authority_model_id,
            "compiled_manifest_digest": self.compiled_manifest_digest,
            "compiled_model_digest": self.compiled_model_digest,
        }

    @property
    def digest(self) -> str:
        return canonical_sha256(self.identity_dict())

    def validate_invocation(
        self,
        profile: OpenAICompletionsProviderProfile,
        *,
        episode_id: str,
        effective_plan_digest: str,
        capability_observation_digest: str,
    ) -> None:
        """Require the runtime's actual profile/context joins before any wire call."""

        if not isinstance(profile, OpenAICompletionsProviderProfile):
            raise NativeResponseBindingError("profile must be OpenAICompletionsProviderProfile")
        if episode_id != self.episode_id:
            raise NativeResponseBindingError("native response episode binding mismatch")
        if effective_plan_digest != self.effective_plan_digest:
            raise NativeResponseBindingError(
                "native response effective-plan binding mismatch"
            )
        if capability_observation_digest != self.capability_observation_digest:
            raise NativeResponseBindingError(
                "native response capability-observation binding mismatch"
            )
        if profile.provider_id != "openai" or profile.runtime_id != "openai_chat":
            raise NativeResponseBindingError("profile is not OpenAI Chat")
        if profile_identity_digest(profile) != self.policy.provider_profile_digest:
            raise NativeResponseBindingError("native response profile binding mismatch")


def admit_native_response_binding(
    compiled_manifest: bytes,
    *,
    expected_compiler_input_digest: str,
    authority_model_id: str,
    profile: OpenAICompletionsProviderProfile,
    capability_observation_digest: str,
    episode_id: str,
    effective_plan_digest: str,
    request_mode: str | None = None,
) -> CompiledNativeResponseBinding:
    """Admit a registered native consumer for the profile's request mode.

    The manifest is revalidated through the public cached-manifest validator;
    the selected model, adapter, policy, profile and context are joined without
    fallback or caller-supplied capability switches.
    """

    if type(compiled_manifest) is not bytes:
        raise NativeResponseBindingError("compiled_manifest must be bytes")
    expected_compiler_input_digest = require_sha256(
        expected_compiler_input_digest, "expected_compiler_input_digest"
    )
    authority_model_id = _require_text(authority_model_id, "authority_model_id")
    _require_text(episode_id, "episode_id")
    require_sha256(capability_observation_digest, "capability_observation_digest")
    require_sha256(effective_plan_digest, "effective_plan_digest")
    if not isinstance(profile, OpenAICompletionsProviderProfile):
        raise NativeResponseBindingError("profile must be OpenAICompletionsProviderProfile")

    effective_mode = profile.request_policy.mode
    if request_mode is not None:
        if request_mode not in {"streaming", "non_streaming"}:
            raise NativeResponseBindingError("request_mode is unsupported")
        if request_mode != effective_mode:
            raise NativeResponseBindingError("native response request mode differs from profile")
    # Keep this import local: server_compiler may use NativeResponsePolicy for
    # semantic input validation, while this binding remains its consumer.
    from breadboard_engine.compilation.server_compiler import verify_cached_manifest

    manifest = verify_cached_manifest(
        compiled_manifest,
        expected_compiler_input_digest=expected_compiler_input_digest,
    )
    providers = manifest.semantic.providers
    models = providers.get("models")
    if not isinstance(models, (list, tuple)):
        raise NativeResponseBindingError("compiled provider models are invalid")
    selected = next(
        (
            model
            for model in models
            if isinstance(model, Mapping) and model.get("model_id") == authority_model_id
        ),
        None,
    )
    if selected is None:
        raise NativeResponseBindingError(
            "authority model is not a model in the compiled manifest"
        )
    if selected.get("adapter_id") != "openai":
        raise NativeResponseBindingError("native response requires OpenAI Chat adapter")
    if selected.get("context_length") != profile.context_window:
        raise NativeResponseBindingError("compiled model context does not match profile")
    routing = selected.get("routing")
    if not isinstance(routing, Mapping):
        raise NativeResponseBindingError("compiled native response routing is invalid")
    fallback_model_ids = routing.get("fallback_model_ids")
    if not isinstance(fallback_model_ids, (list, tuple)) or fallback_model_ids:
        raise NativeResponseBindingError("native response admission forbids fallback")
    try:
        policy = NativeResponsePolicy.from_dict(selected.get("response_policy"))
    except NativeResponseBindingError:
        raise
    except (TypeError, ValueError, KeyError) as exc:
        raise NativeResponseBindingError("compiled native response policy is invalid") from exc
    modes = native_response_consumer_modes(policy.consumer_id)
    if effective_mode not in modes:
        raise NativeResponseBindingError(
            "native response consumer is not admitted for the profile request mode"
        )
    target = manifest.semantic.metadata.get("e4_target")
    if policy.consumer_id == MINI_RESPONSE_CONSUMER_ID:
        if (
            not isinstance(target, Mapping)
            or target.get("version") != 2
            or target.get("target_id") != "mini-swe-agent@2.4.6"
            or target.get("renderer_id") != MINI_RESPONSE_CONSUMER_ID
            or not isinstance(target.get("runtime_profile"), Mapping)
            or effective_mode != "non_streaming"
            or profile.request_policy.max_token_field != "max_tokens"
            or profile.request_policy.strict_tools is not None
            or profile.request_policy.enable_thinking is not None
            or profile.max_output_tokens != 2048
            or profile.sampling.as_dict() != {"temperature": 0.0, "n": 1}
        ):
            raise NativeResponseBindingError("Mini native response requires its compiled source profile")
    elif policy.consumer_id == PI_RESPONSE_CONSUMER_ID:
        if (
            not isinstance(target, Mapping)
            or target.get("version") != 3
            or target.get("target_id") != "pi@0.73.1"
            or target.get("renderer_id") != PI_RESPONSE_CONSUMER_ID
            or not isinstance(target.get("runtime_profile"), Mapping)
            or effective_mode != "streaming"
            or profile.request_policy.max_token_field != "max_tokens"
            or profile.request_policy.strict_tools is not None
            or profile.max_output_tokens != 2048
            or profile.request_policy.enable_thinking is not None
            or not profile.request_policy.include_usage
            or profile.sampling.as_dict() != {"n": 1}
            or not profile.capabilities.supports_store
        ):
            raise NativeResponseBindingError("Pi native response requires its compiled source profile")
    elif policy.consumer_id == OMP_RESPONSE_CONSUMER_ID:
        if (
            not isinstance(target, Mapping)
            or target.get("version") != 3
            or target.get("target_id") != "oh-my-pi@18.1.17"
            or target.get("renderer_id") != OMP_RESPONSE_CONSUMER_ID
            or not isinstance(target.get("runtime_profile"), Mapping)
            or target.get("rendered_prompt_digest") is not None
            or effective_mode != "streaming"
            or profile.request_policy.max_token_field != "max_completion_tokens"
            or profile.request_policy.strict_tools is not None
            or profile.max_output_tokens != 2048
            or profile.request_policy.enable_thinking is not None
            or not profile.request_policy.include_usage
            or profile.sampling.as_dict() != {"n": 1}
            or not profile.capabilities.supports_store
        ):
            raise NativeResponseBindingError("Oh My Pi native response requires its compiled source profile")
    elif policy.consumer_id in NATIVE_CHAT_RESPONSE_TARGETS:
        # Sampling identity and source SDK wire-field presence remain distinct.
        if (
            not isinstance(target, Mapping)
            or target.get("version") != 3
            or target.get("target_id") != NATIVE_CHAT_RESPONSE_TARGETS[policy.consumer_id]
            or target.get("renderer_id") != policy.consumer_id
            or not isinstance(target.get("runtime_profile"), Mapping)
            or target.get("rendered_prompt_digest") is not None
            or profile.request_policy.mode != "non_streaming"
            or profile.request_policy.strict_tools is not None
            or profile.request_policy.enable_thinking is not None
            or profile.request_policy.conversation_key_field != (
                None
                if policy.consumer_id == HERMES_RESPONSE_CONSUMER_ID
                else "prompt_cache_key"
            )
            or profile.max_output_tokens != 2048
            or profile.sampling.as_dict() != (
                {"n": 1}
                if policy.consumer_id == HERMES_RESPONSE_CONSUMER_ID
                else {"temperature": 0.0, "n": 1}
            )
            or (
                policy.consumer_id == HERMES_RESPONSE_CONSUMER_ID
                and profile.request_policy.max_token_field != "max_tokens"
            )
        ):
            raise NativeResponseBindingError(
                "native Chat response requires its compiled source profile"
            )
    if profile_identity_digest(profile) != policy.provider_profile_digest:
        raise NativeResponseBindingError("profile identity does not match policy")

    return CompiledNativeResponseBinding._from_admitted(
        policy=policy,
        episode_id=episode_id,
        effective_plan_digest=effective_plan_digest,
        capability_observation_digest=capability_observation_digest,
        authority_model_id=authority_model_id,
        compiled_manifest_digest=bytes_sha256(compiled_manifest),
        compiled_model_digest=canonical_sha256(selected),
    )

__all__ = [
    "CompiledNativeResponseBinding",
    "MAX_NATIVE_RESPONSE_BYTES",
    "MAX_NATIVE_STREAM_FRAGMENTS",
    "MINI_RESPONSE_CONSUMER_ID",
    "NATIVE_RESPONSE_BINDING_SCHEMA_VERSION",
    "NATIVE_RESPONSE_CONSUMER_ID",
    "OMP_RESPONSE_CONSUMER_ID",
    "OPENHANDS_RESPONSE_CONSUMER_ID",
    "HERMES_RESPONSE_CONSUMER_ID",
    "NATIVE_CHAT_RESPONSE_TARGETS",
    "OPENCLAW_RESPONSE_CONSUMER_ID",
    "NATIVE_RESPONSE_POLICY_SCHEMA_VERSION",
    "NativeResponseBindingError",
    "NativeResponsePolicy",
    "admit_native_response_binding",
    "is_native_response_consumer_registered",
    "native_response_consumer_modes",
    "profile_identity_digest",
]
