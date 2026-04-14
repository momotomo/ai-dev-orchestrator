from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import _bridge_common  # noqa: E402
import bridge_orchestrator  # noqa: E402
import request_next_prompt  # noqa: E402


class RequestNextPromptTests(unittest.TestCase):
    def test_build_initial_request_prefers_ready_issue_reference(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="#20 runtime entry",
            request_body="",
            project_path="/tmp/repo",
        )
        request_text, request_hash, request_source = request_next_prompt.build_initial_request(args)
        self.assertIn("current ready issue: #20 runtime entry", request_text)
        self.assertIn("ready issue を今回の実行単位正本として使う", request_text)
        self.assertTrue(request_hash)
        self.assertTrue(request_source.startswith("ready_issue:"))
        self.assertIn("===CHATGPT_DECISION_JSON===", request_text)
        self.assertIn("===CHATGPT_CODEX_BODY===", request_text)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", request_text)
        self.assertNotIn("===CHATGPT_NO_CODEX===", request_text)

    def test_build_initial_request_uses_override_source_for_request_body(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="",
            request_body="Target repo: /tmp/repo\nOverride reason: recovery",
            project_path="/tmp/repo",
        )
        request_text, _, request_source = request_next_prompt.build_initial_request(args)
        self.assertIn("Override reason: recovery", request_text)
        self.assertTrue(request_source.startswith("override:"))
        self.assertIn("===CHATGPT_DECISION_JSON===", request_text)
        self.assertIn("===CHATGPT_CODEX_BODY===", request_text)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", request_text)
        self.assertNotIn("===CHATGPT_NO_CODEX===", request_text)

    def test_compose_ready_issue_request_text_requires_issue_centric_contract_only(self) -> None:
        request_text = request_next_prompt.compose_ready_issue_request_text(
            "#2 Ready: add rehearsal marker and completion note",
            Path("/tmp/repo"),
        )
        self.assertIn("current ready issue: #2 Ready: add rehearsal marker and completion note", request_text)
        self.assertIn("issue-centric contract only", request_text)
        self.assertIn("===CHATGPT_DECISION_JSON===", request_text)
        self.assertIn("===END_DECISION_JSON===", request_text)
        self.assertIn("===CHATGPT_CODEX_BODY===", request_text)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", request_text)
        self.assertNotIn("===CHATGPT_NO_CODEX===", request_text)

    def test_compose_override_request_text_requires_issue_centric_contract_only(self) -> None:
        request_text = request_next_prompt.compose_override_request_text(
            "repo: /tmp/repo\ntarget_issue: #2\nrequest: keep this bounded"
        )
        self.assertIn("issue-centric contract only", request_text)
        self.assertIn("===CHATGPT_DECISION_JSON===", request_text)
        self.assertIn("===END_DECISION_JSON===", request_text)
        self.assertIn("===CHATGPT_CODEX_BODY===", request_text)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", request_text)
        self.assertNotIn("===CHATGPT_NO_CODEX===", request_text)

    def test_build_initial_request_rejects_ambiguous_entry_flags(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="#20 runtime entry",
            request_body="override text",
            project_path="/tmp/repo",
        )
        with self.assertRaises(request_next_prompt.BridgeError):
            request_next_prompt.build_initial_request(args)

    def test_log_prefixes_follow_request_source_kind(self) -> None:
        self.assertEqual(
            request_next_prompt.request_log_prefixes("ready_issue:abc"),
            ("prepared_prompt_request_from_ready_issue", "sent_prompt_request_from_ready_issue"),
        )
        self.assertEqual(
            request_next_prompt.request_log_prefixes("override:abc"),
            ("prepared_prompt_request_from_override", "sent_prompt_request_from_override"),
        )
        self.assertEqual(
            request_next_prompt.request_log_prefixes("initial:abc"),
            ("prepared_prompt_request_from_override", "sent_prompt_request_from_override"),
        )

    def test_load_retryable_initial_request_accepts_ready_issue_source(self) -> None:
        state = {
            "pending_request_source": "",
            "prepared_request_status": "retry_send",
            "prepared_request_source": "ready_issue:abc",
            "prepared_request_hash": "hash123",
            "prepared_request_log": "logs/request.md",
        }
        with patch.object(request_next_prompt, "read_prepared_request_text", return_value="request text"):
            retryable = request_next_prompt.load_retryable_initial_request(state)
        self.assertEqual(retryable, ("request text", "hash123", "ready_issue:abc"))


class InitialRequestSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "chat_url_prefix": "https://chatgpt.com/",
            "conversation_url_keywords": ["/c/"],
            "project_page_url": "",
        }

    def test_existing_conversation_url_is_still_accepted(self) -> None:
        with (
            patch.object(_bridge_common, "load_browser_config", return_value=self.config),
            patch.object(
                _bridge_common,
                "frontmost_safari_tab_info",
                return_value={"url": "https://chatgpt.com/c/abc", "title": "ChatGPT"},
            ),
            patch.object(_bridge_common, "send_to_chatgpt") as send_to_chatgpt,
            patch.object(_bridge_common, "send_to_chatgpt_in_current_surface") as send_to_surface,
        ):
            _bridge_common.send_initial_request_to_chatgpt("hello")
        send_to_chatgpt.assert_called_once_with("hello")
        send_to_surface.assert_not_called()

    def test_project_page_is_accepted_for_initial_request_boundary(self) -> None:
        with (
            patch.object(_bridge_common, "load_browser_config", return_value=self.config),
            patch.object(
                _bridge_common,
                "frontmost_safari_tab_info",
                return_value={
                    "url": "https://chatgpt.com/g/g-p-123/project",
                    "title": "ChatGPT - Project",
                },
            ),
            patch.object(_bridge_common, "send_to_chatgpt") as send_to_chatgpt,
            patch.object(_bridge_common, "send_to_chatgpt_in_current_surface") as send_to_surface,
        ):
            _bridge_common.send_initial_request_to_chatgpt("hello")
        send_to_chatgpt.assert_not_called()
        send_to_surface.assert_called_once()
        self.assertEqual(send_to_surface.call_args.kwargs["project_page_mode"], True)
        self.assertEqual(send_to_surface.call_args.kwargs["require_conversation"], False)
        self.assertEqual(send_to_surface.call_args.kwargs["require_target_chat"], False)

    def test_non_chatgpt_pages_are_still_rejected(self) -> None:
        with (
            patch.object(_bridge_common, "load_browser_config", return_value=self.config),
            patch.object(
                _bridge_common,
                "frontmost_safari_tab_info",
                side_effect=_bridge_common.BridgeError(
                    "Safari の現在タブが ChatGPT ではありません: Example https://example.com"
                ),
            ),
        ):
            with self.assertRaises(_bridge_common.BridgeError) as ctx:
                _bridge_common.send_initial_request_to_chatgpt("hello")
        self.assertIn("ChatGPT ではありません", str(ctx.exception))

    def test_chatgpt_root_page_stays_clear_when_not_conversation_or_project_page(self) -> None:
        with (
            patch.object(_bridge_common, "load_browser_config", return_value=self.config),
            patch.object(
                _bridge_common,
                "frontmost_safari_tab_info",
                return_value={"url": "https://chatgpt.com/", "title": "ChatGPT"},
            ),
        ):
            with self.assertRaises(_bridge_common.BridgeError) as ctx:
                _bridge_common.send_initial_request_to_chatgpt("hello")
        self.assertIn("対象会話でも project ページでもありません", str(ctx.exception))

    def test_load_retryable_initial_request_accepts_prepared_ready_issue_source(self) -> None:
        state = {
            "pending_request_source": "",
            "prepared_request_status": "prepared",
            "prepared_request_source": "ready_issue:abc",
            "prepared_request_hash": "hash123",
            "prepared_request_log": "logs/request.md",
        }
        with patch.object(request_next_prompt, "read_prepared_request_text", return_value="request text"):
            retryable = request_next_prompt.load_retryable_initial_request(state)
        self.assertEqual(retryable, ("request text", "hash123", "ready_issue:abc"))


class ProjectPageGithubSourcePreflightTests(unittest.TestCase):
    def _base_payload(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "composerFound": True,
            "plusFound": True,
            "plusClicked": False,
            "menuOpened": False,
            "menuItems": [],
            "menuCandidateLabels": [],
            "moreFound": False,
            "moreClicked": False,
            "submenuOpened": False,
            "submenuItems": [],
            "submenuCandidateLabels": [],
            "sourceAddFound": False,
            "githubFound": False,
            "githubClicked": False,
            "githubPillConfirmed": False,
            "githubPillRemoveButtonFound": False,
            "githubPillVisible": False,
            "githubPillLabels": [],
            "githubSelectedLike": False,
            "githubClickConfirmed": False,
            "visibleLabels": [],
            "beforeVisibleLabels": [],
            "afterVisibleLabels": [],
        }
        payload.update(overrides)
        return payload

    def test_project_level_source_dialog_like_payload_does_not_become_unavailable(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": False,
                "moreFound": False,
                "moreClicked": False,
                "submenuOpened": False,
                "sourceAddFound": True,
                "githubFound": False,
                "githubClicked": False,
                "githubSelectedLike": False,
                "githubClickConfirmed": False,
            }
        )
        self.assertEqual(result.status, "probe_failed")
        self.assertEqual(result.boundary, "composer_menu_not_open")

    def test_github_and_add_sources_are_treated_as_parallel_items(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": True,
                "moreFound": True,
                "moreClicked": True,
                "submenuOpened": True,
                "sourceAddFound": True,
                "githubFound": True,
                "githubClicked": True,
                "githubPillConfirmed": True,
                "githubPillRemoveButtonFound": True,
            }
        )
        self.assertEqual(result.status, "available")
        self.assertEqual(result.boundary, "github_pill_confirmed")

    def test_missing_github_under_more_is_unavailable(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": True,
                "moreFound": True,
                "moreClicked": True,
                "submenuOpened": True,
                "sourceAddFound": True,
                "githubFound": False,
                "githubClicked": False,
                "githubSelectedLike": False,
                "githubClickConfirmed": False,
            }
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.boundary, "github_item_missing")

    def test_missing_more_after_menu_open_is_probe_failed(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": True,
                "moreFound": False,
                "moreClicked": False,
                "submenuOpened": False,
                "sourceAddFound": False,
                "githubFound": False,
                "githubClicked": False,
                "githubSelectedLike": False,
                "githubClickConfirmed": False,
            }
        )
        self.assertEqual(result.status, "probe_failed")
        self.assertEqual(result.boundary, "composer_more_missing")

    def test_missing_menu_after_plus_click_is_probe_failed(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": False,
                "moreFound": False,
                "moreClicked": False,
                "submenuOpened": False,
                "sourceAddFound": False,
                "githubFound": False,
                "githubClicked": False,
                "githubSelectedLike": False,
                "githubClickConfirmed": False,
            }
        )
        self.assertEqual(result.status, "probe_failed")
        self.assertEqual(result.boundary, "composer_menu_not_open")

    def test_builder_includes_real_plus_trigger_label(self) -> None:
        script = _bridge_common._build_project_page_github_source_probe_script()
        self.assertIn("#composer-plus-btn", script)
        self.assertIn("[data-testid='composer-plus-btn']", script)
        self.assertIn("ファイルの追加など", script)

    def test_builder_includes_github_and_add_sources_as_parallel_items(self) -> None:
        script = _bridge_common._build_project_page_github_source_probe_script()
        self.assertIn("情報源を追加する", script)
        self.assertIn("GitHub", script)
        self.assertIn("aria-controls", script)
        self.assertIn("GitHub：クリックして削除", script)

    def test_preflight_error_reports_exact_boundary(self) -> None:
        class FakePage:
            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

        fake_page = FakePage()
        payload = self._base_payload(
            plusClicked=True,
            menuOpened=True,
            menuItems=[{"text": "さらに表示", "role": "menuitem"}],
            moreFound=True,
            moreClicked=True,
        )
        with (
            patch.object(_bridge_common, "_probe_project_page_github_source", return_value=payload),
            patch.object(
                _bridge_common,
                "_log_project_page_github_source_probe",
                return_value=Path("/tmp/project_page_github_source_probe.json"),
            ),
            patch.object(
                _bridge_common,
                "log_page_dump",
                return_value=Path("/tmp/project_page_github_source_dump.txt"),
            ),
        ):
            with self.assertRaises(_bridge_common.BridgeError) as ctx:
                _bridge_common.ensure_project_page_github_source_ready(fake_page)
        self.assertIn("boundary=composer_more_submenu_not_open", str(ctx.exception))

    def test_preflight_waits_for_menu_and_submenu_before_accepting_github(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.waits: list[int] = []

            def wait_for_timeout(self, milliseconds: int) -> None:
                self.waits.append(milliseconds)

        fake_page = FakePage()
        probe_sequence = [
            self._base_payload(),
            self._base_payload(
                action="click_plus",
                plusClicked=True,
                plusAriaLabel="ファイルの追加など",
                beforeVisibleLabels=["ファイルの追加など"],
                afterVisibleLabels=["ファイルの追加など"],
            ),
            self._base_payload(
                visibleLabels=["ファイルの追加など"],
            ),
            self._base_payload(
                menuOpened=True,
                menuItems=[{"text": "さらに表示", "role": "menuitem"}],
                menuCandidateLabels=["さらに表示"],
                moreFound=True,
                moreLabel="さらに表示",
                visibleLabels=["さらに表示"],
            ),
            self._base_payload(
                action="click_more",
                plusClicked=True,
                menuOpened=True,
                menuItems=[{"text": "さらに表示", "role": "menuitem"}],
                moreFound=True,
                moreClicked=True,
                moreLabel="さらに表示",
                beforeVisibleLabels=["さらに表示"],
                afterVisibleLabels=["さらに表示"],
            ),
            self._base_payload(
                plusClicked=True,
                menuOpened=True,
                menuItems=[{"text": "さらに表示", "role": "menuitem"}],
                moreFound=True,
                visibleLabels=["さらに表示"],
            ),
            self._base_payload(
                plusClicked=True,
                menuOpened=True,
                menuItems=[{"text": "さらに表示", "role": "menuitem"}],
                moreFound=True,
                submenuOpened=True,
                submenuItems=[
                    {"text": "情報源を追加する", "role": "menuitem"},
                    {"text": "GitHub", "role": "menuitem"},
                ],
                submenuCandidateLabels=["情報源を追加する", "GitHub"],
                moreControlledId="menu-demo",
                sourceAddFound=True,
                githubFound=True,
                githubLabel="GitHub",
                visibleLabels=["GitHub"],
            ),
            self._base_payload(
                action="click_github",
                plusClicked=True,
                menuOpened=True,
                moreFound=True,
                submenuOpened=True,
                submenuItems=[
                    {"text": "情報源を追加する", "role": "menuitem"},
                    {"text": "GitHub", "role": "menuitem"},
                ],
                sourceAddFound=True,
                githubFound=True,
                githubLabel="GitHub",
                githubClicked=True,
                moreControlledId="menu-demo",
                beforeVisibleLabels=["GitHub"],
                afterVisibleLabels=["GitHub"],
                visibleLabels=["GitHub"],
            ),
            self._base_payload(
                plusClicked=True,
                menuOpened=True,
                moreFound=True,
                submenuOpened=True,
                submenuItems=[
                    {"text": "情報源を追加する", "role": "menuitem"},
                    {"text": "GitHub", "role": "menuitem"},
                ],
                sourceAddFound=True,
                githubFound=True,
                githubLabel="GitHub",
                moreControlledId="menu-demo",
                githubPillConfirmed=True,
                githubPillRemoveButtonFound=True,
                githubPillVisible=True,
                githubPillLabels=["GitHub：クリックして削除"],
                visibleLabels=["GitHub"],
                composerScopeText="GitHub",
                finalAttachConfirmationKind="github_pill_remove_button",
            ),
        ]
        with patch.object(_bridge_common, "_probe_project_page_github_source", side_effect=probe_sequence):
            payload = _bridge_common.ensure_project_page_github_source_ready(fake_page)
        self.assertEqual(payload["menuWaitAttempts"], 2)
        self.assertEqual(payload["submenuWaitAttempts"], 2)
        self.assertEqual(payload["githubConfirmAttempts"], 1)
        self.assertEqual(payload["githubConfirmationKind"], "github_pill_remove_button")
        self.assertEqual(
            fake_page.waits,
            [
                _bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS,
                _bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS,
                _bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS,
                _bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS,
                _bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS,
            ],
        )

    def test_preflight_waits_then_stops_at_menu_not_open(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.waits: list[int] = []

            def wait_for_timeout(self, milliseconds: int) -> None:
                self.waits.append(milliseconds)

        fake_page = FakePage()
        probe_sequence = [
            self._base_payload(),
            self._base_payload(
                action="click_plus",
                plusClicked=True,
                plusAriaLabel="ファイルの追加など",
                beforeVisibleLabels=["ファイルの追加など"],
                afterVisibleLabels=["ファイルの追加など"],
            ),
        ]
        for _ in range(_bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_ATTEMPTS):
            probe_sequence.append(self._base_payload(visibleLabels=["ファイルの追加など"]))
        with (
            patch.object(_bridge_common, "_probe_project_page_github_source", side_effect=probe_sequence),
            patch.object(
                _bridge_common,
                "_log_project_page_github_source_probe",
                return_value=Path("/tmp/project_page_github_source_probe.json"),
            ),
            patch.object(
                _bridge_common,
                "log_page_dump",
                return_value=Path("/tmp/project_page_github_source_dump.txt"),
            ),
        ):
            with self.assertRaises(_bridge_common.BridgeError) as ctx:
                _bridge_common.ensure_project_page_github_source_ready(fake_page)
        self.assertIn("boundary=composer_menu_not_open", str(ctx.exception))
        self.assertEqual(
            fake_page.waits,
            [_bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_MS]
            * _bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_ATTEMPTS,
        )

    def test_single_pass_payload_can_reach_github_from_transient_more_submenu(self) -> None:
        payload = {
            "composerFound": True,
            "plusFound": True,
            "plusLabel": "",
            "plusAriaLabel": "ファイルの追加など",
            "plusClicked": True,
            "menuOpened": True,
            "menuItems": [{"text": "さらに表示", "role": "menuitem"}],
            "moreFound": True,
            "moreClicked": True,
            "submenuOpened": True,
            "submenuItems": [
                {"text": "情報源を追加する", "role": "menuitem"},
                {"text": "GitHub", "role": "menuitem"},
            ],
            "sourceAddFound": True,
            "githubFound": True,
            "githubLabel": "GitHub",
            "githubClicked": True,
            "githubPillConfirmed": True,
            "githubPillVisible": True,
        }
        result = _bridge_common.classify_project_page_github_source_preflight(payload)
        self.assertEqual(result.status, "available")
        self.assertEqual(result.boundary, "github_pill_confirmed")

    def test_non_click_more_strategy_counts_as_action_performed(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": True,
                "moreFound": True,
                "moreClicked": False,
                "moreActionPerformed": True,
                "submenuOpened": True,
                "sourceAddFound": True,
                "githubFound": True,
                "githubClicked": True,
                "githubPillConfirmed": True,
                "githubPillVisible": True,
            }
        )
        self.assertEqual(result.status, "available")
        self.assertEqual(result.boundary, "github_pill_confirmed")

    def test_send_path_runs_github_preflight_before_filling_project_page_composer(self) -> None:
        events: list[str] = []

        class FakePage:
            front_tab = {"url": "https://chatgpt.com/g/g-p-123/project", "title": "ChatGPT - Project"}

            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

        fake_page = FakePage()
        config = {
            "chat_url_prefix": "https://chatgpt.com/",
            "conversation_url_keywords": ["/c/"],
            "project_page_url": "",
        }

        @contextmanager
        def fake_open_chatgpt_page(**_: object):
            yield None, fake_page, config, fake_page.front_tab

        def fake_fill_chatgpt_composer(*args: object, **kwargs: object) -> dict[str, object]:
            events.append("fill")
            return {"matchKind": "project_hint", "matchedHint": "内の新しいチャット", "projectName": "demo"}

        def fake_submit_chatgpt_message(_page: object) -> None:
            events.append("submit")

        with (
            patch.object(_bridge_common, "open_chatgpt_page", fake_open_chatgpt_page),
            patch.object(
                _bridge_common,
                "ensure_project_page_github_source_ready",
                side_effect=lambda *args, **kwargs: events.append("preflight") or {
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
            patch.object(_bridge_common, "fill_chatgpt_composer", side_effect=fake_fill_chatgpt_composer),
            patch.object(_bridge_common, "submit_chatgpt_message", side_effect=fake_submit_chatgpt_message),
            patch.object(
                _bridge_common,
                "_read_post_send_state",
                return_value=(
                    {"url": "https://chatgpt.com/c/abc", "title": "ChatGPT"},
                    {"bodyContainsExpected": False, "composerEmpty": False},
                ),
            ),
        ):
            result = _bridge_common.send_to_chatgpt_in_current_surface(
                "hello",
                require_conversation=False,
                require_target_chat=False,
                project_page_mode=True,
            )
        self.assertEqual(events, ["preflight", "fill", "submit"])
        self.assertEqual(result["signal"], "conversation_url")

    def test_send_path_continues_when_attach_fails_in_best_effort_mode(self) -> None:
        events: list[str] = []

        class FakePage:
            front_tab = {"url": "https://chatgpt.com/g/g-p-123/project", "title": "ChatGPT - Project"}

            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

        fake_page = FakePage()
        config = {
            "chat_url_prefix": "https://chatgpt.com/",
            "conversation_url_keywords": ["/c/"],
            "project_page_url": "",
            "require_github_source": False,
        }

        @contextmanager
        def fake_open_chatgpt_page(**_: object):
            yield None, fake_page, config, fake_page.front_tab

        def fake_fill_chatgpt_composer(*args: object, **kwargs: object) -> dict[str, object]:
            events.append("fill")
            return {"matchKind": "project_hint", "matchedHint": "内の新しいチャット", "projectName": "demo"}

        def fake_submit_chatgpt_message(_page: object) -> None:
            events.append("submit")

        attach_outcome = _bridge_common.ProjectPageGithubSourceAttachOutcome(
            status="probe_failed",
            boundary="composer_more_submenu_not_open",
            detail="connector submenu を確認できませんでした。",
            context="initial_request",
            attempted=True,
            continued_without_github_source=True,
            probe_log="logs/probe.md",
            raw_dump="logs/dump.txt",
        )

        with (
            patch.object(_bridge_common, "open_chatgpt_page", fake_open_chatgpt_page),
            patch.object(
                _bridge_common,
                "ensure_project_page_github_source_ready_for_send",
                side_effect=lambda *args, **kwargs: events.append("preflight") or attach_outcome,
            ),
            patch.object(
                _bridge_common,
                "_log_project_page_github_source_attach_outcome",
                return_value=Path("/tmp/project_page_github_source_attach_initial.json"),
            ),
            patch.object(_bridge_common, "fill_chatgpt_composer", side_effect=fake_fill_chatgpt_composer),
            patch.object(_bridge_common, "submit_chatgpt_message", side_effect=fake_submit_chatgpt_message),
            patch.object(
                _bridge_common,
                "_read_post_send_state",
                return_value=(
                    {"url": "https://chatgpt.com/c/abc", "title": "ChatGPT"},
                    {"bodyContainsExpected": False, "composerEmpty": False},
                ),
            ),
            patch("builtins.print"),
        ):
            result = _bridge_common.send_to_chatgpt_in_current_surface(
                "hello",
                require_conversation=False,
                require_target_chat=False,
                project_page_mode=True,
            )
        self.assertEqual(events, ["preflight", "fill", "submit"])
        self.assertEqual(result["signal"], "conversation_url")
        self.assertEqual(result["github_source_attach_status"], "probe_failed")
        self.assertEqual(result["github_source_attach_boundary"], "composer_more_submenu_not_open")
        self.assertTrue(result["request_send_continued_without_github_source"])

    def test_attach_failure_still_stops_in_strict_mode(self) -> None:
        result = _bridge_common.ProjectPageGithubSourcePreflightResult(
            status="unavailable",
            boundary="github_item_missing",
            detail="GitHub は見つかりませんでした。",
        )
        error = _bridge_common.ProjectPageGithubSourcePreflightError(
            "GitHub source preflight に失敗しました。",
            result=result,
            payload={},
            probe_path=Path("/tmp/probe.md"),
            dump_path=Path("/tmp/dump.txt"),
        )

        with patch.object(_bridge_common, "ensure_project_page_github_source_ready", side_effect=error):
            with self.assertRaises(_bridge_common.ProjectPageGithubSourcePreflightError):
                _bridge_common.ensure_project_page_github_source_ready_for_send(
                    object(),
                    {"require_github_source": True},
                    send_context="initial_request",
                )

    def test_preflight_tries_multiple_more_open_strategies_until_submenu_appears(self) -> None:
        class FakePage:
            def __init__(self) -> None:
                self.waits: list[int] = []
                self.boundary_checks = 0

            def wait_for_timeout(self, milliseconds: int) -> None:
                self.waits.append(milliseconds)

            def assert_same_front_tab(self) -> None:
                self.boundary_checks += 1

        fake_page = FakePage()
        probe_sequence = [
            self._base_payload(),
            self._base_payload(
                action="click_plus",
                plusClicked=True,
                plusAriaLabel="ファイルの追加など",
                beforeVisibleLabels=["ファイルの追加など"],
                afterVisibleLabels=["ファイルの追加など"],
            ),
            self._base_payload(
                menuOpened=True,
                menuItems=[{"text": "さらに表示", "role": "menuitem"}],
                menuCandidateLabels=["さらに表示"],
                moreFound=True,
                moreLabel="さらに表示",
                visibleLabels=["さらに表示"],
            ),
            self._base_payload(
                action="open_more_click",
                plusClicked=True,
                menuOpened=True,
                menuItems=[{"text": "さらに表示", "role": "menuitem"}],
                moreFound=True,
                moreLabel="さらに表示",
                moreClicked=True,
                moreActionPerformed=True,
                moreTargetText="さらに表示",
            ),
        ]
        for _ in range(_bridge_common.PROJECT_CHAT_GITHUB_PREFLIGHT_SETTLE_ATTEMPTS):
            probe_sequence.append(
                self._base_payload(
                    plusClicked=True,
                    menuOpened=True,
                    moreFound=True,
                    submenuOpened=False,
                    visibleLabels=["さらに表示"],
                )
            )
        probe_sequence.extend(
            [
            self._base_payload(
                action="open_more_focus",
                plusClicked=True,
                menuOpened=True,
                menuItems=[{"text": "さらに表示", "role": "menuitem"}],
                moreFound=True,
                moreLabel="さらに表示",
                moreActionPerformed=True,
                moreTargetText="さらに表示",
            ),
            self._base_payload(
                plusClicked=True,
                menuOpened=True,
                moreFound=True,
                submenuOpened=True,
                submenuItems=[
                    {"text": "情報源を追加する", "role": "menuitem"},
                    {"text": "GitHub", "role": "menuitem"},
                ],
                submenuCandidateLabels=["情報源を追加する", "GitHub"],
                submenuProbeMenuishLabels=["情報源を追加する", "GitHub"],
                submenuProbeOverlayLabels=[],
                moreControlledId="menu-demo",
                sourceAddFound=True,
                githubFound=True,
                githubLabel="GitHub",
                githubFoundContext="submenu",
                visibleLabels=["GitHub"],
            ),
            self._base_payload(
                action="click_github",
                plusClicked=True,
                menuOpened=True,
                moreFound=True,
                submenuOpened=True,
                submenuItems=[
                    {"text": "情報源を追加する", "role": "menuitem"},
                    {"text": "GitHub", "role": "menuitem"},
                ],
                sourceAddFound=True,
                githubFound=True,
                githubLabel="GitHub",
                githubClicked=True,
                moreControlledId="menu-demo",
                githubFoundContext="submenu",
            ),
            self._base_payload(
                plusClicked=True,
                menuOpened=True,
                moreFound=True,
                submenuOpened=True,
                submenuItems=[
                    {"text": "情報源を追加する", "role": "menuitem"},
                    {"text": "GitHub", "role": "menuitem"},
                ],
                sourceAddFound=True,
                githubFound=True,
                githubLabel="GitHub",
                moreControlledId="menu-demo",
                githubPillConfirmed=True,
                githubPillRemoveButtonFound=True,
                githubPillVisible=True,
                githubPillLabels=["GitHub：クリックして削除"],
                githubFoundContext="submenu",
                composerScopeText="GitHub",
                finalAttachConfirmationKind="github_pill_remove_button",
            ),
            ]
        )
        with patch.object(_bridge_common, "_probe_project_page_github_source", side_effect=probe_sequence):
            payload = _bridge_common.ensure_project_page_github_source_ready(fake_page)
        self.assertEqual(payload["moreOpenStrategiesTried"], ["hover", "focus"])
        self.assertEqual(payload["moreOpenStrategySucceeded"], "focus")
        self.assertEqual(payload["githubFoundContext"], "submenu")
        self.assertTrue(payload["githubPillConfirmed"])
        self.assertEqual(payload["phaseBoundaryChecks"], ["github_attach_start", "github_attach_complete"])
        self.assertEqual(fake_page.boundary_checks, 2)
        self.assertGreater(payload["uncheckedProbeCount"], 0)

    def test_overlay_submenu_payload_counts_as_open(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": True,
                "moreFound": True,
                "moreActionPerformed": True,
                "submenuOpened": True,
                "submenuProbeOverlayLabels": ["GitHub"],
                "connectorSubmenuDetected": True,
                "sourceAddFound": False,
                "githubFound": True,
                "githubClicked": True,
                "githubPillConfirmed": True,
                "githubPillVisible": True,
            }
        )
        self.assertEqual(result.status, "available")
        self.assertEqual(result.boundary, "github_pill_confirmed")

    def test_generic_overlay_does_not_count_as_connector_submenu(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": True,
                "moreFound": True,
                "moreActionPerformed": True,
                "submenuOpened": True,
                "submenuClassifier": "generic_overlay_only",
                "submenuClassifierReason": "overlay_labels_without_connector_evidence",
                "submenuProbeMenuishLabels": [
                    "写真とファイルをアップロードする ⌘ U",
                    "最近のファイル",
                    "さらに表示",
                ],
                "submenuProbeOverlayLabels": [],
                "submenuProbeVisibleLabels": ["サイドバーを開く", "新しいチャット"],
                "sourceAddFound": False,
                "githubFound": False,
            }
        )
        self.assertEqual(result.status, "probe_failed")
        self.assertEqual(result.boundary, "composer_more_submenu_not_open")

    def test_github_item_missing_requires_connector_submenu_evidence(self) -> None:
        result = _bridge_common.classify_project_page_github_source_preflight(
            {
                "composerFound": True,
                "plusFound": True,
                "plusClicked": True,
                "menuOpened": True,
                "moreFound": True,
                "moreActionPerformed": True,
                "submenuOpened": True,
                "submenuItems": [{"text": "情報源を追加する", "role": "menuitem"}],
                "submenuCandidateLabels": ["情報源を追加する"],
                "submenuClassifier": "connector_submenu",
                "submenuClassifierReason": "connector_like_labels_seen",
                "connectorSubmenuDetected": True,
                "connectorLikeLabelsSeen": ["情報源を追加する"],
                "sourceAddFound": True,
                "githubFound": False,
            }
        )
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.boundary, "github_item_missing")

    def test_attach_timeout_becomes_bounded_error(self) -> None:
        class FakePage:
            def wait_for_timeout(self, _milliseconds: int) -> None:
                return None

            def assert_same_front_tab(self) -> None:
                return None

        fake_page = FakePage()
        probe_sequence = [
            self._base_payload(),
            self._base_payload(
                action="click_plus",
                plusClicked=True,
                plusAriaLabel="ファイルの追加など",
            ),
        ]
        with (
            patch.object(_bridge_common, "_probe_project_page_github_source", side_effect=probe_sequence),
            patch.object(_bridge_common, "PROJECT_CHAT_GITHUB_ATTACH_PHASE_TIMEOUT_SECONDS", 0.0),
            patch.object(
                _bridge_common,
                "_log_project_page_github_source_probe",
                return_value=Path("/tmp/project_page_github_source_probe.json"),
            ),
            patch.object(
                _bridge_common,
                "log_page_dump",
                return_value=Path("/tmp/project_page_github_source_dump.txt"),
            ),
        ):
            with self.assertRaises(_bridge_common.BridgeError) as ctx:
                _bridge_common.ensure_project_page_github_source_ready(fake_page)
        self.assertIn("boundary=github_attach_phase_timeout", str(ctx.exception))


class OrchestratorArgForwardingTests(unittest.TestCase):
    def test_build_initial_request_argv_forwards_ready_issue_and_override(self) -> None:
        args = argparse.Namespace(
            worker_repo_path="/tmp/repo",
            ready_issue_ref="#20 runtime entry",
            request_body="override text",
        )
        argv = bridge_orchestrator.build_initial_request_argv(args)
        self.assertEqual(
            argv,
            [
                "--project-path",
                "/tmp/repo",
                "--ready-issue-ref",
                "#20 runtime entry",
                "--request-body",
                "override text",
            ],
        )

    def test_run_prefers_fetch_for_issue_centric_pending_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "codex_run",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/20",
                        "principal_issue_kind": "current_issue",
                        "target_issue": "https://github.com/example/repo/issues/20",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_current_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": str(snapshot_path),
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                "last_issue_centric_pending_generation_id": "summary:logs/summary.json",
                "pending_request_hash": "hash123",
                "pending_request_source": "report:1",
                "pending_request_log": "logs/request.md",
            }
            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
                patch.object(bridge_orchestrator.fetch_next_prompt, "run", return_value=0) as fetch_run,
                patch.object(bridge_orchestrator.request_prompt_from_report, "run", return_value=0) as report_run,
            ):
                rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        fetch_run.assert_called_once()
        report_run.assert_not_called()

    def test_run_uses_issue_centric_preferred_route_for_next_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "issue_create",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/51",
                        "principal_issue_kind": "primary_issue",
                        "target_issue": "https://github.com/example/repo/issues/51",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_primary_issue",
                        "current_issue": None,
                        "created_primary_issue": {
                            "number": "51",
                            "url": "https://github.com/example/repo/issues/51",
                            "title": "Primary issue",
                            "ref": "#51",
                        },
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": str(snapshot_path),
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
            }
            out = io.StringIO()
            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
                patch.object(bridge_orchestrator.request_prompt_from_report, "run", return_value=0) as report_run,
                redirect_stdout(out),
            ):
                rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        report_run.assert_called_once()
        self.assertIn("issue-centric preferred route", out.getvalue())
        self.assertIn("https://github.com/example/repo/issues/51", out.getvalue())

    def test_run_uses_legacy_fallback_for_next_request_when_issue_centric_is_invalidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            snapshot_path = root / "snapshot.json"
            snapshot_path.write_text(
                json.dumps(
                    {
                        "snapshot_status": "issue_centric_snapshot_ready",
                        "snapshot_source": "execution_finalize",
                        "generation_id": "summary:logs/summary.json",
                        "action": "human_review_needed",
                        "dispatch_final_status": "completed",
                        "route_selected": "issue_centric",
                        "route_fallback_reason": "",
                        "recovery_status": "",
                        "recovery_source": "",
                        "recovery_fallback_reason": "",
                        "fallback_reason": "",
                        "principal_issue": "https://github.com/example/repo/issues/20",
                        "principal_issue_kind": "current_issue",
                        "target_issue": "https://github.com/example/repo/issues/20",
                        "target_issue_source": "normalized_summary",
                        "next_request_hint": "continue_on_current_issue",
                        "current_issue": None,
                        "created_primary_issue": None,
                        "created_followup_issue": None,
                        "closed_issue": None,
                        "codex_target_issue": None,
                        "review_target_issue": None,
                        "project_lifecycle_sync": {},
                        "normalized_summary_path": "",
                        "dispatch_result_path": "",
                        "snapshot_path": str(snapshot_path),
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_next": True,
                "last_issue_centric_runtime_snapshot": str(snapshot_path),
                "last_issue_centric_snapshot_status": "issue_centric_snapshot_ready",
                "last_issue_centric_invalidated_generation_id": "summary:logs/summary.json",
                "last_issue_centric_invalidation_status": "issue_centric_invalidated",
                "last_issue_centric_invalidation_reason": "legacy_fallback_selected",
            }
            out = io.StringIO()
            with (
                patch.object(bridge_orchestrator, "load_project_config", return_value={}),
                patch.object(bridge_orchestrator, "print_project_config_warnings"),
                patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
                patch.object(bridge_orchestrator.request_prompt_from_report, "run", return_value=0) as report_run,
                redirect_stdout(out),
            ):
                rc = bridge_orchestrator.run(state, [])

        self.assertEqual(rc, 0)
        report_run.assert_called_once()
        self.assertIn("safety fallback (legacy) route", out.getvalue())
        self.assertIn("legacy_fallback_selected", out.getvalue())

    def test_run_keeps_codex_running_mode_driven_before_issue_centric_routing(self) -> None:
        out = io.StringIO()
        with (
            patch.object(bridge_orchestrator, "load_project_config", return_value={}),
            patch.object(bridge_orchestrator, "print_project_config_warnings"),
            patch.object(bridge_orchestrator, "should_prioritize_unarchived_report", return_value=False),
            patch.object(bridge_orchestrator, "maybe_promote_codex_done", return_value=False),
            patch.object(
                bridge_orchestrator,
                "resolve_runtime_dispatch_plan",
                side_effect=AssertionError("issue-centric next-action should not override codex_running"),
            ),
            redirect_stdout(out),
        ):
            rc = bridge_orchestrator.run({"mode": "codex_running", "need_codex_run": True}, [])

        self.assertEqual(rc, 0)
        self.assertIn("Codex worker の完了待ち", out.getvalue())


class IssueAwareProvenanceTest(unittest.TestCase):
    """#26: build_report_request_source() issue-aware provenance.

    Invariants:
      - With last_issue_centric_principal_issue → report:{file}:issue:{num}
      - Without (absent / empty / falsy) → report:{file} (unchanged)
      - awaiting_user mode still produces handoff:... regardless of principal_issue
      - pending_request_source idempotency guard treats extended source as stable key
    """

    def _src(self, state: dict, resume_note: str = "") -> str:
        from request_prompt_from_report import build_report_request_source
        return build_report_request_source(state, resume_note)

    def test_with_principal_issue_returns_extended_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md",
                 "last_issue_centric_principal_issue": "42"}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md:issue:42")

    def test_without_principal_issue_returns_plain_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md"}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md")

    def test_empty_principal_issue_returns_plain_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md",
                 "last_issue_centric_principal_issue": ""}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md")

    def test_whitespace_only_principal_issue_returns_plain_source(self) -> None:
        state = {"last_report_file": "codex_report_20260408_123456.md",
                 "last_issue_centric_principal_issue": "  "}
        self.assertEqual(self._src(state), "report:codex_report_20260408_123456.md")

    def test_awaiting_user_mode_produces_handoff_regardless_of_principal_issue(self) -> None:
        """awaiting_user path must not be affected by principal_issue addition."""
        from request_prompt_from_report import build_report_request_source
        state = {
            "last_report_file": "report.md",
            "mode": "awaiting_user",
            "chatgpt_decision": "resume",
            "last_issue_centric_principal_issue": "99",
        }
        src = build_report_request_source(state, "some note")
        self.assertTrue(src.startswith("handoff:"), f"Expected handoff: prefix, got {src!r}")
        self.assertNotIn(":issue:", src)

    def test_missing_report_file_uses_unknown_report_fallback(self) -> None:
        state = {"last_issue_centric_principal_issue": "7"}
        self.assertEqual(self._src(state), "report:unknown-report:issue:7")

    def test_idempotency_guard_extended_source_prevents_duplicate_send(self) -> None:
        """pending_request_source == extended source → duplicate send is blocked.

        Simulates the guard check in request_prompt_from_report.main():
          if mode == 'waiting_prompt_reply' and pending_request_source == request_source: skip
        The guard must work unchanged because it is a plain string equality check.
        """
        from request_prompt_from_report import build_report_request_source
        state = {
            "last_report_file": "codex_report_20260408_123456.md",
            "mode": "waiting_prompt_reply",
            "last_issue_centric_principal_issue": "42",
            "pending_request_source": "report:codex_report_20260408_123456.md:issue:42",
        }
        request_source = build_report_request_source(state, "")
        # Guard condition: same report + same issue → same key → duplicate send blocked
        self.assertEqual(
            state["pending_request_source"],
            request_source,
            "Extended source must be a stable key so idempotency guard prevents duplicate send.",
        )

    def test_idempotency_guard_different_issue_does_not_block(self) -> None:
        """A different principal_issue produces a different source key → send is NOT blocked."""
        from request_prompt_from_report import build_report_request_source
        state = {
            "last_report_file": "codex_report_20260408_123456.md",
            "mode": "waiting_prompt_reply",
            "last_issue_centric_principal_issue": "99",
            "pending_request_source": "report:codex_report_20260408_123456.md:issue:42",
        }
        request_source = build_report_request_source(state, "")
        self.assertNotEqual(
            state["pending_request_source"],
            request_source,
            "Different issue number must produce a different source key.",
        )


class IssueCentricContinuationReplyContractTests(unittest.TestCase):
    """#fix: report-based continuation must use issue-centric contract when route is issue_centric.

    Guard against regression where archive_codex_report → request_prompt_from_report
    falls back to legacy visible-text tags (===CHATGPT_PROMPT_REPLY=== / ===CHATGPT_NO_CODEX===)
    instead of maintaining issue-centric contract only.

    Normal (issue_centric route):
      - built request contains ===CHATGPT_DECISION_JSON=== and REPLY_COMPLETE tag
      - built request does NOT contain ===CHATGPT_PROMPT_REPLY=== or ===CHATGPT_NO_CODEX===

    Abnormal (fallback_legacy or empty route):
      - legacy route: does NOT contain CHATGPT_DECISION_JSON (no unintended injection)
      - no route: same as legacy

    Backward compat (build_chatgpt_request without issue_centric_route_selected):
      - default "" behaves as legacy (no extra DECISION_JSON appended)
    """

    def _template(self, tmp: Path) -> Path:
        t = tmp / "template.md"
        t.write_text(
            "## state\n{CURRENT_STATUS}\n\n{ISSUE_CENTRIC_NEXT_REQUEST_SECTION}\n"
            "{RESUME_CONTEXT_SECTION}\n"
            "===CHATGPT_PROMPT_REPLY===\n[body]\n===END_REPLY===\n"
            "===CHATGPT_NO_CODEX===\nstatus\n===END_NO_CODEX===\n",
            encoding="utf-8",
        )
        return t

    def test_build_chatgpt_request_issue_centric_route_appends_ic_contract(self) -> None:
        """issue_centric_route_selected='issue_centric' → DECISION_JSON present, legacy tags absent."""
        from _bridge_common import build_chatgpt_request
        with tempfile.TemporaryDirectory() as tmp:
            t = self._template(Path(tmp))
            result = build_chatgpt_request(
                state={"mode": "idle"},
                template_path=t,
                next_todo="next",
                open_questions="none",
                issue_centric_route_selected="issue_centric",
            )
        self.assertIn("===CHATGPT_DECISION_JSON===", result)
        self.assertIn("===CHATGPT_REPLY_COMPLETE===", result)
        self.assertIn("issue-centric contract only", result)
        self.assertIn("legacy visible-text fallback は使わないでください", result)

    def test_build_chatgpt_request_issue_centric_route_legacy_tags_still_present_in_body(self) -> None:
        """Legacy tag instructions from the template appear before the override contract.

        The issue-centric contract at the end explicitly overrides them.
        Both are present; ChatGPT follows the issue-centric instruction (last wins).
        """
        from _bridge_common import build_chatgpt_request
        with tempfile.TemporaryDirectory() as tmp:
            t = self._template(Path(tmp))
            result = build_chatgpt_request(
                state={"mode": "idle"},
                template_path=t,
                next_todo="next",
                open_questions="none",
                issue_centric_route_selected="issue_centric",
            )
        # Issue-centric override must come AFTER the legacy template block:
        ic_pos = result.index("===CHATGPT_DECISION_JSON===")
        legacy_pos = result.index("===CHATGPT_PROMPT_REPLY===")
        self.assertGreater(
            ic_pos, legacy_pos,
            "Issue-centric contract must appear after legacy template section so it overrides.",
        )

    def test_build_chatgpt_request_fallback_legacy_route_no_ic_contract_injected(self) -> None:
        """fallback_legacy route → no CHATGPT_DECISION_JSON appended (backward compat)."""
        from _bridge_common import build_chatgpt_request
        with tempfile.TemporaryDirectory() as tmp:
            t = self._template(Path(tmp))
            result = build_chatgpt_request(
                state={"mode": "idle"},
                template_path=t,
                next_todo="next",
                open_questions="none",
                issue_centric_route_selected="fallback_legacy",
            )
        self.assertNotIn("===CHATGPT_DECISION_JSON===", result)
        self.assertIn("===CHATGPT_PROMPT_REPLY===", result)

    def test_build_chatgpt_request_default_route_no_ic_contract_injected(self) -> None:
        """Default (no route_selected) behaves as legacy – no extra contract injection."""
        from _bridge_common import build_chatgpt_request
        with tempfile.TemporaryDirectory() as tmp:
            t = self._template(Path(tmp))
            result = build_chatgpt_request(
                state={"mode": "idle"},
                template_path=t,
                next_todo="next",
                open_questions="none",
            )
        self.assertNotIn("===CHATGPT_DECISION_JSON===", result)

    def test_build_chatgpt_handoff_request_issue_centric_route_uses_ic_contract(self) -> None:
        """build_chatgpt_handoff_request with issue_centric_route_selected='issue_centric' uses IC contract."""
        from _bridge_common import build_chatgpt_handoff_request
        result = build_chatgpt_handoff_request(
            state={"mode": "idle"},
            last_report="# Report\n\n===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            next_todo="next",
            open_questions="none",
            issue_centric_route_selected="issue_centric",
        )
        self.assertIn("===CHATGPT_DECISION_JSON===", result)
        self.assertIn("issue-centric contract only", result)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", result)
        self.assertNotIn("===CHATGPT_NO_CODEX===", result)

    def test_build_chatgpt_handoff_request_default_route_uses_legacy_contract(self) -> None:
        """Default (no route) keeps legacy contract in handoff request for backward compat."""
        from _bridge_common import build_chatgpt_handoff_request
        result = build_chatgpt_handoff_request(
            state={"mode": "idle"},
            last_report="# Report\n\n===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            next_todo="next",
            open_questions="none",
        )
        self.assertNotIn("===CHATGPT_DECISION_JSON===", result)
        self.assertIn("===CHATGPT_PROMPT_REPLY===", result)

    def test_run_resume_request_issue_centric_route_sends_ic_contract(self) -> None:
        """run_resume_request with issue_centric_ready snapshot → IC contract in sent request."""
        import request_prompt_from_report
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps({
                    "action": "codex_run",
                    "final_status": "completed",
                    "principal_issue_kind": "current_issue",
                    "principal_issue_candidate": {
                        "number": "6",
                        "url": "https://github.com/example/repo/issues/6",
                        "title": "Ready: verify mutation path",
                        "ref": "#6",
                    },
                    "current_issue": {
                        "number": "6",
                        "url": "https://github.com/example/repo/issues/6",
                        "title": "Ready: verify mutation path",
                        "ref": "#6",
                    },
                    "next_request_hint": "continue_on_current_issue",
                }),
                encoding="utf-8",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "last_report_file": "logs/report.md",
                "last_issue_centric_normalized_summary": str(summary_path),
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/6",
            }
            args = argparse.Namespace(
                next_todo="verify mutation path",
                open_questions="none",
                current_status="",
            )
            sent_requests: list[str] = []

            def fake_send(text: str) -> None:
                sent_requests.append(text)

            with patch.object(request_prompt_from_report, "send_to_chatgpt", side_effect=fake_send):
                with patch.object(request_prompt_from_report, "log_text", side_effect=lambda prefix, text, *a, **kw: root / f"{prefix}.md"):
                    with patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda p: str(p)):
                        with patch.object(request_prompt_from_report, "save_state"):
                            request_prompt_from_report.run_resume_request(state, args, "# Report\n", "")

        self.assertEqual(len(sent_requests), 1)
        text = sent_requests[0]
        self.assertIn("===CHATGPT_DECISION_JSON===", text,
                      "issue-centric ready route must include DECISION_JSON contract")
        self.assertIn("legacy visible-text fallback は使わないでください", text,
                      "issue-centric contract must explicitly forbid legacy tags")

    def test_run_resume_request_no_issue_centric_snapshot_keeps_legacy(self) -> None:
        """Without issue-centric snapshot, legacy contract is used (no unintended IC injection)."""
        import request_prompt_from_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = {
                "mode": "idle",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "last_report_file": "logs/report.md",
            }
            args = argparse.Namespace(
                next_todo="next",
                open_questions="none",
                current_status="",
            )
            sent_requests: list[str] = []

            def fake_send(text: str) -> None:
                sent_requests.append(text)

            with patch.object(request_prompt_from_report, "send_to_chatgpt", side_effect=fake_send):
                with patch.object(request_prompt_from_report, "log_text", side_effect=lambda prefix, text, *a, **kw: root / f"{prefix}.md"):
                    with patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda p: str(p)):
                        with patch.object(request_prompt_from_report, "save_state"):
                            request_prompt_from_report.run_resume_request(state, args, "# Report\n", "")

        self.assertEqual(len(sent_requests), 1)
        text = sent_requests[0]
        self.assertNotIn("===CHATGPT_DECISION_JSON===", text,
                         "No IC snapshot → must not inject IC contract")


if __name__ == "__main__":
    unittest.main()
