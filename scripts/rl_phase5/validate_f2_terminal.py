from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.f2_terminal import canonical_json_bytes, promote, validate_scratch, verify_canonical


def _secret_material(secret_file: Path | None, evidence: Path) -> tuple[bytes, ...]:
    if secret_file is None:
        raise ValueError("--secret-file is required for production F2 secret scanning")
    secret = secret_file.resolve(strict=True)
    root = evidence.resolve(strict=True)
    if secret == root or root in secret.parents:
        raise ValueError("secret file must remain outside evidence")
    if (secret.stat().st_mode & 0o777) != 0o400:
        raise PermissionError("secret file mode must be 0400")
    raw = secret.read_bytes()
    if not raw:
        raise ValueError("secret file is empty")
    return (raw,)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate, promote, or verify F2 terminal evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate"); validate.add_argument("attempt", type=Path)
    validate.add_argument("--secret-file", type=Path, required=True)
    promotion = sub.add_parser("promote"); promotion.add_argument("attempt", type=Path); promotion.add_argument("canonical_root", type=Path)
    promotion.add_argument("--secret-file", type=Path, required=True)
    verify = sub.add_parser("verify"); verify.add_argument("canonical", type=Path)
    verify.add_argument("--secret-file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate":
        result = validate_scratch(args.attempt, secret_material=_secret_material(args.secret_file, args.attempt))
    elif args.command == "promote":
        result = {"canonical_path": str(promote(args.attempt, args.canonical_root, secret_material=_secret_material(args.secret_file, args.attempt)))}
    else:
        result = verify_canonical(args.canonical, secret_material=_secret_material(args.secret_file, args.canonical))
    print(canonical_json_bytes(result).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
