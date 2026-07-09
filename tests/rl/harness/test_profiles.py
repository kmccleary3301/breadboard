from __future__ import annotations

import pytest

from breadboard.rl.harness import (
    BreadBoardEpisodeService,
    EpisodeCreateRequest,
    HarnessProfileRegistry,
    HarnessTask,
)
from breadboard.rl.state.cas import InMemoryCAS


IMAGE_A = "sha256:" + "a" * 64
IMAGE_B = "sha256:" + "b" * 64
SNAPSHOT_A = "sha256:" + "1" * 64
SNAPSHOT_B = "sha256:" + "2" * 64


def _profile(**overrides: object) -> dict[str, object]:
    profile: dict[str, object] = {
        "sandbox_driver": "docker",
        "default_image_digest": IMAGE_A,
        "allowed_image_digests": [IMAGE_A],
        "default_verifier_ref": "tests",
        "verifier_commands": {"tests": "python -m project.verify"},
    }
    profile.update(overrides)
    return profile


class LeaseManagerProbe:
    def __init__(self) -> None:
        self.open_count = 0

    async def open(self, **_: object) -> None:
        self.open_count += 1
        raise AssertionError(
            "lease allocation must not run for a rejected repository binding"
        )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"default_image_digest": "project:latest"}, "image must be digest-qualified"),
        (
            {"allowed_image_digests": [IMAGE_A, "project:latest"]},
            "image must be digest-qualified",
        ),
        ({"default_verifier_ref": "missing"}, "default_verifier_ref is not configured"),
    ],
)
def test_profile_configuration_rejects_mutable_images_and_unconfigured_verifiers(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        HarnessProfileRegistry.from_mapping({"swe": _profile(**overrides)})


def test_profile_rejects_task_image_and_verifier_outside_its_admission_lists() -> None:
    profile = HarnessProfileRegistry.from_mapping({"swe": _profile()}).get("swe")

    with pytest.raises(ValueError, match="image digest is not admitted"):
        profile.admit_image(IMAGE_B)
    with pytest.raises(ValueError, match="verifier_ref 'lint' is not admitted"):
        profile.verifier_command("lint")


def test_repository_binding_admits_the_image_mapped_to_the_snapshot() -> None:
    profile = HarnessProfileRegistry.from_mapping(
        {
            "swe": _profile(
                require_repository_binding=True,
                repository_images={SNAPSHOT_A: IMAGE_B},
            )
        }
    ).get("swe")

    assert profile.admit_image(IMAGE_B, SNAPSHOT_A) == IMAGE_B


@pytest.mark.parametrize(
    ("repository_images", "message"),
    [
        (
            {"sha256:" + "1" * 63: IMAGE_A},
            "repository snapshot keys must be sha256 digests",
        ),
        ({SNAPSHOT_A: "project:latest"}, "repository image must be digest-qualified"),
    ],
)
def test_repository_binding_rejects_malformed_mapping_at_profile_load(
    repository_images: dict[str, str],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        HarnessProfileRegistry.from_mapping(
            {
                "swe": _profile(
                    require_repository_binding=True,
                    repository_images=repository_images,
                )
            }
        )


@pytest.mark.parametrize(
    ("snapshot", "image", "message"),
    [
        (None, IMAGE_A, "requires a repository snapshot sha256 digest"),
        (SNAPSHOT_B, IMAGE_A, "repository snapshot is not admitted"),
        (SNAPSHOT_A, IMAGE_B, "sandbox image does not match"),
    ],
)
async def test_service_rejects_invalid_repository_bindings_before_allocating_a_lease(
    snapshot: str | None,
    image: str,
    message: str,
) -> None:
    profiles = HarnessProfileRegistry.from_mapping(
        {
            "swe": _profile(
                require_repository_binding=True,
                repository_images={SNAPSHOT_A: IMAGE_A},
            )
        }
    )
    lease_manager = LeaseManagerProbe()
    service = BreadBoardEpisodeService(
        profiles=profiles, lease_manager=lease_manager, artifact_store=InMemoryCAS()
    )
    request = EpisodeCreateRequest(
        episode_id="episode-1",
        profile="swe",
        task=HarnessTask(
            task_id="task-1",
            sandbox_image_digest=image,
            repository_snapshot_digest=snapshot,
            verifier_ref="tests",
        ),
    )

    with pytest.raises(ValueError, match=message):
        await service.create_episode(request)
    assert lease_manager.open_count == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("trusted_process", "false"),
        ("trusted_process", 1),
        ("require_repository_binding", "false"),
        ("require_repository_binding", 0),
    ],
)
def test_profile_security_switches_require_actual_boolean_values(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match=f"{field} must be a boolean"):
        HarnessProfileRegistry.from_mapping({"terminal": _profile(**{field: value})})


@pytest.mark.parametrize("trusted_process", [None, False])
def test_process_driver_requires_explicit_trust_opt_in(
    trusted_process: bool | None,
) -> None:
    overrides: dict[str, object] = {"sandbox_driver": "process"}
    if trusted_process is not None:
        overrides["trusted_process"] = trusted_process

    with pytest.raises(ValueError, match="must explicitly set trusted_process"):
        HarnessProfileRegistry.from_mapping({"terminal": _profile(**overrides)})


def test_process_profile_with_explicit_trust_can_admit_its_image_and_verifier() -> None:
    profile = HarnessProfileRegistry.from_mapping(
        {"terminal": _profile(sandbox_driver="process", trusted_process=True)}
    ).get("terminal")

    assert profile.admit_image(IMAGE_A) == IMAGE_A
    assert profile.verifier_command("tests") == ("tests", "python -m project.verify")
