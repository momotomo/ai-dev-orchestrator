"""Tests for safari_tab_probe.py — read-only Safari tab enumeration probe.

These tests exercise the pure Python logic (URL classification, output
formatting, CLI argument parsing) without invoking actual osascript calls.
All subprocess calls are patched with fixtures.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import safari_tab_probe as stp  # noqa: E402


def _make_result(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


# ---------------------------------------------------------------------------
# URL classification helpers
# ---------------------------------------------------------------------------

class UrlClassificationTests(unittest.TestCase):

    def test_is_chatgpt_chatgpt_com(self) -> None:
        self.assertTrue(stp._is_chatgpt("https://chatgpt.com/"))

    def test_is_chatgpt_conversation_url(self) -> None:
        self.assertTrue(stp._is_chatgpt("https://chatgpt.com/c/69ec61f8-2ccc-83a4-af2a-8f88c8b01338"))

    def test_is_chatgpt_gpt_subdomain(self) -> None:
        self.assertTrue(stp._is_chatgpt("https://chatgpt.com/g/g-abc123/c/deadbeef-0000-0000-0000-000000000001"))

    def test_is_chatgpt_openai_legacy(self) -> None:
        self.assertTrue(stp._is_chatgpt("https://chat.openai.com/c/abc"))

    def test_is_chatgpt_false_for_other(self) -> None:
        self.assertFalse(stp._is_chatgpt("https://example.com/"))

    def test_conversation_id_extracts_uuid(self) -> None:
        url = "https://chatgpt.com/c/69ec61f8-2ccc-83a4-af2a-8f88c8b01338"
        self.assertEqual(stp._conversation_id(url), "69ec61f8-2ccc-83a4-af2a-8f88c8b01338")

    def test_conversation_id_empty_for_non_conversation(self) -> None:
        self.assertEqual(stp._conversation_id("https://chatgpt.com/"), "")

    def test_conversation_id_gpt_url_with_conversation(self) -> None:
        url = "https://chatgpt.com/g/g-abc/c/deadbeef-0000-0000-0000-000000000001"
        self.assertEqual(stp._conversation_id(url), "deadbeef-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# enumerate_tabs — AppleScript output parsing
# ---------------------------------------------------------------------------

class EnumerateTabsParsingTests(unittest.TestCase):
    """Test that enumerate_tabs correctly parses AppleScript output lines."""

    def _enumerate(self, stdout: str) -> list[dict]:
        result = _make_result(stdout)
        with patch("safari_tab_probe._run_osascript_script", return_value=result):
            return stp.enumerate_tabs()

    def test_single_tab_parsed(self) -> None:
        stdout = "TAB\t1\t1\t1\t1\thttps://chatgpt.com/c/abc123-0000-0000-0000-000000000001\tTest Chat\n"
        tabs = self._enumerate(stdout)
        self.assertEqual(len(tabs), 1)
        t = tabs[0]
        self.assertEqual(t["window_index"], 1)
        self.assertEqual(t["tab_index"], 1)
        self.assertTrue(t["is_front_window"])
        self.assertTrue(t["is_current_tab"])
        self.assertTrue(t["is_chatgpt"])
        self.assertTrue(t["is_conversation"])
        self.assertEqual(t["conversation_id"], "abc123-0000-0000-0000-000000000001")
        self.assertEqual(t["title"], "Test Chat")

    def test_non_chatgpt_tab(self) -> None:
        stdout = "TAB\t1\t1\t1\t1\thttps://example.com/\tExample\n"
        tabs = self._enumerate(stdout)
        self.assertEqual(len(tabs), 1)
        self.assertFalse(tabs[0]["is_chatgpt"])
        self.assertFalse(tabs[0]["is_conversation"])
        self.assertEqual(tabs[0]["conversation_id"], "")

    def test_multiple_tabs_parsed(self) -> None:
        stdout = (
            "TAB\t1\t1\t1\t1\thttps://chatgpt.com/c/aaa-0000-0000-0000-000000000001\tChat A\n"
            "TAB\t1\t2\t1\t0\thttps://example.com/\tExample\n"
            "TAB\t2\t1\t0\t1\thttps://chatgpt.com/\tChatGPT Home\n"
        )
        tabs = self._enumerate(stdout)
        self.assertEqual(len(tabs), 3)
        self.assertTrue(tabs[0]["is_front_window"])
        self.assertFalse(tabs[1]["is_current_tab"])
        self.assertFalse(tabs[2]["is_front_window"])

    def test_non_front_window_current_tab_flags(self) -> None:
        stdout = "TAB\t2\t3\t0\t1\thttps://chatgpt.com/\tChatGPT\n"
        tabs = self._enumerate(stdout)
        self.assertFalse(tabs[0]["is_front_window"])
        self.assertTrue(tabs[0]["is_current_tab"])

    def test_error_on_nonzero_returncode(self) -> None:
        result = _make_result("", returncode=1, stderr="some applescript error")
        with patch("safari_tab_probe._run_osascript_script", return_value=result):
            tabs = stp.enumerate_tabs()
        self.assertEqual(len(tabs), 1)
        self.assertIn("error", tabs[0])

    def test_error_safari_not_running(self) -> None:
        result = _make_result("", returncode=1, stderr="safari_not_running")
        with patch("safari_tab_probe._run_osascript_script", return_value=result):
            tabs = stp.enumerate_tabs()
        self.assertIn("Safari is not running", tabs[0]["error"])

    def test_error_no_windows(self) -> None:
        result = _make_result("", returncode=1, stderr="no_windows")
        with patch("safari_tab_probe._run_osascript_script", return_value=result):
            tabs = stp.enumerate_tabs()
        self.assertIn("no open windows", tabs[0]["error"])

    def test_timeout_returns_error(self) -> None:
        with patch(
            "safari_tab_probe._run_osascript_script",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=15),
        ):
            tabs = stp.enumerate_tabs()
        self.assertIn("timed out", tabs[0]["error"])

    def test_non_tab_lines_are_ignored(self) -> None:
        stdout = "SOMETHING_ELSE\t1\t1\t1\t1\thttps://example.com/\tExample\n"
        tabs = self._enumerate(stdout)
        self.assertEqual(tabs, [])

    def test_malformed_tab_line_is_skipped(self) -> None:
        stdout = "TAB\t1\t1\t1\n"  # too few fields
        tabs = self._enumerate(stdout)
        self.assertEqual(tabs, [])


# ---------------------------------------------------------------------------
# fetch_body_for_tab
# ---------------------------------------------------------------------------

class FetchBodyForTabTests(unittest.TestCase):

    def _make_tab(self, win=1, tab=1, is_conv=True) -> dict:
        return {
            "window_index": win,
            "tab_index": tab,
            "is_conversation": is_conv,
            "body_fetch_status": "pending",
            "body_preview": "",
        }

    def test_ok_result(self) -> None:
        result = _make_result("Hello ChatGPT content")
        with patch("safari_tab_probe._run_osascript_script", return_value=result):
            tab = self._make_tab()
            stp.fetch_body_for_tab(tab, 500)
        self.assertEqual(tab["body_fetch_status"], "ok")
        self.assertIn("Hello ChatGPT", tab["body_preview"])

    def test_error_result_stores_stderr(self) -> None:
        result = _make_result("", returncode=1, stderr="not allowed")
        with patch("safari_tab_probe._run_osascript_script", return_value=result):
            tab = self._make_tab()
            stp.fetch_body_for_tab(tab, 500)
        self.assertTrue(tab["body_fetch_status"].startswith("error:"))
        self.assertIn("not allowed", tab["body_fetch_status"])

    def test_timeout_stores_error(self) -> None:
        with patch(
            "safari_tab_probe._run_osascript_script",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=15),
        ):
            tab = self._make_tab()
            stp.fetch_body_for_tab(tab, 500)
        self.assertIn("timed out", tab["body_fetch_status"])

    def test_body_preview_trailing_newline_stripped(self) -> None:
        result = _make_result("content\n")
        with patch("safari_tab_probe._run_osascript_script", return_value=result):
            tab = self._make_tab()
            stp.fetch_body_for_tab(tab, 500)
        self.assertEqual(tab["body_preview"], "content")


# ---------------------------------------------------------------------------
# run_probe integration
# ---------------------------------------------------------------------------

class RunProbeTests(unittest.TestCase):

    def _setup_enumerate(self, stdout: str) -> None:
        """Patch _run_osascript_script so enumerate_tabs returns the given stdout."""
        self._enumerate_result = _make_result(stdout)

    def test_body_limit_zero_skips_all(self) -> None:
        stdout = "TAB\t1\t1\t1\t1\thttps://chatgpt.com/c/abc-0000-0000-0000-000000000001\tChat\n"
        with patch(
            "safari_tab_probe._run_osascript_script",
            return_value=_make_result(stdout),
        ):
            tabs = stp.run_probe(body_limit=0)
        self.assertEqual(tabs[0]["body_fetch_status"], "skipped")

    def test_non_conversation_tab_is_skipped(self) -> None:
        stdout = "TAB\t1\t1\t1\t1\thttps://example.com/\tExample\n"
        with patch(
            "safari_tab_probe._run_osascript_script",
            return_value=_make_result(stdout),
        ):
            tabs = stp.run_probe(body_limit=500)
        self.assertEqual(tabs[0]["body_fetch_status"], "skipped")

    def test_enumeration_error_propagates(self) -> None:
        with patch(
            "safari_tab_probe._run_osascript_script",
            return_value=_make_result("", returncode=1, stderr="safari_not_running"),
        ):
            tabs = stp.run_probe()
        self.assertIn("error", tabs[0])


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

class FormatHumanTests(unittest.TestCase):

    def _tab(self, **kwargs) -> dict:
        base = {
            "window_index": 1,
            "tab_index": 1,
            "title": "Test",
            "url": "https://chatgpt.com/c/abc-0000-0000-0000-000000000001",
            "is_front_window": True,
            "is_current_tab": True,
            "is_chatgpt": True,
            "is_conversation": True,
            "conversation_id": "abc-0000-0000-0000-000000000001",
            "body_fetch_status": "ok",
            "body_preview": "Hello world",
        }
        base.update(kwargs)
        return base

    def test_empty_tabs_returns_message(self) -> None:
        out = stp._format_human([])
        self.assertIn("no tabs", out)

    def test_error_dict_returns_error_prefix(self) -> None:
        out = stp._format_human([{"error": "Safari is not running"}])
        self.assertIn("ERROR", out)
        self.assertIn("Safari is not running", out)

    def test_front_window_flag_shown(self) -> None:
        out = stp._format_human([self._tab()])
        self.assertIn("FRONT_WINDOW", out)

    def test_current_tab_flag_shown(self) -> None:
        out = stp._format_human([self._tab()])
        self.assertIn("CURRENT_TAB", out)

    def test_chatgpt_flag_shown(self) -> None:
        out = stp._format_human([self._tab()])
        self.assertIn("CHATGPT", out)

    def test_conversation_flag_shown(self) -> None:
        out = stp._format_human([self._tab()])
        self.assertIn("CONVERSATION", out)

    def test_body_preview_shown(self) -> None:
        out = stp._format_human([self._tab(body_preview="ChatGPT preview text")])
        self.assertIn("ChatGPT preview text", out)

    def test_no_body_when_skipped(self) -> None:
        out = stp._format_human([self._tab(body_fetch_status="skipped", body_preview="")])
        self.assertIn("skipped", out)


# ---------------------------------------------------------------------------
# CLI / main
# ---------------------------------------------------------------------------

class MainCliTests(unittest.TestCase):

    def _tabs_fixture(self) -> list[dict]:
        return [
            {
                "window_index": 1,
                "tab_index": 1,
                "title": "Test Chat",
                "url": "https://chatgpt.com/c/abc-0000-0000-0000-000000000001",
                "is_front_window": True,
                "is_current_tab": True,
                "is_chatgpt": True,
                "is_conversation": True,
                "conversation_id": "abc-0000-0000-0000-000000000001",
                "body_fetch_status": "ok",
                "body_preview": "Hello",
            }
        ]

    def test_json_flag_outputs_json(self) -> None:
        with patch("safari_tab_probe.run_probe", return_value=self._tabs_fixture()):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = stp.main(["--json"])
        self.assertEqual(rc, 0)
        parsed = json.loads(buf.getvalue())
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["window_index"], 1)

    def test_default_outputs_human(self) -> None:
        with patch("safari_tab_probe.run_probe", return_value=self._tabs_fixture()):
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = stp.main([])
        self.assertEqual(rc, 0)
        self.assertIn("W1:T1", buf.getvalue())

    def test_out_json_extension_forces_json(self) -> None:
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = f.name
        try:
            with patch("safari_tab_probe.run_probe", return_value=self._tabs_fixture()):
                rc = stp.main(["--out", tmp])
            self.assertEqual(rc, 0)
            content = Path(tmp).read_text(encoding="utf-8")
            parsed = json.loads(content)
            self.assertEqual(parsed[0]["title"], "Test Chat")
        finally:
            os.unlink(tmp)

    def test_body_limit_zero_passed_to_run_probe(self) -> None:
        calls = []
        def fake_run_probe(body_limit=500):
            calls.append(body_limit)
            return []
        with patch("safari_tab_probe.run_probe", side_effect=fake_run_probe):
            import io
            from contextlib import redirect_stdout
            with redirect_stdout(io.StringIO()):
                stp.main(["--body-limit", "0"])
        self.assertEqual(calls, [0])


if __name__ == "__main__":
    unittest.main()
