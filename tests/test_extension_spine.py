from __future__ import annotations

import pytest

from agentic_coder_prototype.extensions.spine import MiddlewareSpec, order_middleware


def test_extension_spine_orders_by_phase_and_priority() -> None:
    specs = [
        MiddlewareSpec("after_low", "plugin_b", "after_model", priority=5),
        MiddlewareSpec("before_high", "plugin_a", "before_model", priority=10),
        MiddlewareSpec("after_high", "plugin_c", "after_model", priority=1),
    ]
    ordered = order_middleware(specs)
    assert [spec.middleware_id for spec in ordered] == ["before_high", "after_high", "after_low"]


def test_extension_spine_respects_dependencies() -> None:
    specs = [
        MiddlewareSpec("alpha", "plugin_a", "before_model", priority=100, before=("beta",)),
        MiddlewareSpec("beta", "plugin_b", "before_model", priority=1),
    ]
    ordered = order_middleware(specs)
    assert [spec.middleware_id for spec in ordered] == ["alpha", "beta"]


def test_extension_spine_cycle_detection() -> None:
    specs = [
        MiddlewareSpec("alpha", "plugin_a", "before_model", before=("beta",)),
        MiddlewareSpec("beta", "plugin_b", "before_model", before=("alpha",)),
    ]
    with pytest.raises(ValueError):
        order_middleware(specs)
