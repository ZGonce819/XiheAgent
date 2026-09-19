from types import SimpleNamespace

from agent.middleware.tool_error import tool_error_result


def test_tool_error_result_supports_remote_sandbox_backend() -> None:
    backend = SimpleNamespace(sandbox_id="sandbox-1")

    payload = tool_error_result(
        FileNotFoundError("missing.md"),
        tool_name="read_file",
        backend=backend,
    )

    assert payload["workspace"] == "/workspace（远程沙盒 sandbox-1）"
    assert payload["error_type"] == "FileNotFoundError"
