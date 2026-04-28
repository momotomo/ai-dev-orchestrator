"""Tests for github_copilot_wrapper.py — stub safety guard and report-file mode."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import github_copilot_wrapper  # noqa: E402

# ---------------------------------------------------------------------------
# Sample outputs
# ---------------------------------------------------------------------------

_STUB_STDOUT = (
    "provider: github_copilot_provider_stub\n"
    "model: sonnet-4.6\n"
    "input_chars: 10\n"
    "first_line: test prompt\n"
    "\n"
    "stub 応答: provider 疎通確認用の出力です。実 AI 応答は含まれません。\n"
    "実装を進める際はこの stub を実 provider に差し替えてください。\n"
)

_REAL_STDOUT = (
    "provider: my_real_provider\n"
    "model: sonnet-4.6\n"
    "result: The answer is 42.\n"
)


def _make_subprocess_result(stdout: str = "", returncode: int = 0) -> unittest.mock.MagicMock:
    m = unittest.mock.MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = ""
    return m


class StubGuardPreExecutionTests(unittest.TestCase):
    """Pre-execution stub guard: --exec path contains github_copilot_provider_stub.py."""

    def _run(self, exec_path: str, report_path: str) -> tuple[int, str]:
        """Run wrapper and return (exit_code, stderr_text)."""
        captured_stderr = io.StringIO()
        with unittest.mock.patch("sys.stdin", io.StringIO("test prompt")):
            with unittest.mock.patch("sys.stderr", captured_stderr):
                with unittest.mock.patch("subprocess.run") as mock_sub:
                    exit_code = github_copilot_wrapper.run(
                        ["--exec", exec_path, "--report-file", report_path]
                    )
                    subprocess_called = mock_sub.called
        return exit_code, captured_stderr.getvalue(), subprocess_called

    def test_stub_exec_path_returns_nonzero(self) -> None:
        """Stub --exec path → exit non-zero."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = str(Path(tmp) / "report.md")
            exit_code, _, _ = self._run("/path/to/github_copilot_provider_stub.py", report_path)
        self.assertNotEqual(exit_code, 0)

    def test_stub_exec_path_no_report_written(self) -> None:
        """Stub --exec path → report file must NOT be created."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.md"
            self._run("/path/to/github_copilot_provider_stub.py", str(report_path))
            self.assertFalse(report_path.exists())

    def test_stub_exec_path_subprocess_not_called(self) -> None:
        """Stub --exec path → subprocess.run must NOT be called (early exit)."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = str(Path(tmp) / "report.md")
            _, _, subprocess_called = self._run(
                "/path/to/github_copilot_provider_stub.py", report_path
            )
        self.assertFalse(subprocess_called)

    def test_stub_exec_path_stderr_contains_stub_detected(self) -> None:
        """Stub --exec path → stderr must mention STUB DETECTED."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = str(Path(tmp) / "report.md")
            _, stderr, _ = self._run("/path/to/github_copilot_provider_stub.py", report_path)
        self.assertIn("STUB DETECTED", stderr)

    def test_stub_exec_path_stderr_explains_real_provider(self) -> None:
        """Stub --exec path → stderr must tell user to switch to real provider."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = str(Path(tmp) / "report.md")
            _, stderr, _ = self._run("/path/to/github_copilot_provider_stub.py", report_path)
        self.assertIn("実 provider", stderr)

    def test_stub_exec_basename_also_detected(self) -> None:
        """Stub detected even when stub is at root (no directory prefix)."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = str(Path(tmp) / "report.md")
            exit_code, _, _ = self._run("github_copilot_provider_stub.py", report_path)
        self.assertNotEqual(exit_code, 0)


class StubGuardPostExecutionTests(unittest.TestCase):
    """Post-execution stub guard: provider stdout contains stub markers."""

    def _run_with_stdout(self, provider_stdout: str) -> tuple[int, str, bool]:
        """Run wrapper with a non-stub --exec path but controlled stdout. Returns (exit_code, stderr, report_exists)."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.md"
            mock_result = _make_subprocess_result(stdout=provider_stdout, returncode=0)
            captured_stderr = io.StringIO()
            captured_stdout = io.StringIO()
            with unittest.mock.patch("sys.stdin", io.StringIO("test prompt")):
                with unittest.mock.patch("sys.stderr", captured_stderr):
                    with unittest.mock.patch("sys.stdout", captured_stdout):
                        with unittest.mock.patch("subprocess.run", return_value=mock_result):
                            exit_code = github_copilot_wrapper.run(
                                [
                                    "--exec",
                                    "/path/to/some_other_provider",
                                    "--report-file",
                                    str(report_path),
                                ]
                            )
            return exit_code, captured_stderr.getvalue(), report_path.exists()

    def test_stub_stdout_provider_name_returns_nonzero(self) -> None:
        """Stdout containing 'provider: github_copilot_provider_stub' → non-zero exit."""
        exit_code, _, _ = self._run_with_stdout(_STUB_STDOUT)
        self.assertNotEqual(exit_code, 0)

    def test_stub_stdout_provider_name_no_report(self) -> None:
        """Stdout containing stub marker → report must NOT be written."""
        _, _, report_exists = self._run_with_stdout(_STUB_STDOUT)
        self.assertFalse(report_exists)

    def test_stub_stdout_provider_name_stderr_stub_detected(self) -> None:
        """Stdout containing stub marker → stderr must mention STUB DETECTED."""
        _, stderr, _ = self._run_with_stdout(_STUB_STDOUT)
        self.assertIn("STUB DETECTED", stderr)

    def test_stub_stdout_marker_only_stub_応答(self) -> None:
        """Stdout containing only 'stub 応答' keyword → also rejected."""
        minimal_stub = "stub 応答: これはテストです。\n"
        exit_code, stderr, report_exists = self._run_with_stdout(minimal_stub)
        self.assertNotEqual(exit_code, 0)
        self.assertFalse(report_exists)
        self.assertIn("STUB DETECTED", stderr)


class NonStubProviderReportFileModeTests(unittest.TestCase):
    """Non-stub provider in --report-file mode: normal behavior preserved."""

    def _run_with_real_provider(self, provider_stdout: str) -> tuple[int, str, bool]:
        """Run wrapper with non-stub stdout. Returns (exit_code, report_content, report_was_created)."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.md"
            mock_result = _make_subprocess_result(stdout=provider_stdout, returncode=0)
            captured_stderr = io.StringIO()
            captured_stdout = io.StringIO()
            with unittest.mock.patch("sys.stdin", io.StringIO("test prompt")):
                with unittest.mock.patch("sys.stderr", captured_stderr):
                    with unittest.mock.patch("sys.stdout", captured_stdout):
                        with unittest.mock.patch("subprocess.run", return_value=mock_result):
                            exit_code = github_copilot_wrapper.run(
                                [
                                    "--exec",
                                    "/path/to/real_provider",
                                    "--report-file",
                                    str(report_path),
                                ]
                            )
            report_was_created = report_path.exists()
            report_content = report_path.read_text(encoding="utf-8") if report_was_created else ""
        return exit_code, report_content, report_was_created

    def test_real_provider_exits_zero(self) -> None:
        """Non-stub provider → exit 0."""
        exit_code, _, _ = self._run_with_real_provider(_REAL_STDOUT)
        self.assertEqual(exit_code, 0)

    def test_real_provider_writes_report(self) -> None:
        """Non-stub provider → report file is created."""
        _, _, report_was_created = self._run_with_real_provider(_REAL_STDOUT)
        self.assertTrue(report_was_created)

    def test_real_provider_report_contains_bridge_summary(self) -> None:
        """Non-stub provider → report contains BRIDGE_SUMMARY block."""
        _, report_content, _ = self._run_with_real_provider(_REAL_STDOUT)
        self.assertIn(github_copilot_wrapper.BRIDGE_SUMMARY_START, report_content)
        self.assertIn(github_copilot_wrapper.BRIDGE_SUMMARY_END, report_content)

    def test_real_provider_report_contains_completed(self) -> None:
        """Non-stub provider → report marks result as completed."""
        _, report_content, _ = self._run_with_real_provider(_REAL_STDOUT)
        self.assertIn("result: completed", report_content)


class PassthroughModeTests(unittest.TestCase):
    """Passthrough mode (no --report-file): stub guard does NOT interfere."""

    def test_stub_exec_passthrough_no_guard(self) -> None:
        """Passthrough mode with stub --exec → runs normally (guard only applies to --report-file)."""
        mock_result = _make_subprocess_result(stdout=_STUB_STDOUT, returncode=0)
        with unittest.mock.patch("sys.stdin", io.StringIO("test")):
            with unittest.mock.patch("subprocess.run", return_value=mock_result):
                exit_code = github_copilot_wrapper.run(
                    ["--exec", "/path/to/github_copilot_provider_stub.py"]
                )
        self.assertEqual(exit_code, 0)


class NullByteGuardTests(unittest.TestCase):
    """Tests for NUL-byte (\\x00) guard in github_copilot_wrapper (Phase 22)."""

    # ------------------------------------------------------------------
    # Unit tests for _sanitize_prompt_text helper
    # ------------------------------------------------------------------

    def test_sanitize_prompt_strips_nul_bytes(self) -> None:
        """NUL bytes are removed from the returned string."""
        self.assertEqual(
            github_copilot_wrapper._sanitize_prompt_text("hello\x00world"),
            "helloworld",
        )

    def test_sanitize_prompt_no_nul_unchanged(self) -> None:
        """Prompt without NUL bytes is returned unchanged (same object)."""
        original = "hello world\nnew line"
        result = github_copilot_wrapper._sanitize_prompt_text(original)
        self.assertEqual(result, original)

    def test_sanitize_prompt_multiple_nul_all_stripped(self) -> None:
        """Multiple embedded NUL bytes are all removed."""
        result = github_copilot_wrapper._sanitize_prompt_text("a\x00b\x00c\x00")
        self.assertEqual(result, "abc")

    def test_sanitize_prompt_warns_to_stderr(self) -> None:
        """NUL in prompt emits a WARNING message to stderr."""
        captured = io.StringIO()
        with unittest.mock.patch("sys.stderr", captured):
            github_copilot_wrapper._sanitize_prompt_text("a\x00b\x00c")
        err = captured.getvalue()
        self.assertIn("WARNING", err)
        self.assertIn("NUL", err)

    def test_sanitize_prompt_preserves_other_whitespace(self) -> None:
        """Stripping NUL does not affect newlines, tabs, or other whitespace."""
        original = "line1\nline2\ttabbed\r\nwindows"
        result = github_copilot_wrapper._sanitize_prompt_text(original + "\x00")
        self.assertEqual(result, original)

    def test_sanitize_prompt_no_warning_when_no_nul(self) -> None:
        """No warning is emitted when prompt has no NUL bytes."""
        captured = io.StringIO()
        with unittest.mock.patch("sys.stderr", captured):
            github_copilot_wrapper._sanitize_prompt_text("clean prompt")
        self.assertEqual(captured.getvalue(), "")

    # ------------------------------------------------------------------
    # Unit tests for _assert_no_null_in_structural_input helper
    # ------------------------------------------------------------------

    def test_assert_structural_raises_on_nul(self) -> None:
        """Structural input with NUL → ValueError with label in message."""
        with self.assertRaises(ValueError) as ctx:
            github_copilot_wrapper._assert_no_null_in_structural_input("path\x00here", "--exec")
        msg = str(ctx.exception)
        self.assertIn("--exec", msg)
        self.assertIn("NUL", msg)

    def test_assert_structural_passes_clean(self) -> None:
        """Structural input without NUL → no exception."""
        github_copilot_wrapper._assert_no_null_in_structural_input(
            "/usr/local/bin/copilot", "--exec"
        )

    def test_assert_structural_model_label_included(self) -> None:
        """Model arg with NUL → error message identifies --model."""
        with self.assertRaises(ValueError) as ctx:
            github_copilot_wrapper._assert_no_null_in_structural_input(
                "gpt-4\x00bad", "--model"
            )
        self.assertIn("--model", str(ctx.exception))

    # ------------------------------------------------------------------
    # Integration: NUL in prompt must not reach subprocess argv
    # ------------------------------------------------------------------

    def test_nul_in_prompt_sanitized_before_subprocess_call_no_exec(self) -> None:
        """In --report-file (no --exec) mode NUL-tainted prompt is sanitized before
        being embedded as '-p <prompt>' in argv, so subprocess.run never sees NUL."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.md"
            nul_prompt = "hello\x00world"
            captured_argv: list[str] = []

            def fake_run(cmd: list[str], **kwargs):  # type: ignore[override]
                captured_argv.extend(cmd)
                return _make_subprocess_result(stdout="output: all good", returncode=0)

            captured_stderr = io.StringIO()
            with unittest.mock.patch("sys.stdin", io.StringIO(nul_prompt)):
                with unittest.mock.patch("sys.stderr", captured_stderr):
                    with unittest.mock.patch("subprocess.run", side_effect=fake_run):
                        rc = github_copilot_wrapper.run(["--report-file", str(report_path)])

        self.assertEqual(rc, 0, "should exit 0 after sanitization")
        for element in captured_argv:
            self.assertNotIn("\x00", element, f"NUL found in argv element: {element!r}")

    def test_nul_in_prompt_no_raw_valueerror_escapes_no_exec(self) -> None:
        """Raw ValueError must not propagate from --report-file (no --exec) path.

        Belt-and-suspenders: even if the subprocess.run call raises ValueError
        (e.g. a NUL slipped through some other path), the wrapper must catch it
        and return a non-zero int — never raise.
        """
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.md"
            nul_prompt = "hello\x00world"
            captured_stderr = io.StringIO()

            def fake_run_raise_value_error(cmd: list[str], **kwargs):  # type: ignore[override]
                raise ValueError("embedded null byte")

            with unittest.mock.patch("sys.stdin", io.StringIO(nul_prompt)):
                with unittest.mock.patch("sys.stderr", captured_stderr):
                    with unittest.mock.patch(
                        "subprocess.run", side_effect=fake_run_raise_value_error
                    ):
                        try:
                            rc = github_copilot_wrapper.run(["--report-file", str(report_path)])
                            raised = False
                        except ValueError:
                            rc = -1
                            raised = True

        self.assertFalse(raised, "ValueError must NOT escape run()")
        self.assertNotEqual(rc, 0, "error exit code expected")

    def test_nul_in_exec_cmd_returns_error_not_exception(self) -> None:
        """NUL in --exec path → wrapper returns non-zero; no unhandled exception."""
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.md"
            captured_stderr = io.StringIO()
            with unittest.mock.patch("sys.stdin", io.StringIO("clean prompt")):
                with unittest.mock.patch("sys.stderr", captured_stderr):
                    with unittest.mock.patch("subprocess.run") as mock_sub:
                        try:
                            rc = github_copilot_wrapper.run(
                                ["--exec", "cmd\x00path", "--report-file", str(report_path)]
                            )
                            raised = False
                        except (ValueError, SystemExit):
                            rc = -1
                            raised = True

        self.assertFalse(raised, "ValueError/SystemExit must not escape run()")
        self.assertNotEqual(rc, 0, "non-zero exit expected for NUL in exec_cmd")
        self.assertFalse(mock_sub.called, "subprocess.run must not be called")


if __name__ == "__main__":
    unittest.main()
