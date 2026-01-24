import time

from agentic_coder_prototype.provider_health import RouteHealthManager


def test_route_health_opens_and_closes_circuit(monkeypatch):
    manager = RouteHealthManager()
    base = time.time()

    monkeypatch.setattr(time, "time", lambda: base)
    manager.record_failure("route", "error1")
    monkeypatch.setattr(time, "time", lambda: base + 10)
    manager.record_failure("route", "error2")
    monkeypatch.setattr(time, "time", lambda: base + 20)
    manager.record_failure("route", "error3")

    assert manager.is_circuit_open("route", now=base + 21)

    monkeypatch.setattr(time, "time", lambda: base + RouteHealthManager.CIRCUIT_COOLDOWN_SECONDS + 30)
    manager.record_success("route")
    assert not manager.is_circuit_open("route", now=base + RouteHealthManager.CIRCUIT_COOLDOWN_SECONDS + 40)


def test_route_health_prunes_old_failures(monkeypatch):
    manager = RouteHealthManager()
    base = time.time()
    monkeypatch.setattr(time, "time", lambda: base)
    manager.record_failure("route", "error1")
    monkeypatch.setattr(time, "time", lambda: base + RouteHealthManager.FAILURE_WINDOW_SECONDS + 1)
    manager.record_failure("route", "recent")
    state = manager.snapshot()["route"]
    assert len(state["failures"]) == 1
