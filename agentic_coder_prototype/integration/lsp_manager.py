"""
LSP Manager for Enhanced Tool Calling System
Based on OpenCode patterns with Ray integration for agentic workflows.
"""

import asyncio
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
import ray

logger = logging.getLogger(__name__)

@dataclass
class Diagnostic:
    """LSP diagnostic message"""
    message: str
    severity: str  # Error, Warning, Info, Hint
    line: int
    character: int
    source: str
    
    def format_brief(self) -> str:
        """Format for AI model feedback"""
        severity_map = {"Error": "ERROR", "Warning": "WARN", "Info": "INFO", "Hint": "HINT"}
        sev = severity_map.get(self.severity, self.severity.upper())
        return f"{sev} [{self.line+1}:{self.character+1}] {self.message}"

@dataclass
class LanguageServer:
    """Language server configuration"""
    name: str
    extensions: List[str]
    command: List[str]
    env: Dict[str, str]
    project_markers: List[str]
    
    def matches_file(self, file_path: str) -> bool:
        """Check if this server handles the given file"""
        return any(file_path.endswith(ext) for ext in self.extensions)

class LSPManager:
    """
    Multi-language LSP manager with automatic server spawning
    Inspired by OpenCode's architecture but tailored for agentic workflows
    """
    
    # Built-in language server configurations (similar to OpenCode)
    LANGUAGE_SERVERS = {
        "pyright": LanguageServer(
            name="pyright",
            extensions=[".py", ".pyi"],
            command=["pyright-langserver", "--stdio"],
            env={},
            project_markers=["pyproject.toml", "setup.py", "requirements.txt", "pyrightconfig.json"]
        ),
        "typescript": LanguageServer(
            name="typescript",
            extensions=[".ts", ".tsx", ".js", ".jsx", ".mjs"],
            command=["typescript-language-server", "--stdio"],
            env={},
            project_markers=["tsconfig.json", "package.json", "jsconfig.json"]
        ),
        "rust": LanguageServer(
            name="rust-analyzer",
            extensions=[".rs"],
            command=["rust-analyzer"],
            env={},
            project_markers=["Cargo.toml", "Cargo.lock"]
        ),
        "go": LanguageServer(
            name="gopls",
            extensions=[".go"],
            command=["gopls"],
            env={},
            project_markers=["go.mod", "go.sum", "go.work"]
        ),
        "c_cpp": LanguageServer(
            name="clangd",
            extensions=[".c", ".cpp", ".cc", ".cxx", ".h", ".hpp"],
            command=["clangd", "--background-index", "--clang-tidy"],
            env={},
            project_markers=["compile_commands.json", "CMakeLists.txt", "Makefile"]
        )
    }
    
    def __init__(self, workspace_root: str):
        self.workspace_root = Path(workspace_root)
        self.active_servers: Dict[str, subprocess.Popen] = {}
        self.file_diagnostics: Dict[str, List[Diagnostic]] = {}
        
    def find_project_root(self, file_path: str, server: LanguageServer) -> Optional[str]:
        """Find project root by walking up directory tree (OpenCode pattern)"""
        current = Path(file_path).parent
        workspace_root = self.workspace_root
        
        while current >= workspace_root:
            for marker in server.project_markers:
                if (current / marker).exists():
                    return str(current)
            current = current.parent
        
        return str(workspace_root)  # Fallback to workspace root
    
    def get_language_server(self, file_path: str) -> Optional[LanguageServer]:
        """Get appropriate language server for file"""
        for server in self.LANGUAGE_SERVERS.values():
            if server.matches_file(file_path):
                return server
        return None
    
    async def ensure_server_running(self, server: LanguageServer, project_root: str) -> bool:
        """Ensure language server is running for project"""
        server_key = f"{server.name}:{project_root}"
        
        if server_key in self.active_servers:
            proc = self.active_servers[server_key]
            if proc.poll() is None:  # Still running
                return True
            else:
                del self.active_servers[server_key]
        
        try:
            # Spawn language server (OpenCode pattern)
            proc = subprocess.Popen(
                server.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=project_root,
                env={**subprocess.os.environ, **server.env},
                text=True,
                bufsize=0
            )
            
            self.active_servers[server_key] = proc
            logger.info(f"Started {server.name} LSP server for {project_root}")
            return True
            
        except FileNotFoundError:
            logger.warning(f"Language server {server.name} not found. Install with: npm install -g {server.command[0]}")
            return False
        except Exception as e:
            logger.error(f"Failed to start {server.name}: {e}")
            return False
    
    async def validate_file(self, file_path: str, content: Optional[str] = None) -> List[Diagnostic]:
        """
        Validate file and return diagnostics
        Uses simplified validation for agentic workflows
        """
        server = self.get_language_server(file_path)
        if not server:
            return []
        
        project_root = self.find_project_root(file_path, server)
        if not await self.ensure_server_running(server, project_root):
            return []
        
        # For agentic workflows, use simplified file-based validation
        # Write content to temp file and validate with static analysis
        return await self._static_validate(file_path, content, server)
    
    async def _static_validate(self, file_path: str, content: Optional[str], server: LanguageServer) -> List[Diagnostic]:
        """Simplified static validation for agentic workflows"""
        diagnostics = []
        
        # Use existing file or provided content
        if content is None:
            if not Path(file_path).exists():
                return []
            with open(file_path, 'r') as f:
                content = f.read()
        
        # Language-specific static validation
        if server.name == "pyright":
            diagnostics.extend(await self._validate_python(file_path, content))
        elif server.name == "typescript":
            diagnostics.extend(await self._validate_typescript(file_path, content))
        elif server.name == "c_cpp":
            diagnostics.extend(await self._validate_c_cpp(file_path, content))
        
        return diagnostics
    
    async def _validate_python(self, file_path: str, content: str) -> List[Diagnostic]:
        """Python-specific validation using pyflakes/ast"""
        diagnostics = []
        
        try:
            import ast
            import pyflakes.api
            import pyflakes.checker
            import io
            import sys
            
            # AST syntax validation
            try:
                ast.parse(content, filename=file_path)
            except SyntaxError as e:
                diagnostics.append(Diagnostic(
                    message=f"Syntax error: {e.msg}",
                    severity="Error",
                    line=e.lineno - 1 if e.lineno else 0,
                    character=e.offset or 0,
                    source="Python AST"
                ))
                return diagnostics
            
            # Pyflakes validation
            old_stderr = sys.stderr
            sys.stderr = captured_stderr = io.StringIO()
            
            try:
                pyflakes.api.check(content, file_path)
            except Exception as e:
                logger.debug(f"Pyflakes error: {e}")
            finally:
                sys.stderr = old_stderr
            
            # Parse pyflakes output
            for line in captured_stderr.getvalue().splitlines():
                if ':' in line and file_path in line:
                    parts = line.split(':')
                    if len(parts) >= 3:
                        try:
                            line_num = int(parts[1]) - 1
                            message = ':'.join(parts[2:]).strip()
                            diagnostics.append(Diagnostic(
                                message=message,
                                severity="Warning",
                                line=line_num,
                                character=0,
                                source="Pyflakes"
                            ))
                        except ValueError:
                            continue
            
        except ImportError:
            logger.debug("Pyflakes not available for Python validation")
        
        return diagnostics
    
    async def _validate_typescript(self, file_path: str, content: str) -> List[Diagnostic]:
        """TypeScript validation using tsc if available"""
        diagnostics = []
        
        try:
            # Create temp file for validation
            with tempfile.NamedTemporaryFile(mode='w', suffix='.ts', delete=False) as f:
                f.write(content)
                temp_path = f.name
            
            # Run tsc --noEmit for syntax checking
            result = subprocess.run(
                ['tsc', '--noEmit', '--target', 'es2020', temp_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # Parse tsc output
            for line in result.stderr.splitlines():
                if temp_path in line and '(' in line:
                    try:
                        # Format: file.ts(line,col): error message
                        pos_start = line.find('(') + 1
                        pos_end = line.find(')')
                        line_col = line[pos_start:pos_end].split(',')
                        line_num = int(line_col[0]) - 1
                        char_num = int(line_col[1]) if len(line_col) > 1 else 0
                        message = line[line.find(': ') + 2:] if ': ' in line else line
                        
                        severity = "Error" if "error" in line.lower() else "Warning"
                        
                        diagnostics.append(Diagnostic(
                            message=message,
                            severity=severity,
                            line=line_num,
                            character=char_num,
                            source="TypeScript"
                        ))
                    except (ValueError, IndexError):
                        continue
            
            Path(temp_path).unlink()  # Cleanup
            
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("TypeScript compiler not available")
        
        return diagnostics
    
    async def _validate_c_cpp(self, file_path: str, content: str) -> List[Diagnostic]:
        """C/C++ validation using compiler syntax check"""
        diagnostics = []
        
        try:
            # Create temp file
            suffix = '.c' if file_path.endswith('.c') else '.cpp'
            with tempfile.NamedTemporaryFile(mode='w', suffix=suffix, delete=False) as f:
                f.write(content)
                temp_path = f.name
            
            # Run compiler syntax check
            compiler = 'gcc' if suffix == '.c' else 'g++'
            result = subprocess.run(
                [compiler, '-fsyntax-only', '-Wall', '-std=c11' if suffix == '.c' else '-std=c++17', temp_path],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            # Parse compiler output
            for line in result.stderr.splitlines():
                if temp_path in line and ':' in line:
                    try:
                        parts = line.split(':')
                        if len(parts) >= 4:
                            line_num = int(parts[1]) - 1
                            char_num = int(parts[2]) if parts[2].isdigit() else 0
                            severity = "Error" if "error" in parts[3] else "Warning"
                            message = ':'.join(parts[4:]).strip()
                            
                            diagnostics.append(Diagnostic(
                                message=message,
                                severity=severity,
                                line=line_num,
                                character=char_num,
                                source="GCC/G++"
                            ))
                    except (ValueError, IndexError):
                        continue
            
            Path(temp_path).unlink()  # Cleanup
            
        except (FileNotFoundError, subprocess.TimeoutExpired):
            logger.debug("C/C++ compiler not available")
        
        return diagnostics
    
    def format_diagnostics_brief(self, diagnostics: List[Diagnostic], max_errors: int = 3) -> str:
        """Format diagnostics for AI model feedback (OpenCode pattern)"""
        if not diagnostics:
            return "0 linter errors"
        
        errors = [d for d in diagnostics if d.severity == "Error"]
        warnings = [d for d in diagnostics if d.severity == "Warning"]
        
        if not errors and not warnings:
            return "0 linter errors"
        
        # Prioritize errors, then warnings
        to_show = errors[:max_errors]
        if len(to_show) < max_errors:
            to_show.extend(warnings[:max_errors - len(to_show)])
        
        result = []
        for diag in to_show:
            result.append(diag.format_brief())
        
        total_count = len(errors) + len(warnings)
        if total_count > len(to_show):
            result.append(f"... and {total_count - len(to_show)} more issues")
        
        return '\n'.join(result)
    
    async def shutdown(self):
        """Shutdown all language servers"""
        for server_key, proc in self.active_servers.items():
            try:
                proc.terminate()
                await asyncio.sleep(0.1)
                if proc.poll() is None:
                    proc.kill()
            except Exception as e:
                logger.error(f"Error shutting down {server_key}: {e}")
        
        self.active_servers.clear()

@ray.remote
class RemoteLSPManager:
    """Ray remote wrapper for LSPManager"""
    
    def __init__(self, workspace_root: str):
        self.lsp_manager = LSPManager(workspace_root)
    
    async def validate_file(self, file_path: str, content: Optional[str] = None) -> List[Dict]:
        """Validate file and return serializable diagnostics"""
        diagnostics = await self.lsp_manager.validate_file(file_path, content)
        return [
            {
                "message": d.message,
                "severity": d.severity,
                "line": d.line,
                "character": d.character,
                "source": d.source
            }
            for d in diagnostics
        ]
    
    async def format_diagnostics_brief(self, diagnostics: List[Dict], max_errors: int = 3) -> str:
        """Format diagnostics for AI feedback"""
        diag_objects = [
            Diagnostic(
                message=d["message"],
                severity=d["severity"],
                line=d["line"],
                character=d["character"],
                source=d["source"]
            )
            for d in diagnostics
        ]
        return self.lsp_manager.format_diagnostics_brief(diag_objects, max_errors)
    
    async def shutdown(self):
        """Shutdown the manager"""
        await self.lsp_manager.shutdown()