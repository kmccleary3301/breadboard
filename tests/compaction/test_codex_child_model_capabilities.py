"""Selected stock model capabilities must reach the real child executor and wire."""
import json
from pathlib import Path

from PIL import Image

from breadboard_engine.compaction_lanes.codex_lane import EngineMockProvider
from tests.compaction.test_codex_agent_tools import owner, value
from breadboard_engine.execution.codex_agents import STOCK_MODEL_INFO


def test_capability_projection_matches_pinned_stock_catalog():
    catalog = Path('/tmp/codex-a7dff904308535e965aee87680c1fc5ef1d19eec/codex-rs/models-manager/models.json')
    if not catalog.is_file():
        import pytest
        pytest.skip('pinned stock source catalog is not installed')
    models = {model['slug']: model for model in json.loads(catalog.read_text())['models']}
    for slug, metadata in STOCK_MODEL_INFO.items():
        assert metadata == {key: models[slug][key] for key in metadata}


def test_real_gpt52_child_uses_bytes_and_resized_image(owner, tmp_path, monkeypatch):
    image = Path(owner.workspace) / 'capability.png'
    Image.new('RGB', (2, 2), color='red').save(image)
    owner.config["tools"]["registry"]["include"].append("view_image")
    usage = {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}
    script = [
        {'type': 'tool_call', 'name': 'exec_command', 'call_id': 'bytes-child',
         'arguments': json.dumps({'cmd': "printf '%015000d' 0", 'yield_time_ms': 1000}), 'usage': usage},
        {'type': 'tool_call', 'name': 'view_image', 'call_id': 'image-child',
         'arguments': json.dumps({'path': 'capability.png', 'detail': 'original'}), 'usage': usage},
        {'type': 'text', 'text': 'capabilities complete', 'usage': usage},
    ]
    with EngineMockProvider(script=script, record_path=tmp_path / 'child-requests.jsonl') as provider:
        monkeypatch.setenv('OPENAI_BASE_URL', provider.base_url + '/v1')
        monkeypatch.setenv('OPENAI_API_KEY', 'lane-key')
        id = value(owner, 'multi_agent_v1.spawn_agent', message='Exercise selected child capabilities', model='gpt-5.2')['agent_id']
        assert value(owner, 'multi_agent_v1.wait_agent', targets=[id])['status'][id] == {'completed': 'capabilities complete'}
        records = [record['json'] for record in provider.engine.recorded_requests]
    outputs = [item for record in records for item in record['input'] if item.get('type') == 'function_call_output']
    text = next(item['output'] for item in outputs if item['call_id'] == 'bytes-child')
    assert '…1250 tokens truncated…' in text
    assert len(text.encode()) < 11000
    assert '0' * 15000 not in text
    image_output = next(item['output'] for item in outputs if item['call_id'] == 'image-child')
    assert isinstance(image_output, list)
    assert len(image_output) == 1
    assert image_output[0]['type'] == 'input_image'
    assert image_output[0]['detail'] == 'high'
    for record in records:
        assert record['model'] == 'gpt-5.2'
        view = next(tool for tool in record['tools'] if tool.get('name') == 'view_image')
        assert 'detail' not in view['parameters']['properties']


def test_empty_depth_filtered_catalog_stays_empty_on_lazy_rebuild(owner):
    from copy import deepcopy
    from breadboard_engine.agent_llm_openai import OpenAIConductor

    config = deepcopy(owner.config)
    config["tools"]["registry"]["include"] = ["multi_agent_v1", "tool_search"]
    cls = OpenAIConductor.__ray_metadata__.modified_class
    conductor = cls(workspace=owner.workspace, image=owner.image, config=config, local_mode=True)
    assert conductor._setup_native_tools("gpt-5.5", True) is True
    assert {tool.name for tool in conductor.current_native_tools} == {"multi_agent_v1", "tool_search"}
    conductor._codex_agent_depth = 1
    for _ in range(2):  # Initial setup and the receiving-side lazy rebuild.
        assert conductor._setup_native_tools("gpt-5.5", True) is False
        assert conductor.current_native_tools == []
    assert conductor.config["tools"]["registry"]["include"] == ["multi_agent_v1", "tool_search"]
    conductor.config["tools"].pop("native_semantics")
    # The selected driver is immutable for an admitted conductor lifetime.
    assert conductor._setup_native_tools("gpt-5.5", True) is False
    assert conductor.current_native_tools == []
