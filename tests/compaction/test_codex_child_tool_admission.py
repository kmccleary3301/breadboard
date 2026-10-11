"""Depth-limited child calls must fail like stock, not just disappear from tools."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from breadboard_engine.agent_llm_openai import OpenAIConductor
from scripts.compaction_lanes import codex_lane
from scripts.compaction_lanes.codex_lane import BINARY, EngineMockProvider, run_stock
from tests.compaction.test_codex_agent_tools import owner, value
from tests.compaction.test_codex_tool_search import DiscoveryHandler, SearchSpawnHandler, USAGE


class HiddenChildCallsHandler(SearchSpawnHandler):
    def _handle_openai_responses(self, item, request_json):
        inputs = request_json.get('input', [])
        child = any(block.get('text') == 'Write discovery child evidence'
                    for entry in inputs if entry.get('role') == 'user'
                    for block in entry.get('content', []) if isinstance(block, dict))
        if not child:
            return super()._handle_openai_responses(item, request_json)
        outputs = {entry['call_id']: entry for entry in inputs
                   if entry.get('type') in {'function_call_output', 'tool_search_output'}}
        if 'hidden-close' in outputs:
            item = {'type': 'text', 'text': 'child finished', 'usage': USAGE}
        elif 'search-1' in outputs:
            item = {'type': 'tool_call', 'name': 'close_agent', 'namespace': 'multi_agent_v1',
                    'call_id': 'hidden-close', 'arguments': json.dumps({
                        'target': 'aaaaaaaa-aaaa-7aaa-8aaa-aaaaaaaaaaaa'}), 'usage': USAGE}
        else:
            item = {'type': 'native_search', 'arguments': {'query': 'close agent'}}
        return DiscoveryHandler._handle_openai_responses(self, item, request_json)


def test_hidden_child_search_and_close_match_real_stock(owner, tmp_path, monkeypatch):
    if not BINARY.is_file():
        pytest.skip('pinned Codex 0.139.0 binary not installed')
    config = deepcopy(owner.config)
    config['tools']['registry']['include'] = [
        'exec_command', 'write_stdin', 'update_plan', 'request_user_input', 'apply_patch',
        'view_image', 'get_goal', 'create_goal', 'update_goal', 'tool_search', 'web_search', 'multi_agent_v1',
    ]
    config['provider_tools']['openai'].update({
        'responses_namespaces': True, 'responses_tool_search': True,
        'responses_deferred_tools': ['multi_agent_v1'], 'responses_custom_tools': ['apply_patch'],
        'responses_hosted_outputs': ['web_search_call'],
    })
    engine = OpenAIConductor.__ray_metadata__.modified_class(
        workspace=owner.workspace, image=owner.image, config=config, local_mode=True)
    stock_dir = tmp_path / 'stock'
    stock_dir.mkdir()
    script = [{'type': 'text', 'text': 'unused', 'usage': USAGE} for _ in range(7)]
    monkeypatch.setattr(codex_lane, 'EngineResponsesHandler', HiddenChildCallsHandler)
    try:
        with EngineMockProvider(script=script, record_path=stock_dir / 'requests.jsonl') as provider:
            result = run_stock('mid_turn_compaction', Path(owner.workspace), stock_dir, provider.base_url,
                               {'model': 'gpt-5.5', 'window': 272000, 'limit': 244800})
            expected_records = list(provider.engine.recorded_requests)
        assert result['runs'][0]['returncode'] == 0, result
        with EngineMockProvider(script=script, record_path=tmp_path / 'bb-requests.jsonl') as provider:
            monkeypatch.setenv('OPENAI_BASE_URL', provider.base_url + '/v1')
            monkeypatch.setenv('OPENAI_API_KEY', 'lane-key')
            result = engine.run_agentic_loop(
                '', 'Execute mid-turn test prompt', 'gpt-5.5', max_steps=8,
                stream_responses=False, tool_prompt_mode='system_once', completion_sentinel=None,
                completion_config=config['completion'], context={
                    'session_id': '11111111-1111-7111-8111-111111111111',
                    'input_id': '22222222-2222-7222-8222-222222222222',
                    'turn_id': '33333333-3333-7333-8333-333333333333',
                })
            actual_records = list(provider.engine.recorded_requests)
        assert not (result.get('completion_summary') or {}).get('error'), result
        def child_outputs(records):
            return next([item for item in record['json']['input']
                         if item.get('type') in {'tool_search_output', 'function_call_output'}]
                        for record in records if any(item.get('call_id') == 'hidden-close'
                            and item.get('type') == 'function_call_output' for item in record['json']['input']))
        expected, actual = child_outputs(expected_records), child_outputs(actual_records)
        assert json.dumps(actual, sort_keys=True, separators=(',', ':')).encode() == (
            json.dumps(expected, sort_keys=True, separators=(',', ':')).encode())
        assert actual[0] == {'type': 'tool_search_output', 'call_id': 'search-1',
                             'status': 'completed', 'execution': 'client', 'tools': []}
        assert actual[1]['output'] == 'unsupported call: multi_agent_v1close_agent'
        child = next(iter(engine._codex_agent_registry.children.values()))
        assert child.status == {'completed': 'child finished'}
    finally:
        registry = getattr(engine, '_codex_agent_registry', None)
        if registry:
            for child in registry.children.values():
                if not child.closed:
                    value(engine, 'multi_agent_v1.close_agent', target=child.id)


def test_real_child_compaction_survives_input_disk_resume_and_fork(owner, tmp_path, monkeypatch):
    owner.config['compaction'] = {
        'enabled': True, 'preset': 'codex@0.139.0', 'contextWindow': 16000,
    }
    owner.config['provider_tools']['openai']['responses_message_phase'] = True
    owner.config['provider_tools']['openai']['client_metadata_session'] = {
        'x-codex-window-id': '{session_id}:{compaction_boundary_count}',
    }
    high_usage = {'input_tokens': 15000, 'output_tokens': 10, 'total_tokens': 15010}
    script = [
        {'type': 'tool_call', 'name': 'exec_command', 'call_id': 'before-compact',
         'arguments': json.dumps({'cmd': 'true', 'yield_time_ms': 1000}), 'usage': high_usage},
        {'type': 'text', 'text': 'Preserved child checkpoint', 'usage': USAGE},
        *({'type': 'text', 'text': text, 'phase': 'final_answer', 'usage': USAGE}
          for text in ['first completed', 'second completed', 'resumed completed', 'fork completed']),
    ]
    captured = []
    conductor_class = type(owner)
    original = conductor_class._run_main_loop

    def capture_state(conductor, *args, **kwargs):
        result = original(conductor, *args, **kwargs)
        captured.append(conductor._active_session_state)
        return result

    monkeypatch.setattr(conductor_class, '_run_main_loop', capture_state)
    with EngineMockProvider(script=script, record_path=tmp_path / 'compacted-child-requests.jsonl') as provider:
        monkeypatch.setenv('OPENAI_BASE_URL', provider.base_url + '/v1')
        monkeypatch.setenv('OPENAI_API_KEY', 'lane-key')
        agent_id = value(owner, 'multi_agent_v1.spawn_agent', message='First child input')['agent_id']
        assert value(owner, 'multi_agent_v1.wait_agent', targets=[agent_id])['status'][agent_id] == {
            'completed': 'first completed',
        }
        child = owner._codex_agent_registry.children[agent_id]
        assert len(child.compaction_state) == 1
        value(owner, 'multi_agent_v1.send_input', target=agent_id, message='Second child input')
        assert value(owner, 'multi_agent_v1.wait_agent', targets=[agent_id])['status'][agent_id] == {
            'completed': 'second completed',
        }
        value(owner, 'multi_agent_v1.close_agent', target=agent_id)
        del owner._codex_agent_registry.children[agent_id]
        value(owner, 'multi_agent_v1.resume_agent', id=agent_id)
        value(owner, 'multi_agent_v1.send_input', target=agent_id, message='Resumed child input')
        assert value(owner, 'multi_agent_v1.wait_agent', targets=[agent_id])['status'][agent_id] == {
            'completed': 'resumed completed',
        }
        # Borrow the actual compacted parent state solely to invoke the public
        # fork tool while that state remains available to the owning conductor.
        owner._active_session_state = captured[-1]
        fork_id = value(owner, 'multi_agent_v1.spawn_agent', message='Forked child input',
                        fork_context=True)['agent_id']
        assert value(owner, 'multi_agent_v1.wait_agent', targets=[fork_id])['status'][fork_id] == {
            'completed': 'fork completed',
        }
        records = provider.engine.recorded_requests
    assert len(records) == 6
    for record in records[2:]:
        text = json.dumps(record['json']['input'])
        assert 'Preserved child checkpoint' in text
        assert 'before-compact' not in text
        assert record['json']['client_metadata']['x-codex-window-id'].endswith(':1')
    assert 'Second child input' in json.dumps(records[3]['json']['input'])
    assert 'Resumed child input' in json.dumps(records[4]['json']['input'])
    assert 'Forked child input' in json.dumps(records[5]['json']['input'])
    assert 'resumed completed' in json.dumps(records[5]['json']['input'])
