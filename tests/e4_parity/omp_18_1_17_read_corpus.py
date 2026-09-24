"""Check framed native read routing against independently observed pinned read.ts."""
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import sys

from breadboard.rl.harness.omp_native_tools import NativeToolWorker, PinnedNativeWorkerSpec
from tests.rl.harness.test_omp_18_1_17_native_worker import _lease_model


def main() -> None:
    source_report, corpus, workspace, config_file, start, stop, output = sys.argv[1:]
    source_bytes = Path(source_report).read_bytes()
    report = json.loads(source_bytes)
    inputs = json.loads(Path(corpus).read_text(encoding="utf-8"))
    if len(inputs) != len(report["rows"]) or sha256(Path(corpus).read_bytes()).hexdigest() != report["corpus_sha256"]:
        raise ValueError("observed pinned source and corpus differ")
    config = json.loads(Path(config_file).read_text(encoding="utf-8"))
    surface = json.loads((Path(config_file).parent / "tool-surface.json").read_text(encoding="utf-8"))
    provenance = surface["native_description_provenance"]
    description_policy = {
        name: {
            "original_sha256": "sha256:" + provenance["original_sha256"][name],
            "bounded_sha256": "sha256:" + provenance["bounded_sha256"][name],
            "removed_spans": provenance["removed_spans"][name],
        }
        for name in surface["ordered_tools"]
    }
    base = Path(workspace)
    scratch = base / ".omp-read-corpus"
    home = scratch / "home"
    package_dir = scratch / "package"
    home.mkdir(parents=True, exist_ok=True)
    package_dir.mkdir(parents=True, exist_ok=True)
    source_root = os.environ.get("BB_OMP_TEST_SOURCE_ROOT")
    bun = os.environ.get("BB_OMP_TEST_BUN")
    if (source_root is None) != (bun is None):
        raise ValueError("test source and Bun override must both be supplied")
    spec = PinnedNativeWorkerSpec.for_test(bun=Path(bun), source_root=Path(source_root)) if source_root and bun else None
    worker = NativeToolWorker(cwd=str(base), spec=spec)
    counters = Counter()
    disagreements = []
    over_admissions = []
    named = {}
    try:
        worker.phase("initialize", {
            "task": "classify pinned source-observed read corpus", "model_config": _lease_model(),
            "advertisement": {
                "bounded_description_policy": description_policy,
                "model_registry": config["model_registry"],
                "capability_denials": config["capability_denials"],
            },
            "workspace": str(base), "scratch": str(scratch), "package_dir": str(package_dir),
            "runtime_inputs": {"cwd": str(base), "home": str(home), "current_date": "2026-09-24", "package_dir": str(package_dir)},
        })
        for offset in range(int(start), int(stop), 72):
            end = min(offset + 72, int(stop))
            prepared = worker.phase("prepare_tools", {
                "calls": [{"id": str(index), "name": "read", "arguments": {"path": inputs[index]}} for index in range(offset, end)],
            }, timeout_seconds=90)["calls"]
            for index, actual in zip(range(offset, end), prepared, strict=True):
                source_route = report["rows"][index]["route"]
                actual_route = actual.get("route", {}).get("route", "unclassified")
                if actual_route.startswith("internal:"):
                    actual_route = "internal"
                denied = bool(actual.get("error"))
                counters[("source", source_route)] += 1
                counters[("worker", actual_route)] += 1
                counters[("admitted", str(not denied))] += 1
                outcome = {"index": index, "input": inputs[index], "source": source_route, "worker": actual_route, "denied": denied, "error": actual.get("error")}
                if source_route != actual_route:
                    disagreements.append(outcome)
                if source_route != "file" and not denied:
                    over_admissions.append(outcome)
                if inputs[index] in ("bundle.zip:state.sqlite", "file://evil/xyz.sqlite:users", "file://evil/data.zip:member"):
                    named[inputs[index]] = outcome
    finally:
        worker.stop()
    result = {
        "cases": int(stop) - int(start), "start": int(start), "stop": int(stop),
        "source_sha256": sha256(source_bytes).hexdigest(),
        "counts": {f"{kind}:{route}": count for (kind, route), count in sorted(counters.items())},
        "mismatch_count": len(disagreements), "over_admission_count": len(over_admissions),
        "mismatch_examples": disagreements[:8], "over_admission_examples": over_admissions[:8], "named": named,
    }
    Path(output).write_text(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    if disagreements or over_admissions:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
