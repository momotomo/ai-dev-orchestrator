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
        self.assertEqual(cmd[0], "/usr/local/bin/my-gh")
        # --report-file is now forwarded to custom bins
        self.assertIn("--report-file", cmd)
        self.assertEqual(cmd[cmd.index("--report-file") + 1], "/tmp/r.md")

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

    def test_launch_copilot_build_command_bin_with_inline_exec_is_split(self) -> None:
        """github_copilot_bin with inline --exec is split so wrapper receives --exec.

        Regression: build_github_copilot_command used [bin_path] (single element) which
        treated the whole string as a command name, making inline --exec impossible.
        Now shlex.split is used so "wrapper.py --exec /provider" is split correctly.
        """
        config = {
            "agent_model": "sonnet-4.6",
            "github_copilot_bin": "/path/to/github_copilot_wrapper.py --exec /usr/local/bin/my-provider",
            "codex_timeout_seconds": 7200,
            "worker_repo_path": "/tmp",
            "bridge_runtime_root": ".",
        }
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                ["--prompt-file", "/tmp/p.md", "--report-file", "/tmp/r.md"],
                config,
            )
        cmd = launch_github_copilot.build_github_copilot_command(args)
        # First element: the wrapper binary
        self.assertEqual(cmd[0], "/path/to/github_copilot_wrapper.py")
        # Second+third: the inline --exec that was embedded in github_copilot_bin
        self.assertIn("--exec", cmd)
        self.assertEqual(cmd[cmd.index("--exec") + 1], "/usr/local/bin/my-provider")
        # model and report-file are also forwarded
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet-4.6")
        self.assertIn("--report-file", cmd)


# ---------------------------------------------------------------------------
# github_copilot_wrapper unit tests
# ---------------------------------------------------------------------------


class GithubCopilotWrapperTests(unittest.TestCase):
    """Tests for scripts/github_copilot_wrapper.py."""

    def setUp(self) -> None:
        import github_copilot_wrapper
        self.wrapper = github_copilot_wrapper

    # ------------------------------------------------------------------
    # parse_args
    # ------------------------------------------------------------------

    def test_parse_args_defaults(self) -> None:
        args = self.wrapper.parse_args([])
        self.assertEqual(args.model, "")
        self.assertEqual(args.exec, "")

    def test_parse_args_autopilot_default_is_false(self) -> None:
        args = self.wrapper.parse_args([])
        self.assertFalse(args.autopilot)

    def test_parse_args_accepts_autopilot(self) -> None:
        args = self.wrapper.parse_args(["--autopilot"])
        self.assertTrue(args.autopilot)

    def test_parse_args_accepts_reasoning_effort(self) -> None:
        args = self.wrapper.parse_args(["--reasoning-effort", "high"])
        self.assertEqual(args.reasoning_effort, "high")

    def test_parse_args_reasoning_effort_default_is_empty(self) -> None:
        args = self.wrapper.parse_args([])
        self.assertEqual(args.reasoning_effort, "")

    def test_parse_args_accepts_model(self) -> None:
        args = self.wrapper.parse_args(["--model", "sonnet-4.6"])
        self.assertEqual(args.model, "sonnet-4.6")

    def test_parse_args_accepts_exec(self) -> None:
        args = self.wrapper.parse_args(["--exec", "/usr/local/bin/my-provider"])
        self.assertEqual(args.exec, "/usr/local/bin/my-provider")

    def test_parse_args_model_and_exec_together(self) -> None:
        args = self.wrapper.parse_args(["--model", "sonnet-4.6", "--exec", "/usr/local/bin/p"])
        self.assertEqual(args.model, "sonnet-4.6")
        self.assertEqual(args.exec, "/usr/local/bin/p")

    # ------------------------------------------------------------------
    # build_command
    # ------------------------------------------------------------------

    def test_build_command_no_exec_no_model_is_gh_default(self) -> None:
        """No --exec, no model → fall back to gh copilot suggest."""
        args = self.wrapper.parse_args([])
        cmd = self.wrapper.build_command(args)
        self.assertEqual(cmd[0], "gh")
        self.assertIn("copilot", cmd)
        self.assertNotIn("--model", cmd)

    def test_build_command_no_exec_with_model_still_gh_default(self) -> None:
        """No --exec even with --model → gh copilot suggest (model not forwarded)."""
        args = self.wrapper.parse_args(["--model", "sonnet-4.6"])
        cmd = self.wrapper.build_command(args)
        self.assertEqual(cmd[0], "gh")
        self.assertNotIn("--model", cmd)

    def test_build_command_exec_with_model_forwards_model(self) -> None:
        """With --exec and --model, the model is forwarded."""
        args = self.wrapper.parse_args([
            "--model", "sonnet-4.6",
            "--exec", "/usr/local/bin/my-provider",
        ])
        cmd = self.wrapper.build_command(args)
        self.assertEqual(cmd[0], "/usr/local/bin/my-provider")
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet-4.6")

    def test_build_command_exec_without_model_no_model_flag(self) -> None:
        """With --exec but no --model, --model flag is omitted."""
        args = self.wrapper.parse_args(["--exec", "/usr/local/bin/my-provider"])
        cmd = self.wrapper.build_command(args)
        self.assertEqual(cmd[0], "/usr/local/bin/my-provider")
        self.assertNotIn("--model", cmd)

    # ------------------------------------------------------------------
    # build_copilot_cli_command
    # ------------------------------------------------------------------

    def test_build_copilot_cli_command_basic(self) -> None:
        """build_copilot_cli_command() produces copilot ... -p <prompt> -s --allow-all-tools."""
        args = self.wrapper.parse_args([])
        cmd = self.wrapper.build_copilot_cli_command("Do something.", args)
        self.assertEqual(cmd[0], "copilot")
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[cmd.index("-p") + 1], "Do something.")
        self.assertIn("-s", cmd)
        self.assertIn("--allow-all-tools", cmd)

    def test_build_copilot_cli_command_with_model(self) -> None:
        """build_copilot_cli_command() forwards --model."""
        args = self.wrapper.parse_args(["--model", "claude-sonnet-4.6"])
        cmd = self.wrapper.build_copilot_cli_command("prompt", args)
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-sonnet-4.6")

    def test_build_copilot_cli_command_with_autopilot(self) -> None:
        """build_copilot_cli_command() includes --autopilot when set."""
        args = self.wrapper.parse_args(["--autopilot"])
        cmd = self.wrapper.build_copilot_cli_command("prompt", args)
        self.assertIn("--autopilot", cmd)

    def test_build_copilot_cli_command_without_autopilot_no_flag(self) -> None:
        """build_copilot_cli_command() omits --autopilot by default."""
        args = self.wrapper.parse_args([])
        cmd = self.wrapper.build_copilot_cli_command("prompt", args)
        self.assertNotIn("--autopilot", cmd)

    def test_build_copilot_cli_command_with_reasoning_effort(self) -> None:
        """build_copilot_cli_command() forwards --reasoning-effort."""
        args = self.wrapper.parse_args(["--reasoning-effort", "high"])
        cmd = self.wrapper.build_copilot_cli_command("prompt", args)
        self.assertIn("--reasoning-effort", cmd)
        self.assertEqual(cmd[cmd.index("--reasoning-effort") + 1], "high")

    # ------------------------------------------------------------------
    # run() integration: subprocess is mocked
    # ------------------------------------------------------------------

    def test_run_default_gh_sets_copilot_model_env_and_warns(self) -> None:
        """When model is set but no --exec, COPILOT_MODEL env is set and a note is printed."""
        import io
        with patch.object(self.wrapper.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            buf = io.StringIO()
            with patch("sys.stdin", io.StringIO("test prompt")):
                with patch("sys.stderr", buf):
                    ret = self.wrapper.run(["--model", "sonnet-4.6"])
        self.assertEqual(ret, 0)
        call_kwargs = mock_run.call_args
        env_used = call_kwargs[1]["env"] if isinstance(call_kwargs[1], dict) else call_kwargs.kwargs["env"]
        self.assertEqual(env_used.get("COPILOT_MODEL"), "sonnet-4.6")
        self.assertIn("NOTE", buf.getvalue())

    def test_run_exec_path_forwards_model_no_warning(self) -> None:
        """With --exec, model is forwarded to provider; no 'NOTE' warning emitted."""
        import io
        with patch.object(self.wrapper.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            buf = io.StringIO()
            with patch("sys.stdin", io.StringIO("prompt text")):
                with patch("sys.stderr", buf):
                    ret = self.wrapper.run([
                        "--model", "sonnet-4.6",
                        "--exec", "/usr/local/bin/custom",
                    ])
        self.assertEqual(ret, 0)
        call_args = mock_run.call_args
        cmd_used = call_args[0][0] if call_args[0] else call_args.args[0]
        self.assertEqual(cmd_used[0], "/usr/local/bin/custom")
        self.assertIn("--model", cmd_used)
        self.assertNotIn("NOTE", buf.getvalue())

    def test_run_returns_127_on_command_not_found(self) -> None:
        """FileNotFoundError → exit code 127 (command not found)."""
        with patch.object(self.wrapper.subprocess, "run", side_effect=FileNotFoundError("not found")):
            with patch("sys.stdin", __import__("io").StringIO("p")):
                ret = self.wrapper.run(["--exec", "/no/such/binary"])
        self.assertEqual(ret, 127)

    # ------------------------------------------------------------------
    # --report-file argument
    # ------------------------------------------------------------------

    def test_parse_args_accepts_report_file(self) -> None:
        args = self.wrapper.parse_args(["--report-file", "/tmp/report.md"])
        self.assertEqual(args.report_file, "/tmp/report.md")

    def test_parse_args_report_file_default_is_empty(self) -> None:
        args = self.wrapper.parse_args([])
        self.assertEqual(args.report_file, "")

    # ------------------------------------------------------------------
    # build_bridge_report
    # ------------------------------------------------------------------

    def test_build_bridge_report_contains_bridge_summary(self) -> None:
        report = self.wrapper.build_bridge_report(
            "Provider reply text.\n", model="sonnet-4.6", exec_cmd="/usr/bin/provider"
        )
        self.assertIn("===BRIDGE_SUMMARY===", report)
        self.assertIn("===END_BRIDGE_SUMMARY===", report)
        self.assertIn("result: completed", report)
        self.assertIn("Provider reply text.", report)
        self.assertIn("sonnet-4.6", report)

    def test_build_bridge_report_no_model_no_exec_is_valid(self) -> None:
        report = self.wrapper.build_bridge_report("Output.")
        self.assertIn("===BRIDGE_SUMMARY===", report)
        self.assertIn("result: completed", report)
        self.assertIn("Output.", report)

    # ------------------------------------------------------------------
    # run() with --report-file: capture + write bridge report
    # ------------------------------------------------------------------

    def test_run_with_report_file_writes_bridge_report_on_success(self) -> None:
        """--report-file + provider exit 0 + stdout → bridge report written."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            with patch.object(self.wrapper.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stdout="Provider response.\n",
                    stderr="",
                )
                with patch("sys.stdin", io.StringIO("prompt")):
                    ret = self.wrapper.run([
                        "--model", "sonnet-4.6",
                        "--exec", "/usr/bin/provider",
                        "--report-file", str(report_path),
                    ])
            self.assertEqual(ret, 0)
            self.assertTrue(report_path.exists())
            text = report_path.read_text(encoding="utf-8")
            self.assertIn("===BRIDGE_SUMMARY===", text)
            self.assertIn("result: completed", text)
            self.assertIn("Provider response.", text)

    def test_run_with_report_file_no_write_on_provider_failure(self) -> None:
        """--report-file + provider exit 1 → NO report written, returns 1."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            with patch.object(self.wrapper.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
                with patch("sys.stdin", io.StringIO("prompt")):
                    ret = self.wrapper.run([
                        "--exec", "/usr/bin/provider",
                        "--report-file", str(report_path),
                    ])
            self.assertEqual(ret, 1)
            self.assertFalse(report_path.exists())

    def test_run_with_report_file_returns_1_on_empty_provider_stdout(self) -> None:
        """--report-file + provider exit 0 + empty stdout → returns 1 (no content = no report)."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "report.md"
            with patch.object(self.wrapper.subprocess, "run") as mock_run:
                mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
                with patch("sys.stdin", io.StringIO("prompt")):
                    ret = self.wrapper.run([
                        "--exec", "/usr/bin/provider",
                        "--report-file", str(report_path),
                    ])
            self.assertEqual(ret, 1)
            self.assertFalse(report_path.exists())

    def test_run_without_report_file_is_transparent_passthrough(self) -> None:
        """Without --report-file, behavior is transparent passthrough (unchanged)."""
        with patch.object(self.wrapper.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("sys.stdin", io.StringIO("prompt")):
                ret = self.wrapper.run(["--exec", "/usr/bin/provider"])
        self.assertEqual(ret, 0)
        call_kwargs = mock_run.call_args.kwargs if hasattr(mock_run.call_args, "kwargs") else mock_run.call_args[1]
        self.assertFalse(call_kwargs.get("capture_output", True))

    # ------------------------------------------------------------------
    # Regression: --report-file without --exec must fail immediately
    # (not try gh copilot suggest, which requires extension + produces no report)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Regression removed: --report-file without --exec now uses copilot CLI directly
    # (old: "requires --exec"; new: copilot binary is called with -p <prompt>)
    # ------------------------------------------------------------------

    def test_run_with_report_file_no_exec_calls_copilot_cli_and_writes_report(self) -> None:
        """--report-file + no --exec → copilot CLI called directly, bridge report written."""
        import tempfile, io as _io
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            mock_result = MagicMock(returncode=0, stdout="Issue #15 was completed.\nNow updating docs.\n", stderr="")
            stdout_buf = _io.StringIO()
            with (
                unittest.mock.patch("sys.stdin", _io.StringIO("test prompt")),
                unittest.mock.patch("sys.stdout", stdout_buf),
                unittest.mock.patch.object(self.wrapper.subprocess, "run", return_value=mock_result) as mock_run,
            ):
                ret = self.wrapper.run([
                    "--model", "claude-sonnet-4.6",
                    "--report-file", str(report_path),
                    # NOTE: no --exec → copilot CLI path
                ])
            self.assertEqual(ret, 0)
            self.assertTrue(report_path.exists(), "bridge report が生成されるべき")
            text = report_path.read_text(encoding="utf-8")
            self.assertIn("===BRIDGE_SUMMARY===", text)
            self.assertIn("===END_BRIDGE_SUMMARY===", text)
            self.assertIn("result: completed", text)
            # command starts with "copilot"
            cmd_used = mock_run.call_args.args[0]
            self.assertEqual(cmd_used[0], "copilot")
            self.assertIn("--model", cmd_used)
            self.assertIn("-p", cmd_used)
            self.assertIn("-s", cmd_used)
            self.assertIn("--allow-all-tools", cmd_used)

    def test_run_with_report_file_no_exec_planning_notes_synthesize_report(self) -> None:
        """--report-file + no --exec + planning-notes-only stdout → report synthesized."""
        import tempfile, io as _io
        planning_notes = (
            "Issue #15 was previously completed. Let me check the current state.\n"
            "Now let me check foundation-note.md...\n"
            "Now update foundation-note.md with the current progress.\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            mock_result = MagicMock(returncode=0, stdout=planning_notes, stderr="")
            with (
                unittest.mock.patch("sys.stdin", _io.StringIO("test prompt")),
                unittest.mock.patch("sys.stdout", _io.StringIO()),
                unittest.mock.patch.object(self.wrapper.subprocess, "run", return_value=mock_result),
            ):
                ret = self.wrapper.run([
                    "--report-file", str(report_path),
                ])
            self.assertEqual(ret, 0)
            self.assertTrue(report_path.exists())
            text = report_path.read_text(encoding="utf-8")
            self.assertIn("===BRIDGE_SUMMARY===", text)
            # planning notes included in Provider Output section
            self.assertIn("Issue #15 was previously completed", text)

    def test_run_with_report_file_no_exec_copilot_failure_no_report(self) -> None:
        """--report-file + no --exec + copilot exit non-0 → no report written, returns non-0."""
        import tempfile, io as _io
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            mock_result = MagicMock(returncode=1, stdout="", stderr="copilot error")
            with (
                unittest.mock.patch("sys.stdin", _io.StringIO("test prompt")),
                unittest.mock.patch("sys.stdout", _io.StringIO()),
                unittest.mock.patch.object(self.wrapper.subprocess, "run", return_value=mock_result),
            ):
                ret = self.wrapper.run([
                    "--report-file", str(report_path),
                ])
            self.assertNotEqual(ret, 0)
            self.assertFalse(report_path.exists())

    def test_run_with_report_file_no_exec_autopilot_forwarded(self) -> None:
        """--autopilot is forwarded to the copilot CLI command."""
        import tempfile, io as _io
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            mock_result = MagicMock(returncode=0, stdout="Done.\n", stderr="")
            with (
                unittest.mock.patch("sys.stdin", _io.StringIO("prompt")),
                unittest.mock.patch("sys.stdout", _io.StringIO()),
                unittest.mock.patch.object(self.wrapper.subprocess, "run", return_value=mock_result) as mock_run,
            ):
                ret = self.wrapper.run([
                    "--autopilot",
                    "--report-file", str(report_path),
                ])
            self.assertEqual(ret, 0)
            cmd_used = mock_run.call_args.args[0]
            self.assertIn("--autopilot", cmd_used)

    def test_run_with_report_file_no_exec_reasoning_effort_forwarded(self) -> None:
        """--reasoning-effort is forwarded to the copilot CLI command."""
        import tempfile, io as _io
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            mock_result = MagicMock(returncode=0, stdout="Done.\n", stderr="")
            with (
                unittest.mock.patch("sys.stdin", _io.StringIO("prompt")),
                unittest.mock.patch("sys.stdout", _io.StringIO()),
                unittest.mock.patch.object(self.wrapper.subprocess, "run", return_value=mock_result) as mock_run,
            ):
                ret = self.wrapper.run([
                    "--reasoning-effort", "high",
                    "--report-file", str(report_path),
                ])
            self.assertEqual(ret, 0)
            cmd_used = mock_run.call_args.args[0]
            self.assertIn("--reasoning-effort", cmd_used)
            self.assertEqual(cmd_used[cmd_used.index("--reasoning-effort") + 1], "high")

    def test_run_without_report_file_no_exec_still_uses_gh_default(self) -> None:
        """Without --report-file, no --exec still calls gh copilot suggest (unchanged)."""
        with patch.object(self.wrapper.subprocess, "run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            with patch("sys.stdin", io.StringIO("prompt")):
                ret = self.wrapper.run([])
        self.assertEqual(ret, 0)
        cmd_used = mock_run.call_args.args[0] if mock_run.call_args.args else mock_run.call_args[0][0]
        self.assertEqual(cmd_used[0], "gh")


# ---------------------------------------------------------------------------
# launch_github_copilot.run() report generation tests
# ---------------------------------------------------------------------------


class LaunchGithubCopilotReportGenerationTests(unittest.TestCase):
    """Verify launch_github_copilot.run() report detection / failure handling.

    Success path: the custom bin (wrapper) writes a bridge report to --report-file.
    launch_github_copilot.run() detects it via codex_report_is_ready() and returns 0.

    Key invariant — 「stdout だけで誤成功しない」:
    Raw stdout content alone must NOT create a report.  The wrapper must explicitly
    write to --report-file.  This prevents false-success when the provider emits
    output to stdout but the report file is absent.
    """

    def setUp(self) -> None:
        import tempfile
        self._tmpdir_ctx = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir_ctx.__enter__())
        self.prompt_path = self.tmpdir / "codex_prompt.md"
        self.report_path = self.tmpdir / "codex_report.md"
        self.prompt_path.write_text("# GitHub Copilot Prompt\n\nDo something.\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmpdir_ctx.__exit__(None, None, None)

    def _minimal_config(self) -> dict[str, object]:
        return {
            "github_copilot_bin": "/scripts/github_copilot_wrapper.py",
            "codex_timeout_seconds": 60,
            "worker_repo_path": str(self.tmpdir),
            "bridge_runtime_root": str(self.tmpdir),
        }

    # ------------------------------------------------------------------
    # build_github_copilot_command: --report-file forwarding
    # ------------------------------------------------------------------

    def test_build_command_passes_report_file_to_custom_bin(self) -> None:
        """For a non-'gh' custom bin, --report-file is appended to the command."""
        config = self._minimal_config()
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                [
                    "--github-copilot-bin", "/scripts/github_copilot_wrapper.py",
                    "--prompt-file", str(self.prompt_path),
                    "--report-file", str(self.report_path),
                ],
                config,
            )
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertIn("--report-file", cmd)
        self.assertEqual(cmd[cmd.index("--report-file") + 1], str(self.report_path))

    def test_build_command_passes_autopilot_to_wrapper(self) -> None:
        """For custom wrapper bin, --autopilot is forwarded."""
        config = {**self._minimal_config(), "github_copilot_autopilot": True}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                [
                    "--github-copilot-bin", "/scripts/github_copilot_wrapper.py",
                    "--prompt-file", str(self.prompt_path),
                    "--report-file", str(self.report_path),
                    "--autopilot",
                ],
                config,
            )
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertIn("--autopilot", cmd)

    def test_build_command_passes_reasoning_effort_to_wrapper(self) -> None:
        """For custom wrapper bin, --reasoning-effort is forwarded."""
        config = {**self._minimal_config(), "github_copilot_reasoning_effort": "high"}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                [
                    "--github-copilot-bin", "/scripts/github_copilot_wrapper.py",
                    "--prompt-file", str(self.prompt_path),
                    "--report-file", str(self.report_path),
                ],
                config,
            )
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertIn("--reasoning-effort", cmd)
        self.assertEqual(cmd[cmd.index("--reasoning-effort") + 1], "high")

    def test_build_command_does_not_add_report_file_for_gh_bin(self) -> None:
        """The default 'gh' bin must NOT receive --report-file."""
        config = {**self._minimal_config(), "github_copilot_bin": "gh"}
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                [
                    "--github-copilot-bin", "gh",
                    "--prompt-file", str(self.prompt_path),
                    "--report-file", str(self.report_path),
                ],
                config,
            )
        cmd = launch_github_copilot.build_github_copilot_command(args)
        self.assertEqual(cmd[0], "gh")
        self.assertNotIn("--report-file", cmd)

    # ------------------------------------------------------------------
    # run(): success path — wrapper writes report, launch detects it
    # ------------------------------------------------------------------

    def test_run_succeeds_when_wrapper_writes_report(self) -> None:
        """When the subprocess writes a bridge report to --report-file, run returns 0."""
        config = self._minimal_config()
        state = {"mode": "ready_for_codex", "need_codex_run": True}
        report_content = "===BRIDGE_SUMMARY===\n- result: completed\n===END_BRIDGE_SUMMARY===\nDone.\n"

        def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd):
            # Simulate wrapper writing bridge report to --report-file.
            if "--report-file" in cmd:
                rf_path = cmd[cmd.index("--report-file") + 1]
                Path(rf_path).write_text(report_content, encoding="utf-8")
            proc = MagicMock()
            proc.stdin = MagicMock()
            proc.poll.return_value = 0
            return proc

        with (
            patch("launch_github_copilot.load_project_config", return_value=config),
            patch("launch_github_copilot.print_project_config_warnings"),
            patch("launch_github_copilot.worker_repo_path", return_value=self.tmpdir),
            patch("launch_github_copilot.save_state"),
            patch("launch_github_copilot.runtime_logs_dir", return_value=self.tmpdir),
            patch("launch_github_copilot.recover_codex_report", return_value=None),
            patch("launch_github_copilot.subprocess.Popen", side_effect=fake_popen),
        ):
            rc = launch_github_copilot.run(
                dict(state),
                [
                    "--prompt-file", str(self.prompt_path),
                    "--report-file", str(self.report_path),
                    "--worker-repo-path", str(self.tmpdir),
                ],
            )
        self.assertEqual(rc, 0)
        self.assertTrue(self.report_path.exists())

    # ------------------------------------------------------------------
    # run(): failure — non-zero exit → BridgeError
    # ------------------------------------------------------------------

    def test_nonzero_exit_raises_bridge_error(self) -> None:
        """Provider exit non-0 → BridgeError with exit_code in message."""
        from _bridge_common import BridgeError
        config = self._minimal_config()
        state = {"mode": "ready_for_codex", "need_codex_run": True}

        def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd):
            proc = MagicMock()
            proc.stdin = MagicMock()
            proc.poll.return_value = 1
            return proc

        with (
            patch("launch_github_copilot.load_project_config", return_value=config),
            patch("launch_github_copilot.print_project_config_warnings"),
            patch("launch_github_copilot.worker_repo_path", return_value=self.tmpdir),
            patch("launch_github_copilot.save_state"),
            patch("launch_github_copilot.runtime_logs_dir", return_value=self.tmpdir),
            patch("launch_github_copilot.recover_codex_report", return_value=None),
            patch("launch_github_copilot.subprocess.Popen", side_effect=fake_popen),
        ):
            with self.assertRaises(BridgeError) as ctx:
                launch_github_copilot.run(
                    dict(state),
                    [
                        "--prompt-file", str(self.prompt_path),
                        "--report-file", str(self.report_path),
                        "--worker-repo-path", str(self.tmpdir),
                    ],
                )
        self.assertIn("exit_code=1", str(ctx.exception))

    # ------------------------------------------------------------------
    # run(): stdout alone does NOT create report (no 誤成功)
    # ------------------------------------------------------------------

    def test_stdout_alone_does_not_create_report(self) -> None:
        """Exit 0 + stdout content but no wrapper-written --report-file → BridgeError.

        Guards the 'stdout だけで誤成功しない' invariant: raw stdout must not become a report.
        """
        from _bridge_common import BridgeError, ready_codex_report_text
        config = self._minimal_config()
        state = {"mode": "ready_for_codex", "need_codex_run": True}

        def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd):
            # Write stdout but do NOT write to --report-file.
            stdout.write("Some provider output that looks like content.\n")
            proc = MagicMock()
            proc.stdin = MagicMock()
            proc.poll.return_value = 0
            return proc

        with (
            patch("launch_github_copilot.load_project_config", return_value=config),
            patch("launch_github_copilot.print_project_config_warnings"),
            patch("launch_github_copilot.worker_repo_path", return_value=self.tmpdir),
            patch("launch_github_copilot.save_state"),
            patch("launch_github_copilot.runtime_logs_dir", return_value=self.tmpdir),
            patch("launch_github_copilot.recover_codex_report", return_value=None),
            patch("launch_github_copilot.subprocess.Popen", side_effect=fake_popen),
        ):
            with self.assertRaises(BridgeError):
                launch_github_copilot.run(
                    dict(state),
                    [
                        "--prompt-file", str(self.prompt_path),
                        "--report-file", str(self.report_path),
                        "--worker-repo-path", str(self.tmpdir),
                    ],
                )
        # Report must not contain real content.
        self.assertEqual(ready_codex_report_text(self.report_path), "")

    # ------------------------------------------------------------------
    # run(): pre-existing report wins (recovered before launch)
    # ------------------------------------------------------------------

    def test_existing_report_is_respected(self) -> None:
        """If recover_codex_report already placed a report, run returns 0 immediately."""
        config = self._minimal_config()
        state = {"mode": "ready_for_codex", "need_codex_run": True}
        prior_content = "===BRIDGE_SUMMARY===\n- result: completed\n===END_BRIDGE_SUMMARY===\nRecovered.\n"
        self.report_path.write_text(prior_content, encoding="utf-8")

        with (
            patch("launch_github_copilot.load_project_config", return_value=config),
            patch("launch_github_copilot.print_project_config_warnings"),
            patch("launch_github_copilot.worker_repo_path", return_value=self.tmpdir),
            patch("launch_github_copilot.save_state"),
            patch("launch_github_copilot.runtime_logs_dir", return_value=self.tmpdir),
            # recover_codex_report returns a recovered path before launch
            patch("launch_github_copilot.recover_codex_report", return_value=self.report_path),
        ):
            rc = launch_github_copilot.run(
                dict(state),
                [
                    "--prompt-file", str(self.prompt_path),
                    "--report-file", str(self.report_path),
                    "--worker-repo-path", str(self.tmpdir),
                ],
            )
        self.assertEqual(rc, 0)
        # Content unchanged.
        after = self.report_path.read_text(encoding="utf-8")
        self.assertIn("Recovered.", after)


# ---------------------------------------------------------------------------
# copilot CLI new syntax tests (Phase 5)
# ---------------------------------------------------------------------------


class CopilotCliNewSyntaxTests(unittest.TestCase):
    """'copilot' bin 新構文 (copilot -p <prompt> -s --allow-all-tools) のテスト群。"""

    def _minimal_config(self) -> dict[str, object]:
        return {
            "execution_agent": "github_copilot",
            "agent_model": "claude-sonnet-4.6",
            "github_copilot_bin": "copilot",
            "codex_timeout_seconds": 60,
            "worker_repo_path": "/tmp",
            "bridge_runtime_root": ".",
        }

    def _make_args(
        self,
        config_overrides: dict | None = None,
        argv_extra: list[str] | None = None,
    ):
        config = {**self._minimal_config(), **(config_overrides or {})}
        argv = ["--prompt-file", "/tmp/p.md", "--report-file", "/tmp/r.md"] + (argv_extra or [])
        with patch("launch_github_copilot.load_project_config", return_value=config):
            return launch_github_copilot.parse_args(argv, config)

    # ------------------------------------------------------------------
    # build_github_copilot_command: copilot bin structure
    # ------------------------------------------------------------------

    def test_build_command_copilot_bin_starts_with_copilot(self) -> None:
        """'copilot' bin のコマンドは 'copilot' で始まる必要がある。"""
        args = self._make_args()
        cmd = launch_github_copilot.build_github_copilot_command(args, "test prompt")
        self.assertEqual(cmd[0], "copilot")

    def test_build_command_copilot_bin_includes_model(self) -> None:
        """'copilot' bin では --model が転送される。"""
        args = self._make_args({"agent_model": "claude-sonnet-4.6"})
        cmd = launch_github_copilot.build_github_copilot_command(args, "test prompt")
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-sonnet-4.6")

    def test_build_command_copilot_bin_prompt_via_p_flag(self) -> None:
        """'copilot' bin ではプロンプトが -p フラグ経由で渡される。"""
        args = self._make_args()
        prompt = "Do something important."
        cmd = launch_github_copilot.build_github_copilot_command(args, prompt)
        self.assertIn("-p", cmd)
        self.assertEqual(cmd[cmd.index("-p") + 1], prompt)

    def test_build_command_copilot_bin_has_s_flag(self) -> None:
        """'copilot' bin には非インタラクティブ用 -s フラグが含まれる。"""
        args = self._make_args()
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertIn("-s", cmd)

    def test_build_command_copilot_bin_has_allow_all_tools(self) -> None:
        """'copilot' bin には --allow-all-tools フラグが含まれる。"""
        args = self._make_args()
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertIn("--allow-all-tools", cmd)

    def test_build_command_copilot_bin_with_reasoning_effort_high(self) -> None:
        """github_copilot_reasoning_effort=high → --reasoning-effort high がコマンドに含まれる。"""
        args = self._make_args({"github_copilot_reasoning_effort": "high"})
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertIn("--reasoning-effort", cmd)
        self.assertEqual(cmd[cmd.index("--reasoning-effort") + 1], "high")

    def test_build_command_copilot_bin_with_reasoning_effort_low(self) -> None:
        """github_copilot_reasoning_effort=low → --reasoning-effort low がコマンドに含まれる。"""
        args = self._make_args({"github_copilot_reasoning_effort": "low"})
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertIn("--reasoning-effort", cmd)
        self.assertEqual(cmd[cmd.index("--reasoning-effort") + 1], "low")

    def test_build_command_copilot_bin_no_reasoning_effort_when_empty(self) -> None:
        """github_copilot_reasoning_effort が空のとき --reasoning-effort はコマンドに含まれない。"""
        args = self._make_args({"github_copilot_reasoning_effort": ""})
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertNotIn("--reasoning-effort", cmd)

    def test_build_command_copilot_bin_with_autopilot_true(self) -> None:
        """github_copilot_autopilot=True → --autopilot がコマンドに含まれる。"""
        args = self._make_args({"github_copilot_autopilot": True})
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertIn("--autopilot", cmd)

    def test_build_command_copilot_bin_no_autopilot_when_false(self) -> None:
        """github_copilot_autopilot=False のとき --autopilot はコマンドに含まれない。"""
        args = self._make_args({"github_copilot_autopilot": False})
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertNotIn("--autopilot", cmd)

    def test_build_command_copilot_bin_does_not_include_report_file(self) -> None:
        """'copilot' bin のコマンドには --report-file が含まれない (copilot CLI の概念でない)。"""
        args = self._make_args()
        cmd = launch_github_copilot.build_github_copilot_command(args, "prompt")
        self.assertNotIn("--report-file", cmd)

    # ------------------------------------------------------------------
    # validate_github_copilot_args
    # ------------------------------------------------------------------

    def test_validate_invalid_reasoning_effort_raises(self) -> None:
        """不正な reasoning_effort 値 → BridgeError が発生する。"""
        args = self._make_args({"github_copilot_reasoning_effort": "extreme"})
        with self.assertRaises(BridgeError) as ctx:
            launch_github_copilot.validate_github_copilot_args(args)
        self.assertIn("extreme", str(ctx.exception))

    def test_validate_valid_reasoning_efforts_do_not_raise(self) -> None:
        """有効な reasoning_effort 値 (low/medium/high) はエラーにならない。"""
        for effort in ("low", "medium", "high"):
            with self.subTest(effort=effort):
                args = self._make_args({"github_copilot_reasoning_effort": effort})
                launch_github_copilot.validate_github_copilot_args(args)  # must not raise

    def test_validate_empty_reasoning_effort_passes(self) -> None:
        """空の reasoning_effort はバリデーションを通過する。"""
        args = self._make_args({"github_copilot_reasoning_effort": ""})
        launch_github_copilot.validate_github_copilot_args(args)  # must not raise

    def test_validate_unset_reasoning_effort_passes(self) -> None:
        """github_copilot_reasoning_effort が config にない場合もバリデーション通過。"""
        config = self._minimal_config()
        config.pop("github_copilot_reasoning_effort", None)
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                ["--prompt-file", "/tmp/p.md", "--report-file", "/tmp/r.md"], config
            )
        launch_github_copilot.validate_github_copilot_args(args)  # must not raise

    # ------------------------------------------------------------------
    # parse_args: 新 config キーの伝播
    # ------------------------------------------------------------------

    def test_parse_args_reads_autopilot_true_from_config(self) -> None:
        """github_copilot_autopilot: true が config にある → args.autopilot が True になる。"""
        args = self._make_args({"github_copilot_autopilot": True})
        self.assertTrue(args.autopilot)

    def test_parse_args_reads_autopilot_false_from_config(self) -> None:
        """github_copilot_autopilot: false (デフォルト) → args.autopilot が False になる。"""
        args = self._make_args({"github_copilot_autopilot": False})
        self.assertFalse(args.autopilot)

    def test_parse_args_reads_reasoning_effort_from_config(self) -> None:
        """github_copilot_reasoning_effort が config にある → args.reasoning_effort に反映される。"""
        args = self._make_args({"github_copilot_reasoning_effort": "high"})
        self.assertEqual(args.reasoning_effort, "high")

    def test_parse_args_reasoning_effort_defaults_to_empty(self) -> None:
        """github_copilot_reasoning_effort が config にないとき args.reasoning_effort は ''。"""
        config = self._minimal_config()
        config.pop("github_copilot_reasoning_effort", None)
        with patch("launch_github_copilot.load_project_config", return_value=config):
            args = launch_github_copilot.parse_args(
                ["--prompt-file", "/tmp/p.md", "--report-file", "/tmp/r.md"], config
            )
        self.assertEqual(args.reasoning_effort, "")

    def test_parse_args_autopilot_override_via_argv(self) -> None:
        """argv に --autopilot を渡すと config が False でも args.autopilot が True になる。"""
        args = self._make_args({"github_copilot_autopilot": False}, ["--autopilot"])
        self.assertTrue(args.autopilot)

    def test_parse_args_reasoning_effort_override_via_argv(self) -> None:
        """argv の --reasoning-effort は config の値を上書きする。"""
        args = self._make_args(
            {"github_copilot_reasoning_effort": "low"},
            ["--reasoning-effort", "medium"],
        )
        self.assertEqual(args.reasoning_effort, "medium")


# ---------------------------------------------------------------------------
# extract_codex_report_from_stdout unit tests
# ---------------------------------------------------------------------------


class StdoutReportExtractionTests(unittest.TestCase):
    """launch_github_copilot.extract_codex_report_from_stdout() のテスト群。

    copilot CLI は report を stdout に直接出力する。
    有効なレポート本文を抽出できること、雑多なログを誤検出しないことを確認する。
    """

    # Block marker format (existing compatibility).
    _VALID_REPORT = (
        "===BRIDGE_SUMMARY===\n"
        "- summary: テスト実施\n"
        "- result: completed\n"
        "===END_BRIDGE_SUMMARY===\n"
        "\n"
        "1. 実施概要\n"
        "- テストを実行した。\n"
    )

    # Actual copilot CLI markdown format.
    _MARKDOWN_REPORT = (
        "## Codex Report\n"
        "\n"
        "### BRIDGE_SUMMARY\n"
        "- summary: テスト実施\n"
        "- result: completed\n"
        "\n"
        "### 変更ファイル\n"
        "- scripts/test.py\n"
        "\n"
        "### 実行した確認\n"
        "- pytest 実行\n"
    )

    # ------------------------------------------------------------------
    # 有効ケース: block marker 形式 (既存互換)
    # ------------------------------------------------------------------

    def test_valid_report_with_heading_is_extracted(self) -> None:
        """# Codex Report 見出し付きの有効 report が抽出される。"""
        text = f"# Codex Report\n\n{self._VALID_REPORT}"
        result = launch_github_copilot.extract_codex_report_from_stdout(text)
        self.assertIn("===BRIDGE_SUMMARY===", result)
        self.assertIn("===END_BRIDGE_SUMMARY===", result)
        self.assertTrue(result.startswith("# Codex Report"))

    def test_valid_report_with_h2_heading_is_extracted(self) -> None:
        """## Codex Report 見出しでも抽出される。"""
        text = f"## Codex Report\n\n{self._VALID_REPORT}"
        result = launch_github_copilot.extract_codex_report_from_stdout(text)
        self.assertTrue(result.startswith("## Codex Report"))
        self.assertIn("===BRIDGE_SUMMARY===", result)

    def test_valid_report_without_heading_extracted_from_bridge_summary(self) -> None:
        """見出しなし・BRIDGE_SUMMARY のみでも抽出される。"""
        result = launch_github_copilot.extract_codex_report_from_stdout(self._VALID_REPORT)
        self.assertTrue(result.startswith("===BRIDGE_SUMMARY==="))
        self.assertIn("===END_BRIDGE_SUMMARY===", result)

    def test_leading_chatter_is_stripped(self) -> None:
        """見出し前の雑多なログが削ぎ落とされる。"""
        text = "Starting copilot...\nSome debug line\n# Codex Report\n\n" + self._VALID_REPORT
        result = launch_github_copilot.extract_codex_report_from_stdout(text)
        self.assertTrue(result.startswith("# Codex Report"))
        self.assertNotIn("Starting copilot", result)

    def test_extracted_result_is_stripped(self) -> None:
        """抽出結果の前後の空白が除去される。"""
        text = "  \n\n# Codex Report\n\n" + self._VALID_REPORT + "\n\n  "
        result = launch_github_copilot.extract_codex_report_from_stdout(text)
        self.assertEqual(result, result.strip())

    # ------------------------------------------------------------------
    # 有効ケース: markdown 形式 (実際の copilot CLI stdout)
    # ------------------------------------------------------------------

    def test_markdown_format_is_extracted(self) -> None:
        """実際の copilot CLI 形式 (## Codex Report + ### BRIDGE_SUMMARY) が抽出される。"""
        result = launch_github_copilot.extract_codex_report_from_stdout(self._MARKDOWN_REPORT)
        self.assertTrue(result.startswith("## Codex Report"))
        self.assertIn("### BRIDGE_SUMMARY", result)

    def test_markdown_format_with_leading_chatter_is_extracted(self) -> None:
        """markdown 形式でも前置ログが除去される。"""
        text = "INFO: copilot started\nLoading tools...\n\n" + self._MARKDOWN_REPORT
        result = launch_github_copilot.extract_codex_report_from_stdout(text)
        self.assertTrue(result.startswith("## Codex Report"))
        self.assertNotIn("copilot started", result)

    def test_markdown_format_h1_heading_is_extracted(self) -> None:
        """# Codex Report (h1) + ### BRIDGE_SUMMARY の markdown 形式も抽出される。"""
        text = self._MARKDOWN_REPORT.replace("## Codex Report", "# Codex Report", 1)
        result = launch_github_copilot.extract_codex_report_from_stdout(text)
        self.assertTrue(result.startswith("# Codex Report"))
        self.assertIn("### BRIDGE_SUMMARY", result)

    def test_markdown_format_result_contains_sections(self) -> None:
        """markdown 形式の抽出結果に各セクションが含まれる。"""
        result = launch_github_copilot.extract_codex_report_from_stdout(self._MARKDOWN_REPORT)
        self.assertIn("### 変更ファイル", result)
        self.assertIn("### 実行した確認", result)

    # ------------------------------------------------------------------
    # 無効ケース (誤検出防止)
    # ------------------------------------------------------------------

    def test_empty_text_returns_empty(self) -> None:
        """空文字列は空を返す。"""
        self.assertEqual(launch_github_copilot.extract_codex_report_from_stdout(""), "")

    def test_no_bridge_summary_returns_empty(self) -> None:
        """Codex Report 見出しだけで BRIDGE_SUMMARY が存在しない場合は空を返す。"""
        text = "# Codex Report\n\n1. 実施概要\n- テストした\n"
        self.assertEqual(launch_github_copilot.extract_codex_report_from_stdout(text), "")

    def test_only_bridge_summary_start_returns_empty(self) -> None:
        """===BRIDGE_SUMMARY=== のみで END がない場合は空を返す (不完全)。"""
        text = "===BRIDGE_SUMMARY===\n- summary: テスト\n"
        self.assertEqual(launch_github_copilot.extract_codex_report_from_stdout(text), "")

    def test_only_end_marker_returns_empty(self) -> None:
        """END マーカーのみで START がない場合は空を返す。"""
        text = "===END_BRIDGE_SUMMARY===\nsome text\n"
        self.assertEqual(launch_github_copilot.extract_codex_report_from_stdout(text), "")

    def test_random_log_output_returns_empty(self) -> None:
        """無関係なログは誤検出しない。"""
        text = "INFO: running copilot\nDone.\nExit code 0\n"
        self.assertEqual(launch_github_copilot.extract_codex_report_from_stdout(text), "")

    def test_codex_report_heading_only_no_bridge_summary_returns_empty(self) -> None:
        """# Codex Report 見出しがあっても ### BRIDGE_SUMMARY も === も ない → 空を返す。"""
        text = "## Codex Report\n\n### 変更ファイル\n- scripts/foo.py\n"
        self.assertEqual(launch_github_copilot.extract_codex_report_from_stdout(text), "")

    def test_bridge_summary_md_only_no_report_heading_returns_empty(self) -> None:
        """### BRIDGE_SUMMARY だけで Codex Report 見出しも === マーカーもない → 空を返す。"""
        text = "### BRIDGE_SUMMARY\n- summary: テスト\n"
        self.assertEqual(launch_github_copilot.extract_codex_report_from_stdout(text), "")

    # ------------------------------------------------------------------
    # run() integration: stdout から report が回収されること
    # ------------------------------------------------------------------

    def test_run_extracts_report_from_stdout(self) -> None:
        """run(): copilot stdout に有効 report がある場合に outbox へ保存して 0 を返す。"""
        import tempfile
        report_content = "# Codex Report\n\n" + self._VALID_REPORT

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            prompt_path = tmpdir_path / "codex_prompt.md"
            report_path = tmpdir_path / "codex_report.md"
            prompt_path.write_text("# GitHub Copilot Prompt\n\nDo something.\n", encoding="utf-8")

            config = {
                "github_copilot_bin": "copilot",
                "codex_timeout_seconds": 60,
                "worker_repo_path": str(tmpdir_path),
                "bridge_runtime_root": str(tmpdir_path),
            }
            state = {"mode": "ready_for_codex", "need_codex_run": True}

            def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd):
                # Simulate copilot writing report content to stdout (no file written).
                stdout.write(report_content)
                proc = MagicMock()
                proc.stdin = MagicMock()
                proc.poll.return_value = 0
                return proc

            with (
                patch("launch_github_copilot.load_project_config", return_value=config),
                patch("launch_github_copilot.print_project_config_warnings"),
                patch("launch_github_copilot.worker_repo_path", return_value=tmpdir_path),
                patch("launch_github_copilot.save_state"),
                patch("launch_github_copilot.runtime_logs_dir", return_value=tmpdir_path),
                patch("launch_github_copilot.recover_codex_report", return_value=None),
                patch("launch_github_copilot.subprocess.Popen", side_effect=fake_popen),
            ):
                rc = launch_github_copilot.run(
                    dict(state),
                    [
                        "--prompt-file", str(prompt_path),
                        "--report-file", str(report_path),
                        "--worker-repo-path", str(tmpdir_path),
                    ],
                )

            self.assertEqual(rc, 0)
            self.assertTrue(report_path.exists(), "codex_report.md が生成されるべき")
            saved = report_path.read_text(encoding="utf-8")
            self.assertIn("===BRIDGE_SUMMARY===", saved)

    def test_run_extracts_report_from_stdout_markdown_format(self) -> None:
        """run(): 実際の copilot CLI 形式 (## Codex Report + ### BRIDGE_SUMMARY) でも回収される。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            prompt_path = tmpdir_path / "codex_prompt.md"
            report_path = tmpdir_path / "codex_report.md"
            prompt_path.write_text("# GitHub Copilot Prompt\n\nDo something.\n", encoding="utf-8")

            config = {
                "github_copilot_bin": "copilot",
                "codex_timeout_seconds": 60,
                "worker_repo_path": str(tmpdir_path),
                "bridge_runtime_root": str(tmpdir_path),
            }
            state = {"mode": "ready_for_codex", "need_codex_run": True}

            def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd):
                stdout.write(self._MARKDOWN_REPORT)
                proc = MagicMock()
                proc.stdin = MagicMock()
                proc.poll.return_value = 0
                return proc

            with (
                patch("launch_github_copilot.load_project_config", return_value=config),
                patch("launch_github_copilot.print_project_config_warnings"),
                patch("launch_github_copilot.worker_repo_path", return_value=tmpdir_path),
                patch("launch_github_copilot.save_state"),
                patch("launch_github_copilot.runtime_logs_dir", return_value=tmpdir_path),
                patch("launch_github_copilot.recover_codex_report", return_value=None),
                patch("launch_github_copilot.subprocess.Popen", side_effect=fake_popen),
            ):
                rc = launch_github_copilot.run(
                    dict(state),
                    [
                        "--prompt-file", str(prompt_path),
                        "--report-file", str(report_path),
                        "--worker-repo-path", str(tmpdir_path),
                    ],
                )

            self.assertEqual(rc, 0)
            self.assertTrue(report_path.exists(), "codex_report.md が生成されるべき")
            saved = report_path.read_text(encoding="utf-8")
            self.assertIn("### BRIDGE_SUMMARY", saved)
            self.assertTrue(saved.startswith("## Codex Report"))

    def test_run_raises_when_stdout_has_no_report(self) -> None:
        """run(): stdout に有効 report がない場合は従来どおり BridgeError を上げる。"""
        import tempfile
        from _bridge_common import BridgeError

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            prompt_path = tmpdir_path / "codex_prompt.md"
            report_path = tmpdir_path / "codex_report.md"
            prompt_path.write_text("# GitHub Copilot Prompt\n\nDo something.\n", encoding="utf-8")

            config = {
                "github_copilot_bin": "copilot",
                "codex_timeout_seconds": 60,
                "worker_repo_path": str(tmpdir_path),
                "bridge_runtime_root": str(tmpdir_path),
            }
            state = {"mode": "ready_for_codex", "need_codex_run": True}

            def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd):
                # Only debug chatter — no report markers.
                stdout.write("INFO: copilot started\nDone.\n")
                proc = MagicMock()
                proc.stdin = MagicMock()
                proc.poll.return_value = 0
                return proc

            with (
                patch("launch_github_copilot.load_project_config", return_value=config),
                patch("launch_github_copilot.print_project_config_warnings"),
                patch("launch_github_copilot.worker_repo_path", return_value=tmpdir_path),
                patch("launch_github_copilot.save_state"),
                patch("launch_github_copilot.runtime_logs_dir", return_value=tmpdir_path),
                patch("launch_github_copilot.recover_codex_report", return_value=None),
                patch("launch_github_copilot.subprocess.Popen", side_effect=fake_popen),
            ):
                with self.assertRaises(BridgeError):
                    launch_github_copilot.run(
                        dict(state),
                        [
                            "--prompt-file", str(prompt_path),
                            "--report-file", str(report_path),
                            "--worker-repo-path", str(tmpdir_path),
                        ],
                    )
            self.assertFalse(report_path.exists(), "report が存在してはならない")

    def test_existing_report_file_not_overwritten_by_stdout(self) -> None:
        """wrapper が subprocess 内で直接 report を書いた場合、stdout extraction で上書きされない。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            prompt_path = tmpdir_path / "codex_prompt.md"
            report_path = tmpdir_path / "codex_report.md"
            prompt_path.write_text("# Prompt\n\nDo it.\n", encoding="utf-8")

            direct_report = (
                "===BRIDGE_SUMMARY===\n- summary: direct write\n===END_BRIDGE_SUMMARY===\nDirect.\n"
            )

            config = {
                "github_copilot_bin": "/scripts/github_copilot_wrapper.py",
                "codex_timeout_seconds": 60,
                "worker_repo_path": str(tmpdir_path),
                "bridge_runtime_root": str(tmpdir_path),
            }
            state = {"mode": "ready_for_codex", "need_codex_run": True}

            def fake_popen(cmd, *, stdin, stdout, stderr, text, cwd):
                # Wrapper writes report directly to --report-file.
                report_path.write_text(direct_report, encoding="utf-8")
                # Stdout also contains a (different) report-like payload.
                stdout.write(
                    "## Codex Report\n===BRIDGE_SUMMARY===\n"
                    "- summary: stdout version\n===END_BRIDGE_SUMMARY===\nStdout.\n"
                )
                proc = MagicMock()
                proc.stdin = MagicMock()
                proc.poll.return_value = 0
                return proc

            with (
                patch("launch_github_copilot.load_project_config", return_value=config),
                patch("launch_github_copilot.print_project_config_warnings"),
                patch("launch_github_copilot.worker_repo_path", return_value=tmpdir_path),
                patch("launch_github_copilot.save_state"),
                patch("launch_github_copilot.runtime_logs_dir", return_value=tmpdir_path),
                patch("launch_github_copilot.recover_codex_report", return_value=None),
                patch("launch_github_copilot.subprocess.Popen", side_effect=fake_popen),
            ):
                rc = launch_github_copilot.run(
                    dict(state),
                    [
                        "--prompt-file", str(prompt_path),
                        "--report-file", str(report_path),
                        "--worker-repo-path", str(tmpdir_path),
                    ],
                )

            self.assertEqual(rc, 0)
            saved = report_path.read_text(encoding="utf-8")
            # Directly-written content is preserved; extraction skipped because
            # codex_report_is_ready() already returns True after subprocess.
            self.assertIn("direct write", saved, "wrapper が書いた内容が保持されるべき")
            self.assertNotIn("stdout version", saved, "stdout による上書きがないこと")


# ---------------------------------------------------------------------------
# _is_ic_close_completed_for_auto_continuation unit tests
# ---------------------------------------------------------------------------


class IsIcCloseCompletedForAutoContinuationTests(unittest.TestCase):
    """Tests for bridge_orchestrator._is_ic_close_completed_for_auto_continuation."""

    def _call(self, state: dict) -> bool:
        return bridge_orchestrator._is_ic_close_completed_for_auto_continuation(state)

    def test_returns_true_when_close_status_closed_and_ic_decision(self) -> None:
        """close_status=closed + issue_centric: decision → True."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_close_status": "closed",
        }
        self.assertTrue(self._call(state))

    def test_returns_true_when_close_status_already_closed_and_ic_decision(self) -> None:
        """close_status=already_closed + issue_centric: decision → True."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_close_status": "already_closed",
        }
        self.assertTrue(self._call(state))

    def test_returns_false_when_close_status_not_a_complete_value(self) -> None:
        """close_status not in {closed, already_closed} → False even with IC decision."""
        for status in ("", "blocked", "partial", "not_requested", "completed", "failed_after_mutation_attempt"):
            with self.subTest(status=status):
                state = {
                    "chatgpt_decision": "issue_centric:no_action",
                    "last_issue_centric_close_status": status,
                }
                self.assertFalse(self._call(state))

    def test_returns_false_when_chatgpt_decision_not_ic(self) -> None:
        """Non-IC chatgpt_decision → False even with close_status=closed."""
        for decision in ("", "human_review", "need_info", "legacy_contract_detected"):
            with self.subTest(decision=decision):
                state = {
                    "chatgpt_decision": decision,
                    "last_issue_centric_close_status": "closed",
                }
                self.assertFalse(self._call(state))

    def test_returns_false_when_state_is_empty(self) -> None:
        """Empty state → False."""
        self.assertFalse(self._call({}))

    def test_returns_true_with_codex_run_action_and_close_status_closed(self) -> None:
        """issue_centric:codex_run + close_status=closed → True."""
        state = {
            "chatgpt_decision": "issue_centric:codex_run",
            "last_issue_centric_close_status": "closed",
        }
        self.assertTrue(self._call(state))

    def test_returns_false_when_pending_request_is_initial_selection(self) -> None:
        """pending_request_source=initial_selection: → False (transition already in flight)."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_close_status": "closed",
            "pending_request_source": "initial_selection:abc123",
        }
        self.assertFalse(self._call(state))

    def test_returns_false_when_pending_request_is_ready_issue(self) -> None:
        """pending_request_source=ready_issue: → False (ready-issue request already sent)."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_close_status": "closed",
            "pending_request_source": "ready_issue:def456",
        }
        self.assertFalse(self._call(state))

    def test_returns_false_when_prepared_request_is_initial_selection(self) -> None:
        """prepared_request_source=initial_selection: → False (staged but not yet promoted)."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_close_status": "closed",
            "prepared_request_source": "initial_selection:abc123",
        }
        self.assertFalse(self._call(state))

    def test_returns_false_when_prepared_request_is_ready_issue(self) -> None:
        """prepared_request_source=ready_issue: → False (ready-issue staged but not promoted)."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_close_status": "closed",
            "prepared_request_source": "ready_issue:def456",
        }
        self.assertFalse(self._call(state))

    def test_returns_true_when_unrelated_pending_request_exists(self) -> None:
        """A non-IC pending request (e.g. override:) does not suppress auto-continuation."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "last_issue_centric_close_status": "closed",
            "pending_request_source": "override:xyz",
        }
        self.assertTrue(self._call(state))


# ---------------------------------------------------------------------------
# bridge_orchestrator.run() IC close auto-continuation integration tests
# ---------------------------------------------------------------------------


class BridgeOrchestratorAutoNextIssueTests(unittest.TestCase):
    """Integration tests for Phase 9 auto-continuation after IC close."""

    def _make_ic_close_completed_state(self) -> dict:
        """Return a state that represents a successfully closed IC issue.

        close_status is 'closed' — the value written by
        issue_centric_close_current_issue.execute_close_current_issue() for the
        normal close path.  ('completed' was the old expected value that caused
        the auto-continuation judgment to silently never fire.)
        """
        return {
            "mode": "awaiting_user",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "issue_centric:no_action",
            "chatgpt_decision_note": "issue closed",
            "last_issue_centric_action": "no_action",
            "last_issue_centric_close_status": "closed",
            "last_issue_centric_close_order": "after_no_action",
            "last_issue_centric_closed_issue_number": "42",
        }

    def test_auto_continuation_calls_request_next_prompt_with_select_issue(self) -> None:
        """After IC close, bridge dispatches request_next_prompt with --select-issue."""
        state = self._make_ic_close_completed_state()
        config = _make_minimal_project_config("codex")
        captured = []

        def fake_request_next_prompt_run(s, argv):
            captured.append(argv)
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_request_next_prompt_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, ["--worker-repo-path", "/tmp/test-repo"])

        self.assertEqual(rc, 0)
        self.assertEqual(len(captured), 1, "request_next_prompt.run should be called once")
        argv = captured[0]
        self.assertIn("--select-issue", argv)
        self.assertNotIn("--ready-issue-ref", argv)
        self.assertNotIn("--request-body", argv)

    def test_auto_continuation_includes_project_path_when_worker_repo_path_set(self) -> None:
        """--project-path is passed from worker_repo_path when set."""
        state = self._make_ic_close_completed_state()
        config = _make_minimal_project_config("codex")
        captured = []

        def fake_run(s, argv):
            captured.append(argv)
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            bridge_orchestrator.run(state, ["--worker-repo-path", "/tmp/proj"])

        argv = captured[0]
        self.assertIn("--project-path", argv)
        idx = argv.index("--project-path")
        self.assertEqual(argv[idx + 1], "/tmp/proj")

    def test_initial_selection_stop_auto_continues_with_ready_issue_ref(self) -> None:
        """initial_selection_stop: bridge auto-continues with --ready-issue-ref when selected_ref is set."""
        state = self._make_ic_close_completed_state()
        state["selected_ready_issue_ref"] = "#42 some issue"
        config = _make_minimal_project_config("codex")
        captured = []

        def fake_run(s, argv):
            captured.append(argv)
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        self.assertEqual(len(captured), 1)
        argv = captured[0]
        self.assertIn("--ready-issue-ref", argv)
        idx = argv.index("--ready-issue-ref")
        self.assertEqual(argv[idx + 1], "#42 some issue")
        self.assertNotIn("--select-issue", argv)

    def test_initial_selection_stop_clears_selected_ref_in_forwarded_state(self) -> None:
        """initial_selection_stop: selected_ready_issue_ref is cleared in the forwarded state."""
        state = self._make_ic_close_completed_state()
        state["selected_ready_issue_ref"] = "#42 some issue"
        config = _make_minimal_project_config("codex")
        forwarded_states = []

        def capture_state(s, argv):
            forwarded_states.append(dict(s))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=capture_state),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            bridge_orchestrator.run(state, [])

        self.assertEqual(len(forwarded_states), 1)
        self.assertEqual(forwarded_states[0].get("selected_ready_issue_ref"), "")

    def test_initial_selection_stop_fallback_stops_when_no_selected_ref(self) -> None:
        """When selected_ready_issue_ref is empty, no --ready-issue-ref dispatch occurs.

        In normal flow detect_ic_stop_path requires selected_ready_issue_ref to be
        non-empty to return "initial_selection_stop", so this state (_ic_stop == "")
        falls through to IC close auto-continuation (--select-issue), not --ready-issue-ref.
        """
        state = self._make_ic_close_completed_state()
        state["selected_ready_issue_ref"] = ""
        config = _make_minimal_project_config("codex")
        captured = []

        def fake_run(s, argv):
            captured.append(list(argv))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        # --ready-issue-ref must NOT appear in any call (--select-issue may appear
        # via IC close auto-continuation, but that's a different path).
        for argv in captured:
            self.assertNotIn("--ready-issue-ref", argv)

    def test_initial_selection_stop_prints_selected_ref_in_auto_continue_message(self) -> None:
        """initial_selection_stop auto-continue: prints the selected issue ref."""
        state = self._make_ic_close_completed_state()
        state["selected_ready_issue_ref"] = "#99 target issue"
        config = _make_minimal_project_config("codex")

        buf = io.StringIO()
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", return_value=0),
            patch("bridge_orchestrator.print_project_config_warnings"),
            redirect_stdout(buf),
        ):
            bridge_orchestrator.run(state, [])

        output = buf.getvalue()
        self.assertIn("#99", output)
        self.assertIn("選定", output)

    def test_human_review_needed_still_stops(self) -> None:
        """human_review_needed IC stop path: bridge does NOT auto-continue."""
        state = {
            "mode": "awaiting_user",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "issue_centric:human_review_needed",
            "chatgpt_decision_note": "人レビューが必要です。",
        }
        config = _make_minimal_project_config("codex")

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run") as mock_rnp,
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        mock_rnp.assert_not_called()
        self.assertEqual(rc, 0)

    def test_auto_continuation_does_not_fire_when_close_status_not_complete_value(self) -> None:
        """No auto-continuation when last_issue_centric_close_status is not a complete value."""
        state = self._make_ic_close_completed_state()
        state["last_issue_centric_close_status"] = ""
        config = _make_minimal_project_config("codex")

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run") as mock_rnp,
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        mock_rnp.assert_not_called()
        self.assertEqual(rc, 0)

    def test_auto_continuation_does_not_fire_for_non_ic_state(self) -> None:
        """Non-IC chatgpt_decision: no auto-continuation even if close_status=closed."""
        state = self._make_ic_close_completed_state()
        # Use a decision value that is NOT "human_review" / "need_info" to avoid routing
        # to request_prompt_from_report, and NOT an issue_centric: prefix.
        state["chatgpt_decision"] = "legacy_contract_detected"
        config = _make_minimal_project_config("codex")

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run") as mock_rnp,
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        mock_rnp.assert_not_called()
        self.assertEqual(rc, 0)

    def test_auto_continuation_prints_close_detected_message(self) -> None:
        """Auto-continuation prints the close-detected message to stdout."""
        state = self._make_ic_close_completed_state()
        config = _make_minimal_project_config("codex")

        buf = io.StringIO()
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", return_value=0),
            patch("bridge_orchestrator.print_project_config_warnings"),
            redirect_stdout(buf),
        ):
            bridge_orchestrator.run(state, [])

        output = buf.getvalue()
        self.assertIn("クローズを検出", output)
        self.assertIn("選定", output)


# ---------------------------------------------------------------------------
# _is_ic_issue_create_completed_for_auto_continuation unit tests
# ---------------------------------------------------------------------------


class IsIcIssueCreateCompletedForAutoContinuationTests(unittest.TestCase):
    """Tests for bridge_orchestrator._is_ic_issue_create_completed_for_auto_continuation."""

    def _call(self, state: dict) -> bool:
        return bridge_orchestrator._is_ic_issue_create_completed_for_auto_continuation(state)

    def test_returns_true_when_issue_created_no_close(self) -> None:
        """created_issue_number set + no close + IC decision → True."""
        state = {
            "chatgpt_decision": "issue_centric:issue_create",
            "last_issue_centric_created_issue_number": "42",
            "last_issue_centric_close_status": "",
        }
        self.assertTrue(self._call(state))

    def test_returns_false_when_close_completed(self) -> None:
        """close_status in {closed, already_closed} → False (issue_create_then_close handled elsewhere)."""
        for close_status in ("closed", "already_closed"):
            with self.subTest(close_status=close_status):
                state = {
                    "chatgpt_decision": "issue_centric:issue_create",
                    "last_issue_centric_created_issue_number": "42",
                    "last_issue_centric_close_status": close_status,
                }
                self.assertFalse(self._call(state))

    def test_returns_false_when_created_issue_number_empty(self) -> None:
        """Empty created_issue_number → False regardless of IC decision."""
        state = {
            "chatgpt_decision": "issue_centric:issue_create",
            "last_issue_centric_created_issue_number": "",
            "last_issue_centric_close_status": "",
        }
        self.assertFalse(self._call(state))

    def test_returns_false_when_chatgpt_decision_not_ic(self) -> None:
        """Non-IC chatgpt_decision → False even with created_issue_number set."""
        for decision in ("", "human_review", "need_info", "legacy_contract_detected"):
            with self.subTest(decision=decision):
                state = {
                    "chatgpt_decision": decision,
                    "last_issue_centric_created_issue_number": "42",
                    "last_issue_centric_close_status": "",
                }
                self.assertFalse(self._call(state))

    def test_returns_false_when_state_is_empty(self) -> None:
        """Empty state → False."""
        self.assertFalse(self._call({}))

    def test_returns_true_for_partial_close_status(self) -> None:
        """Non-completed close_status (e.g. 'blocked') with created issue → True."""
        state = {
            "chatgpt_decision": "issue_centric:issue_create",
            "last_issue_centric_created_issue_number": "55",
            "last_issue_centric_close_status": "blocked",
        }
        self.assertTrue(self._call(state))


# ---------------------------------------------------------------------------
# bridge_orchestrator.run() issue_create auto-continuation integration tests
# ---------------------------------------------------------------------------


class BridgeOrchestratorIssueCreateAutoContinueTests(unittest.TestCase):
    """Integration tests for issue_create auto-continuation after IC issue creation."""

    def _make_ic_issue_create_state(
        self,
        number: str = "42",
        title: str = "my new feature",
        close_status: str = "",
    ) -> dict:
        """Return a state representing a successful issue_create without close."""
        return {
            "mode": "awaiting_user",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "issue_centric:issue_create",
            "chatgpt_decision_note": "issue created",
            "last_issue_centric_action": "issue_create",
            "last_issue_centric_created_issue_number": number,
            "last_issue_centric_created_issue_title": title,
            "last_issue_centric_created_issue_url": f"https://github.com/org/repo/issues/{number}",
            "last_issue_centric_close_status": close_status,
        }

    def test_auto_continue_calls_request_next_prompt_with_ready_issue_ref(self) -> None:
        """After issue_create, bridge dispatches request_next_prompt with --ready-issue-ref."""
        state = self._make_ic_issue_create_state()
        config = _make_minimal_project_config("codex")
        captured = []

        def fake_run(s, argv):
            captured.append(list(argv))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        self.assertEqual(len(captured), 1)
        argv = captured[0]
        self.assertIn("--ready-issue-ref", argv)
        idx = argv.index("--ready-issue-ref")
        ref = argv[idx + 1]
        self.assertIn("42", ref)
        self.assertNotIn("--select-issue", argv)

    def test_auto_continue_ref_includes_title(self) -> None:
        """--ready-issue-ref contains both issue number and title."""
        state = self._make_ic_issue_create_state(number="7", title="add login flow")
        config = _make_minimal_project_config("codex")
        captured_argv = []

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run",
                  side_effect=lambda s, argv: captured_argv.extend([list(argv)]) or 0),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            bridge_orchestrator.run(state, [])

        argv = captured_argv[0]
        idx = argv.index("--ready-issue-ref")
        ref = argv[idx + 1]
        self.assertIn("#7", ref)
        self.assertIn("add login flow", ref)

    def test_auto_continue_clears_created_issue_fields_in_forwarded_state(self) -> None:
        """Forwarded state has last_issue_centric_created_issue_* cleared."""
        state = self._make_ic_issue_create_state()
        config = _make_minimal_project_config("codex")
        forwarded_states = []

        def capture_state(s, argv):
            forwarded_states.append(dict(s))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=capture_state),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            bridge_orchestrator.run(state, [])

        self.assertEqual(len(forwarded_states), 1)
        fwd = forwarded_states[0]
        self.assertEqual(fwd.get("last_issue_centric_created_issue_number"), "")
        self.assertEqual(fwd.get("last_issue_centric_created_issue_url"), "")
        self.assertEqual(fwd.get("last_issue_centric_created_issue_title"), "")

    def test_auto_continue_prints_issue_number_in_message(self) -> None:
        """Auto-continue message includes the created issue number."""
        state = self._make_ic_issue_create_state(number="99", title="fix cache bug")
        config = _make_minimal_project_config("codex")
        buf = io.StringIO()

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", return_value=0),
            patch("bridge_orchestrator.print_project_config_warnings"),
            redirect_stdout(buf),
        ):
            bridge_orchestrator.run(state, [])

        output = buf.getvalue()
        self.assertIn("#99", output)
        self.assertIn("作成", output)

    def test_auto_continue_does_not_fire_when_close_completed(self) -> None:
        """When issue_create_then_close (close_status=closed): no issue_create auto-continue."""
        state = self._make_ic_issue_create_state(close_status="closed")
        config = _make_minimal_project_config("codex")
        captured = []

        def fake_run(s, argv):
            captured.append(list(argv))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        # Only close auto-continuation (--select-issue) should fire, not --ready-issue-ref.
        for argv in captured:
            self.assertNotIn("--ready-issue-ref", argv)

    def test_auto_continue_does_not_fire_when_no_created_issue(self) -> None:
        """No auto-continue when last_issue_centric_created_issue_number is empty."""
        state = self._make_ic_issue_create_state()
        state["last_issue_centric_created_issue_number"] = ""
        config = _make_minimal_project_config("codex")

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run") as mock_rnp,
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        mock_rnp.assert_not_called()
        self.assertEqual(rc, 0)

    def test_auto_continue_does_not_fire_for_non_ic_state(self) -> None:
        """Non-IC chatgpt_decision: no auto-continue even with created_issue_number set."""
        state = self._make_ic_issue_create_state()
        state["chatgpt_decision"] = "legacy_contract_detected"
        config = _make_minimal_project_config("codex")

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run") as mock_rnp,
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(state, [])

        mock_rnp.assert_not_called()
        self.assertEqual(rc, 0)

    def test_auto_continue_includes_project_path_when_set(self) -> None:
        """--project-path is passed when worker_repo_path is given."""
        state = self._make_ic_issue_create_state()
        config = _make_minimal_project_config("codex")
        captured = []

        def fake_run(s, argv):
            captured.append(list(argv))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            bridge_orchestrator.run(state, ["--worker-repo-path", "/tmp/project"])

        self.assertEqual(len(captured), 1)
        argv = captured[0]
        self.assertIn("--project-path", argv)
        idx = argv.index("--project-path")
        self.assertEqual(argv[idx + 1], "/tmp/project")


# ---------------------------------------------------------------------------
# _resolve_post_fetch_initial_selection_ref unit tests
# ---------------------------------------------------------------------------


class ResolvePostFetchInitialSelectionRefTests(unittest.TestCase):
    """Tests for bridge_orchestrator._resolve_post_fetch_initial_selection_ref."""

    def _call(self, state: dict) -> str:
        return bridge_orchestrator._resolve_post_fetch_initial_selection_ref(state)

    def test_returns_selected_ready_issue_ref_when_set(self) -> None:
        """Primary path: selected_ready_issue_ref non-empty → return it."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "selected_ready_issue_ref": "#2 implement auth",
        }
        self.assertEqual(self._call(state), "#2 implement auth")

    def test_returns_target_issue_when_selected_ref_empty_and_note_has_sentei(self) -> None:
        """Fallback path: selected_ready_issue_ref empty, note has 選定 → use target_issue."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "selected_ready_issue_ref": "",
            "last_issue_centric_target_issue": "#2",
            "chatgpt_decision_note": "ChatGPT が ready issue #2 を選定しました。 --ready-issue-ref で指定してください。",
        }
        self.assertEqual(self._call(state), "#2")

    def test_returns_empty_when_chatgpt_decision_not_ic(self) -> None:
        """Non-IC chatgpt_decision → empty string."""
        state = {
            "chatgpt_decision": "human_review",
            "selected_ready_issue_ref": "#2",
        }
        self.assertEqual(self._call(state), "")

    def test_returns_empty_when_state_empty(self) -> None:
        """Empty state → empty string."""
        self.assertEqual(self._call({}), "")

    def test_returns_empty_when_selected_ref_empty_and_no_sentei_in_note(self) -> None:
        """Fallback path: note does not contain 選定 → empty string (not auto-continue)."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "selected_ready_issue_ref": "",
            "last_issue_centric_target_issue": "#2",
            "chatgpt_decision_note": "dispatch を継続します。",
        }
        self.assertEqual(self._call(state), "")

    def test_returns_empty_for_codex_run_stop_state(self) -> None:
        """codex_run_stop: selected_ready_issue_ref empty and no 選定 note → empty string."""
        state = {
            "chatgpt_decision": "issue_centric:codex_run",
            "selected_ready_issue_ref": "",
            "last_issue_centric_target_issue": "#2",
            "chatgpt_decision_note": "prepared Codex body は保存済みです。",
        }
        self.assertEqual(self._call(state), "")

    def test_returns_empty_when_target_issue_is_none_string(self) -> None:
        """target_issue == 'none' → empty string (guard for unresolved targets)."""
        state = {
            "chatgpt_decision": "issue_centric:no_action",
            "selected_ready_issue_ref": "",
            "last_issue_centric_target_issue": "none",
            "chatgpt_decision_note": "ChatGPT が ready issue を選定しました。",
        }
        self.assertEqual(self._call(state), "")


# ---------------------------------------------------------------------------
# bridge_orchestrator.run() fallback-legacy initial selection auto-continue tests
# ---------------------------------------------------------------------------


class BridgeOrchestratorFetchInitialSelectionAutoContinueTests(unittest.TestCase):
    """Tests for in-run auto-continue when fetch_next_prompt raises BridgeStop
    with initial_selection_stop (fallback-legacy or issue-centric path)."""

    def _make_pre_fetch_state(self) -> dict:
        """State when bridge is about to call fetch_next_prompt (waiting_prompt_reply)."""
        return {
            "mode": "waiting_prompt_reply",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "",
            "pending_request_hash": "abc123",
            "pending_request_source": "initial_selection:hash456",
        }

    def _make_post_fetch_state(
        self,
        selected_ref: str = "#2 implement auth",
        target_issue: str = "#2",
        decision_note: str = "ChatGPT が ready issue #2 を選定しました。 --ready-issue-ref で指定してください。",
    ) -> dict:
        """State saved by fetch_next_prompt after initial_selection_stop."""
        return {
            "mode": "awaiting_user",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
            "chatgpt_decision": "issue_centric:no_action",
            "chatgpt_decision_note": decision_note,
            "selected_ready_issue_ref": selected_ref,
            "last_issue_centric_action": "no_action",
            "last_issue_centric_target_issue": target_issue,
            "pending_request_source": "",  # cleared by clear_pending_request_fields
            "pending_request_hash": "",
        }

    def test_auto_continue_on_fetch_initial_selection_stop(self) -> None:
        """fetch raises BridgeStop for initial_selection_stop → auto-continue with --ready-issue-ref."""
        pre_state = self._make_pre_fetch_state()
        post_state = self._make_post_fetch_state()
        config = _make_minimal_project_config("codex")
        captured_rnp = []

        def fake_fetch_run(s, argv):
            raise bridge_orchestrator.BridgeStop("initial_selection: ChatGPT が ready issue を選定しました: #2. ...")

        def fake_rnp_run(s, argv):
            captured_rnp.append(list(argv))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.fetch_next_prompt.run", side_effect=fake_fetch_run),
            patch("bridge_orchestrator.load_state", return_value=post_state),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_rnp_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(pre_state, [])

        self.assertEqual(rc, 0)
        self.assertEqual(len(captured_rnp), 1)
        argv = captured_rnp[0]
        self.assertIn("--ready-issue-ref", argv)
        idx = argv.index("--ready-issue-ref")
        self.assertEqual(argv[idx + 1], "#2 implement auth")
        self.assertNotIn("--select-issue", argv)

    def test_auto_continue_clears_selected_ready_issue_ref_in_forwarded_state(self) -> None:
        """Forwarded state to request_next_prompt has selected_ready_issue_ref cleared."""
        pre_state = self._make_pre_fetch_state()
        post_state = self._make_post_fetch_state()
        config = _make_minimal_project_config("codex")
        forwarded = []

        def fake_fetch_run(s, argv):
            raise bridge_orchestrator.BridgeStop("initial_selection: ...")

        def capture_state(s, argv):
            forwarded.append(dict(s))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.fetch_next_prompt.run", side_effect=fake_fetch_run),
            patch("bridge_orchestrator.load_state", return_value=post_state),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=capture_state),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            bridge_orchestrator.run(pre_state, [])

        self.assertEqual(len(forwarded), 1)
        self.assertEqual(forwarded[0].get("selected_ready_issue_ref"), "")

    def test_auto_continue_prints_selected_ref_in_message(self) -> None:
        """Auto-continue message includes the selected issue ref."""
        pre_state = self._make_pre_fetch_state()
        post_state = self._make_post_fetch_state(selected_ref="#99 fix cache")
        config = _make_minimal_project_config("codex")

        buf = io.StringIO()
        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.fetch_next_prompt.run",
                  side_effect=lambda s, a: (_ for _ in ()).throw(bridge_orchestrator.BridgeStop("..."))),
            patch("bridge_orchestrator.load_state", return_value=post_state),
            patch("bridge_orchestrator.request_next_prompt.run", return_value=0),
            patch("bridge_orchestrator.print_project_config_warnings"),
            redirect_stdout(buf),
        ):
            bridge_orchestrator.run(pre_state, [])

        output = buf.getvalue()
        self.assertIn("#99", output)
        self.assertIn("選定", output)

    def test_non_initial_selection_bridge_stop_is_reraised(self) -> None:
        """BridgeStop for codex_run_stop (not initial_selection) is re-raised."""
        pre_state = self._make_pre_fetch_state()
        post_state = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:codex_run",
            "chatgpt_decision_note": "prepared Codex body は保存済みです。",
            "selected_ready_issue_ref": "",
            "last_issue_centric_target_issue": "#2",
            "pending_request_source": "",
        }
        config = _make_minimal_project_config("codex")

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.fetch_next_prompt.run",
                  side_effect=lambda s, a: (_ for _ in ()).throw(bridge_orchestrator.BridgeStop("codex_run: ..."))),
            patch("bridge_orchestrator.load_state", return_value=post_state),
            patch("bridge_orchestrator.request_next_prompt.run") as mock_rnp,
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            with self.assertRaises(bridge_orchestrator.BridgeStop):
                bridge_orchestrator.run(pre_state, [])

        mock_rnp.assert_not_called()

    def test_auto_continue_fallback_uses_target_issue_with_sentei_note(self) -> None:
        """Fallback path: selected_ready_issue_ref empty but note has 選定 → use target_issue."""
        pre_state = self._make_pre_fetch_state()
        post_state = self._make_post_fetch_state(
            selected_ref="",  # Not set (edge case)
            target_issue="#3 edge case issue",
            decision_note="ChatGPT が ready issue #3 edge case issue を選定しました。 --ready-issue-ref で指定してください。",
        )
        config = _make_minimal_project_config("codex")
        captured_rnp = []

        def fake_fetch_run(s, argv):
            raise bridge_orchestrator.BridgeStop("initial_selection: ...")

        def fake_rnp_run(s, argv):
            captured_rnp.append(list(argv))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.fetch_next_prompt.run", side_effect=fake_fetch_run),
            patch("bridge_orchestrator.load_state", return_value=post_state),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_rnp_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            rc = bridge_orchestrator.run(pre_state, [])

        self.assertEqual(rc, 0)
        self.assertEqual(len(captured_rnp), 1)
        argv = captured_rnp[0]
        self.assertIn("--ready-issue-ref", argv)
        idx = argv.index("--ready-issue-ref")
        self.assertEqual(argv[idx + 1], "#3 edge case issue")

    def test_auto_continue_includes_project_path_when_set(self) -> None:
        """--project-path is passed when worker_repo_path is given."""
        pre_state = self._make_pre_fetch_state()
        post_state = self._make_post_fetch_state()
        config = _make_minimal_project_config("codex")
        captured_rnp = []

        def fake_fetch_run(s, argv):
            raise bridge_orchestrator.BridgeStop("...")

        def fake_rnp_run(s, argv):
            captured_rnp.append(list(argv))
            return 0

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.fetch_next_prompt.run", side_effect=fake_fetch_run),
            patch("bridge_orchestrator.load_state", return_value=post_state),
            patch("bridge_orchestrator.request_next_prompt.run", side_effect=fake_rnp_run),
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            bridge_orchestrator.run(pre_state, ["--worker-repo-path", "/tmp/proj"])

        self.assertEqual(len(captured_rnp), 1)
        argv = captured_rnp[0]
        self.assertIn("--project-path", argv)
        idx = argv.index("--project-path")
        self.assertEqual(argv[idx + 1], "/tmp/proj")

    def test_non_ic_bridge_stop_is_reraised(self) -> None:
        """BridgeStop with non-IC post-fetch state is re-raised (e.g. STOP-file, pause)."""
        pre_state = self._make_pre_fetch_state()
        post_state = {
            "mode": "awaiting_user",
            "chatgpt_decision": "",  # non-IC
            "selected_ready_issue_ref": "",
            "pending_request_source": "",
        }
        config = _make_minimal_project_config("codex")

        with (
            patch("bridge_orchestrator.load_project_config", return_value=config),
            patch("bridge_orchestrator.fetch_next_prompt.run",
                  side_effect=lambda s, a: (_ for _ in ()).throw(bridge_orchestrator.BridgeStop("bridge/STOP が存在するため停止しました。"))),
            patch("bridge_orchestrator.load_state", return_value=post_state),
            patch("bridge_orchestrator.request_next_prompt.run") as mock_rnp,
            patch("bridge_orchestrator.print_project_config_warnings"),
        ):
            with self.assertRaises(bridge_orchestrator.BridgeStop):
                bridge_orchestrator.run(pre_state, [])

        mock_rnp.assert_not_called()


# ---------------------------------------------------------------------------
# github_copilot_provider_stub unit tests
# ---------------------------------------------------------------------------


class ProviderStubTests(unittest.TestCase):
    """Tests for scripts/github_copilot_provider_stub.py."""

    def setUp(self) -> None:
        import github_copilot_provider_stub
        self.stub = github_copilot_provider_stub

    def test_run_returns_zero(self) -> None:
        """run() exits 0 on normal input."""
        with patch("sys.stdin", io.StringIO("Hello prompt\nline two")):
            rc = self.stub.run(["--model", "sonnet-4.6"])
        self.assertEqual(rc, 0)

    def test_stdout_is_non_empty(self) -> None:
        """run() always produces non-empty stdout."""
        import io as _io
        with (
            patch("sys.stdin", _io.StringIO("some prompt")),
            patch("sys.stdout", _io.StringIO()) as mock_stdout,
        ):
            self.stub.run(["--model", "test-model"])
        output = mock_stdout.getvalue()
        self.assertTrue(output.strip(), "stdout must not be empty")

    def test_output_contains_provider_name(self) -> None:
        """Output includes the provider name for traceability."""
        import io as _io
        buf = _io.StringIO()
        with (
            patch("sys.stdin", _io.StringIO("prompt")),
            patch("sys.stdout", buf),
        ):
            self.stub.run(["--model", "sonnet-4.6"])
        self.assertIn("github_copilot_provider_stub", buf.getvalue())

    def test_output_contains_model(self) -> None:
        """Output includes the --model value."""
        import io as _io
        buf = _io.StringIO()
        with (
            patch("sys.stdin", _io.StringIO("prompt")),
            patch("sys.stdout", buf),
        ):
            self.stub.run(["--model", "my-model-42"])
        self.assertIn("my-model-42", buf.getvalue())

    def test_output_contains_input_char_count(self) -> None:
        """Output includes input character count (input_chars field)."""
        import io as _io
        prompt = "x" * 50
        buf = _io.StringIO()
        with (
            patch("sys.stdin", _io.StringIO(prompt)),
            patch("sys.stdout", buf),
        ):
            self.stub.run(["--model", ""])
        self.assertIn("50", buf.getvalue())

    def test_first_line_truncated_at_80_chars(self) -> None:
        """First line preview is truncated to 80 chars with '...'."""
        import io as _io
        long_line = "A" * 120
        buf = _io.StringIO()
        with (
            patch("sys.stdin", _io.StringIO(long_line)),
            patch("sys.stdout", buf),
        ):
            self.stub.run(["--model", ""])
        output = buf.getvalue()
        self.assertIn("...", output)
        # The truncated preview must not exceed max chars + "..."
        for line in output.splitlines():
            if line.startswith("first_line:"):
                preview = line[len("first_line:"):].strip()
                self.assertLessEqual(len(preview), 80 + 3)

    def test_empty_model_defaults_to_none_label(self) -> None:
        """When --model is empty or omitted, output shows '(none)'."""
        import io as _io
        buf = _io.StringIO()
        with (
            patch("sys.stdin", _io.StringIO("prompt")),
            patch("sys.stdout", buf),
        ):
            self.stub.run([])
        self.assertIn("(none)", buf.getvalue())

    def test_wrapper_integration_stub_rejected_in_report_file_mode(self) -> None:
        """End-to-end: wrapper + stub in --report-file mode is rejected by stub safety guard.

        Since github_copilot_provider_stub.py is疎通確認専用で実 AI 応答を返さない,
        the wrapper must NOT write a success bridge report.  This prevents the stub
        from being mistaken for a real AI execution (completed / live_ready: confirmed).
        """
        import tempfile, io as _io
        import github_copilot_wrapper
        stub_path = str(
            Path(__file__).parent.parent / "scripts" / "github_copilot_provider_stub.py"
        )
        captured_stderr = _io.StringIO()
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "codex_report.md"
            with patch("sys.stdin", _io.StringIO("test prompt content")):
                with patch("sys.stderr", captured_stderr):
                    rc = github_copilot_wrapper.run([
                        "--model", "sonnet-4.6",
                        "--exec", stub_path,
                        "--report-file", str(report_path),
                    ])
            # Stub guard: must fail (non-zero) and not write any report.
            self.assertNotEqual(rc, 0, "stub must not exit 0 in --report-file mode")
            self.assertFalse(report_path.exists(), "stub must not produce a bridge report")
        self.assertIn("STUB DETECTED", captured_stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
