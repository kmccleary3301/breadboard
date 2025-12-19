from __future__ import annotations

from agentic_coder_prototype.plan_bootstrapper import PlanBootstrapper


class FakeSessionState:
    def __init__(self) -> None:
        self.meta = {}

    def get_provider_metadata(self, key: str):
        return self.meta.get(key)

    def set_provider_metadata(self, key: str, value):
        self.meta[key] = value


def test_hard_strategy_seeds_on_first_turn(tmp_path):
    config = {
        "guardrails": {
            "plan_bootstrap": {
                "strategy": "hard",
                "max_turns": 1,
                "warmup_turns": 0,
                "seed": {
                    "todos": [{"content": "stub", "status": "pending"}],
                    "extra_calls": [
                        {"function": "list", "arguments": {"path": "."}},
                    ],
                },
            }
        }
    }

    bootstrapper = PlanBootstrapper(config)
    session = FakeSessionState()
    session.set_provider_metadata("current_turn_index", 1)

    assert bootstrapper.is_enabled()
    assert bootstrapper.should_seed(session)

    calls = bootstrapper.build_calls()
    assert calls
    assert calls[0].function == "todo.write_board"
    assert any(call.function == "list" for call in calls)


def test_should_not_seed_when_already_seeded():
    config = {
        "guardrails": {
            "plan_bootstrap": {
                "strategy": "hard",
                "max_turns": 1,
                "warmup_turns": 0,
                "seed": {"todos": [{"content": "done", "status": "completed"}]},
            }
        }
    }
    bootstrapper = PlanBootstrapper(config)
    session = FakeSessionState()
    session.set_provider_metadata("current_turn_index", 1)
    session.set_provider_metadata("plan_bootstrap_seeded", True)

    assert not bootstrapper.should_seed(session)


def test_soft_strategy_requires_warning_event():
    config = {
        "guardrails": {
            "plan_bootstrap": {
                "strategy": "soft",
                "max_turns": 2,
                "warmup_turns": 1,
                "seed": {"todos": [{"content": "soft-seed", "status": "pending"}]},
            }
        }
    }
    bootstrapper = PlanBootstrapper(config)
    session = FakeSessionState()
    session.set_provider_metadata("current_turn_index", 3)

    # Without warning event emitted yet, soft strategy will not seed.
    assert not bootstrapper.should_seed(session)

    session.set_provider_metadata("plan_bootstrap_event_emitted", True)
    assert bootstrapper.should_seed(session)
