"""
State management for agentic coding system
"""

from .session_state import SessionState
from .completion_detector import CompletionDetector

__all__ = ["SessionState", "CompletionDetector"]