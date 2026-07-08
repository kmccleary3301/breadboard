"""Trajectory graph primitives for RL replay and projection."""

from breadboard.rl.trace.credit import CreditFrame, build_terminal_credit_frame
from breadboard.rl.trace.edges import TraceEdge
from breadboard.rl.trace.graph import TrajectoryGraph, build_graph_from_session_events, validate_graph_invariants
from breadboard.rl.trace.nodes import TraceNode

__all__ = [
    "CreditFrame",
    "TraceEdge",
    "TraceNode",
    "TrajectoryGraph",
    "build_graph_from_session_events",
    "build_terminal_credit_frame",
    "validate_graph_invariants",
]
