"""
Integration layer for Enhanced Tool Calling with existing LSP v2 system.
Provides factory functions and utilities to integrate with LSPEnhancedSandbox.
"""

import logging
from typing import Dict, Any, Optional
from pathlib import Path

from ..execution.enhanced_executor import EnhancedToolExecutor
from ..execution.dialect_manager import DialectManager

logger = logging.getLogger(__name__)

class LSPIntegratedToolExecutor:
    """Factory and wrapper for creating LSP-integrated tool executors"""
    
    @staticmethod
    def create_from_config(sandbox, config: Dict[str, Any]) -> EnhancedToolExecutor:
        """
        Create an enhanced tool executor optimized for LSP integration.
        
        Args:
            sandbox: LSPEnhancedSandbox or regular DevSandboxV2
            config: Configuration from profiles like gpt5_nano_lsp_enhanced.yaml
            
        Returns:
            EnhancedToolExecutor configured for LSP integration
        """
        # Check if sandbox has LSP capabilities
        has_lsp = hasattr(sandbox, 'lsp_manager') or hasattr(sandbox, 'lsp_diagnostics')
        
        if has_lsp:
            logger.info("Detected LSP-enhanced sandbox, enabling LSP feedback integration")
            config = _ensure_lsp_config(config)
        else:
            logger.info("Using regular sandbox, LSP integration disabled")
            config = _disable_lsp_config(config)
        
        return EnhancedToolExecutor(sandbox, config)
    
    @staticmethod
    def create_with_lsp_sandbox(base_sandbox_ref, workspace: str, config: Dict[str, Any]):
        """
        Create an enhanced tool executor with LSPEnhancedSandbox wrapper.
        
        Args:
            base_sandbox_ref: Ray ObjectRef to base DevSandboxV2
            workspace: Workspace root path
            config: Configuration dictionary
            
        Returns:
            EnhancedToolExecutor with LSPEnhancedSandbox
        """
        try:
            from breadboard.sandbox_lsp_integration import LSPEnhancedSandbox
            import ray
            
            # Wrap base sandbox with LSP capabilities
            lsp_sandbox = LSPEnhancedSandbox.remote(base_sandbox_ref, workspace)
            
            # Ensure LSP is enabled in config
            config = _ensure_lsp_config(config)
            
            logger.info(f"Created LSP-enhanced sandbox for workspace: {workspace}")
            return EnhancedToolExecutor(lsp_sandbox, config)
            
        except ImportError as e:
            logger.warning(f"LSP integration unavailable: {e}")
            return EnhancedToolExecutor(base_sandbox_ref, config)

def _ensure_lsp_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure LSP integration is properly configured"""
    config = config.copy()
    
    # Enable LSP integration
    if "enhanced_tools" not in config:
        config["enhanced_tools"] = {}
    
    if "lsp_integration" not in config["enhanced_tools"]:
        config["enhanced_tools"]["lsp_integration"] = {}
    
    lsp_config = config["enhanced_tools"]["lsp_integration"]
    lsp_config["enabled"] = True
    lsp_config["format_feedback"] = True
    
    return config

def _disable_lsp_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Disable LSP integration in config"""
    config = config.copy()
    
    if "enhanced_tools" in config and "lsp_integration" in config["enhanced_tools"]:
        config["enhanced_tools"]["lsp_integration"]["enabled"] = False
    
    return config

def create_feedback_formatter():
    """Create a standalone feedback formatter for LSP diagnostics"""
    
    def format_diagnostics_for_ai(diagnostics: Dict[str, Any], operation: str) -> str:
        """Format LSP diagnostics for AI model feedback (Claude Code style)"""
        if not diagnostics or not any(diagnostics.values()):
            return f"{operation.capitalize()} completed with 0 linter errors"
        
        # Extract error counts
        total_errors = 0
        error_lines = []
        
        for file_path, file_diags in diagnostics.items():
            if not file_diags:
                continue
                
            for diag in file_diags:
                severity = diag.get("severity", "error").lower()
                if "error" in severity:
                    total_errors += 1
                    line = diag.get("line", 0) + 1
                    char = diag.get("character", 0) + 1
                    message = diag.get("message", "")
                    error_lines.append(f"ERROR [{line}:{char}] {message}")
        
        if total_errors == 0:
            return f"{operation.capitalize()} completed with 0 linter errors"
        
        # Format response
        feedback = f"{operation.capitalize()} completed with {total_errors} linter error(s)"
        if error_lines and len(error_lines) <= 3:
            feedback += ":\n" + "\n".join(error_lines)
        elif error_lines:
            feedback += ":\n" + "\n".join(error_lines[:3])
            feedback += f"\n... and {len(error_lines) - 3} more errors"
        
        return feedback
    
    return format_diagnostics_for_ai

def integrate_with_agent_llm(agent_instance, config: Dict[str, Any]) -> bool:
    """
    Integrate enhanced tool calling with agent_llm_openai.py
    
    Args:
        agent_instance: Instance of AgentLLM class
        config: Enhanced tool calling configuration
        
    Returns:
        True if integration successful, False otherwise
    """
    try:
        # Check if agent has sandbox
        if not hasattr(agent_instance, 'sandbox'):
            logger.warning("Agent instance has no sandbox attribute")
            return False
        
        # Create enhanced executor
        enhanced_executor = LSPIntegratedToolExecutor.create_from_config(
            agent_instance.sandbox, config
        )
        
        # Check if sandbox has LSP capabilities
        if hasattr(agent_instance.sandbox, 'lsp_manager'):
            logger.info("Agent sandbox already has LSP integration")
        else:
            logger.info("Regular sandbox detected, LSP feedback will be limited")
        
        # Store enhanced executor for potential use
        agent_instance._enhanced_executor = enhanced_executor
        
        logger.info("Successfully integrated enhanced tool calling with agent")
        return True
        
    except Exception as e:
        logger.error(f"Failed to integrate with agent: {e}")
        return False

# Utility for extracting LSP feedback from tool results
def extract_lsp_feedback(tool_result: Dict[str, Any]) -> Optional[str]:
    """Extract and format LSP feedback from tool execution result"""
    if "lsp_feedback" in tool_result:
        return tool_result["lsp_feedback"]
    
    if "lsp_diagnostics" in tool_result:
        formatter = create_feedback_formatter()
        return formatter(tool_result["lsp_diagnostics"], "operation")
    
    return None