from pathlib import Path
from agentic_coder_prototype.monitoring.telemetry import TelemetryLogger

def test_telemetry_jsonl(tmp_path):
    outp = tmp_path / 'telemetry.jsonl'
    tl = TelemetryLogger(str(outp))
    tl.log({"event": "turn_start", "step": 1})
    tl.log({"event": "assistant_choice", "has_content": True})
    tl.close()
    lines = outp.read_text().splitlines()
    assert len(lines) == 2
    assert 'turn_start' in lines[0]


