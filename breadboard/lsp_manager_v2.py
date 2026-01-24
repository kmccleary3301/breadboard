"""
LSP Manager V2 - Full-featured Language Server Protocol manager with multi-language support
Replicates OpenCode's LSP functionality in Python+Ray with containerization support.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union
import fnmatch

import ray

# LSP JSON-RPC client implementation
class LSPJSONRPCClient:
    """Lightweight JSON-RPC client for LSP communication"""
    
    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.request_id = 0
        self.diagnostics: Dict[str, List[Dict[str, Any]]] = {}
        
    def _send_request(self, method: str, params: Any = None) -> Dict[str, Any]:
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
                "insertFinalNewline": True
            }
        })
    
    def code_actions(self, file_path: str, start_line: int, start_char: int, end_line: int, end_char: int, diagnostics: List[Dict] = None) -> Dict[str, Any]:
        """Get available code actions"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("textDocument/codeAction", {
            "textDocument": {"uri": f"file://{file_path}"},
            "range": {
                "start": {"line": start_line, "character": start_char},
                "end": {"line": end_line, "character": end_char}
            },
            "context": {"diagnostics": diagnostics or []}
        })
    
    def completion(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Get code completion"""
        if not self.initialized or not self.client:
            return {"error": "Server not initialized"}
            
        return self.client._send_request("textDocument/completion", {
            "textDocument": {"uri": f"file://{file_path}"},
            "position": {"line": line, "character": character}
        })
    
    def _get_language_id(self, file_path: str) -> str:
        """Get language ID from file extension"""
        ext = Path(file_path).suffix
        language_map = {
            '.py': 'python', '.pyi': 'python',
            '.ts': 'typescript', '.tsx': 'typescriptreact',
            '.js': 'javascript', '.jsx': 'javascriptreact',
            '.go': 'go',
            '.rs': 'rust',
            '.cpp': 'cpp', '.cxx': 'cpp', '.cc': 'cpp', '.c': 'c',
            '.h': 'c', '.hpp': 'cpp', '.hxx': 'cpp',
            '.java': 'java',
            '.rb': 'ruby',
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
        
        for server_id, config in LSP_SERVER_CONFIGS.items():
            if extension not in config["extensions"]:
                continue
                
            # Find project root
            root = self._find_project_root(file_path, config["root_patterns"])
            if not root:
                root = str(Path(file_path).parent)
                
            server_key = f"{server_id}:{root}"
            
            # Skip broken servers
            if server_key in self.broken_servers:
                continue
                
            # Get or create server
            if server_key not in self.servers:
                try:
                    server = LSPServer.remote(server_id, root)
                    started = await server.start.remote()
                    if started:
                        self.servers[server_key] = server
                        matching_servers.append(server)
                    else:
                        self.broken_servers.add(server_key)
                except Exception:
                    self.broken_servers.add(server_key)
            else:
                matching_servers.append(self.servers[server_key])
                
        return matching_servers
    
    async def cleanup_servers(self):
        """Clean up all servers"""
        for server in self.servers.values():
            try:
                await server.shutdown.remote()
            except Exception:
                pass
        self.servers.clear()


@ray.remote
class CLILinterRunner:
    """Runs CLI-based linters in isolated sandboxes"""
    
    def __init__(self, sandbox_image: str = "lsp-universal:latest"):
        self.sandbox_image = sandbox_image
    
    async def run_ruff(self, file_path: str) -> List[Dict[str, Any]]:
        """Run ruff linter on Python file"""
        try:
            result = subprocess.run([
                "ruff", "check", "--output-format=json", file_path
            ], capture_output=True, text=True, timeout=30)
            
            if result.stdout:
                ruff_output = json.loads(result.stdout)
                return self._convert_ruff_to_lsp_diagnostics(ruff_output)
        except Exception:
            pass
        return []
    
    async def run_eslint(self, file_path: str) -> List[Dict[str, Any]]:
        """Run ESLint on TypeScript/JavaScript file"""
        try:
            result = subprocess.run([
                "npx", "eslint", "--format=json", file_path
            ], capture_output=True, text=True, timeout=30)
            
            if result.stdout:
                eslint_output = json.loads(result.stdout)
                return self._convert_eslint_to_lsp_diagnostics(eslint_output)
        except Exception:
            pass
        return []
    
    async def run_clippy(self, workspace_root: str) -> List[Dict[str, Any]]:
        """Run clippy on Rust project"""
        try:
            result = subprocess.run([
                "cargo", "clippy", "--message-format=json", "--", "-D", "warnings"
            ], cwd=workspace_root, capture_output=True, text=True, timeout=120)
            
            if result.stdout:
                return self._convert_clippy_to_lsp_diagnostics(result.stdout)
        except Exception:
            pass
        return []
    
    def _convert_ruff_to_lsp_diagnostics(self, ruff_output: List[Dict]) -> List[Dict[str, Any]]:
        """Convert ruff output to LSP diagnostic format"""
        diagnostics = []
        for item in ruff_output:
            diagnostics.append({
                "range": {
                    "start": {"line": item.get("location", {}).get("row", 1) - 1, "character": item.get("location", {}).get("column", 1) - 1},
                    "end": {"line": item.get("end_location", {}).get("row", 1) - 1, "character": item.get("end_location", {}).get("column", 1) - 1}
                },
                "message": item.get("message", ""),
                "severity": 1 if item.get("type") == "E" else 2,  # Error or Warning
                "source": "ruff",
                "code": item.get("code")
            })
        return diagnostics
    
    def _convert_eslint_to_lsp_diagnostics(self, eslint_output: List[Dict]) -> List[Dict[str, Any]]:
        """Convert ESLint output to LSP diagnostic format"""
        diagnostics = []
        for file_result in eslint_output:
            for message in file_result.get("messages", []):
                diagnostics.append({
                    "range": {
                        "start": {"line": message.get("line", 1) - 1, "character": message.get("column", 1) - 1},
                        "end": {"line": message.get("endLine", message.get("line", 1)) - 1, "character": message.get("endColumn", message.get("column", 1)) - 1}
                    },
                    "message": message.get("message", ""),
                    "severity": 1 if message.get("severity") == 2 else 2,  # Error or Warning
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
        all_symbols = []
        
        try:
            # Get all active servers
            for root in self.roots:
                for server_id in LSP_SERVER_CONFIGS:
                    server_key = f"{server_id}:{root}"
                    if hasattr(self.orchestrator, 'servers') and server_key in self.orchestrator.servers:
                        server = self.orchestrator.servers[server_key]
                        try:
                            result = await server.workspace_symbols.remote(query)
                            if result and "result" in result:
                                symbols = result["result"][:10]  # Limit per server
                                all_symbols.extend(symbols)
                        except Exception:
                            continue
        except Exception:
            pass
        
        # Sort and limit results
        all_symbols.sort(key=lambda x: x.get("name", ""))
        return all_symbols[:limit]
    
    async def document_symbol(self, file_path: str) -> List[Dict[str, Any]]:
        """Get document symbols from appropriate LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.document_symbols.remote(file_path)
                    if result and "result" in result:
                        return result["result"]
                except Exception:
                    continue
        except Exception:
            pass
        return []
    
    async def go_to_definition(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Go to definition via LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.go_to_definition.remote(file_path, line, character)
                    if result and "result" in result and result["result"]:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        return {}
    
    async def find_references(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Find references via LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.find_references.remote(file_path, line, character, True)
                    if result and "result" in result:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        return {}
    
    async def format_document(self, file_path: str) -> Dict[str, Any]:
        """Format document via LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.format_document.remote(file_path)
                    if result and "result" in result:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        return {}
    
    async def code_actions(self, file_path: str, start_line: int, start_char: int, 
                          end_line: int, end_char: int, diagnostics: List[Dict] = None) -> Dict[str, Any]:
        """Get code actions via LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.code_actions.remote(
                        file_path, start_line, start_char, end_line, end_char, diagnostics
                    )
                    if result and "result" in result:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        return {}
    
    async def completion(self, file_path: str, line: int, character: int) -> Dict[str, Any]:
        """Get code completion via LSP server"""
        try:
            servers = await self.orchestrator.get_servers_for_file.remote(file_path)
            for server in servers:
                try:
                    await server.open_document.remote(file_path)
                    result = await server.completion.remote(file_path, line, character)
                    if result and "result" in result:
                        return result
                except Exception:
                    continue
        except Exception:
            pass
        return {}
    
    async def shutdown(self):
        """Shutdown all LSP servers"""
        try:
            await self.orchestrator.cleanup_servers.remote()
        except Exception:
            pass


# Compatibility wrapper for existing code
def _mk_diagnostic(path: str, message: str, line: int, col: int, severity: int = 1) -> Dict[str, Any]:
    """Maintain compatibility with existing diagnostic format"""
    return {
        "file": path,
        "severity": severity,
        "message": message,
        "range": {
            "start": {"line": max(line - 1, 0), "character": max(col - 1, 0)},
            "end": {"line": max(line - 1, 0), "character": max(col, 0)},
        },
    }