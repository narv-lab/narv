import os
import tempfile
from pathlib import Path
from src.actor.actor import Actor
import pytest

@pytest.fixture
def sandbox_env():
    with tempfile.TemporaryDirectory() as temp_dir:
        sandbox_root = Path(temp_dir)
        actor = Actor(sandbox_root=sandbox_root, allowed_commands=["echo", "ls"])
        yield actor, sandbox_root

def test_actor_get_capabilities(sandbox_env):
    actor, sandbox_root = sandbox_env
    caps = actor.get_capabilities()
    assert "FILE_WRITE" in caps["allowed_actions"]
    assert "echo" in caps["allowed_commands"]

def test_actor_file_write(sandbox_env):
    actor, sandbox_root = sandbox_env
    actions = [{
        "action_type": "FILE_WRITE",
        "params": {
            "FILE_WRITE_PARAMS": {
                "target_path": "test.txt",
                "content": "hello world"
            }
        }
    }]
    result = actor.execute_actions(actions)
    assert result.get("success") is True
    
    written_file = sandbox_root / "test.txt"
    assert written_file.exists()
    assert written_file.read_text() == "hello world"

def test_actor_file_write_path_traversal(sandbox_env):
    actor, sandbox_root = sandbox_env
    actions = [{
        "action_type": "FILE_WRITE",
        "params": {
            "FILE_WRITE_PARAMS": {
                "target_path": "../out_of_sandbox.txt",
                "content": "hacked"
            }
        }
    }]
    result = actor.execute_actions(actions)
    assert "EXECUTION_DENIED" in result.get("error_code")

def test_actor_command_exec(sandbox_env):
    actor, sandbox_root = sandbox_env
    actions = [{
        "action_type": "COMMAND_EXEC",
        "params": {
            "COMMAND_EXEC_PARAMS": {
                "command": "echo test"
            }
        }
    }]
    result = actor.execute_actions(actions)
    assert result.get("success") is True
    assert "test" in result.get("execution_result", "")

def test_actor_command_exec_not_allowed(sandbox_env):
    actor, sandbox_root = sandbox_env
    actions = [{
        "action_type": "COMMAND_EXEC",
        "params": {
            "COMMAND_EXEC_PARAMS": {
                "command": "rm -rf /"
            }
        }
    }]
    result = actor.execute_actions(actions)
    assert "EXECUTION_DENIED" in result.get("error_code")

def test_actor_notify(sandbox_env, capsys):
    actor, _ = sandbox_env
    actions = [{
        "action_type": "NOTIFY",
        "params": {
            "NOTIFY_PARAMS": {
                "recipient": "USER",
                "message": "Hello from tests"
            }
        }
    }]
    result = actor.execute_actions(actions)
    assert result.get("success") is True
    
    captured = capsys.readouterr()
    assert "[NOTIFY → USER] Hello from tests" in captured.out
