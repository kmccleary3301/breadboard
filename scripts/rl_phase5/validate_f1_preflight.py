from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.f1_preflight import canonical_json_bytes, promote, validate_scratch, verify_canonical
from scripts.rl_phase5.build_f1_preflight_bundle import build_bundle
from scripts.rl_phase5.run_f1_target_command import derive_secret_material


def _secrets(
    seed_file: Path | None, evidence_root: Path
) -> tuple[bytes, ...]:
    if seed_file is None:
        return ()
    seed = seed_file.resolve(strict=True)
    evidence = evidence_root.resolve(strict=True)
    if seed == evidence or evidence in seed.parents:
        raise ValueError("secret seed must remain outside evidence")
    return derive_secret_material(seed.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser(description="Build, run, validate, and promote F1 preflight evidence")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--breadboard-root", type=Path, required=True)
    build.add_argument("--wrapper-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--inventory", type=Path, required=True)


    validate = sub.add_parser("validate")
    validate.add_argument("attempt", type=Path)
    validate.add_argument("--seed-file", type=Path)

    promotion = sub.add_parser("promote")
    promotion.add_argument("attempt", type=Path)
    promotion.add_argument("canonical_root", type=Path)
    promotion.add_argument("--seed-file", type=Path)

    verify = sub.add_parser("verify-canonical")
    verify.add_argument("canonical", type=Path)
    verify.add_argument("--seed-file", type=Path)

    args = parser.parse_args()
    if args.command == "build":
        result = build_bundle(args.breadboard_root.resolve(), args.wrapper_root.resolve(), args.output.resolve())
        args.inventory.write_bytes(canonical_json_bytes(result) + b"\n")
    elif args.command == "validate":
        result = validate_scratch(args.attempt, secret_material=_secrets(args.seed_file, args.attempt))
    elif args.command == "promote":
        result = {"canonical_path": str(promote(args.attempt, args.canonical_root, secret_material=_secrets(args.seed_file, args.attempt)))}
    else:
        result = verify_canonical(args.canonical, secret_material=_secrets(args.seed_file, args.canonical))
    print(canonical_json_bytes(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
