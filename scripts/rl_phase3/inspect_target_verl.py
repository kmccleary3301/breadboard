from __future__ import annotations

import importlib
import inspect
import json
import os
import traceback
from pathlib import Path
from typing import Any


_SYMBOL_IMPORTS = {
    "verl.__version__": ("verl", ("__version__",)),
    "verl.protocol.DataProto": ("verl.protocol", ("DataProto",)),
    "verl.protocol.DataProto.from_dict": ("verl.protocol", ("DataProto", "from_dict")),
    "verl.protocol.DataProto.from_single_dict": ("verl.protocol", ("DataProto", "from_single_dict")),
    "verl.protocol.DataProto.to": ("verl.protocol", ("DataProto", "to")),
    "verl.trainer.main_ppo_sync": ("verl.trainer.main_ppo_sync", ()),
    "verl.trainer.main_ppo": ("verl.trainer.main_ppo", ()),
    "verl.trainer.ppo.ray_trainer.compute_advantage": ("verl.trainer.ppo.ray_trainer", ("compute_advantage",)),
}


def _symbol(name: str) -> dict[str, Any]:
    if name in _SYMBOL_IMPORTS:
        module_name, attr_parts = _SYMBOL_IMPORTS[name]
        try:
            obj = importlib.import_module(module_name)
            for part in attr_parts:
                obj = getattr(obj, part)
            file_name = inspect.getfile(obj) if not isinstance(obj, str) else inspect.getfile(importlib.import_module(module_name))
            signature = str(inspect.signature(obj)) if callable(obj) else ""
            return {"present": True, "file": file_name, "signature": signature}
        except Exception as exc:  # noqa: BLE001
            return {"present": False, "error": exc.__class__.__name__, "message": str(exc), "module": module_name}
    parts = name.split(".")
    last_error = ""
    for split_at in range(len(parts), 0, -1):
        module_name = ".".join(parts[:split_at])
        attr_parts = parts[split_at:]
        try:
            obj = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001
            last_error = exc.__class__.__name__
            continue
        try:
            for part in attr_parts:
                obj = getattr(obj, part)
            return {"present": True, "file": inspect.getfile(obj), "signature": str(inspect.signature(obj)) if callable(obj) else ""}
        except Exception as exc:  # noqa: BLE001
            return {"present": False, "error": exc.__class__.__name__, "message": str(exc), "module": module_name}
    return {"present": False, "error": last_error or "ModuleNotFoundError"}


def main() -> int:
    evidence_root = Path(os.environ.get("BREADBOARD_EVIDENCE_ROOT", "../docs_tmp"))
    target_run_id = os.environ.get("PHASE3_TARGET_RUN_ID", "")
    output_dir = evidence_root / "ZYPHRA" / "RL_PHASE_3" / "runs" / "verl_api_introspection"
    output_dir.mkdir(parents=True, exist_ok=True)
    symbols = {}
    try:
        import verl  # type: ignore
        symbols["verl.__version__"] = getattr(verl, "__version__", "unknown")
    except Exception as exc:  # noqa: BLE001
        symbols["verl.__version__"] = {"present": False, "error": exc.__class__.__name__}
    for name in (
        "verl.protocol.DataProto",
        "verl.protocol.DataProto.from_dict",
        "verl.protocol.DataProto.from_single_dict",
        "verl.protocol.DataProto.to",
        "verl.trainer.main_ppo_sync",
        "verl.trainer.main_ppo",
        "verl.trainer.ppo.ray_trainer.compute_advantage",
    ):
        symbols[name] = _symbol(name)
    torch_report = {"present": False, "devices": []}
    try:
        import torch  # type: ignore
        torch_report = {
            "present": True,
            "version": torch.__version__,
            "cuda_available": bool(torch.cuda.is_available()),
            "device_count": int(torch.cuda.device_count()),
            "devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())],
        }
    except Exception as exc:  # noqa: BLE001
        torch_report = {"present": False, "error": exc.__class__.__name__, "traceback": traceback.format_exc()}
    report = {
        "schema_version": "bb.rl.phase3.verl_api_introspection.v1",
        "report_id": "phase3_verl_api_introspection",
        "claim_boundary": "phase3_target_verl_api_introspection_named_scope",
        "target_run_id": target_run_id,
        "symbols": symbols,
        "torch": torch_report,
        "scorecard_update_allowed": False,
        "passed": torch_report.get("device_count") == 8 and all("MI300X" in name for name in torch_report.get("devices", [])),
    }
    (output_dir / "verl_api_introspection.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print("PHASE3_INTROSPECTION_REPORT=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
