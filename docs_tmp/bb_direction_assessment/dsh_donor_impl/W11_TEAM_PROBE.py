"""W11 Run T2 fixture; execute start then resume in separate Python processes."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from collections import Counter
from dataclasses import replace

from breadboard.product.coordination.work_items import WorkItem, WorkItemRepository
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.children import ChildSpec, DurableChildFactory, ProcessExecutionAdapter
from breadboard.product.runtime.events import Session
from breadboard.product.runtime.session_store import create_session, load_session, mutate_session
from breadboard.product.runtime.workflows import ReplayableWorkflowController, WorkflowDefinition, WorkflowStep
from breadboard_engine.api.cli_bridge.registry import SessionRegistry


WORKER = """import os, pathlib, sys, time
name = sys.stdin.read()
pathlib.Path(name + '.started').write_text(str(os.getpid()))
deadline = time.monotonic() + 60
while not pathlib.Path(name + '.release').exists():
    if time.monotonic() >= deadline:
        raise TimeoutError('experiment release gate did not arrive')
    time.sleep(.01)
pathlib.Path(name + '.result').write_text('accepted:' + name)
"""


def wait_until(predicate):
    deadline = time.monotonic() + 15
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError('experiment condition did not arrive')
        time.sleep(.01)


def run(phase: str, root: Path) -> dict:
    if not __debug__:
        raise RuntimeError("T2 evidence requires Python optimization to be disabled")
    workspace = root / 'workspace'
    workspace.mkdir(parents=True, exist_ok=True)
    lock = EffectiveHarnessLock._from_record({'graph_hash': 'sha256:' + '1' * 64})
    repository = WorkItemRepository(root / 'work-items.jsonl')
    if phase == 'start':
        create_session(workspace, Session.start(lock, 'two-child experiment', session_id='parent'))
        parent = WorkItem.create('team', work_item_id='parent-work', repository=repository)
        parent.acquire_lease('parent-worker', lease_id='parent-lease')
        parent.start_attempt('parent', lease_id='parent-lease', attempt_id='parent-attempt')
    registry = SessionRegistry(state_root=root / 'registry')
    adapter = ProcessExecutionAdapter((sys.executable, '-c', WORKER))
    factory = DurableChildFactory(workspace, registry=registry, repository=repository, adapters=[adapter])
    definition = WorkflowDefinition(tuple(
        WorkflowStep(name, ChildSpec(name, name, lock, name + '-worker', adapter.family))
        for name in ('inspect', 'verify')
    ))
    controller = ReplayableWorkflowController(
        factory, workflow_id='team', parent_session_id='parent', root_session_id='parent',
        parent_work_item_id='parent-work', definition=definition,
    )

    def states():
        return {state.child_spec['workflow_step_id']: state for state in
                factory.child_states(parent_work_item_id='parent-work')}

    def snapshot():
        children = states()
        ids = ['parent-work'] + [children[name].child_work_item_id for name in sorted(children)]
        sessions = ['parent'] + [children[name].child_session_id for name in sorted(children)]
        return {
            'decision': controller.decision().as_dict(),
            'children': {name: state.retained() for name, state in sorted(children.items())},
            'work_events': {identity: [event.as_dict() for event in repository.read(identity)] for identity in ids},
            'session_events': {identity: [event.as_dict() for event in load_session(workspace, identity)[0].events]
                               for identity in sessions},
        }

    if phase == 'cleanup':
        for name in ('inspect', 'verify'):
            (workspace / (name + '.release')).touch()
        for state in states().values():
            if not state.terminal_count:
                factory.cancel(state.child_session_id, expected_revision=state.revision, reason='experiment cleanup')
        wait_until(lambda: all(adapter.observe(state.execution_target) in {'absent', 'completed'}
                               for state in states().values()))
        return {'phase': phase, 'owned_process_groups_terminal': True}

    if phase == 'start':
        for step in definition.steps:
            factory.start(
                parent_session_id='parent', root_session_id='parent', parent_work_item_id='parent-work',
                spec=replace(step.child, workflow_id='team', workflow_step_id=step.step_id,
                             workflow_definition_hash=definition.identity('team')),
            )
        wait_until(lambda: all((workspace / (name + '.started')).exists() for name in ('inspect', 'verify')))
        assert all(adapter.observe(state.execution_target) == 'running' for state in states().values())
        child_pids = {name: int((workspace / (name + '.started')).read_text()) for name in ('inspect', 'verify')}
        assert len(set(child_pids.values())) == 2
        (workspace / 'inspect.release').touch()
        wait_until(lambda: adapter.observe(states()['inspect'].execution_target) == 'completed')
        factory.reconcile(states()['inspect'].recovery_ref)
        assert states()['inspect'].terminal_count == 1
        assert adapter.observe(states()['verify'].execution_target) == 'running'
        before = snapshot()
        assert before['decision']['completed_step_ids'] == ['inspect']
        assert before['decision']['active_step_ids'] == ['verify']
        assert snapshot() == before
        (root / 'observation-oracle.json').write_text(json.dumps(before, sort_keys=True))
        return {'phase': phase, 'coordinator_pid': os.getpid(), 'child_pids': child_pids,
                'both_started_running': True, 'one_settled_other_running': True, 'snapshot': before}

    if phase != 'resume':
        raise ValueError('phase must be start, resume, or cleanup')
    before = json.loads((root / 'observation-oracle.json').read_text())
    assert adapter.observe(states()['verify'].execution_target) == 'running'
    restarted = snapshot()
    assert restarted == before
    assert snapshot() == restarted
    (workspace / 'verify.release').touch()
    wait_until(lambda: adapter.observe(states()['verify'].execution_target) == 'completed')
    factory.reconcile(states()['verify'].recovery_ref)
    assert controller.decision().action == 'complete'
    parent = WorkItem.restore(repository, 'parent-work')
    parent.complete('both children joined', attempt_id='parent-attempt')
    mutate_session(workspace, 'parent', lambda current: current.complete('both children joined'))
    final = snapshot()
    children = states()
    assert all(state.terminal_count == 1 and state.joined for state in children.values())
    counts = {identity: dict(Counter(event['kind'] for event in stream))
              for identity, stream in final['work_events'].items()}
    assert counts['parent-work']['child.delegated'] == 2
    assert counts['parent-work']['child.joined'] == 2
    for count in counts.values():
        for kind in ('work_item.created', 'lease.acquired', 'attempt.started', 'work.completed'):
            assert count[kind] == 1
    for identity in final['session_events']:
        session = load_session(workspace, identity)[0]
        assert session.read_model.status == 'completed'
        assert Session.restore(session.events).read_model == session.read_model
    fresh = WorkItemRepository(root / 'work-items.jsonl')
    for identity in final['work_events']:
        assert [event.as_dict() for event in fresh.read(identity)] == final['work_events'][identity]
        assert WorkItem.restore(fresh, identity).read_model.status == 'completed'
    assert all(adapter.observe(state.execution_target) in {'absent', 'completed'} for state in children.values())
    return {'phase': phase, 'coordinator_pid': os.getpid(), 'restart_equal': True,
            'observation_wrote_no_events': True, 'terminal_settlements': [state.terminal_count for state in children.values()],
            'parent_joins': 2, 'parent_completed': True, 'replay_equal': True,
            'owned_process_groups_terminal': True, 'event_counts': counts, 'snapshot': final}


if __name__ == '__main__':
    output = run(sys.argv[1], Path(sys.argv[2]).resolve())
    output['source_repository'] = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], text=True).strip()
    output['source_head'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    print(json.dumps(output, sort_keys=True))
