import argparse
import json
import shutil
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict
import yaml

sys.path.insert(0, str(Path(__file__).parent.resolve()))


import ray
from dotenv import load_dotenv

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from kylecode.sandbox_virtualized import SandboxFactory, DeploymentMode


DEFAULT_CONFIG_PATH = Path(__file__).with_name("test_agent.yaml")


def _load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def main():
    parser = argparse.ArgumentParser(description="Run agentic coding test via YAML config")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to YAML config (default: test_agent.yaml)")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"Config not found: {cfg_path}", file=sys.stderr)
        sys.exit(1)
    cfg = _load_config(cfg_path)

    # Resolve all paths relative to project root, not config directory
    project_root = Path(__file__).parent
    
    # Workspace path
    workspace = Path(cfg.get("workspace", "./agent_ws"))
    if not workspace.is_absolute():
        workspace = (project_root / workspace).resolve()

    # Output paths should also be relative to project root
    out_json = cfg.get("output", {}).get("json", "./session_out.json")
    out_md = cfg.get("output", {}).get("markdown")
    out_json_path = (project_root / out_json).resolve() if not Path(out_json).is_absolute() else Path(out_json)
    out_md_path = (project_root / out_md).resolve() if out_md and not Path(out_md).is_absolute() else (Path(out_md) if out_md else None)

    model_id = cfg.get("model", {}).get("id", os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    prompts_cfg = cfg.get("prompts", {}) or {}
    tool_prompt_mode = prompts_cfg.get("tool_prompt_mode") or cfg.get("prompt", {}).get("mode", "system_once")
    completion_sentinel = cfg.get("prompt", {}).get("completion_sentinel", ">>>>>> END RESPONSE")

    system_path = cfg.get("prompt", {}).get("system")
    system_text = _read_text_file((project_root / system_path).resolve()) if system_path else _read_text_file(Path(__file__).with_name("SYSTEM_PROMPT.md"))

    task_file = cfg.get("task", {}).get("file")
    user_text = _read_text_file((project_root / task_file).resolve()) if task_file else ""

    # Options
    stream_responses = bool(cfg.get("limits", {}).get("stream_responses", False))
    clean_ws = bool(cfg.get("limits", {}).get("clean_workspace", True))
    max_steps = int(cfg.get("limits", {}).get("max_steps", 20))

    # Deployment configuration (NEW)
    deployment_config = cfg.get("deployment", {})
    isolation_enabled = deployment_config.get("isolation", True)  # Default to True
    deployment_mode = deployment_config.get("mode", "development")  # Default to development
    
    print(f"🔒 Workspace Isolation: {'Enabled' if isolation_enabled else 'Disabled'}")
    print(f"🚀 Deployment Mode: {deployment_mode}")

    # Load .env
    load_dotenv()
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
        print("Missing API key. Set OPENAI_API_KEY or OPENROUTER_API_KEY in .env or environment.", file=sys.stderr)
        sys.exit(1)

    # Handle workspace setup based on isolation mode
    if isolation_enabled:
        # NEW: Use virtualized sandbox with isolation
        print(f"📁 Creating isolated workspace (mirrored to: {workspace})")
        
        # Clean mirror directory if requested
        ws = Path(str(workspace))
        if clean_ws and ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True, exist_ok=True)
        
        # Use factory to create isolated sandbox
        factory = SandboxFactory()
        mode_map = {
            "development": DeploymentMode.DEVELOPMENT,
            "testing": DeploymentMode.TESTING,
            "production": DeploymentMode.PRODUCTION
        }
        mode = mode_map.get(deployment_mode, DeploymentMode.DEVELOPMENT)
        
        ray.init(ignore_reinit_error=True)
        try:
            virtualized_sandbox, session_id = factory.create_sandbox(mode, cfg)
            print(f"✓ Created isolated session: {session_id}")
            
            # Create agent with virtualized sandbox
            # Note: For now, just create regular agent - sandbox integration happens in agent_llm_openai.py
            agent_config = dict(cfg)
            # Inject provider-native config overrides and defs_dir resolution
            provider_tools_cfg = agent_config.get("provider_tools", {}) or {}
            agent_config["provider_tools"] = {
                "use_native": bool(provider_tools_cfg.get("use_native", False)),
                "suppress_prompts": bool(provider_tools_cfg.get("suppress_prompts", False)),
            }
            defs_dir = (agent_config.get("tools", {}) or {}).get("defs_dir")
            if defs_dir:
                resolved_defs = str((project_root / defs_dir).resolve()) if not os.path.isabs(str(defs_dir)) else str(defs_dir)
                agent_config.setdefault("tools", {})["defs_dir"] = resolved_defs
            agent = OpenAIConductor.options(name=f"oa-{uuid.uuid4()}").remote(
                workspace=str(ws), 
                image=cfg.get("runtime", {}).get("image", "python-dev:latest"), 
                config=agent_config
            )
            print("✓ Created agent with virtualized sandbox support")
            
        except Exception as e:
            print(f"⚠️  Virtualization failed, falling back to regular sandbox: {e}")
            isolation_enabled = False
    
    if not isolation_enabled:
        # ORIGINAL: Use regular workspace (backward compatibility)
        print(f"📁 Using regular workspace: {workspace}")
        ws = Path(str(workspace))
        if clean_ws and ws.exists():
            shutil.rmtree(ws)
        ws.mkdir(parents=True, exist_ok=True)

        ray.init(ignore_reinit_error=True)
        agent_config = dict(cfg)
        provider_tools_cfg = agent_config.get("provider_tools", {}) or {}
        agent_config["provider_tools"] = {
            "use_native": bool(provider_tools_cfg.get("use_native", False)),
            "suppress_prompts": bool(provider_tools_cfg.get("suppress_prompts", False)),
        }
        defs_dir = (agent_config.get("tools", {}) or {}).get("defs_dir")
        if defs_dir:
            resolved_defs = str((project_root / defs_dir).resolve()) if not os.path.isabs(str(defs_dir)) else str(defs_dir)
            agent_config.setdefault("tools", {})["defs_dir"] = resolved_defs
        agent = OpenAIConductor.options(name=f"oa-{uuid.uuid4()}").remote(
            workspace=str(ws), 
            image=cfg.get("runtime", {}).get("image", "python-dev:latest"), 
            config=agent_config
        )
    
    # Common execution path for both isolated and regular modes
    try:
        # Initialize git repo for diffs
        _ = ray.get(agent.vcs.remote({"action": "init", "user": {"name": "tester", "email": "t@example.com"}}))
        
        print(f"🤖 Starting agentic loop with {model_id}")
        print(f"📝 Max steps: {max_steps}")
        if isolation_enabled:
            print(f"🔒 Using isolated workspace with path virtualization")
            
        result = ray.get(agent.run_agentic_loop.remote(
            system_text,
            user_text,
            model=model_id,
            max_steps=max_steps,
            output_json_path=str(out_json_path),
            stream_responses=stream_responses,
            output_md_path=(str(out_md_path) if out_md_path else None),
            tool_prompt_mode=str(tool_prompt_mode),
            completion_sentinel=str(completion_sentinel),
        ))
        # Capture a diff snapshot if present
        diff = ray.get(agent.vcs.remote({"action": "diff", "params": {"staged": False, "unified": 3}}))
        session = {
            "workspace": str(ws),
            "model": model_id,
            "messages": result.get("messages", []),
            "transcript": result.get("transcript", []),
            "diff": diff,
        }
        if "error" in result:
            session["error"] = result.get("error")
            session["error_type"] = result.get("error_type")
            session["hint"] = result.get("hint")
            print(f"Agent error: {result.get('error_type')}: {result.get('error')}", file=sys.stderr)
        out_json_path.write_text(json.dumps(session, indent=2))
        print(f"Wrote session to {out_json_path}")
    finally:
        ray.shutdown()


if __name__ == "__main__":
    main()

