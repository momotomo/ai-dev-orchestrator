from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _bridge_common import BridgeError, resolve_execution_agent  # noqa: E402
import bridge_orchestrator  # noqa: E402


# ---------------------------------------------------------------------------
# resolve_execution_agent helper tests
# ---------------------------------------------------------------------------


class ResolveExecutionAgentTests(unittest.TestCase):
    def test_codex_is_valid(self) -> None:
        self.assertEqual(resolve_execution_agent({"execution_agent": "codex"}), "codex")

    def test_github_copilot_is_valid(self) -> None:
        self.assertEqual(
            resolve_execution_agent({"execution_agent": "github_copilot"}),
            "github_copilot",
        )

    def test_strips_surrounding_whitespace(self) -> None:
        self.assertEqual(resolve_execution_agent({"execution_agent": "  codex  "}), "codex")

    def test_invalid_value_raises_bridge_error(self) -> None:
        with self.assertRaises(BridgeError) as ctx:
            resolve_execution_agent({"execution_agent": "openai"})
        self.assertIn("openai", str(ctx.exception))

    def test_empty_string_raises_bridge_error(self) -> None:
        with self.assertRaises(BridgeError):
            resolve_execution_agent({"execution_agent": ""})

    def test_missing_key_raises_bridge_error(self) -> None:
        with self.assertRaises(BridgeError):
            resolve_execution_agent({})

    def test_non_string_raises_bridge_error(self) -> None:
        with self.assertRaises(BridgeError):
            resolve_execution_agent({"execution_agent": 42})


# ---------------------------------------------------------------------------
# bridge_orchestrator.run() provider routing tests
# ---------------------------------------------------------------------------

# Minimal state that keeps the bridge in a known "idle / pending request" path
# so we don't accidentally trigger real Safari / GitHub API calls.
_IDLE_STATE: dict[str, object] = {
    "mode": "idle",
    "need_chatgpt_prompt": False,
    "need_chatgpt_next": False,
    "need_codex_run": False,
}


def _make_minimal_project_config(execution_agent: str = "codex") -> dict[str, object]:
    return {
        "project_name": "test-project",
        "bridge_runtime_root": ".",
        "worker_repo_path": "/tmp/test-repo",
        "worker_repo_marker_mode": "strict",
        "worker_repo_markers": [],
        "github_repository": "",
        "github_project_url": "",
        "github_project_state_field_name": "State",
        "github_project_default_issue_state": "",
        "github_project_in_progress_state": "in_progress",
        "github_project_review_state": "review",
        "github_project_done_state": "done",
        "execution_agent": execution_agent,
        "codex_bin": "codex",
        "codex_model": "",
        "codex_sandbox": "",
        "codex_timeout_seconds": 7200,
        "report_request_next_todo": "next todo",
        "report_request_open_questions": "open questions",
        "_project_config_warnings": [],
    }


class ExecutionAgentRoutingTests(unittest.TestCase):
    """Tests that bridge_orchestrator.run() routes based on execution_agent."""

    def _run_with_agent(self, agent: str) -> tuple[int, str]:
        config = _make_minimal_project_config(agent)
        buf = io.StringIO()
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.print_project_config_warnings"),
            redirect_stdout(buf),
        ):
            result = bridge_orchestrator.run(
                dict(_IDLE_STATE),
                argv=["--execution-agent", agent],
            )
        return result, buf.getvalue()

    def test_codex_agent_proceeds_to_dispatch(self) -> None:
        # With codex agent and idle state (no pending actions), run() should
        # reach the normal-path dispatch and return 0 without hitting the stub.
        config = _make_minimal_project_config("codex")
        buf = io.StringIO()
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.print_project_config_warnings"),
            patch("bridge_orchestrator.resolve_runtime_dispatch_plan") as mock_plan,
            patch("bridge_orchestrator.resolve_unified_next_action", return_value="noop"),
            patch("bridge_orchestrator.present_bridge_status") as mock_status,
            redirect_stdout(buf),
        ):
            from _bridge_common import BridgeStatusView
            mock_status.return_value = BridgeStatusView(label="テスト", detail="")
            mock_plan.return_value = type("Plan", (), {"next_action": "noop", "note": "test note"})()
            result = bridge_orchestrator.run(
                dict(_IDLE_STATE),
                argv=["--execution-agent", "codex"],
            )
        output = buf.getvalue()
        self.assertEqual(result, 0)
        # Must NOT contain the github_copilot stub message
        self.assertNotIn("github_copilot execution: not yet implemented", output)

    def test_github_copilot_agent_hits_stub_and_returns_zero(self) -> None:
        result, output = self._run_with_agent("github_copilot")
        self.assertEqual(result, 0)
        self.assertIn("github_copilot execution: not yet implemented", output)

    def test_github_copilot_stub_does_not_reach_codex_launch(self) -> None:
        with patch("bridge_orchestrator.launch_codex_once") as mock_launch:
            self._run_with_agent("github_copilot")
        mock_launch.run.assert_not_called()

    def test_invalid_agent_via_cli_raises_bridge_error(self) -> None:
        config = _make_minimal_project_config("codex")
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            with self.assertRaises(BridgeError):
                bridge_orchestrator.run(
                    dict(_IDLE_STATE),
                    argv=["--execution-agent", "invalid_agent"],
                )


if __name__ == "__main__":
    unittest.main()
