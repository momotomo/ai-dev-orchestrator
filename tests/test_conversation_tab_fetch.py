"""Tests for conversation-tab-aware fetch helpers added to _bridge_common.py.

Covers:
- _extract_conversation_id URL parsing
- _enumerate_safari_tabs AppleScript output parsing
- _find_safari_tab_by_conversation_id selection logic
- _read_chatgpt_dom_for_fetch routing (conversation_tab / fallback paths)
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common as bc


def _make_result(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.returncode = returncode
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# _extract_conversation_id
# ---------------------------------------------------------------------------

class ExtractConversationIdTests(unittest.TestCase):

    def test_standard_chatgpt_url(self) -> None:
        url = "https://chatgpt.com/c/69ec61f8-2ccc-83a4-af2a-8f88c8b01338"
        self.assertEqual(bc._extract_conversation_id(url), "69ec61f8-2ccc-83a4-af2a-8f88c8b01338")

    def test_gpt_url_with_conversation(self) -> None:
        url = "https://chatgpt.com/g/g-abc123/c/deadbeef-0000-0000-0000-000000000001"
        self.assertEqual(bc._extract_conversation_id(url), "deadbeef-0000-0000-0000-000000000001")

    def test_no_conversation_in_url(self) -> None:
        self.assertEqual(bc._extract_conversation_id("https://chatgpt.com/"), "")

    def test_empty_url(self) -> None:
        self.assertEqual(bc._extract_conversation_id(""), "")

    def test_non_chatgpt_url_with_c_path(self) -> None:
        # "/c/<uuid>" pattern anywhere in URL is matched (intentionally permissive)
        url = "https://example.com/c/12345678-0000-0000-0000-000000000001"
        self.assertEqual(bc._extract_conversation_id(url), "12345678-0000-0000-0000-000000000001")

    def test_short_id_not_matched(self) -> None:
        # UUID must be at least 8 chars after /c/
        self.assertEqual(bc._extract_conversation_id("https://chatgpt.com/c/abc"), "")


# ---------------------------------------------------------------------------
# _enumerate_safari_tabs  (AppleScript output parsing)
# ---------------------------------------------------------------------------

class EnumerateSafariTabsTests(unittest.TestCase):

    def _run(self, stdout: str, returncode: int = 0, stderr: str = "") -> list[dict]:
        result = _make_result(stdout, returncode, stderr)
        with patch("_bridge_common._run_osascript_script", return_value=result):
            return bc._enumerate_safari_tabs()

    def test_single_conversation_tab(self) -> None:
        line = "TAB\t1\t1\t1\t1\thttps://chatgpt.com/c/69ec61f8-2ccc-83a4-af2a-8f88c8b01338\tTest Chat\n"
        tabs = self._run(line)
        self.assertEqual(len(tabs), 1)
        t = tabs[0]
        self.assertEqual(t["window_index"], 1)
        self.assertEqual(t["tab_index"], 1)
        self.assertTrue(t["is_front_window"])
        self.assertTrue(t["is_current_tab"])
        self.assertEqual(t["conversation_id"], "69ec61f8-2ccc-83a4-af2a-8f88c8b01338")

    def test_multiple_tabs(self) -> None:
        stdout = (
            "TAB\t1\t1\t1\t1\thttps://chatgpt.com/c/aaa-0000-0000-0000-000000000001\tChat A\n"
            "TAB\t1\t2\t1\t0\thttps://example.com/\tExample\n"
            "TAB\t2\t1\t0\t1\thttps://chatgpt.com/c/bbb-0000-0000-0000-000000000002\tChat B\n"
        )
        tabs = self._run(stdout)
        self.assertEqual(len(tabs), 3)
        self.assertEqual(tabs[0]["conversation_id"], "aaa-0000-0000-0000-000000000001")
        self.assertEqual(tabs[1]["conversation_id"], "")
        self.assertEqual(tabs[2]["conversation_id"], "bbb-0000-0000-0000-000000000002")

    def test_nonzero_returncode_returns_empty(self) -> None:
        tabs = self._run("", returncode=1, stderr="error")
        self.assertEqual(tabs, [])

    def test_timeout_returns_empty(self) -> None:
        with patch(
            "_bridge_common._run_osascript_script",
            side_effect=subprocess.TimeoutExpired(cmd="osascript", timeout=15),
        ):
            tabs = bc._enumerate_safari_tabs()
        self.assertEqual(tabs, [])

    def test_non_tab_lines_ignored(self) -> None:
        tabs = self._run("SOMETHING\t1\t1\t1\t1\thttps://chatgpt.com/\tTest\n")
        self.assertEqual(tabs, [])

    def test_malformed_line_skipped(self) -> None:
        tabs = self._run("TAB\t1\t1\t1\n")
        self.assertEqual(tabs, [])


# ---------------------------------------------------------------------------
# _find_safari_tab_by_conversation_id
# ---------------------------------------------------------------------------

class FindSafariTabByConversationIdTests(unittest.TestCase):

    TARGET_ID = "69ec61f8-2ccc-83a4-af2a-8f88c8b01338"

    def _tab(self, win=1, tab=1, front=True, current=True, conv_id=None) -> dict:
        cid = conv_id if conv_id is not None else self.TARGET_ID
        return {
            "window_index": win,
            "tab_index": tab,
            "is_front_window": front,
            "is_current_tab": current,
            "url": f"https://chatgpt.com/c/{cid}",
            "title": "Test",
            "conversation_id": cid,
        }

    def _patch_enumerate(self, tabs: list[dict]):
        return patch("_bridge_common._enumerate_safari_tabs", return_value=tabs)

    def test_returns_none_for_empty_conversation_id(self) -> None:
        self.assertIsNone(bc._find_safari_tab_by_conversation_id(""))

    def test_returns_none_when_no_tabs(self) -> None:
        with self._patch_enumerate([]):
            result = bc._find_safari_tab_by_conversation_id(self.TARGET_ID)
        self.assertIsNone(result)

    def test_returns_matching_tab(self) -> None:
        tab = self._tab()
        with self._patch_enumerate([tab]):
            result = bc._find_safari_tab_by_conversation_id(self.TARGET_ID)
        self.assertIsNotNone(result)
        self.assertEqual(result["conversation_id"], self.TARGET_ID)

    def test_returns_none_when_id_not_found(self) -> None:
        tab = self._tab(conv_id="00000000-0000-0000-0000-000000000000")
        with self._patch_enumerate([tab]):
            result = bc._find_safari_tab_by_conversation_id(self.TARGET_ID)
        self.assertIsNone(result)

    def test_prefers_front_current_tab(self) -> None:
        # Multiple matching tabs — front+current wins
        t1 = self._tab(win=1, tab=1, front=False, current=True)
        t2 = self._tab(win=2, tab=1, front=True, current=True)
        with self._patch_enumerate([t1, t2]):
            result = bc._find_safari_tab_by_conversation_id(self.TARGET_ID)
        self.assertEqual(result["window_index"], 2)

    def test_prefers_current_tab_over_non_current(self) -> None:
        t1 = self._tab(win=1, tab=2, front=True, current=False)
        t2 = self._tab(win=1, tab=1, front=False, current=True)
        with self._patch_enumerate([t1, t2]):
            result = bc._find_safari_tab_by_conversation_id(self.TARGET_ID)
        self.assertEqual(result["tab_index"], 1)  # current_tab preferred

    def test_falls_back_to_first_match(self) -> None:
        t1 = self._tab(win=1, tab=2, front=False, current=False)
        t2 = self._tab(win=2, tab=1, front=False, current=False)
        with self._patch_enumerate([t1, t2]):
            result = bc._find_safari_tab_by_conversation_id(self.TARGET_ID)
        self.assertEqual(result["tab_index"], 2)  # first match


# ---------------------------------------------------------------------------
# _read_chatgpt_dom_for_fetch — routing
# ---------------------------------------------------------------------------

class ReadChatgptDomForFetchTests(unittest.TestCase):

    CONV_URL = "https://chatgpt.com/c/69ec61f8-2ccc-83a4-af2a-8f88c8b01338"
    CONV_ID = "69ec61f8-2ccc-83a4-af2a-8f88c8b01338"

    def _mock_page(self, body_text: str = "page body") -> MagicMock:
        page = MagicMock()
        page.evaluate.return_value = body_text
        page.config = {"conversation_url_keywords": ["/c/"]}
        return page

    def _tab(self, win=1, tab=1) -> dict:
        return {
            "window_index": win,
            "tab_index": tab,
            "is_front_window": True,
            "is_current_tab": True,
            "url": self.CONV_URL,
            "title": "Test",
            "conversation_id": self.CONV_ID,
        }

    def test_no_conversation_url_uses_current_tab(self) -> None:
        page = self._mock_page("body text")
        text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(page, conversation_url="")
        self.assertEqual(route, bc.FETCH_ROUTE_CURRENT_TAB)
        self.assertEqual(text, "body text")
        self.assertEqual(resolved_url, "")

    def test_conversation_tab_found_returns_conversation_tab_route(self) -> None:
        page = self._mock_page()
        tab = self._tab()
        with patch("_bridge_common._find_safari_tab_by_conversation_id", return_value=tab):
            with patch("_bridge_common._run_safari_javascript_in_tab", return_value="conv body"):
                text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(page, self.CONV_URL)
        self.assertEqual(route, bc.FETCH_ROUTE_CONVERSATION_TAB)
        self.assertEqual(text, "conv body")
        self.assertEqual(resolved_url, self.CONV_URL)

    def test_conversation_tab_not_found_falls_back_to_current(self) -> None:
        page = self._mock_page("fallback body")
        with patch("_bridge_common._find_safari_tab_by_conversation_id", return_value=None):
            text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(page, self.CONV_URL)
        self.assertEqual(route, bc.FETCH_ROUTE_CONVERSATION_TAB_NOT_FOUND)
        self.assertEqual(text, "fallback body")
        self.assertEqual(resolved_url, "")

    def test_conversation_tab_read_error_falls_back(self) -> None:
        page = self._mock_page("fallback after error")
        tab = self._tab()
        with patch("_bridge_common._find_safari_tab_by_conversation_id", return_value=tab):
            with patch(
                "_bridge_common._run_safari_javascript_in_tab",
                side_effect=bc.BridgeError("JS error"),
            ):
                text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(page, self.CONV_URL)
        self.assertEqual(route, bc.FETCH_ROUTE_CONVERSATION_TAB_READ_ERROR)
        self.assertEqual(text, "fallback after error")
        self.assertEqual(resolved_url, "")

    def test_conversation_tab_empty_body_falls_back(self) -> None:
        page = self._mock_page("fallback from empty")
        tab = self._tab()
        with patch("_bridge_common._find_safari_tab_by_conversation_id", return_value=tab):
            with patch("_bridge_common._run_safari_javascript_in_tab", return_value=""):
                text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(page, self.CONV_URL)
        self.assertEqual(route, bc.FETCH_ROUTE_FALLBACK_CURRENT_TAB)
        self.assertEqual(text, "fallback from empty")
        self.assertEqual(resolved_url, "")

    def test_request_anchor_found_returns_request_anchor_route(self) -> None:
        page = self._mock_page("wrong active tab")
        tab = self._tab()
        with patch("_bridge_common._conversation_tab_candidates", return_value=[tab]):
            with patch(
                "_bridge_common._run_safari_javascript_in_tab",
                return_value="あなた:\nhello world\nChatGPT:\nThinking",
            ):
                text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(
                    page,
                    conversation_url="https://chatgpt.com/g/demo/project",
                    request_anchor_text="hello\nworld",
                )
        self.assertEqual(route, bc.FETCH_ROUTE_REQUEST_ANCHOR_CONVERSATION_TAB)
        self.assertIn("hello world", text)
        self.assertEqual(resolved_url, self.CONV_URL)

    def test_request_anchor_not_found_reports_route_and_falls_back(self) -> None:
        page = self._mock_page("active fallback")
        tab = self._tab()
        with patch("_bridge_common._conversation_tab_candidates", return_value=[tab]):
            with patch("_bridge_common._run_safari_javascript_in_tab", return_value="unrelated"):
                text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(
                    page,
                    conversation_url="https://chatgpt.com/g/demo/project",
                    request_anchor_text="hello world",
                )
        self.assertEqual(route, bc.FETCH_ROUTE_REQUEST_ANCHOR_TAB_NOT_FOUND)
        self.assertEqual(text, "active fallback")
        self.assertEqual(resolved_url, "")

    def test_request_anchor_not_found_ignores_missing_osascript_during_fallback(self) -> None:
        page = self._mock_page("active fallback")
        tab = self._tab()
        with patch("_bridge_common._conversation_tab_candidates", return_value=[tab]):
            with patch("_bridge_common._run_safari_javascript_in_tab", return_value="unrelated"):
                with patch(
                    "_bridge_common.frontmost_safari_tab_info",
                    side_effect=FileNotFoundError("osascript"),
                ):
                    text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(
                        page,
                        conversation_url="https://chatgpt.com/g/demo/project",
                        request_anchor_text="hello world",
                    )
        self.assertEqual(route, bc.FETCH_ROUTE_REQUEST_ANCHOR_TAB_NOT_FOUND)
        self.assertEqual(text, "active fallback")
        self.assertEqual(resolved_url, "")

    def test_request_anchor_current_conversation_fallback_resolves_url(self) -> None:
        page = self._mock_page("あなた:\nhello world\nChatGPT:\nThinking")
        tab = self._tab()
        with patch("_bridge_common._conversation_tab_candidates", return_value=[tab]):
            with patch("_bridge_common._run_safari_javascript_in_tab", return_value="unrelated"):
                with patch(
                    "_bridge_common.frontmost_safari_tab_info",
                    return_value={"url": self.CONV_URL, "title": "ChatGPT"},
                ):
                    text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(
                        page,
                        conversation_url="https://chatgpt.com/g/demo/project",
                        request_anchor_text="hello\nworld",
                    )
        self.assertEqual(route, bc.FETCH_ROUTE_REQUEST_ANCHOR_CONVERSATION_TAB)
        self.assertIn("hello world", text)
        self.assertEqual(resolved_url, self.CONV_URL)

    def test_request_anchor_read_error_reports_route_and_falls_back(self) -> None:
        page = self._mock_page("active fallback")
        tab = self._tab()
        with patch("_bridge_common._conversation_tab_candidates", return_value=[tab]):
            with patch(
                "_bridge_common._run_safari_javascript_in_tab",
                side_effect=bc.BridgeError("read failed"),
            ):
                text, route, resolved_url = bc._read_chatgpt_dom_for_fetch(
                    page,
                    conversation_url="https://chatgpt.com/g/demo/project",
                    request_anchor_text="hello world",
                )
        self.assertEqual(route, bc.FETCH_ROUTE_REQUEST_ANCHOR_TAB_READ_ERROR)
        self.assertEqual(text, "active fallback")
        self.assertEqual(resolved_url, "")

    def test_fetch_route_constants_match_spec(self) -> None:
        """Verify route constants have the expected string values."""
        self.assertEqual(bc.FETCH_ROUTE_CURRENT_TAB, "current_tab")
        self.assertEqual(bc.FETCH_ROUTE_CONVERSATION_TAB, "conversation_tab")
        self.assertEqual(bc.FETCH_ROUTE_REQUEST_ANCHOR_CONVERSATION_TAB, "request_anchor_conversation_tab")
        self.assertEqual(bc.FETCH_ROUTE_FALLBACK_CURRENT_TAB, "fallback_current_tab")
        self.assertEqual(bc.FETCH_ROUTE_CONVERSATION_TAB_NOT_FOUND, "conversation_tab_not_found")
        self.assertEqual(bc.FETCH_ROUTE_CONVERSATION_TAB_READ_ERROR, "conversation_tab_read_error")
        self.assertEqual(bc.FETCH_ROUTE_REQUEST_ANCHOR_TAB_NOT_FOUND, "request_anchor_tab_not_found")
        self.assertEqual(bc.FETCH_ROUTE_REQUEST_ANCHOR_TAB_READ_ERROR, "request_anchor_tab_read_error")


if __name__ == "__main__":
    unittest.main()
