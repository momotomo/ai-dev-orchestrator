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


if __name__ == "__main__":
    unittest.main()
