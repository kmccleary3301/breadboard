import uuid
from storage import JSONStorage


def test_json_storage_roundtrip(tmp_path):
    root = tmp_path / f"store-{uuid.uuid4()}"
    s = JSONStorage(str(root))
    s.write_json("session/info/a.json", {"x": 1})
    obj = s.read_json("session/info/a.json")
    assert obj["x"] == 1
    files = s.list("session")
    assert any(p.endswith("a.json") for p in files)


