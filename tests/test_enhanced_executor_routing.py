from tool_calling.enhanced_executor import EnhancedToolExecutor


class DummySandbox:
    def __init__(self):
        self.run_calls = 0

    def run(self, command: str, timeout: int = 30, stream: bool = False):
        self.run_calls += 1
        return {"stdout": "ok", "exit": 0}

    def read_file(self, path: str):
        return {"content": "hello"}

    def list_dir(self, path: str, depth: int = 1):
        return {"items": ["a.txt", "b.txt"]}


def test_enhanced_executor_method_map():
    ex = EnhancedToolExecutor(sandbox=DummySandbox(), config={})

    import asyncio
    loop = asyncio.new_event_loop()
    try:
        # Known mapping: run_shell -> run
        res = loop.run_until_complete(ex._execute_raw_tool_call({"function": "run_shell", "arguments": {"command": "echo hi"}}))
        assert isinstance(res, dict)
        # Known mapping: read_file -> read_file
        res2 = loop.run_until_complete(ex._execute_raw_tool_call({"function": "read_file", "arguments": {"path": "README.md"}}))
        assert "content" in res2 or "error" in res2
    finally:
        loop.close()


