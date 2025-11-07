#!/usr/bin/env python3
"""
KyleCode - Simplified Agentic Coding System

A streamlined interface to the agentic coding prototype.
"""
import argparse
import json
import sys
from pathlib import Path

import os
from agentic_coder_prototype import create_agent


def main():
    parser = argparse.ArgumentParser(description="KyleCode Agentic Coding System")
    parser.add_argument('config', help='Path to agent configuration file')
    parser.add_argument('-w', '--workspace', help='Workspace directory (default: auto-generated)')
    parser.add_argument('--schema-v2', action='store_true', help='Enable Agent Schema v2 loader (sets AGENT_SCHEMA_V2_ENABLED=1)')
    parser.add_argument('-i', '--interactive', action='store_true', help='Start interactive session')
    parser.add_argument('-t', '--task', help='Run a specific task')
    parser.add_argument('-m', '--max-iterations', type=int, default=20, help='Maximum iterations')
    parser.add_argument('--overrides', help='JSON object of configuration overrides', default=None)
    
    args = parser.parse_args()
    
    # Check if config file exists
    config_path = Path(args.config)
    if not config_path.exists():
        # Try in agent_configs directory
        config_path = Path('agent_configs') / args.config
        if not config_path.exists():
            print(f"Error: Configuration file '{args.config}' not found")
            sys.exit(1)
    
    # Load .env (gitignored) if present to set API keys
    def _load_dotenv():
        try:
            # Prefer repo root (same dir as this file)
            root = Path(__file__).resolve().parent
            env_path = root / '.env'
            if not env_path.exists():
                # fallback to CWD
                env_path = Path('.').resolve() / '.env'
            if env_path.exists():
                for raw in env_path.read_text(encoding='utf-8', errors='ignore').splitlines():
                    line = raw.strip()
                    if not line or line.startswith('#'):
                        continue
                    if line.startswith('export '):
                        line = line[len('export '):].strip()
                    if '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    # Do not overwrite if already set in the environment
                    if k and v and k not in os.environ:
                        os.environ[k] = v
        except Exception:
            # Best-effort only; ignore failures
            pass

    _load_dotenv()

    # Feature flags
    if args.schema_v2:
        os.environ['AGENT_SCHEMA_V2_ENABLED'] = '1'

    # Create agent
    overrides = None
    if args.overrides:
        try:
            overrides = json.loads(args.overrides)
        except json.JSONDecodeError as exc:
            print(f"Error: failed to parse overrides JSON: {exc}")
            sys.exit(1)

    agent = create_agent(str(config_path), args.workspace, overrides=overrides)
    
    try:
        if args.interactive:
            agent.interactive_session()
        elif args.task:
            print(f"Running task: {args.task}")
            result = agent.run_task(args.task, args.max_iterations)
            if isinstance(result, dict):
                print(f"Task completed with status: {result.get('completion_reason', 'unknown')}")
            else:
                print("Task completed with status: unknown (non-dict result)")
                try:
                    print(f"Raw result: {result}")
                except Exception:
                    pass
            
            # Show workspace files
            files = agent.get_workspace_files()
            if files:
                print(f"\nWorkspace files created/modified:")
                for file in sorted(files):
                    print(f"  {file}")
        else:
            print("Please specify either --interactive or --task")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\nOperation interrupted by user")
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
