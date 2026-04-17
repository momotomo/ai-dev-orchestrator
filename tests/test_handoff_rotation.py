from __future__ import annotations

import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common  # noqa: E402
import fetch_next_prompt  # noqa: E402
import request_prompt_from_report  # noqa: E402
from _bridge_common import BridgeError, BridgeStop  # noqa: E402


class _DummyPage:
    def __init__(self, url: str, title: str = "ChatGPT") -> None:
        self.front_tab = {"url": url, "title": title}


class HandoffRotationTests(unittest.TestCase):
    def test_project_page_send_treats_empty_probe_as_submitted_unconfirmed(self) -> None:
        page = _DummyPage("https://chatgpt.com/g/g-p-demo/project")

        @contextmanager
        def fake_open_chatgpt_page(**_: object):
            yield None, page, {"chat_url_prefix": "https://chatgpt.com/"}, page.front_tab

        with (
            patch.object(_bridge_common, "open_chatgpt_page", fake_open_chatgpt_page),
            patch.object(
                _bridge_common,
                "ensure_project_page_github_source_ready",
                return_value={
                    "composerFound": True,
                    "plusFound": True,
                    "plusClicked": True,
                    "menuOpened": True,
                    "moreFound": True,
                    "moreActionPerformed": True,
                    "submenuOpened": True,
                    "sourceAddFound": True,
                    "githubFound": True,
                    "githubClicked": True,
                    "githubPillConfirmed": True,
                    "githubPillRemoveButtonFound": True,
                    "finalAttachConfirmationKind": "github_pill_remove_button",
                },
            ),
            patch.object(
                _bridge_common,
                "fill_chatgpt_composer",
                return_value={"matchKind": "preferred_hint", "matchedHint": "作曲アプリ開発 内の新しいチャット", "projectName": "作曲アプリ開発"},
            ),
            patch.object(_bridge_common, "submit_chatgpt_message", return_value=None),
            patch.object(
                _bridge_common,
                "_read_post_send_state",
                side_effect=BridgeError("新チャット送信後の状態確認に失敗しました: Safari から空の応答が返りました。"),
            ),
            patch.object(_bridge_common.time, "time", side_effect=[0, 1, 16]),
            patch.object(_bridge_common.time, "sleep", return_value=None),
        ):
            result = _bridge_common.send_to_chatgpt_in_current_surface(
                "handoff body",
                preferred_hint="内の新しいチャット",
                project_page_mode=True,
            )

        self.assertEqual(result["signal"], "submitted_unconfirmed")
        self.assertIn("空の応答", result["warning"])
        self.assertEqual(result["match_kind"], "preferred_hint")
        self.assertEqual(result["project_name"], "作曲アプリ開発")

    def test_rotate_chat_does_not_resend_after_unconfirmed_submit(self) -> None:
        project_url = "https://chatgpt.com/g/g-p-demo/project"
        conversation_url = "https://chatgpt.com/g/g-p-demo/c/demo"

        frontmost_calls: list[bool] = []

        def fake_frontmost(config: object, *, require_conversation: bool = True) -> dict[str, str]:
            del config
            frontmost_calls.append(require_conversation)
            if len(frontmost_calls) == 1:
                return {"url": conversation_url, "title": "ChatGPT"}
            if require_conversation:
                raise BridgeError(
                    f"Safari の現在タブが ChatGPT の対象会話ではありません。 対象チャットを開いてください: ChatGPT {project_url}"
                )
            return {"url": project_url, "title": "ChatGPT"}

        with (
            patch.object(_bridge_common, "load_browser_config", return_value={}),
            patch.object(_bridge_common, "frontmost_safari_tab_info", side_effect=fake_frontmost),
            patch.object(_bridge_common, "derive_chatgpt_project_page_url", return_value=project_url),
            patch.object(_bridge_common, "navigate_current_chatgpt_tab", return_value={"url": project_url, "title": "ChatGPT"}),
            patch.object(
                _bridge_common,
                "send_to_chatgpt_in_current_surface",
                return_value={
                    "url": project_url,
                    "title": "ChatGPT",
                    "signal": "submitted_unconfirmed",
                    "warning": "新チャット送信後の状態確認に失敗しました: Safari から空の応答が返りました。",
                },
            ) as send_mock,
            patch.object(_bridge_common.time, "time", side_effect=[0, 1, 31]),
            patch.object(_bridge_common.time, "sleep", return_value=None),
        ):
            result = _bridge_common.rotate_chat_with_handoff("handoff body")

        self.assertEqual(send_mock.call_count, 1)
        self.assertTrue(send_mock.call_args.kwargs["project_page_mode"])
        self.assertEqual(send_mock.call_args.kwargs["send_context"], "rotation_handoff")
        self.assertEqual(result["signal"], "submitted_unconfirmed")
        self.assertEqual(result["url"], project_url)


class HandoffWaitTransitionTests(unittest.TestCase):
    def test_fetch_uses_project_page_wait_after_submitted_unconfirmed(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "report:1",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "submitted_unconfirmed",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }

        with (
            patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request text"),
            patch.object(
                fetch_next_prompt,
                "wait_for_issue_centric_reply_text",
                return_value="\n".join(
                    [
                        "あなた:",
                        "request text",
                        "ChatGPT:",
                        "===CHATGPT_PROMPT_REPLY===",
                        "Phase: next prompt",
                        "===END_REPLY===",
                    ]
                ),
            ) as wait_mock,
            patch.object(fetch_next_prompt, "log_text", side_effect=["raw-log", "legacy-log"]),
            patch.object(fetch_next_prompt, "runtime_prompt_path", return_value=REPO_ROOT / "tests" / "tmp_prompt.md"),
            patch.object(fetch_next_prompt, "read_text", return_value=""),
            patch.object(fetch_next_prompt, "write_text", return_value=None),
            patch.object(fetch_next_prompt, "save_state", return_value=None) as save_mock,
        ):
            with self.assertRaises(BridgeStop) as cm:
                fetch_next_prompt.run(dict(state), [])

        # allow_project_page_wait must be True even though the reply was legacy
        wait_mock.assert_called_once()
        self.assertTrue(wait_mock.call_args.kwargs["allow_project_page_wait"])
        # State must reflect the legacy-stop (error, not success)
        saved_state = save_mock.call_args.args[0]
        self.assertEqual(saved_state["mode"], "awaiting_user")
        self.assertTrue(saved_state.get("error"))
        self.assertIn("legacy", str(cm.exception).lower())

    def test_soft_wait_uses_distinct_request_log_prefix(self) -> None:
        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "pending_handoff_source": "report-source",
            "pending_handoff_log": "logs/handoff_received.md",
        }

        logged_prefixes: list[str] = []

        def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
            del text, suffix
            logged_prefixes.append(prefix)
            return REPO_ROOT / "logs" / f"{prefix}.md"

        args = request_prompt_from_report.parse_args([])
        args.next_todo = ""
        args.open_questions = ""
        args.current_status = ""

        with (
            patch.object(request_prompt_from_report, "build_report_request_source", return_value="report-source"),
            patch.object(request_prompt_from_report, "read_pending_handoff_text", return_value="handoff body"),
            patch.object(
                request_prompt_from_report,
                "rotate_chat_with_handoff",
                return_value={
                    "url": "https://chatgpt.com/g/g-p-demo/project",
                    "title": "ChatGPT",
                    "signal": "submitted_unconfirmed",
                    "warning": "新チャット送信後の状態確認に失敗しました: Safari から空の応答が返りました。",
                    "github_source_attach_status": "probe_failed",
                    "github_source_attach_boundary": "composer_more_submenu_not_open",
                    "github_source_attach_detail": "connector submenu を確認できませんでした。",
                    "github_source_attach_context": "rotation_handoff",
                    "github_source_attach_log": "logs/project_page_github_source_attach_rotation.md",
                    "request_send_continued_without_github_source": True,
                    "match_kind": "preferred_hint",
                    "matched_hint": "作曲アプリ開発 内の新しいチャット",
                    "project_name": "作曲アプリ開発",
                },
            ),
            patch.object(request_prompt_from_report, "log_text", side_effect=fake_log_text),
            patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda path: str(path)),
            patch.object(request_prompt_from_report, "save_state", return_value=None) as save_mock,
            patch("builtins.print"),
        ):
            rc = request_prompt_from_report.run_rotated_report_request(state, args, "last report")

        self.assertEqual(rc, 0)
        self.assertIn("chat_rotated", logged_prefixes)
        self.assertIn("sent_prompt_request_from_report_soft_wait", logged_prefixes)
        self.assertNotIn("sent_prompt_request_from_report", logged_prefixes)
        saved_state = save_mock.call_args.args[0]
        self.assertEqual(saved_state["pending_request_signal"], "submitted_unconfirmed")
        self.assertEqual(saved_state["github_source_attach_status"], "probe_failed")
        self.assertTrue(saved_state["request_send_continued_without_github_source"])


if __name__ == "__main__":
    unittest.main()
