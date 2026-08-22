from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from breadboard.rl.harness.runners.base import (
    RunnerAdapterDescriptor,
    RunnerAdapterRegistry,
    RunnerRegistrationError,
    RunnerResolutionError,
)
from breadboard.rl.harness.runners.conductor import (
    CONDUCTOR_ADAPTER_ID,
    CONDUCTOR_RUNTIME_ABI,
    ConductorAdapter,
)


@dataclass(frozen=True)
class DescriptorOnlyAdapter:
    descriptor: Any

    async def open(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("registry resolution must not open an adapter")


def _adapter(
    adapter_id: str,
    runtime_abi: str,
    digest_character: str,
) -> DescriptorOnlyAdapter:
    return DescriptorOnlyAdapter(
        RunnerAdapterDescriptor(
            adapter_id=adapter_id,
            runtime_abi=runtime_abi,
            implementation_digest="sha256:" + digest_character * 64,
        )
    )


def test_runner_registry_resolves_only_exact_adapter_id_and_runtime_abi() -> None:
    terminal_v1 = _adapter("breadboard.terminal-responses.v1", "abi.v1", "a")
    terminal_v2 = _adapter("breadboard.terminal-responses.v1", "abi.v2", "b")
    conductor = ConductorAdapter(CONDUCTOR_RUNTIME_ABI)
    registry = RunnerAdapterRegistry((terminal_v1, terminal_v2, conductor))

    assert registry.resolve("breadboard.terminal-responses.v1", "abi.v1") is terminal_v1
    assert registry.resolve("breadboard.terminal-responses.v1", "abi.v2") is terminal_v2
    assert registry.resolve(CONDUCTOR_ADAPTER_ID, CONDUCTOR_RUNTIME_ABI) is conductor


@pytest.mark.parametrize(
    ("adapter_id", "runtime_abi", "code", "message"),
    [
        (
            "breadboard.terminal-responses",
            "abi.v1",
            "adapter_not_found",
            "runner adapter 'breadboard.terminal-responses' is not registered",
        ),
        (
            "terminal",
            "abi.v1",
            "adapter_not_found",
            "runner adapter 'terminal' is not registered",
        ),
        (
            "breadboard.terminal-responses.v1",
            "abi",
            "runtime_abi_not_supported",
            "runner adapter 'breadboard.terminal-responses.v1' does not support runtime ABI 'abi'",
        ),
        (
            "",
            "",
            "adapter_not_found",
            "runner adapter '' is not registered",
        ),
    ],
)
def test_runner_registry_has_no_default_prefix_or_family_fallback(
    adapter_id: str,
    runtime_abi: str,
    code: str,
    message: str,
) -> None:
    registry = RunnerAdapterRegistry(
        (_adapter("breadboard.terminal-responses.v1", "abi.v1", "a"),)
    )

    with pytest.raises(RunnerResolutionError) as captured:
        registry.resolve(adapter_id, runtime_abi)

    assert captured.value.category == "registry"
    assert captured.value.code == code
    assert str(captured.value) == message


def test_runner_registry_rejects_duplicate_tuple_without_replacing_first() -> None:
    first = _adapter("breadboard.terminal-responses.v1", "abi.v1", "a")
    second = _adapter("breadboard.terminal-responses.v1", "abi.v1", "b")

    with pytest.raises(RunnerRegistrationError) as captured:
        RunnerAdapterRegistry((first, second))

    assert captured.value.category == "registry"
    assert captured.value.code == "duplicate_adapter"
    assert str(captured.value) == (
        "duplicate runner adapter registration for "
        "'breadboard.terminal-responses.v1' and 'abi.v1'"
    )


@pytest.mark.parametrize(
    "descriptor",
    [
        None,
        "breadboard.terminal-responses.v1",
        object(),
    ],
)
def test_runner_registry_rejects_malformed_descriptors_with_typed_error(
    descriptor: Any,
) -> None:
    with pytest.raises(RunnerRegistrationError) as captured:
        RunnerAdapterRegistry((DescriptorOnlyAdapter(descriptor),))

    assert captured.value.category == "registry"
    assert captured.value.code == "malformed_descriptor"


class RunnerAdapterDescriptorSubclass(RunnerAdapterDescriptor):
    pass


def test_runner_registry_rejects_descriptor_subclass_before_registration() -> None:
    descriptor = RunnerAdapterDescriptorSubclass(
        adapter_id=CONDUCTOR_ADAPTER_ID,
        runtime_abi=CONDUCTOR_RUNTIME_ABI,
        implementation_digest="sha256:" + "d" * 64,
    )

    with pytest.raises(RunnerRegistrationError) as captured:
        RunnerAdapterRegistry((DescriptorOnlyAdapter(descriptor),))

    assert captured.value.category == "registry"
    assert captured.value.code == "malformed_descriptor"


class MutableDescriptorAdapter:
    def __init__(self, descriptor: RunnerAdapterDescriptor) -> None:
        self.descriptor = descriptor

    async def open(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("drifted adapter must never open")


def test_runner_registry_rejects_descriptor_drift_under_old_and_new_keys() -> None:
    original = RunnerAdapterDescriptor(
        adapter_id="breadboard.terminal-responses.v1",
        runtime_abi="abi.v1",
        implementation_digest="sha256:" + "a" * 64,
    )
    drifted = RunnerAdapterDescriptor(
        adapter_id="breadboard.changed.v1",
        runtime_abi="abi.v2",
        implementation_digest="sha256:" + "b" * 64,
    )
    adapter = MutableDescriptorAdapter(original)
    registry = RunnerAdapterRegistry((adapter,))
    adapter.descriptor = drifted

    with pytest.raises(RunnerResolutionError) as old_key:
        registry.resolve(original.adapter_id, original.runtime_abi)
    assert old_key.value.code == "descriptor_drift"
    assert str(old_key.value) == "runner adapter descriptor changed after registration"

    with pytest.raises(RunnerResolutionError) as new_key:
        registry.resolve(drifted.adapter_id, drifted.runtime_abi)
    assert new_key.value.code == "adapter_not_found"
    assert str(new_key.value) == (
        "runner adapter 'breadboard.changed.v1' is not registered"
    )
