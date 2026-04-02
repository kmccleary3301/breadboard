#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from quickstart_first_time import _cli_capabilities


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit the current BreadBoard CLI capability snapshot.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()

    payload = _cli_capabilities()
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
