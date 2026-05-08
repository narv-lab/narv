"""actor module

Executes actions on the environment (file writing, command execution, notification).
- Synchronously executes the passed array of ActionCommands in order from index 0.
- Fail-fast: Aborts immediately on IO exceptions or non-zero exit codes.
- FILE_WRITE: Validates path within sandbox (prevents path traversal).
- COMMAND_EXEC: Checks against allowed_commands list & enforces timeout.
- NOTIFY: Notification via standard output.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from src.core.config import config
from src.core.logger import setup_logger

logger = setup_logger("actor")


# -------------------------------------------------------------------------
# Actor Class
# -------------------------------------------------------------------------
class Actor:
    """Action execution class. Implements the action_exec_v1 interface."""

    def __init__(
        self,
        sandbox_root: Optional[Path] = None,
        allowed_commands: Optional[list[str]] = None,
    ) -> None:
        self._sandbox_root: Path = sandbox_root or Path(config.actor_sandbox_root)
        self._allowed_commands: list[str] = allowed_commands or config.actor_allowed_commands
        # Pre-create sandbox directory
        self._sandbox_root.mkdir(parents=True, exist_ok=True)
        logger.info(
            "Actor initialized. sandbox_root=%s allowed_commands=%s",
            self._sandbox_root, self._allowed_commands,
        )

    def get_capabilities(self) -> dict:
        """Returns the list of action types and allowed commands currently supported by the Actor."""
        return {
            "allowed_actions": [
                "NOTIFY",
                "FILE_WRITE",
                "COMMAND_EXEC"
            ],
            "action_schemas": {
                "NOTIFY": {
                    "params": {
                        "NOTIFY_PARAMS": {
                            "recipient": "SYSTEM|USER|LOG",
                            "message": "string (The message content)"
                        }
                    }
                },
                "FILE_WRITE": {
                    "params": {
                        "FILE_WRITE_PARAMS": {
                            "target_path": "string (File path relative to sandbox_root)",
                            "content": "string (File contents to write)"
                        }
                    }
                },
                "COMMAND_EXEC": {
                    "params": {
                        "COMMAND_EXEC_PARAMS": {
                            "command": "string (Must start with one of allowed_commands)",
                            "timeout_ms": "integer (Optional, default is 30000)"
                        }
                    }
                }
            },
            "allowed_commands": self._allowed_commands,
            "sandbox_root": str(self._sandbox_root)
        }

    # ------------------------------------------------------------------
    # Internal: Handlers for each action type
    # ------------------------------------------------------------------
    def _execute_file_write(self, params: dict) -> str:
        """Executes the FILE_WRITE action."""
        fw_params = params.get("FILE_WRITE_PARAMS", {})
        raw_path: str = fw_params.get("target_path") or params.get("target_path", "")
        content: str = fw_params.get("content") or params.get("content", "")

        if not raw_path:
            raise ValueError("FILE_WRITE_PARAMS.target_path is required")

        # Prevent path traversal: Ensure path resolves within the sandbox
        target = (self._sandbox_root / raw_path).resolve()
        try:
            target.relative_to(self._sandbox_root.resolve())
        except ValueError:
            raise PermissionError(
                f"EXECUTION_DENIED: Path traversal detected. target_path='{raw_path}' "
                f"must be under sandbox_root='{self._sandbox_root}'"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        logger.info("FILE_WRITE: wrote %d bytes to %s", len(content), target)
        return f"FILE_WRITE: success. path={target}, bytes={len(content)}"

    def _execute_command_exec(self, params: dict) -> str:
        """Executes the COMMAND_EXEC action."""
        exec_params = params.get("COMMAND_EXEC_PARAMS", {})
        command: str = exec_params.get("command") or params.get("command", "")
        timeout_val = exec_params.get("timeout_ms") or params.get("timeout_ms", config.actor_timeout_ms)
        timeout_ms: int = int(timeout_val)

        if not command:
            raise ValueError("COMMAND_EXEC_PARAMS.command is required")

        # Command whitelist check
        cmd_name = command.split()[0]
        if cmd_name not in self._allowed_commands:
            raise PermissionError(
                f"EXECUTION_DENIED: command '{cmd_name}' is not in allowed_commands={self._allowed_commands}"
            )

        timeout_sec = timeout_ms / 1000.0
        logger.info("COMMAND_EXEC: running '%s' (timeout=%.1fs)", command, timeout_sec)

        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(self._sandbox_root),
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"EXECUTION_FAILED: command exited with code {result.returncode}. "
                f"stderr={result.stderr.strip()}"
            )

        output = result.stdout.strip()
        logger.info("COMMAND_EXEC: success. returncode=0 output_len=%d", len(output))
        return f"COMMAND_EXEC: success. output={output[:500]}"

    def _execute_notify(self, params: dict) -> str:
        """Executes the NOTIFY action (via standard output)."""
        notify_params = params.get("NOTIFY_PARAMS", {})
        recipient: str = notify_params.get("recipient") or params.get("recipient", "SYSTEM")
        message: str = notify_params.get("message") or params.get("message", "")

        if not message:
            raise ValueError("NOTIFY_PARAMS.message is required")

        # Validate valid recipients
        valid_recipients = {"SYSTEM", "USER", "LOG"}
        if recipient not in valid_recipients:
            raise ValueError(
                f"EXECUTION_DENIED: recipient='{recipient}' is not a valid recipient. "
                f"Valid: {valid_recipients}"
            )

        notification = f"[NOTIFY → {recipient}] {message}"
        print(notification, file=sys.stdout, flush=True)
        logger.info("NOTIFY: recipient=%s message_len=%d", recipient, len(message))
        return f"NOTIFY: success. recipient={recipient}"

    # ------------------------------------------------------------------
    # Public API (action_exec_v1)
    # ------------------------------------------------------------------
    def execute_actions(
        self,
        actions: list[dict],
        caller_id: Optional[str] = None,
    ) -> dict:
        """Executes a list of ActionCommands synchronously in index order with Fail-fast (execute_actions operation).

        Args:
            actions: Array of ActionCommands
            caller_id: Caller identifier injected by the kernel

        Returns:
            { success: bool, execution_result: str }
        Raises:
            RuntimeError: EXECUTION_DENIED | EXECUTION_FAILED (Returns immediately on Fail-fast)
        """
        execution_results: list[str] = []

        for idx, action in enumerate(actions):
            action_type: str = action.get("action_type", "")
            params: dict = action.get("params", {})

            logger.debug(
                "execute_actions caller_id=%s idx=%d action_type=%s",
                caller_id, idx, action_type,
            )

            try:
                if action_type == "FILE_WRITE":
                    result_msg = self._execute_file_write(params)
                elif action_type == "COMMAND_EXEC":
                    result_msg = self._execute_command_exec(params)
                elif action_type == "NOTIFY":
                    result_msg = self._execute_notify(params)
                else:
                    raise ValueError(
                        f"EXECUTION_FAILED: Unknown action_type='{action_type}'"
                    )
                execution_results.append(result_msg)

            except (PermissionError, ValueError, RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
                # Fail-fast: No subsequent actions are executed
                error_msg = str(exc)
                logger.error(
                    "FAIL-FAST at idx=%d action_type=%s error=%s", idx, action_type, error_msg
                )
                # L2 error_schema compliance: return error_code as a machine-parsable field
                if "EXECUTION_DENIED" in error_msg:
                    error_code = "EXECUTION_DENIED"
                else:
                    error_code = "EXECUTION_FAILED"
                return {
                    "error_code": error_code,
                    "message": f"FAILED at action[{idx}] ({action_type}): {error_msg}",
                }

        summary = "; ".join(execution_results) if execution_results else "No actions executed"
        return {"success": True, "execution_result": summary}
