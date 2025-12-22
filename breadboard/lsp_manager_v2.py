"""
LSP Manager V2 - Full-featured Language Server Protocol manager with multi-language support
Replicates OpenCode's LSP functionality in Python+Ray with containerization support.
"""
from __future__ import annotations

import asyncio
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
import fnmatch
from urllib.parse import unquote

import ray

# LSP JSON-RPC client implementation
class LSPJSONRPCClient:
    """Lightweight JSON-RPC client for LSP communication"""
    
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.request_id = 0
        """Send JSON-RPC request and get response"""
        self.request_id += 1
        request = {
            "jsonrpc": "2.0",
            "id": self.request_id,
            "method": method,
            "params": params or {}
        }
        
        content = json.dumps(request)
        message = f"Content-Length: {len(content)}\r\n\r\n{content}"
        
        try:
            self.process.stdin.write(message.encode())
            self.process.stdin.flush()
            
            # Read response (simplified - production would handle streaming)
            return self._read_response()
        except Exception as e:
            return {"error": str(e)}
    
    def _send_notification(self, method: str, params: Any = None):
        """Send JSON-RPC notification (no response expected)"""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {}
        }
        
        content = json.dumps(notification)
        message = f"Content-Length: {len(content)}\r\n\r\n{content}"
        
        try:
            self.process.stdin.write(message.encode())
            self.process.stdin.flush()
        except Exception:
            pass
    
    def _read_response(self) -> Dict[str, Any]:
        """Read JSON-RPC response (simplified implementation)"""
        try:
            # This is a simplified implementation - production would need proper streaming parser
            line = self.process.stdout.readline().decode().strip()
            if line.startswith("Content-Length:"):
                length = int(line.split(":")[1].strip())
                self.process.stdout.readline()  # Skip empty line
                content = self.process.stdout.read(length).decode()
                return json.loads(content)
        except Exception:
            pass
        return {}
    
    def shutdown(self):
        """Shutdown LSP server"""
        try:
            self._send_request("shutdown")
            self._send_notification("exit")
            self.process.terminate()
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()


# Server configurations (OpenCode pattern)
LSP_SERVER_CONFIGS = {
    "typescript": {
        "id": "typescript",
        "extensions": [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"],
        "root_patterns": ["tsconfig.json", "package.json", "jsconfig.json"],
        "command": ["npx", "typescript-language-server", "--stdio"],
        "requires": ["npm", "typescript"],
        "initialization": {}
    },
    "python": {
        "id": "python", 
        "extensions": [".py", ".pyi"],
        "root_patterns": ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile"],
        "command": ["pyright-langserver", "--stdio"],
        "requires": ["pyright"],
        "initialization": {}
    },
    "go": {
        "id": "go",
        "extensions": [".go"],
        "root_patterns": ["go.mod", "go.sum", "go.work"],
        "command": ["gopls"],
        "requires": ["go"],
        "initialization": {}
    },
    "rust": {
        "id": "rust",
        "extensions": [".rs"],
        "root_patterns": ["Cargo.toml", "Cargo.lock"],
        "command": ["rust-analyzer"],
        "requires": ["rustc"],
        "initialization": {
            "cargo": {"buildScripts": {"enable": True}},
            "checkOnSave": {"command": "clippy"}
        }
    },
    "cpp": {
        "id": "cpp",
        "extensions": [".cpp", ".cxx", ".cc", ".c", ".h", ".hpp", ".hxx"],
        "root_patterns": ["compile_commands.json", "CMakeLists.txt", ".clangd"],
        "command": ["clangd", "--background-index"],
        "requires": ["clangd"],
        "initialization": {}
    },
    "java": {
        "id": "java",
        "extensions": [".java"],
        "root_patterns": ["pom.xml", "build.gradle", ".project"],
        "command": ["jdtls"],
        "requires": ["java"],
        "initialization": {}
    },
    "ruby": {
        "id": "ruby",
        "extensions": [".rb", ".rake", ".gemspec", ".ru"],
        "root_patterns": ["Gemfile", "Rakefile"],
        "command": ["ruby-lsp", "--stdio"],
        "requires": ["ruby"],
        "initialization": {}
    },
    "csharp": {
        "id": "csharp",
        "extensions": [".cs"],
        "root_patterns": [".sln", ".csproj", "global.json"],
        "command": ["csharp-ls"],
        "requires": ["dotnet"],
        "initialization": {}
    }
}

@ray.remote
class LSPServer:
    """Individual LSP server instance running in isolated container"""
    
    def __init__(self, server_id: str, workspace_root: str, container_image: str = "lsp-universal:latest"):
        self.server_id = server_id
        self.workspace_root = workspace_root
        self.container_image = container_image
        self.config = LSP_SERVER_CONFIGS.get(server_id, {})
        self.client: Optional[LSPJSONRPCClient] = None
        self.process: Optional[subprocess.Popen] = None
        self.initialized = False
        self.session_id = str(uuid.uuid4())
        
    async def start(self) -> bool:
        """Start LSP server in containerized environment"""
        try:
            # Check if required tools are available
            if not self._check_requirements():
                return False
            
            # Start server process (can be containerized)
            self.process = await self._spawn_server()
            if not self.process:
                return False
                
            self.client = LSPJSONRPCClient(self.process)
            
            # Initialize LSP server
            init_result = self.client._send_request("initialize", {
                "rootUri": f"file://{self.workspace_root}",
                "processId": os.getpid(),
                "capabilities": {
                    "textDocument": {
                        "synchronization": {"didOpen": True, "didChange": True, "didClose": True},
                        "publishDiagnostics": {"versionSupport": True},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "definition": {"linkSupport": True},
                        "references": {"context": True},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                        "completion": {"completionItem": {"snippetSupport": True}},
                        "codeAction": {"codeActionLiteralSupport": True},
                        "formatting": {},
                        "rangeFormatting": {}
                    },
                    "workspace": {
                        "symbol": {},
                        "configuration": True,
                        "didChangeConfiguration": {"dynamicRegistration": True}
                    }
                },
                "initializationOptions": self.config.get("initialization", {})
            })
            
            if "error" not in init_result:
                self.client._send_notification("initialized")
                self.initialized = True
                return True
                
        except Exception as e:
            print(f"Failed to start LSP server {self.server_id}: {e}")
            
        return False
    
    def _check_requirements(self) -> bool:
        """Check if required tools are installed"""
        for req in self.config.get("requires", []):
            if not shutil.which(req):
                return False
        return True
    
    async def _spawn_server(self) -> Optional[subprocess.Popen]:
        """Spawn LSP server process (containerized if specified)"""
        command = self.config.get("command", [])
        if not command:
            return None
            
        try:
            # Option 1: Direct process spawn
            if os.environ.get("LSP_USE_CONTAINERS", "0") == "0":
                return subprocess.Popen(
                    command,
                    cwd=self.workspace_root,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=False
                )
            
            # Option 2: Container spawn (Docker/Podman)
            container_cmd = [
                "docker", "run", "-i", "--rm",
                "--network=none",  # Isolated networking
                f"--workdir=/workspace",
                f"--volume={self.workspace_root}:/workspace:ro",
                f"--name=lsp-{self.server_id}-{self.session_id}",
                self.container_image
            ] + command
            
            return subprocess.Popen(
                container_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE,
                text=False
            )
            
        except Exception as e:
            print(f"Failed to spawn server {self.server_id}: {e}")
            return None
    
    def open_document(self, file_path: str) -> Dict[str, Any]:
        """Open document in LSP server"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
                
            self.client._send_notification("textDocument/didOpen", {
                "textDocument": {
                    "uri": f"file://{file_path}",
                    "languageId": self._get_language_id(file_path),
                    "version": 1,
                    "text": content
                }
            })
            return {"status": "opened"}
            
        except Exception as e:
            return {"error": str(e)}
    
    def get_diagnostics(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get current diagnostics"""
        if self.client:
            return self.client.diagnostics.copy()
        return {}
    
    def hover(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Get hover information"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("textDocument/hover", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character}
        })
    
    def go_to_definition(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Go to definition"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("textDocument/definition", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character}
        })
    
    def find_references(self, file_path: str, line: int, character: int, include_declaration: bool = True) -> Dict[str, Any]:
        """Find references"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("textDocument/references", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration}
        })
    
    def workspace_symbols(self, query: str) -> Dict[str, Any]:
        """Search workspace symbols"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("workspace/symbol", {"query": query})
    
    def document_symbols(self, file_path: str) -> Dict[str, Any]:
        """Get document symbols"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": f"file://{file_path}"}
        })
    
    def format_document(self, file_path: str) -> Dict[str, Any]:
        """Format entire document"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("textDocument/formatting", {
            "textDocument": {"uri": f"file://{file_path}"},
            "options": {
                "tabSize": 4,
                "insertSpaces": True,
                "trimTrailingWhitespace": True,
# [RECOVERY MISSING LINE 361]
# [RECOVERY MISSING LINE 362]
# [RECOVERY MISSING LINE 363]
# [RECOVERY MISSING LINE 364]
# [RECOVERY MISSING LINE 365]
# [RECOVERY MISSING LINE 366]
# [RECOVERY MISSING LINE 367]
# [RECOVERY MISSING LINE 368]
# [RECOVERY MISSING LINE 369]
# [RECOVERY MISSING LINE 370]
# [RECOVERY MISSING LINE 371]
# [RECOVERY MISSING LINE 372]
# [RECOVERY MISSING LINE 373]
# [RECOVERY MISSING LINE 374]
# [RECOVERY MISSING LINE 375]
# [RECOVERY MISSING LINE 376]
# [RECOVERY MISSING LINE 377]
# [RECOVERY MISSING LINE 378]
# [RECOVERY MISSING LINE 379]
# [RECOVERY MISSING LINE 380]
# [RECOVERY MISSING LINE 381]
# [RECOVERY MISSING LINE 382]
# [RECOVERY MISSING LINE 383]
# [RECOVERY MISSING LINE 384]
# [RECOVERY MISSING LINE 385]
# [RECOVERY MISSING LINE 386]
# [RECOVERY MISSING LINE 387]
# [RECOVERY MISSING LINE 388]
# [RECOVERY MISSING LINE 389]
# [RECOVERY MISSING LINE 390]
# [RECOVERY MISSING LINE 391]
# [RECOVERY MISSING LINE 392]
# [RECOVERY MISSING LINE 393]
# [RECOVERY MISSING LINE 394]
# [RECOVERY MISSING LINE 395]
# [RECOVERY MISSING LINE 396]
# [RECOVERY MISSING LINE 397]
# [RECOVERY MISSING LINE 398]
# [RECOVERY MISSING LINE 399]
# [RECOVERY MISSING LINE 400]
# [RECOVERY MISSING LINE 401]
# [RECOVERY MISSING LINE 402]
# [RECOVERY MISSING LINE 403]
# [RECOVERY MISSING LINE 404]
# [RECOVERY MISSING LINE 405]
# [RECOVERY MISSING LINE 406]
# [RECOVERY MISSING LINE 407]
# [RECOVERY MISSING LINE 408]
# [RECOVERY MISSING LINE 409]
# [RECOVERY MISSING LINE 410]
# [RECOVERY MISSING LINE 411]
# [RECOVERY MISSING LINE 412]
# [RECOVERY MISSING LINE 413]
# [RECOVERY MISSING LINE 414]
# [RECOVERY MISSING LINE 415]
# [RECOVERY MISSING LINE 416]
# [RECOVERY MISSING LINE 417]
# [RECOVERY MISSING LINE 418]
# [RECOVERY MISSING LINE 419]
# [RECOVERY MISSING LINE 420]
# [RECOVERY MISSING LINE 421]
# [RECOVERY MISSING LINE 422]
# [RECOVERY MISSING LINE 423]
# [RECOVERY MISSING LINE 424]
# [RECOVERY MISSING LINE 425]
# [RECOVERY MISSING LINE 426]
# [RECOVERY MISSING LINE 427]
# [RECOVERY MISSING LINE 428]
# [RECOVERY MISSING LINE 429]
# [RECOVERY MISSING LINE 430]
# [RECOVERY MISSING LINE 431]
# [RECOVERY MISSING LINE 432]
# [RECOVERY MISSING LINE 433]
# [RECOVERY MISSING LINE 434]
# [RECOVERY MISSING LINE 435]
# [RECOVERY MISSING LINE 436]
# [RECOVERY MISSING LINE 437]
# [RECOVERY MISSING LINE 438]
# [RECOVERY MISSING LINE 439]
# [RECOVERY MISSING LINE 440]
# [RECOVERY MISSING LINE 441]
# [RECOVERY MISSING LINE 442]
# [RECOVERY MISSING LINE 443]
# [RECOVERY MISSING LINE 444]
# [RECOVERY MISSING LINE 445]
# [RECOVERY MISSING LINE 446]
# [RECOVERY MISSING LINE 447]
# [RECOVERY MISSING LINE 448]
# [RECOVERY MISSING LINE 449]
# [RECOVERY MISSING LINE 450]
# [RECOVERY MISSING LINE 451]
# [RECOVERY MISSING LINE 452]
# [RECOVERY MISSING LINE 453]
# [RECOVERY MISSING LINE 454]
# [RECOVERY MISSING LINE 455]
# [RECOVERY MISSING LINE 456]
# [RECOVERY MISSING LINE 457]
# [RECOVERY MISSING LINE 458]
# [RECOVERY MISSING LINE 459]
# [RECOVERY MISSING LINE 460]
# [RECOVERY MISSING LINE 461]
# [RECOVERY MISSING LINE 462]
# [RECOVERY MISSING LINE 463]
# [RECOVERY MISSING LINE 464]
# [RECOVERY MISSING LINE 465]
# [RECOVERY MISSING LINE 466]
# [RECOVERY MISSING LINE 467]
# [RECOVERY MISSING LINE 468]
# [RECOVERY MISSING LINE 469]
# [RECOVERY MISSING LINE 470]
# [RECOVERY MISSING LINE 471]
# [RECOVERY MISSING LINE 472]
# [RECOVERY MISSING LINE 473]
# [RECOVERY MISSING LINE 474]
# [RECOVERY MISSING LINE 475]
# [RECOVERY MISSING LINE 476]
# [RECOVERY MISSING LINE 477]
# [RECOVERY MISSING LINE 478]
# [RECOVERY MISSING LINE 479]
# [RECOVERY MISSING LINE 480]
# [RECOVERY MISSING LINE 481]
# [RECOVERY MISSING LINE 482]
# [RECOVERY MISSING LINE 483]
# [RECOVERY MISSING LINE 484]
# [RECOVERY MISSING LINE 485]
# [RECOVERY MISSING LINE 486]
# [RECOVERY MISSING LINE 487]
# [RECOVERY MISSING LINE 488]
# [RECOVERY MISSING LINE 489]
# [RECOVERY MISSING LINE 490]
# [RECOVERY MISSING LINE 491]
# [RECOVERY MISSING LINE 492]
# [RECOVERY MISSING LINE 493]
# [RECOVERY MISSING LINE 494]
# [RECOVERY MISSING LINE 495]
# [RECOVERY MISSING LINE 496]
# [RECOVERY MISSING LINE 497]
# [RECOVERY MISSING LINE 498]
# [RECOVERY MISSING LINE 499]
# [RECOVERY MISSING LINE 500]
# [RECOVERY MISSING LINE 501]
# [RECOVERY MISSING LINE 502]
# [RECOVERY MISSING LINE 503]
# [RECOVERY MISSING LINE 504]
# [RECOVERY MISSING LINE 505]
# [RECOVERY MISSING LINE 506]
# [RECOVERY MISSING LINE 507]
# [RECOVERY MISSING LINE 508]
# [RECOVERY MISSING LINE 509]
# [RECOVERY MISSING LINE 510]
# [RECOVERY MISSING LINE 511]
# [RECOVERY MISSING LINE 512]
# [RECOVERY MISSING LINE 513]
# [RECOVERY MISSING LINE 514]
# [RECOVERY MISSING LINE 515]
# [RECOVERY MISSING LINE 516]
# [RECOVERY MISSING LINE 517]
# [RECOVERY MISSING LINE 518]
# [RECOVERY MISSING LINE 519]
            '.cs': 'csharp'
        }
        return language_map.get(ext, 'plaintext')
    
    def get_session_id(self) -> str:
        """Get the session ID of this LSP server"""
        return self.session_id
    
    def shutdown(self):
        """Shutdown LSP server"""
        if self.client:
            self.client.shutdown()
        self.initialized = False


@ray.remote
class LSPOrchestrator:
    """Orchestrates multiple LSP servers and handles client lifecycle"""
    
    def __init__(self):
        self.servers: Dict[str, ray.ObjectRef] = {}  # (server_id, root) -> server
        self.broken_servers: Set[str] = set()
        
    def _find_project_root(self, file_path: str, patterns: List[str]) -> Optional[str]:
        """Find project root by searching for patterns upward"""
        current = Path(file_path).parent
        
        while current != current.parent:
            for pattern in patterns:
                if (current / pattern).exists():
                    return str(current)
            current = current.parent
            
        return None
    
    async def get_servers_for_file(self, file_path: str) -> List[ray.ObjectRef]:
        """Get appropriate LSP servers for a file (OpenCode pattern)"""
        extension = Path(file_path).suffix
        matching_servers = []
        
                    "source": "eslint",
                    "code": message.get("ruleId")
                })
        return diagnostics
    
    def _convert_clippy_to_lsp_diagnostics(self, clippy_output: str) -> List[Dict[str, Any]]:
        """Convert clippy output to LSP diagnostic format"""
        diagnostics = []
        for line in clippy_output.strip().split('\n'):
            if not line:
                continue
            try:
                message = json.loads(line)
                if message.get("reason") == "compiler-message":
                    msg = message.get("message", {})
                    if msg.get("spans"):
                        span = msg["spans"][0]
                        diagnostics.append({
                            "range": {
                                "start": {"line": span.get("line_start", 1) - 1, "character": span.get("column_start", 1) - 1},
                                "end": {"line": span.get("line_end", 1) - 1, "character": span.get("column_end", 1) - 1}
                            },
                            "message": msg.get("message", ""),
                            "severity": 1 if msg.get("level") == "error" else 2,
                            "source": "clippy",
                            "code": msg.get("code", {}).get("code")
                        })
            except Exception:
                continue
        return diagnostics


@ray.remote
class UnifiedDiagnostics:
    """Aggregates diagnostics from LSP servers and CLI linters"""
    
    def __init__(self):
        self.orchestrator = LSPOrchestrator.remote()
        self.linter_runner = CLILinterRunner.remote()
    
    async def collect_all_diagnostics(self, file_path: str) -> Dict[str, List[Dict[str, Any]]]:
        """Collect diagnostics from all sources"""
        all_diagnostics = {}
        
        try:
            # Get LSP servers for this file
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            
            # Collect LSP diagnostics
            lsp_futures = []
            for server in servers:
                # Open document first
                await server.open_document.remote(file_path)
                lsp_futures.append(server.get_diagnostics.remote())
            
            # Collect CLI linter diagnostics
            cli_futures = []
            extension = Path(file_path).suffix
            
            if extension == ".py":
                cli_futures.append(self.linter_runner.run_ruff.remote(file_path))
            elif extension in [".ts", ".tsx", ".js", ".jsx"]:
                cli_futures.append(self.linter_runner.run_eslint.remote(file_path))
            elif extension == ".rs":
                workspace_root = Path(file_path).parent
                while workspace_root != workspace_root.parent:
                    if (workspace_root / "Cargo.toml").exists():
                        break
                    workspace_root = workspace_root.parent
                cli_futures.append(self.linter_runner.run_clippy.remote(str(workspace_root)))
            
            # Wait for all diagnostics
            if lsp_futures:
                lsp_results = ray.get(lsp_futures)
                for result in lsp_results:
                    for path, diagnostics in result.items():
                        if path not in all_diagnostics:
                            all_diagnostics[path] = []
                        all_diagnostics[path].extend(diagnostics)
            
            if cli_futures:
                cli_results = ray.get(cli_futures)
                for diagnostics in cli_results:
                    if file_path not in all_diagnostics:
                        all_diagnostics[file_path] = []
                    all_diagnostics[file_path].extend(diagnostics)
            
            # Deduplicate diagnostics
            for path in all_diagnostics:
                all_diagnostics[path] = self._deduplicate_diagnostics(all_diagnostics[path])
                
        except Exception as e:
            print(f"Error collecting diagnostics: {e}")
            
        return all_diagnostics
    
    def _deduplicate_diagnostics(self, diagnostics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate diagnostics"""
        seen = set()
        unique_diagnostics = []
        
        for diag in diagnostics:
            # Create key for deduplication
            key = (
                diag.get("range", {}).get("start", {}).get("line"),
                diag.get("range", {}).get("start", {}).get("character"),
                diag.get("message", ""),
                diag.get("code")
            )
            
            if key not in seen:
                seen.add(key)
                unique_diagnostics.append(diag)
                
        # Sort by severity (errors first), then by line number
        unique_diagnostics.sort(key=lambda x: (
            x.get("severity", 99),
            x.get("range", {}).get("start", {}).get("line", 0)
        ))
        
        return unique_diagnostics


@ray.remote
class LSPManagerV2:
    """
    Enhanced LSP Manager with full multi-language support.
    Drop-in replacement for the original lsp_manager.py with OpenCode feature parity.
    """
    
    def __init__(self):
        self.roots: Set[str] = set()
        self.touched_files: Set[str] = set()
        self.orchestrator = LSPOrchestrator.remote()
        self.unified_diagnostics = UnifiedDiagnostics.remote()
        
    def register_root(self, root: str) -> None:
        """Register a workspace root"""
        self.roots.add(os.path.abspath(root))
    
    def touch_file(self, path: str, wait: bool = True) -> None:
        """Mark file as touched for diagnostics refresh"""
        self.touched_files.add(os.path.abspath(path))
    
    async def diagnostics(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get comprehensive diagnostics from all LSP servers and CLI linters.
        Returns mapping of file_path -> list of diagnostics.
        """
        all_diagnostics = {}
        
        # Process touched files
        files_to_process = list(self.touched_files) if self.touched_files else []
        
        # If no touched files, scan workspace roots
        if not files_to_process:
            for root in self.roots:
                for ext in ['.py', '.ts', '.tsx', '.js', '.jsx', '.go', '.rs', '.cpp', '.c', '.java', '.rb', '.cs']:
                    for file_path in Path(root).rglob(f'*{ext}'):
                        files_to_process.append(str(file_path))
                        if len(files_to_process) > 100:  # Limit for performance
                            break
                    if len(files_to_process) > 100:
                        break
        
        # Collect diagnostics for all files
        diagnostic_futures = []
        for file_path in files_to_process[:50]:  # Process max 50 files at once
            if os.path.isfile(file_path):
                diagnostic_futures.append(
                    self.unified_diagnostics.collect_all_diagnostics.remote(file_path)
                )
        
        if diagnostic_futures:
            results = ray.get(diagnostic_futures)
            for result in results:
                all_diagnostics.update(result)
        
        # Clear touched files after processing
        self.touched_files.clear()
        
        return all_diagnostics
    
    async def hover(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Get hover information from appropriate LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.hover.remote(file_path, line, character)
                    if result and "error" not in result:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        return {}
    
    async def workspace_symbol(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search workspace symbols across all language servers"""
                    if file_path not in all_diagnostics:
                        all_diagnostics[file_path] = []
                    all_diagnostics[file_path].extend(diagnostics)
            
            # Deduplicate diagnostics
            for path in all_diagnostics:
                all_diagnostics[path] = self._deduplicate_diagnostics(all_diagnostics[path])
                
        except Exception as e:
            print(f"Error collecting diagnostics: {e}")
            
        return all_diagnostics
    
    def _deduplicate_diagnostics(self, diagnostics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate diagnostics"""
        seen = set()
        unique_diagnostics = []
        
        for diag in diagnostics:
            # Create key for deduplication
            key = (
                diag.get("range", {}).get("start", {}).get("line"),
                diag.get("range", {}).get("start", {}).get("character"),
                diag.get("message", ""),
                diag.get("code")
            )
            
            if key not in seen:
                seen.add(key)
                unique_diagnostics.append(diag)

        return unique_diagnostics


@ray.remote
class LSPManagerV2:
    """
    Enhanced LSP Manager with full multi-language support.
    Drop-in replacement for the original lsp_manager.py with OpenCode feature parity.
    """
    
    def __init__(self):
        self.roots: Set[str] = set()
        self.touched_files: Set[str] = set()
        self.orchestrator = LSPOrchestrator.remote()
        self.unified_diagnostics = UnifiedDiagnostics.remote()
        
    def register_root(self, root: str) -> None:
        """Register a workspace root"""
        self.roots.add(os.path.abspath(root))
    
    def touch_file(self, path: str, wait: bool = True) -> None:
        """Mark file as touched for diagnostics refresh"""
        self.touched_files.add(os.path.abspath(path))
    
    async def diagnostics(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get comprehensive diagnostics from all LSP servers and CLI linters.
        Returns mapping of file_path -> list of diagnostics.
        """
        all_diagnostics = {}
        
        # Process touched files
        files_to_process = list(self.touched_files) if self.touched_files else []
        
        # If no touched files, scan workspace roots
        if not files_to_process:
            for root in self.roots:
                for ext in ['.py', '.ts', '.tsx', '.js', '.jsx', '.go', '.rs', '.cpp', '.c', '.java', '.rb', '.cs']:
                    for file_path in Path(root).rglob(f'*{ext}'):
                        files_to_process.append(str(file_path))
                        if len(files_to_process) > 100:  # Limit for performance
                            break
                    if len(files_to_process) > 100:
                        break
        
        # Collect diagnostics for all files
        diagnostic_futures = []
        for file_path in files_to_process[:50]:  # Process max 50 files at once
            if os.path.isfile(file_path):
                diagnostic_futures.append(
                    self.unified_diagnostics.collect_all_diagnostics.remote(file_path)
                )
        
        if diagnostic_futures:
            results = ray.get(diagnostic_futures)
            for result in results:
                all_diagnostics.update(result)
        
        # Clear touched files after processing
        self.touched_files.clear()
        
        return all_diagnostics
    
    async def hover(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Get hover information from appropriate LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.hover.remote(file_path, line, character)
                    if result and "error" not in result:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        return {}
    
    async def workspace_symbol(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search workspace symbols across all language servers"""
        all_symbols = []
        
        try:
            # Get all active servers
            for root in self.roots:
                for server_id in LSP_SERVER_CONFIGS:
                    server_key = f"{server_id}:{root}"
                    if hasattr(self.orchestrator, 'servers') and server_key in self.orchestrator.servers:
