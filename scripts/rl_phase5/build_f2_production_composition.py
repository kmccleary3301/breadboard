from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from breadboard.rl.phase5.f2_composition import (
    F2ProductionCompositionInput,
    TlsCallbackRuntimeInputV1,
    build_f2_production_composition,
    canonical_json_bytes,
    required_target_discovery_inputs,
)


def _author(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{Path(__file__).name} author",
        description="Author fixed-C4 production authority from narrow semantic inputs.",
    )
    parser.add_argument("--semantic-input")
    parser.add_argument("--output")
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args(argv)
    from breadboard.rl.phase5.f2_authority_authoring import (
        F2C4SemanticInput,
        author_f2_operator_input,
    )
    if args.print_schema:
        sys.stdout.buffer.write(canonical_json_bytes(F2C4SemanticInput.model_json_schema()) + b"\n")
        return 0
    if args.semantic_input is None or args.output is None:
        parser.error("--semantic-input and --output are required")

    operator_input_path = author_f2_operator_input(
        semantic_input_path=args.semantic_input, output_dir=args.output
    )
    sys.stdout.buffer.write(
        canonical_json_bytes({"operator_input_path": str(operator_input_path)}) + b"\n"
    )
    return 0

def _author_static(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{Path(__file__).name} author-static",
        description="Publish one reviewed immutable F2 C4 static-authority fragment.",
    )
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args(argv)
    from breadboard.rl.phase5.f2_authority_authoring import (
        F2C4StaticAuthorityInput,
        build_f2_c4_static_authority,
    )
    if args.print_schema:
        sys.stdout.buffer.write(
            canonical_json_bytes(F2C4StaticAuthorityInput.model_json_schema()) + b"\n"
        )
        return 0
    if args.input is None or args.output is None:
        parser.error("--input and --output are required")
    input_path = Path(args.input)
    if not input_path.is_absolute():
        raise ValueError("--input must be absolute")
    payload = input_path.read_bytes()
    value = json.loads(payload)
    if canonical_json_bytes(value) != payload:
        raise ValueError("static authority input must be canonical JSON")
    parsed = F2C4StaticAuthorityInput.model_validate(value, strict=True)
    static_path = build_f2_c4_static_authority(parsed, args.output)
    sys.stdout.buffer.write(
        canonical_json_bytes({"static_authority_path": static_path}) + b"\n"
    )
    return 0



def _service_fd(value: str) -> tuple[str, int]:
    if value.count("=") != 1:
        raise argparse.ArgumentTypeError("service fd must be ROLE=FD")
    role, raw_fd = value.split("=", 1)
    try:
        fd = int(raw_fd)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("service fd must be ROLE=FD") from exc
    if not role or fd < 0:
        raise argparse.ArgumentTypeError("service fd must be ROLE=FD")
    return role, fd


def _secret_file(value: str) -> tuple[str, str]:
    if value.count("=") != 1:
        raise argparse.ArgumentTypeError("live secret file must be HANDLE=/absolute/path")
    handle, path = value.split("=", 1)
    if not handle or not path.startswith("/"):
        raise argparse.ArgumentTypeError("live secret file must be HANDLE=/absolute/path")
    return handle, path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and production-inspect an exact F2 BreadBoard composition packet."
    )
    parser.add_argument("--input", help="Absolute path to canonical F2 input JSON")
    parser.add_argument("--output", help="New absolute output directory")
    parser.add_argument(
        "--prebound-service-fd",
        action="append",
        type=_service_fd,
        default=[],
        metavar="ROLE=FD",
        help="Inherited IP_FREEBIND socket descriptor; repeat once per service role",
    )
    parser.add_argument("--tls-callback-runtime", help="Canonical dynamic TLS callback runtime JSON")
    parser.add_argument("--callback-tls-private-key-fd", type=int)
    parser.add_argument(
        "--live-secret-file",
        action="append",
        type=_secret_file,
        default=[],
        metavar="HANDLE=/absolute/path",
    )
    parser.add_argument("--print-schema", action="store_true", help="Print the exact operator-input JSON Schema")
    parser.add_argument("--print-required-inputs", action="store_true", help="Print remaining external target inputs")
    parser.add_argument("--print-tls-callback-schema", action="store_true", help="Print dynamic TLS callback runtime JSON Schema")
    return parser


def main(argv: list[str] | None = None) -> int:
    resolved_argv = list(sys.argv[1:] if argv is None else argv)
    if resolved_argv and resolved_argv[0] == "author":
        return _author(resolved_argv[1:])
    if resolved_argv and resolved_argv[0] == "author-static":
        return _author_static(resolved_argv[1:])
    args = _parser().parse_args(resolved_argv)
    if args.print_schema:
        sys.stdout.buffer.write(canonical_json_bytes(F2ProductionCompositionInput.model_json_schema()) + b"\n")
        return 0
    if args.print_required_inputs:
        sys.stdout.buffer.write(
            canonical_json_bytes(list(required_target_discovery_inputs())) + b"\n"
        )
        return 0
    if args.print_tls_callback_schema:
        sys.stdout.buffer.write(
            canonical_json_bytes(TlsCallbackRuntimeInputV1.model_json_schema()) + b"\n"
        )
        return 0
    if args.input is None or args.output is None:
        _parser().error("--input and --output are required for a build")
    input_path = Path(args.input)
    if not input_path.is_absolute():
        raise ValueError("--input must be absolute")
    payload = input_path.read_bytes()
    value = json.loads(payload)
    if canonical_json_bytes(value) != payload:
        raise ValueError("input manifest must be canonical JSON")
    spec = F2ProductionCompositionInput.model_validate(value, strict=True)
    socket_fds = dict(args.prebound_service_fd)
    if len(socket_fds) != len(args.prebound_service_fd):
        raise ValueError("duplicate prebound service socket role")
    if (
        args.tls_callback_runtime is None
        or args.callback_tls_private_key_fd is None
    ):
        raise ValueError("callback TLS runtime and private-key descriptor are required")
    if "callback_tls" not in socket_fds:
        raise ValueError("callback_tls prebound service descriptor is required")
    callback_path = Path(args.tls_callback_runtime)
    callback_bytes = callback_path.read_bytes()
    callback_value = json.loads(callback_bytes)
    if canonical_json_bytes(callback_value) != callback_bytes:
        raise ValueError("callback TLS runtime must be canonical JSON")
    callback_runtime = TlsCallbackRuntimeInputV1.model_validate(
        callback_value, strict=True
    )
    live_secret_files = dict(args.live_secret_file)
    if len(live_secret_files) != len(args.live_secret_file):
        raise ValueError("duplicate live secret handle")
    result = build_f2_production_composition(
        spec,
        args.output,
        prebound_service_socket_fds=socket_fds,
        callback_tls_runtime=callback_runtime,
        callback_tls_private_key_fd=args.callback_tls_private_key_fd,
        live_secret_files=live_secret_files,
    )
    sys.stdout.buffer.write(canonical_json_bytes(result.model_dump(mode="json")) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
