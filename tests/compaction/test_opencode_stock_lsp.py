from pathlib import Path

import pytest
from tests.compaction.test_opencode_stock_tools import binary, response
from tests.compaction.test_opencode_stock_omo_groups import omo, direct_omo


@pytest.mark.parametrize("filename,content,code", [
    ("sample.ts", "export const count: number = 'incorrect';\n", "2322"),
    ("sample.py", "value: int = 'incorrect'\n", "reportAssignmentType"),
])
def test_staged_language_diagnostics_match_direct_stock(omo, binary, tmp_path, filename, content, code):
    (Path(omo.workspace) / filename).write_text(content)
    call = ("call_staged_diagnostics", "lsp_diagnostics", {"filePath": filename, "severity": "error"})
    omo.user([{"type": "text", "text": "Diagnose the type error with the stock server."}])
    omo.submit(response(omo, [call]))
    actual = omo.result(call[0])["__mvi_text_output"]
    assert code in actual and "assign" in actual.lower()
    omo.submit(response(omo))
    expected = direct_omo(binary, omo.workspace, tmp_path / "oracle", [call])
    assert actual == expected[call[0]]
