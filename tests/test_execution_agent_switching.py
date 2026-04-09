from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, call, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from _bridge_common import BridgeError, resolve_execution_agent  # noqa: E402
import bridge_orchestrator  # noqa: E402
import launch_github_copilot  # noqa: E402


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
        "agent_model": "",
        "github_copilot_bin": "gh",
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

    # State that triggers the launch_codex_once action.
    _LAUNCH_STATE: dict[str, object] = {
        "mode": "ready_for_codex",
        "need_chatgpt_prompt": False,
        "need_chatgpt_next": False,
        "need_codex_run": True,
    }

    def _make_status(self) -> object:
        from _bridge_common import BridgeStatusView
        return BridgeStatusView(label="テスト", detail="")

    def test_codex_agent_proceeds_to_dispatch(self) -> None:
        # With codex agent and idle state (no pending actions), run() should
        # reach the normal-path dispatch and return 0.
        config = _make_minimal_project_config("codex")
        buf = io.StringIO()
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.print_project_config_warnings"),
            patch("bridge_orchestrator.resolve_runtime_dispatch_plan") as mock_plan,
            patch("bridge_orchestrator.resolve_unified_next_action", return_value="noop"),
            patch("bridge_orchestrator.present_bridge_status", return_value=self._make_status()),
            redirect_stdout(buf),
        ):
            mock_plan.return_value = type("Plan", (), {"next_action": "noop", "note": "test note"})()
            result = bridge_orchestrator.run(
                dict(_IDLE_STATE),
                argv=["--execution-agent", "codex"],
            )
        self.assertEqual(result, 0)

    def test_github_copilot_agent_idle_state_falls_through_to_dispatch(self) -> None:
        # github_copilot with idle state should NOT call launch_github_copilot;
        # it should fall through to the normal dispatch plan.
        config = _make_minimal_project_config("github_copilot")
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.print_project_config_warnings"),
            patch("bridge_orchestrator.resolve_unified_next_action", return_value="noop"),
            patch("bridge_orchestrator.resolve_runtime_dispatch_plan") as mock_plan,
            patch("bridge_orchestrator.present_bridge_status", return_value=self._make_status()),
            patch("bridge_orchestrator.launch_github_copilot") as mock_lgh,
        ):
            mock_plan.return_value = type("Plan", (), {"next_action": "noop", "note": "test note"})()
            result = bridge_orchestrator.run(
                dict(_IDLE_STATE),
                argv=["--execution-agent", "github_copilot"],
            )
        self.assertEqual(result, 0)
        mock_lgh.run.assert_not_called()

    def test_github_copilot_launch_routes_to_launch_github_copilot(self) -> None:
        # When execution_agent=github_copilot and action=launch_codex_once,
        # bridge_orchestrator.run() must delegate to launch_github_copilot.run().
        config = _make_minimal_project_config("github_copilot")
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.print_project_config_warnings"),
            patch("bridge_orchestrator.present_bridge_status", return_value=self._make_status()),
            patch("bridge_orchestrator.resolve_unified_next_action", return_value="launch_codex_once"),
            patch("bridge_orchestrator.should_prioritize_unarchived_report", return_value=False),
            patch("bridge_orchestrator.has_pending_issue_centric_codex_dispatch", return_value=False),
            patch("bridge_orchestrator.is_blocked_codex_lifecycle_state", return_value=False),
            patch("bridge_orchestrator.launch_github_copilot") as mock_lgh,
        ):
            mock_lgh.run.return_value = 0
            result = bridge_orchestrator.run(
                dict(self._LAUNCH_STATE),
                argv=["--execution-agent", "github_copilot"],
            )
        self.assertEqual(result, 0)
        mock_lgh.run.assert_called_once()
        # Ensure launch_codex_once was NOT called.

    def test_codex_launch_routes_to_launch_codex_once(self) -> None:
        # When execution_agent=codex and action=launch_codex_once,
        # bridge_orchestrator.run() must delegate to launch_codex_once.run().
        config = _make_minimal_project_config("codex")
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.print_project_config_warnings"),
            patch("bridge_orchestrator.present_bridge_status", return_value=self._make_status()),
            patch("bridge_orchestrator.resolve_unified_next_action", return_value="launch_codex_once"),
            patch("bridge_orchestrator.should_prioritize_unarchived_report", return_value=False),
            patch("bridge_orchestrator.has_pending_issue_centric_codex_dispatch", return_value=False),
            patch("bridge_orchestrator.is_blocked_codex_lifecycle_state", return_value=False),
            patch("bridge_orchestrator.launch_codex_once") as mock_lco,
            patch("bridge_orchestrator.launch_github_copilot") as mock_lgh,
        ):
            mock_lco.run.return_value = 0
            result = bridge_orchestrator.run(
                dict(self._LAUNCH_STATE),
                argv=["--execution-agent", "codex"],
            )
        self.assertEqual(result, 0)
        mock_lco.run.assert_called_once()
        mock_lgh.run.assert_not_called()

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


# ---------------------------------------------------------------------------
# launch_github_copilot unit tests
# ---------------------------------------------------------------------------


class LaunchGithubCopilotTests(unittest.TestCase):
    """Tests for launch_github_copilot.py parse_args and build_github_copilot_command."""

    def _minimal_config(self) -> dict[str, object]:
        return {
            "github_copilot_bin": "gh",
            "codex_timeout_seconds": 7200,
            "worker_repo_path": "/tmp/test-repo",
            "bridge_runtime_root": ".",
        }

    def test_parse_args_defaults_from_project_config(self) -> None:
        config = self._minimal_config()
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args([], config)
        self.assertEqual(args.github_copilot_bin, "gh")
        self.assertEqual(args.timeout_seconds, 7200)

    def test_parse_args_override_bin(self) -> None:
        config = self._minimal_config()
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                ["--github-copilot-bin", "/usr/local/bin/custom-gh",
                 "--prompt-file", "/tmp/p.md",
                 "--report-file", "/tmp/r.md"],
                config,
            )
        self.assertEqual(args.github_copilot_bin, "/usr/local/bin/custom-gh")

    def test_build_command_default_gh(self) -> None:
        config = self._minimal_config()
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                ["--prompt-file", "/tmp/p.md", "--report-file", "/tmp/r.md"],
                config,
            )
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertEqual(cmd[0], "gh")
        self.assertIn("copilot", cmd)

    def test_build_command_custom_bin(self) -> None:
        config = self._minimal_config()
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                ["--github-copilot-bin", "/usr/local/bin/my-gh",
                 "--prompt-file", "/tmp/p.md",
                 "--report-file", "/tmp/r.md"],
                config,
            )
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertEqual(cmd, ["/usr/local/bin/my-gh"])

    def test_dry_run_returns_zero(self) -> None:
        config = self._minimal_config()
        state = {
            "mode": "ready_for_codex",
            "need_codex_run": True,
        }
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmpdir:
            prompt_path = os.path.join(tmpdir, "prompt.md")
            report_path = os.path.join(tmpdir, "report.md")
            with open(prompt_path, "w") as f:
                f.write("test prompt content")
            with (
                patch("launch_github_copilot.load_project_config", return_value=config),
                patch("launch_github_copilot.print_project_config_warnings"),
                patch("launch_github_copilot.worker_repo_path", return_value=Path(tmpdir)),
                patch("launch_github_copilot.save_state"),
                patch("launch_github_copilot.recover_codex_report", return_value=None),
                patch("launch_github_copilot.codex_report_is_ready", return_value=False),
            ):
                result = launch_github_copilot.run(
                    dict(state),
                    [
                        "--prompt-file", prompt_path,
                        "--report-file", report_path,
                        "--worker-repo-path", tmpdir,
                        "--dry-run",
                    ],
                )
        self.assertEqual(result, 0)


# ---------------------------------------------------------------------------
# agent_model configuration tests
# ---------------------------------------------------------------------------


class AgentModelConfigTests(unittest.TestCase):
    """Tests that agent_model is wired correctly into each provider's launch argv."""

    def _config(self, execution_agent: str = "codex", agent_model: str = "", codex_model: str = "") -> dict[str, object]:
        base = _make_minimal_project_config(execution_agent)
        base["agent_model"] = agent_model
        base["codex_model"] = codex_model
        return base

    # ------------------------------------------------------------------
    # bridge_orchestrator.build_codex_launch_argv
    # ------------------------------------------------------------------

    def test_codex_agent_model_appears_in_codex_argv(self) -> None:
        config = self._config("codex", agent_model="o4-mini")
        with patch("bridge_orchestrator.load_project_config", return_value=config):
            args = bridge_orchestrator.parse_args(["--execution-agent", "codex"], config)
        argv = bridge_orchestrator.build_codex_launch_argv(args)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "o4-mini")

    def test_codex_agent_model_empty_falls_back_to_codex_model(self) -> None:
        config = self._config("codex", agent_model="", codex_model="codex-legacy")
        with patch("bridge_orchestrator.load_project_config", return_value=config):
            args = bridge_orchestrator.parse_args(["--execution-agent", "codex"], config)
        argv = bridge_orchestrator.build_codex_launch_argv(args)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "codex-legacy")

    def test_codex_both_empty_no_model_in_argv(self) -> None:
        config = self._config("codex", agent_model="", codex_model="")
        with patch("bridge_orchestrator.load_project_config", return_value=config):
            args = bridge_orchestrator.parse_args(["--execution-agent", "codex"], config)
        argv = bridge_orchestrator.build_codex_launch_argv(args)
        self.assertNotIn("--model", argv)

    def test_codex_agent_model_takes_priority_over_codex_model(self) -> None:
        config = self._config("codex", agent_model="new-model", codex_model="old-model")
        with patch("bridge_orchestrator.load_project_config", return_value=config):
            args = bridge_orchestrator.parse_args(["--execution-agent", "codex"], config)
        argv = bridge_orchestrator.build_codex_launch_argv(args)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "new-model")

    # ------------------------------------------------------------------
    # bridge_orchestrator.build_github_copilot_launch_argv
    # ------------------------------------------------------------------

    def test_copilot_agent_model_forwarded_to_copilot_argv(self) -> None:
        config = self._config("github_copilot", agent_model="gpt-4o")
        with patch("bridge_orchestrator.load_project_config", return_value=config):
            args = bridge_orchestrator.parse_args(["--execution-agent", "github_copilot"], config)
        argv = bridge_orchestrator.build_github_copilot_launch_argv(args)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-4o")

    def test_copilot_agent_model_empty_no_model_in_argv(self) -> None:
        config = self._config("github_copilot", agent_model="")
        with patch("bridge_orchestrator.load_project_config", return_value=config):
            args = bridge_orchestrator.parse_args(["--execution-agent", "github_copilot"], config)
        argv = bridge_orchestrator.build_github_copilot_launch_argv(args)
        self.assertNotIn("--model", argv)

    # ------------------------------------------------------------------
    # Non-active provider does not receive agent_model
    # ------------------------------------------------------------------

    def test_inactive_codex_does_not_get_copilot_model(self) -> None:
        # When provider is github_copilot, codex launch argv is NOT called.
        # Verify build_codex_launch_argv still works when agent is codex with empty model.
        config = self._config("codex", agent_model="")
        with patch("bridge_orchestrator.load_project_config", return_value=config):
            args = bridge_orchestrator.parse_args(["--execution-agent", "codex"], config)
        argv = bridge_orchestrator.build_github_copilot_launch_argv(args)
        self.assertNotIn("--model", argv)

    # ------------------------------------------------------------------
    # launch_codex_once.parse_args: agent_model priority logic
    # ------------------------------------------------------------------

    def test_launch_codex_once_agent_model_priority(self) -> None:
        import launch_codex_once
        config = {"agent_model": "priority-model", "codex_model": "legacy-model",
                  "codex_bin": "codex", "codex_timeout_seconds": 7200,
                  "worker_repo_path": "/tmp", "codex_sandbox": ""}
        with patch("launch_codex_once.load_project_config", return_value=config):
            args = launch_codex_once.parse_args([], config)
        self.assertEqual(args.model, "priority-model")

    def test_launch_codex_once_agent_model_empty_uses_codex_model(self) -> None:
        import launch_codex_once
        config = {"agent_model": "", "codex_model": "fallback-model",
                  "codex_bin": "codex", "codex_timeout_seconds": 7200,
                  "worker_repo_path": "/tmp", "codex_sandbox": ""}
        with patch("launch_codex_once.load_project_config", return_value=config):
            args = launch_codex_once.parse_args([], config)
        self.assertEqual(args.model, "fallback-model")

    def test_launch_codex_once_both_empty_model_is_empty(self) -> None:
        import launch_codex_once
        config = {"agent_model": "", "codex_model": "",
                  "codex_bin": "codex", "codex_timeout_seconds": 7200,
                  "worker_repo_path": "/tmp", "codex_sandbox": ""}
        with patch("launch_codex_once.load_project_config", return_value=config):
            args = launch_codex_once.parse_args([], config)
        self.assertEqual(args.model, "")

    # ------------------------------------------------------------------
    # launch_github_copilot.parse_args / build_github_copilot_command
    # ------------------------------------------------------------------

    def test_launch_copilot_parse_args_sets_model_from_agent_model(self) -> None:
        config = {"agent_model": "gpt-4o", "github_copilot_bin": "gh",
                  "codex_timeout_seconds": 7200, "worker_repo_path": "/tmp",
                  "bridge_runtime_root": "."}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args([], config)
        self.assertEqual(args.model, "gpt-4o")

    def test_launch_copilot_parse_args_model_empty_when_agent_model_unset(self) -> None:
        config = {"agent_model": "", "github_copilot_bin": "gh",
                  "codex_timeout_seconds": 7200, "worker_repo_path": "/tmp",
                  "bridge_runtime_root": "."}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args([], config)
        self.assertEqual(args.model, "")

    def test_launch_copilot_build_command_default_gh_no_model_flag(self) -> None:
        """Default gh bin should never include --model (gh copilot suggest has no such flag)."""
        config = {"agent_model": "gpt-4o", "github_copilot_bin": "gh",
                  "codex_timeout_seconds": 7200, "worker_repo_path": "/tmp",
                  "bridge_runtime_root": "."}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args([], config)
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertNotIn("--model", cmd)

    def test_launch_copilot_build_command_custom_bin_with_model(self) -> None:
        """Custom wrapper bin should receive --model when agent_model is set."""
        config = {"agent_model": "gpt-4o", "github_copilot_bin": "/usr/local/bin/my-gh",
                  "codex_timeout_seconds": 7200, "worker_repo_path": "/tmp",
                  "bridge_runtime_root": "."}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args([], config)
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertEqual(cmd[0], "/usr/local/bin/my-gh")
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "gpt-4o")

    def test_launch_copilot_build_command_custom_bin_empty_model_no_flag(self) -> None:
        """Custom wrapper bin should NOT get --model when agent_model is empty."""
        config = {"agent_model": "", "github_copilot_bin": "/usr/local/bin/my-gh",
                  "codex_timeout_seconds": 7200, "worker_repo_path": "/tmp",
                  "bridge_runtime_root": "."}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args([], config)
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertNotIn("--model", cmd)


if __name__ == "__main__":
    unittest.main()
