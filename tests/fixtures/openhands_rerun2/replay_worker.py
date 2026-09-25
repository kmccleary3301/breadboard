"""Run pinned OpenHands SDK tool phases against the packet's scripted responses."""
from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

from breadboard.rl.harness.openhands_worker import OpenHandsActor


def replace_workspace(value, source: str, destination: str):
    if isinstance(value, str):
        return value.replace(source, destination)
    if isinstance(value, list):
        return [replace_workspace(item, source, destination) for item in value]
    if isinstance(value, dict):
        return {key: replace_workspace(item, source, destination) for key, item in value.items()}
    return value


class Channel:
    def __init__(self, responses, workspace: Path):
        self.responses = responses
        self.workspace = workspace
        self.requests = []
        self.cursor = 0

    def respond(self, value):
        self.requests.append(json.loads(base64.b64decode(value['http_request']['body_b64'], validate=True)))

    def receive(self):
        if self.cursor >= len(self.responses):
            raise RuntimeError('supplier scripted responses exhausted')
        response = replace_workspace(
            self.responses[self.cursor]['response'],
            '/opt/openhands/case/workspace', str(self.workspace),
        )
        self.cursor += 1
        return {
            'operation': 'provider_response',
            'payload': {
                'status_code': 200,
                'headers': [['Content-Type', 'application/json']],
                'body_b64': base64.b64encode(json.dumps(response, separators=(',', ':'), ensure_ascii=False).encode()).decode(),
            },
        }


def main(root: Path, temporary: Path):
    cases = json.loads((root / 'kit/openhands_capture_cases.json').read_text())['cases']
    outcomes = {}
    for case_id, case in cases.items():
        trace = json.loads((root / 'captures' / case_id / 'trace.json').read_text())
        workspace = temporary / case_id / 'workspace'
        scratch = temporary / case_id / 'scratch'
        workspace.mkdir(parents=True)
        scratch.mkdir(parents=True)
        channel = Channel(trace['responses'], workspace)
        actor = OpenHandsActor(channel)
        status = None
        try:
            init_reply = actor.dispatch('initialize', {
                'task': case['task'],
                'model_config': {
                    'model_name': 'openai/gpt-4o-mini',
                    'model_canonical_name': None,
                    'max_input_tokens': 131072,
                    'base_url': 'http://127.0.0.1:1234/v1',
                },
                'workspace': str(workspace),
                'scratch': str(scratch),
                'max_iteration_per_run': case.get('max_iteration_per_run', 16),
            })
            conversation_id = str(init_reply['conversation_id'])
            for _ in range(case.get('max_iteration_per_run', 16)):
                actor.dispatch('sample', {})
                prepared = actor.dispatch('prepare', {})
                for action in prepared.get('actions', []):
                    actor.dispatch('execute', {'index': action['index'], 'tool_id': action['tool_id']})
                committed = actor.dispatch('commit', {})
                status = committed.get('status')
                if status in ('FINISHED', 'ERROR', 'STUCK'):
                    break
        finally:
            actor.close()
        outcomes[case_id] = {
            'requests': channel.requests,
            'conversation_id': conversation_id,
            'workspace': str(workspace),
            'status': status,
        }
    print('BB_OH_RESULT:' + json.dumps(outcomes, separators=(',', ':')))


if __name__ == '__main__':
    os.environ['OPENHANDS_SUPPRESS_BANNER'] = '1'
    main(Path(sys.argv[1]), Path(sys.argv[2]))
