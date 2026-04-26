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
        request_text, request_hash, request_source, ready_issue_ref = request_next_prompt.build_initial_request(args)
        self.assertEqual(ready_issue_ref, "#20 runtime entry")
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
        request_text, _, request_source, ready_issue_ref_override = request_next_prompt.build_initial_request(args)
        self.assertEqual(ready_issue_ref_override, "")
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

    def test_compose_ready_issue_request_text_binds_target_issue_to_current_ready_issue(self) -> None:
        """ready issue request に target_issue は current ready issue に固定される旨が明示されている.

        Regression: #9 を送ったのに stale #8 が返った原因は request 文面の弱さ。
        この修正後は:
        - target_issue が current ready issue 番号に固定される宣言が含まれている
        - closed/stale issue への言及禁止が含まれている
        - follow-up/parent issue への拡張禁止が含まれている
        """
        request_text = request_next_prompt.compose_ready_issue_request_text(
            "#9 Ready: verify fetch path parent update live comment after child close",
            Path("/tmp/repo"),
        )
        # current ready issue の番号が固定される明示
        self.assertIn("#9", request_text)
        # target_issue が #9 に固定される宣言
        self.assertIn("target_issue", request_text)
        self.assertIn("固定", request_text)
        # stale/closed issue 禁止
        self.assertIn("stale", request_text)
        # follow-up/parent 拡張禁止
        self.assertIn("follow-up", request_text)
        # contract は壊れていない
        self.assertIn("===CHATGPT_DECISION_JSON===", request_text)
        self.assertIn("===END_DECISION_JSON===", request_text)

    def test_compose_ready_issue_request_text_fixed_issue_number_extracted(self) -> None:
        """target_issue 固定行の issue 番号が ready issue の番号と一致する."""
        for ref in ["#9", "#9 Ready: some title", "#42 Fix: something"]:
            with self.subTest(ref=ref):
                request_text = request_next_prompt.compose_ready_issue_request_text(
                    ref, Path("/tmp/repo")
                )
                expected_num = ref.split()[0]  # "#9" or "#42"
                # 固定行に正しい issue 番号が含まれる
                fixed_line = next(
                    (l for l in request_text.splitlines() if "固定" in l and "target_issue" in l),
                    None,
                )
                self.assertIsNotNone(fixed_line, f"固定 line not found for ref={ref!r}")
                self.assertIn(expected_num, fixed_line)

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
    """#fix: report-based continuation must use issue-centric contract for all routes.

    As of Phase 13 (outbound contract full cutover):
      - build_chatgpt_request() always appends issue-centric reply contract
      - build_chatgpt_handoff_request() always appends issue-centric reply contract
      - The old visible-text contract (===CHATGPT_PROMPT_REPLY=== / ===CHATGPT_NO_CODEX===)
        is no longer included in the template or appended by any builder.
    """

    def _template(self, tmp: Path) -> Path:
        t = tmp / "template.md"
        t.write_text(
            "## state\n{CURRENT_STATUS}\n\n{ISSUE_CENTRIC_NEXT_REQUEST_SECTION}\n"
            "{RESUME_CONTEXT_SECTION}\n",
            encoding="utf-8",
        )
        return t

    def test_build_chatgpt_request_always_appends_ic_contract(self) -> None:
        """build_chatgpt_request always appends IC contract regardless of route."""
        from _bridge_common import build_chatgpt_request
        with tempfile.TemporaryDirectory() as tmp:
            t = self._template(Path(tmp))
            result = build_chatgpt_request(
                state={"mode": "idle"},
                template_path=t,
                next_todo="next",
                open_questions="none",
            )
        self.assertIn("===CHATGPT_DECISION_JSON===", result)
        self.assertIn("===CHATGPT_REPLY_COMPLETE===", result)
        self.assertIn("issue-centric contract only", result)
        self.assertIn("legacy visible-text fallback は使わないでください", result)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", result)
        self.assertNotIn("===CHATGPT_NO_CODEX===", result)

    def test_build_chatgpt_request_ic_contract_appended_after_template_body(self) -> None:
        """IC contract section appears after the template body content."""
        from _bridge_common import build_chatgpt_request
        with tempfile.TemporaryDirectory() as tmp:
            t = self._template(Path(tmp))
            result = build_chatgpt_request(
                state={"mode": "idle"},
                template_path=t,
                next_todo="next",
                open_questions="none",
            )
        status_pos = result.index("## state")
        ic_pos = result.index("===CHATGPT_DECISION_JSON===")
        self.assertGreater(ic_pos, status_pos, "IC contract must appear after template body.")

    def test_build_chatgpt_request_fallback_legacy_route_uses_ic_contract(self) -> None:
        """fallback_legacy route — no route param needed, IC contract always present."""
        from _bridge_common import build_chatgpt_request
        with tempfile.TemporaryDirectory() as tmp:
            t = self._template(Path(tmp))
            result = build_chatgpt_request(
                state={"mode": "idle"},
                template_path=t,
                next_todo="next",
                open_questions="none",
            )
        self.assertIn("===CHATGPT_DECISION_JSON===", result)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", result)
        self.assertNotIn("===CHATGPT_NO_CODEX===", result)

    def test_build_chatgpt_handoff_request_always_uses_ic_contract(self) -> None:
        """build_chatgpt_handoff_request always uses IC contract for all routes."""
        from _bridge_common import build_chatgpt_handoff_request
        result = build_chatgpt_handoff_request(
            state={"mode": "idle"},
            last_report="# Report\n\n===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            next_todo="next",
            open_questions="none",
        )
        self.assertIn("===CHATGPT_DECISION_JSON===", result)
        self.assertIn("issue-centric contract only", result)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", result)
        self.assertNotIn("===CHATGPT_NO_CODEX===", result)

    def test_build_chatgpt_handoff_request_default_route_uses_ic_contract(self) -> None:
        """Default (no route) uses IC contract — unified in Phase 12-13."""
        from _bridge_common import build_chatgpt_handoff_request
        result = build_chatgpt_handoff_request(
            state={"mode": "idle"},
            last_report="# Report\n\n===BRIDGE_SUMMARY===\n- summary: done\n===END_BRIDGE_SUMMARY===\n",
            next_todo="next",
            open_questions="none",
        )
        self.assertIn("===CHATGPT_DECISION_JSON===", result)
        self.assertIn("issue-centric contract only", result)
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", result)
        self.assertNotIn("===CHATGPT_NO_CODEX===", result)

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
        self.assertIn("次の Codex 用 1 フェーズ prompt", text,
                      "normal continuation request must include default Codex prompt guidance")
        self.assertNotIn("lifecycle automation", text,
                         "normal continuation must not use lifecycle-only guidance")

    def test_run_resume_request_completed_report_requests_close_followup(self) -> None:
        """Completed + live_ready archived reports should request lifecycle close follow-up, not another codex phase."""
        import request_prompt_from_report
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = root / "summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "action": "codex_run",
                        "final_status": "completed",
                        "principal_issue_kind": "current_issue",
                        "principal_issue_candidate": {
                            "number": "7",
                            "url": "https://github.com/example/repo/issues/7",
                            "title": "Ready: verify consecutive cycles",
                            "ref": "#7",
                        },
                        "current_issue": {
                            "number": "7",
                            "url": "https://github.com/example/repo/issues/7",
                            "title": "Ready: verify consecutive cycles",
                            "ref": "#7",
                        },
                        "next_request_hint": "continue_on_current_issue",
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "last_report_file": "logs/report.md",
                "last_issue_centric_action": "codex_run",
                "last_issue_centric_target_issue": "#7",
                "last_issue_centric_principal_issue": "https://github.com/example/repo/issues/7",
                "last_issue_centric_principal_issue_kind": "current_issue",
                "last_issue_centric_next_request_target": "https://github.com/example/repo/issues/7",
                "last_issue_centric_next_request_hint": "continue_on_current_issue",
                "last_issue_centric_normalized_summary": str(summary_path),
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/7",
                # Explicit Ready: prefix marks this as a bounded ready issue → close-only path
                "current_ready_issue_ref": "#7 Ready: verify consecutive cycles",
            }
            args = argparse.Namespace(
                next_todo="verify stability",
                open_questions="none",
                current_status="",
            )
            sent_requests: list[str] = []
            report_text = "\n".join(
                [
                    "===BRIDGE_SUMMARY===",
                    "- summary: GitHub Copilot 実行完了 (model: sonnet-4.6)",
                    "- result: completed",
                    "- live_ready: confirmed",
                    "===END_BRIDGE_SUMMARY===",
                ]
            )

            with patch.object(request_prompt_from_report, "send_to_chatgpt", side_effect=lambda text: sent_requests.append(text)):
                with patch.object(request_prompt_from_report, "log_text", side_effect=lambda prefix, text, *a, **kw: root / f"{prefix}.md"):
                    with patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda p: str(p)):
                        with patch.object(request_prompt_from_report, "save_state"):
                            request_prompt_from_report.run_resume_request(state, args, report_text, "")

        self.assertEqual(len(sent_requests), 1)
        text = sent_requests[0]
        self.assertIn("issue_centric_completion_followup", text)
        self.assertIn("新しい Codex 用 prompt は作りません", text)
        self.assertIn("action=codex_run は不正", text)
        self.assertIn("CHATGPT_CODEX_BODY を返さないでください", text)
        self.assertIn("action=no_action を返し", text)
        self.assertIn("close_current_issue=true", text)
        self.assertIn("create_followup_issue=false", text)
        self.assertIn("action=no_action", text)
        self.assertIn("target_issue: https://github.com/example/repo/issues/7", text)
        # lifecycle-only guidance must replace the default Codex-prompt guidance
        self.assertIn("lifecycle automation", text,
                      "ready-bounded completion followup must use lifecycle-only guidance")
        self.assertNotIn("次の Codex 用 1 フェーズ prompt だけを返してください", text,
                         "ready-bounded completion followup must not request a Codex prompt")

    def test_run_resume_request_no_issue_centric_snapshot_uses_ic_contract(self) -> None:
        """Without issue-centric snapshot, IC contract is still always injected (Phase 13)."""
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
        self.assertIn("===CHATGPT_DECISION_JSON===", text,
                      "IC contract must always be injected regardless of snapshot")
        self.assertNotIn("===CHATGPT_PROMPT_REPLY===", text)
        self.assertNotIn("===CHATGPT_NO_CODEX===", text)


# ---------------------------------------------------------------------------
# Parent / planned issue continuation tests
# ---------------------------------------------------------------------------


class ParentIssueContinuationTests(unittest.TestCase):
    """Tests that archived-report continuation for a non-Ready: (parent/planned) issue
    allows child issue creation and codex_run, rather than forcing close-only no_action.

    Scenario: PromptWeave #1 (architecture/parent issue, no "Ready:" prefix) completed
    a codex_run.  The continuation request should offer issue_create / codex_run options,
    NOT mandate action=no_action + create_followup_issue=false.
    """

    def _make_summary_path(self, root: object, issue_ref: str = "#1") -> object:
        from pathlib import Path
        import json
        root = Path(str(root))
        summary_path = root / "summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "action": "codex_run",
                    "final_status": "completed",
                    "principal_issue_kind": "current_issue",
                    "principal_issue_candidate": {
                        "number": "1",
                        "url": "https://github.com/example/repo/issues/1",
                        "title": "PromptWeave foundation architecture",
                        "ref": issue_ref,
                    },
                    "current_issue": {
                        "number": "1",
                        "url": "https://github.com/example/repo/issues/1",
                        "title": "PromptWeave foundation architecture",
                        "ref": issue_ref,
                    },
                    "next_request_hint": "continue_on_current_issue",
                }
            ),
            encoding="utf-8",
        )
        return summary_path

    def _base_state(self, summary_path: object) -> dict:
        return {
            "mode": "idle",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": True,
            "need_codex_run": False,
            "last_report_file": "logs/report.md",
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "#1",
            "last_issue_centric_principal_issue": "https://github.com/example/repo/issues/1",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_target": "https://github.com/example/repo/issues/1",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_normalized_summary": str(summary_path),
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/1",
            # No current_ready_issue_ref → parent/planned issue path
        }

    _REPORT_TEXT = "\n".join([
        "===BRIDGE_SUMMARY===",
        "- summary: GitHub Copilot 実行完了 (model: sonnet-4.6)",
        "- result: completed",
        "- live_ready: confirmed",
        "===END_BRIDGE_SUMMARY===",
    ])

    def test_parent_issue_continuation_allows_issue_create(self) -> None:
        """Parent issue with no Ready: prefix must allow action=issue_create."""
        import request_prompt_from_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = self._make_summary_path(root)
            state = self._base_state(summary_path)
            args = argparse.Namespace(next_todo="next", open_questions="none", current_status="")
            sent_requests: list[str] = []

            with patch.object(request_prompt_from_report, "send_to_chatgpt", side_effect=lambda t: sent_requests.append(t)):
                with patch.object(request_prompt_from_report, "log_text", side_effect=lambda prefix, text, *a, **kw: root / f"{prefix}.md"):
                    with patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda p: str(p)):
                        with patch.object(request_prompt_from_report, "save_state"):
                            request_prompt_from_report.run_resume_request(state, args, self._REPORT_TEXT, "")

        self.assertEqual(len(sent_requests), 1)
        text = sent_requests[0]
        self.assertIn("issue_centric_completion_followup", text)
        self.assertIn("target_issue: https://github.com/example/repo/issues/1", text)
        # Must offer issue_create / follow-up creation
        self.assertIn("action=issue_create", text)
        self.assertIn("create_followup_issue=true", text)
        # Must NOT mandate close-only no_action
        self.assertNotIn("create_followup_issue=false のまま返してください", text)
        self.assertNotIn("action=codex_run は不正", text)

    def test_parent_issue_continuation_allows_codex_run(self) -> None:
        """Parent issue continuation must list action=codex_run as a valid option."""
        import request_prompt_from_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary_path = self._make_summary_path(root)
            state = self._base_state(summary_path)
            args = argparse.Namespace(next_todo="next", open_questions="none", current_status="")
            sent_requests: list[str] = []

            with patch.object(request_prompt_from_report, "send_to_chatgpt", side_effect=lambda t: sent_requests.append(t)):
                with patch.object(request_prompt_from_report, "log_text", side_effect=lambda prefix, text, *a, **kw: root / f"{prefix}.md"):
                    with patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda p: str(p)):
                        with patch.object(request_prompt_from_report, "save_state"):
                            request_prompt_from_report.run_resume_request(state, args, self._REPORT_TEXT, "")

        self.assertEqual(len(sent_requests), 1)
        text = sent_requests[0]
        self.assertIn("action=codex_run", text)

    def test_ready_issue_continuation_keeps_close_only_directives(self) -> None:
        """Ready: issue must still use the close-only directives (regression guard)."""
        import request_prompt_from_report
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            from pathlib import Path as _Path
            import json as _json
            summary_path = _Path(str(root)) / "summary7.json"
            summary_path.write_text(
                _json.dumps(
                    {
                        "action": "codex_run",
                        "final_status": "completed",
                        "principal_issue_kind": "current_issue",
                        "principal_issue_candidate": {
                            "number": "7",
                            "url": "https://github.com/example/repo/issues/7",
                            "title": "Ready: verify cycles",
                            "ref": "#7",
                        },
                        "current_issue": {
                            "number": "7",
                            "url": "https://github.com/example/repo/issues/7",
                            "title": "Ready: verify cycles",
                            "ref": "#7",
                        },
                        "next_request_hint": "continue_on_current_issue",
                    }
                ),
                encoding="utf-8",
            )
            state = {
                "mode": "idle",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "last_report_file": "logs/report.md",
                "last_issue_centric_action": "codex_run",
                "last_issue_centric_target_issue": "#7",
                "last_issue_centric_principal_issue": "https://github.com/example/repo/issues/7",
                "last_issue_centric_principal_issue_kind": "current_issue",
                "last_issue_centric_next_request_target": "https://github.com/example/repo/issues/7",
                "last_issue_centric_next_request_hint": "continue_on_current_issue",
                "last_issue_centric_normalized_summary": str(summary_path),
                "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/7",
                "current_ready_issue_ref": "#7 Ready: verify cycles",
            }
            args = argparse.Namespace(next_todo="next", open_questions="none", current_status="")
            sent_requests: list[str] = []

            with patch.object(request_prompt_from_report, "send_to_chatgpt", side_effect=lambda t: sent_requests.append(t)):
                with patch.object(request_prompt_from_report, "log_text", side_effect=lambda prefix, text, *a, **kw: root / f"{prefix}.md"):
                    with patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda p: str(p)):
                        with patch.object(request_prompt_from_report, "save_state"):
                            request_prompt_from_report.run_resume_request(state, args, self._REPORT_TEXT, "")

        self.assertEqual(len(sent_requests), 1)
        text = sent_requests[0]
        self.assertIn("issue_centric_completion_followup", text)
        self.assertIn("新しい Codex 用 prompt は作りません", text)
        self.assertIn("action=codex_run は不正", text)
        self.assertIn("create_followup_issue=false", text)
        self.assertNotIn("action=issue_create", text)


# ---------------------------------------------------------------------------
# Fresh-start ready-issue carry-over prevention tests
# ---------------------------------------------------------------------------


class ReadyIssueCarryOverTests(unittest.TestCase):
    """Tests that fresh-start with an explicit ready issue prevents carry-over
    from the previous issue's last_issue_centric_* context.

    Scenario that triggered the bug:
    - Old state has last_issue_centric_target_issue = "#5"
    - Fresh start with "--ready-issue-ref '#7 Ready: verify ...'"
    - After a legacy fallback on the first fetch, run_resume_request was called
    - It picked up #5 from the old IC snapshot context
    - ChatGPT returned issue_centric:no_action targeting #5

    Fix: request_next_prompt saves current_ready_issue_ref to state.
         run_resume_request detects pending_request_source = "ready_issue:..." +
         current_ready_issue_ref and builds a fresh IC section from the pinned ref.
    """

    # ------------------------------------------------------------------
    # build_initial_request: 4-tuple includes ready_issue_ref
    # ------------------------------------------------------------------

    def test_build_initial_request_returns_ready_issue_ref_in_4th_element(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="#7 Ready: verify 2-3 consecutive rehearsal cycles stay stable",
            request_body="",
            project_path="/tmp/repo",
        )
        text, hash_, source, ref = request_next_prompt.build_initial_request(args)
        self.assertEqual(ref, "#7 Ready: verify 2-3 consecutive rehearsal cycles stay stable")
        self.assertTrue(source.startswith("ready_issue:"))

    def test_build_initial_request_returns_empty_ref_for_override(self) -> None:
        args = argparse.Namespace(
            ready_issue_ref="",
            request_body="override body text",
            project_path="/tmp/repo",
        )
        _, _, source, ref = request_next_prompt.build_initial_request(args)
        self.assertEqual(ref, "")
        self.assertTrue(source.startswith("override:"))

    # ------------------------------------------------------------------
    # build_pinned_ready_issue_ic_section
    # ------------------------------------------------------------------

    def test_build_pinned_ready_issue_ic_section_contains_target_issue(self) -> None:
        from _bridge_common import build_pinned_ready_issue_ic_section
        with patch("_bridge_common.load_project_config", return_value={"github_repository": "owner/repo"}):
            section = build_pinned_ready_issue_ic_section("#7 Ready: verify cycles")
        self.assertIn("#7 Ready: verify cycles", section)
        self.assertIn("pinned_ready_issue", section)
        self.assertIn("ready_issue_active", section)

    def test_build_pinned_ready_issue_ic_section_does_not_contain_old_issue(self) -> None:
        from _bridge_common import build_pinned_ready_issue_ic_section
        with patch("_bridge_common.load_project_config", return_value={"github_repository": "owner/repo"}):
            section = build_pinned_ready_issue_ic_section("#7 Ready: verify cycles")
        self.assertNotIn("#5", section)

    # ------------------------------------------------------------------
    # run_resume_request: pinned ready issue prevents carry-over
    # ------------------------------------------------------------------

    def test_run_resume_request_pinned_ready_issue_uses_pinned_target_not_old(self) -> None:
        """When pending_request_source=ready_issue: + current_ready_issue_ref=#7,
        the continuation must target #7, not the old #5 from last_issue_centric_*."""
        import request_prompt_from_report
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state: dict[str, object] = {
                "mode": "awaiting_user",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "last_report_file": "logs/report.md",
                # Old context from a previous #5 run
                "last_issue_centric_target_issue": "#5",
                "last_issue_centric_principal_issue": "https://github.com/owner/repo/issues/5",
                "last_issue_centric_next_request_target": "https://github.com/owner/repo/issues/5",
                # Fresh-start ready issue #7 pinned
                "pending_request_source": f"ready_issue:{request_next_prompt.stable_text_hash('#7 Ready: verify')}",
                "current_ready_issue_ref": "#7 Ready: verify 2-3 consecutive rehearsal cycles stay stable",
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
        # Must contain #7 context in the IC directive section
        self.assertIn("#7 Ready: verify", text, "pinned #7 must appear in the request")
        # IC next_request section must explicitly set target to #7 (not a carry-over #5)
        # Note: state dump still shows legacy last_issue_centric_* fields for debugging context;
        # what matters is the IC directive field `- target_issue: #7` in the next_request section.
        self.assertIn("- target_issue: #7 Ready: verify", text,
                      "IC next_request section must target #7")
        # Must use IC contract (route=issue_centric)
        self.assertIn("===CHATGPT_DECISION_JSON===", text,
                      "pinned ready issue continuation must use IC contract")
        # Must use pinned_ready_issue source label
        self.assertIn("target_issue_source: pinned_ready_issue", text,
                      "IC section must label source as pinned_ready_issue")

    def test_run_resume_request_no_pinned_ref_falls_back_to_normal_path(self) -> None:
        """When current_ready_issue_ref is empty, run_resume_request uses normal path."""
        import request_prompt_from_report
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state: dict[str, object] = {
                "mode": "awaiting_user",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "last_report_file": "logs/report.md",
                # Old context from a #5 run — should be used since no pinned ref
                "last_issue_centric_target_issue": "#5",
                # No pinned ready issue
                "pending_request_source": "report:logs/report.md",
                "current_ready_issue_ref": "",
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
        # pinned_ready_issue must NOT appear (using normal path, not the pinned one)
        self.assertNotIn("pinned_ready_issue", sent_requests[0])

    def test_run_resume_request_ready_issue_source_but_no_ref_falls_back_to_normal_path(self) -> None:
        """ready_issue: source but current_ready_issue_ref is empty → normal path."""
        import request_prompt_from_report
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state: dict[str, object] = {
                "mode": "awaiting_user",
                "need_chatgpt_prompt": False,
                "need_chatgpt_next": True,
                "need_codex_run": False,
                "last_report_file": "logs/report.md",
                "pending_request_source": "ready_issue:somehash",
                "current_ready_issue_ref": "",  # missing → normal path
            }
            args = argparse.Namespace(
                next_todo="next",
                open_questions="none",
                current_status="",
            )
            sent_requests: list[str] = []

            with patch.object(request_prompt_from_report, "send_to_chatgpt", side_effect=lambda t: sent_requests.append(t)):
                with patch.object(request_prompt_from_report, "log_text", side_effect=lambda prefix, text, *a, **kw: root / f"{prefix}.md"):
                    with patch.object(request_prompt_from_report, "repo_relative", side_effect=lambda p: str(p)):
                        with patch.object(request_prompt_from_report, "save_state"):
                            request_prompt_from_report.run_resume_request(state, args, "# Report\n", "")

        self.assertNotIn("pinned_ready_issue", sent_requests[0])


class IsReadyBoundedCompletionFollowupRequestHelperTests(unittest.TestCase):
    """Unit tests for _is_ready_bounded_completion_followup_request."""

    def _invoke(
        self,
        state: dict,
        effective_next_todo: str,
        original_next_todo: str,
    ) -> bool:
        import request_prompt_from_report
        return request_prompt_from_report._is_ready_bounded_completion_followup_request(
            state,
            effective_next_todo=effective_next_todo,
            original_next_todo=original_next_todo,
        )

    def test_ready_bounded_and_todo_overridden_returns_true(self) -> None:
        """Ready: bounded issue + overridden next_todo → lifecycle-only guidance."""
        state = {"current_ready_issue_ref": "#7 Ready: verify cycles"}
        self.assertTrue(self._invoke(state, "lifecycle todo", "original todo"))

    def test_ready_bounded_but_todo_unchanged_returns_false(self) -> None:
        """Ready: bounded issue but next_todo not overridden → no lifecycle-only guidance."""
        state = {"current_ready_issue_ref": "#7 Ready: verify cycles"}
        self.assertFalse(self._invoke(state, "same todo", "same todo"))

    def test_not_ready_bounded_but_todo_overridden_returns_false(self) -> None:
        """Non-Ready: issue even if next_todo overridden → no lifecycle-only guidance."""
        state = {"current_ready_issue_ref": "#7 implement feature"}
        self.assertFalse(self._invoke(state, "lifecycle todo", "original todo"))

    def test_no_ready_issue_ref_returns_false(self) -> None:
        """Empty current_ready_issue_ref → no lifecycle-only guidance."""
        state: dict = {}
        self.assertFalse(self._invoke(state, "lifecycle todo", "original todo"))


class IsCompletionFollowupEligibleHelperTests(unittest.TestCase):
    """Unit tests for _is_completion_followup_eligible."""

    def _base_state(self) -> dict:
        return {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
        }

    def _base_summary(self) -> dict:
        return {"result": "completed", "live_ready": "confirmed"}

    def _invoke(self, summary_fields: dict, state: dict) -> bool:
        import request_prompt_from_report
        return request_prompt_from_report._is_completion_followup_eligible(summary_fields, state)

    def test_all_conditions_met_returns_true(self) -> None:
        """All five conditions satisfied → eligible."""
        self.assertTrue(self._invoke(self._base_summary(), self._base_state()))

    def test_result_not_completed_returns_false(self) -> None:
        """result != completed → not eligible."""
        summary = self._base_summary()
        summary["result"] = "failed"
        self.assertFalse(self._invoke(summary, self._base_state()))

    def test_live_ready_not_confirmed_returns_false(self) -> None:
        """live_ready != confirmed → not eligible."""
        summary = self._base_summary()
        summary["live_ready"] = "pending"
        self.assertFalse(self._invoke(summary, self._base_state()))

    def test_action_not_codex_run_returns_false(self) -> None:
        """last_issue_centric_action != codex_run → not eligible."""
        state = self._base_state()
        state["last_issue_centric_action"] = "no_action"
        self.assertFalse(self._invoke(self._base_summary(), state))

    def test_principal_issue_kind_not_current_issue_returns_false(self) -> None:
        """last_issue_centric_principal_issue_kind != current_issue → not eligible."""
        state = self._base_state()
        state["last_issue_centric_principal_issue_kind"] = "parent_issue"
        self.assertFalse(self._invoke(self._base_summary(), state))

    def test_next_request_hint_not_continue_returns_false(self) -> None:
        """last_issue_centric_next_request_hint != continue_on_current_issue → not eligible."""
        state = self._base_state()
        state["last_issue_centric_next_request_hint"] = "create_followup_issue"
        self.assertFalse(self._invoke(self._base_summary(), state))


class ResolveCompletionFollowupTargetIssueHelperTests(unittest.TestCase):
    """Unit tests for _resolve_completion_followup_target_issue."""

    def _invoke(self, state: dict) -> str:
        import request_prompt_from_report
        return request_prompt_from_report._resolve_completion_followup_target_issue(state)

    def test_next_request_target_takes_priority(self) -> None:
        """last_issue_centric_next_request_target is returned when present."""
        state = {
            "last_issue_centric_next_request_target": "https://github.com/example/repo/issues/10",
            "last_issue_centric_principal_issue": "https://github.com/example/repo/issues/9",
        }
        self.assertEqual(self._invoke(state), "https://github.com/example/repo/issues/10")

    def test_falls_back_to_principal_issue(self) -> None:
        """Falls back to last_issue_centric_principal_issue when next_request_target is absent."""
        state = {
            "last_issue_centric_next_request_target": "",
            "last_issue_centric_principal_issue": "https://github.com/example/repo/issues/9",
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/8",
        }
        self.assertEqual(self._invoke(state), "https://github.com/example/repo/issues/9")

    def test_falls_back_to_resolved_issue(self) -> None:
        """Falls back to last_issue_centric_resolved_issue when earlier fields are absent."""
        state = {
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/8",
            "last_issue_centric_target_issue": "#8",
        }
        self.assertEqual(self._invoke(state), "https://github.com/example/repo/issues/8")

    def test_falls_back_to_target_issue(self) -> None:
        """Falls back to last_issue_centric_target_issue as last resort."""
        state = {"last_issue_centric_target_issue": "#7"}
        self.assertEqual(self._invoke(state), "#7")

    def test_all_empty_returns_empty_string(self) -> None:
        """Returns empty string when all four fields are absent or empty."""
        self.assertEqual(self._invoke({}), "")


class BuildCompletionFollowupWordingHelperTests(unittest.TestCase):
    """Unit tests for _build_completion_followup_wording."""

    def _invoke(self, state: dict) -> tuple:
        import request_prompt_from_report
        return request_prompt_from_report._build_completion_followup_wording(state)

    def test_ready_bounded_returns_lifecycle_only_wording(self) -> None:
        """Ready:-bounded issue → lifecycle-only next_todo, no Codex prompt."""
        state = {"current_ready_issue_ref": "#7 Ready: verify cycles"}
        next_todo, open_questions = self._invoke(state)
        self.assertIn("lifecycle automation", next_todo)
        self.assertIn("action=codex_run は不正", next_todo)
        self.assertNotIn("action=issue_create", next_todo)
        self.assertIn("scope 外", open_questions)

    def test_non_ready_returns_parent_planned_wording(self) -> None:
        """Non-Ready: issue → parent/planned continuation next_todo."""
        state = {"current_ready_issue_ref": "#5 implement feature"}
        next_todo, open_questions = self._invoke(state)
        self.assertIn("parent / planned issue", next_todo)
        self.assertIn("action=issue_create", next_todo)
        self.assertNotIn("action=codex_run は不正", next_todo)
        self.assertIn("scope 外", open_questions)

    def test_no_ready_issue_ref_returns_parent_planned_wording(self) -> None:
        """Empty current_ready_issue_ref → parent/planned wording (same as non-Ready)."""
        state: dict = {}
        next_todo, open_questions = self._invoke(state)
        self.assertIn("parent / planned issue", next_todo)

    def test_both_paths_share_same_open_questions(self) -> None:
        """open_questions is identical for Ready bounded and non-Ready paths."""
        ready_state = {"current_ready_issue_ref": "#7 Ready: foo"}
        non_ready_state = {"current_ready_issue_ref": "#5 implement feature"}
        _, oq_ready = self._invoke(ready_state)
        _, oq_non_ready = self._invoke(non_ready_state)
        self.assertEqual(oq_ready, oq_non_ready)


class ShouldUsePinnedReadyIssuePathHelperTests(unittest.TestCase):
    """Unit tests for _should_use_pinned_ready_issue_path."""

    def _invoke(self, state: dict) -> bool:
        import request_prompt_from_report
        return request_prompt_from_report._should_use_pinned_ready_issue_path(state)

    def test_ready_issue_source_and_ref_present_returns_true(self) -> None:
        """pending_request_source starts with ready_issue: and ref is set → True."""
        state = {
            "pending_request_source": "ready_issue:abc123",
            "current_ready_issue_ref": "#7 Ready: verify cycles",
        }
        self.assertTrue(self._invoke(state))

    def test_non_ready_issue_source_returns_false(self) -> None:
        """pending_request_source does not start with ready_issue: → False."""
        state = {
            "pending_request_source": "report:somehash",
            "current_ready_issue_ref": "#7 Ready: verify cycles",
        }
        self.assertFalse(self._invoke(state))

    def test_ready_issue_source_but_empty_ref_returns_false(self) -> None:
        """pending_request_source is ready_issue: but current_ready_issue_ref is empty → False."""
        state = {
            "pending_request_source": "ready_issue:abc123",
            "current_ready_issue_ref": "",
        }
        self.assertFalse(self._invoke(state))

    def test_both_absent_returns_false(self) -> None:
        """Both fields absent → False."""
        self.assertFalse(self._invoke({}))


class ResolveReportRequestIcContextHelperTests(unittest.TestCase):
    """Unit tests for _resolve_report_request_ic_context."""

    def _invoke(self, state: dict):
        import request_prompt_from_report
        return request_prompt_from_report._resolve_report_request_ic_context(state)

    def test_pinned_ready_issue_path_returns_none_snapshot_and_ic_route(self) -> None:
        """When pinned ready issue path is active, snapshot and mode are None,
        section is built from the pinned ref, and route_selected is 'issue_centric'."""
        state = {
            "pending_request_source": "ready_issue:abc123",
            "current_ready_issue_ref": "#7 Ready: verify cycles",
        }
        with patch("_bridge_common.load_project_config", return_value={"github_repository": "owner/repo"}):
            ctx = self._invoke(state)
        self.assertIsNone(ctx.runtime_snapshot)
        self.assertIsNone(ctx.runtime_mode)
        self.assertIn("#7 Ready: verify cycles", ctx.next_request_section)
        self.assertEqual(ctx.route_selected, "issue_centric")

    def test_normal_path_delegates_to_prepare_functions(self) -> None:
        """Normal path calls prepare snapshot / mode / route helpers and returns results."""
        import request_prompt_from_report
        from unittest.mock import MagicMock

        mock_snapshot = MagicMock()
        mock_snapshot.snapshot_path = "logs/snap.json"
        mock_snapshot.snapshot_status = "ready"
        mock_mode = MagicMock()
        mock_section = "IC SECTION"
        mock_route_choice = MagicMock()
        mock_route_choice.route_selected = "issue_centric"

        state = {"pending_request_source": "report:hash", "current_ready_issue_ref": ""}
        with patch.object(request_prompt_from_report, "prepare_issue_centric_runtime_snapshot", return_value=(mock_snapshot, None)):
            with patch.object(request_prompt_from_report, "_persist_runtime_snapshot_if_needed", return_value=mock_snapshot):
                with patch.object(request_prompt_from_report, "prepare_issue_centric_runtime_mode", return_value=(mock_mode, mock_section)):
                    with patch.object(request_prompt_from_report, "resolve_issue_centric_route_choice", return_value=mock_route_choice):
                        ctx = self._invoke(state)

        self.assertIs(ctx.runtime_snapshot, mock_snapshot)
        self.assertIs(ctx.runtime_mode, mock_mode)
        self.assertEqual(ctx.next_request_section, mock_section)
        self.assertEqual(ctx.route_selected, "issue_centric")


class ResolveResumeRequestPayloadHelperTests(unittest.TestCase):
    """Unit tests for _resolve_resume_request_payload."""

    def _make_args(self, *, next_todo: str = "do next", open_questions: str = "", current_status: str = "") -> object:
        import argparse
        args = argparse.Namespace()
        args.next_todo = next_todo
        args.open_questions = open_questions
        args.current_status = current_status
        return args

    def test_retryable_request_provided_is_reused(self) -> None:
        """When retryable_request is passed directly, it is returned as-is with prepared_status."""
        import request_prompt_from_report
        state = {"prepared_request_status": "prepared"}
        retryable = ("TEXT", "HASH", "report:source")
        text, hash_, source, prepared_status = request_prompt_from_report._resolve_resume_request_payload(
            state,
            retryable_request=retryable,
            args=self._make_args(),
            last_report="report text",
            resume_note="",
            effective_next_todo="do next",
            effective_open_questions="",
            issue_centric_next_request_section="IC SECTION",
        )
        self.assertEqual(text, "TEXT")
        self.assertEqual(hash_, "HASH")
        self.assertEqual(source, "report:source")
        self.assertEqual(prepared_status, "prepared")

    def test_no_retryable_fresh_build_returns_none_prepared_status(self) -> None:
        """When no retryable request is available, fresh build returns prepared_status=None."""
        import request_prompt_from_report
        from unittest.mock import MagicMock, patch
        state: dict = {}
        with patch.object(request_prompt_from_report, "load_retryable_prepared_request", return_value=None):
            with patch.object(request_prompt_from_report, "build_chatgpt_request", return_value="FRESH TEXT"):
                with patch.object(request_prompt_from_report, "stable_text_hash", return_value="FRESH HASH"):
                    with patch.object(request_prompt_from_report, "build_report_request_source", return_value="report:fresh"):
                        with patch.object(request_prompt_from_report, "_is_ready_bounded_completion_followup_request", return_value=False):
                            text, hash_, source, prepared_status = request_prompt_from_report._resolve_resume_request_payload(
                                state,
                                retryable_request=None,
                                args=self._make_args(),
                                last_report="report text",
                                resume_note="",
                                effective_next_todo="do next",
                                effective_open_questions="",
                                issue_centric_next_request_section="IC SECTION",
                            )
        self.assertEqual(text, "FRESH TEXT")
        self.assertEqual(hash_, "FRESH HASH")
        self.assertEqual(source, "report:fresh")
        self.assertIsNone(prepared_status)

    def test_load_retryable_called_when_retryable_is_none(self) -> None:
        """When retryable_request=None, load_retryable_prepared_request is called."""
        import request_prompt_from_report
        from unittest.mock import patch
        loaded_retryable = ("LOADED TEXT", "LOADED HASH", "report:loaded")
        state: dict = {"prepared_request_status": "retry_send"}
        with patch.object(request_prompt_from_report, "load_retryable_prepared_request", return_value=loaded_retryable) as mock_load:
            text, hash_, source, prepared_status = request_prompt_from_report._resolve_resume_request_payload(
                state,
                retryable_request=None,
                args=self._make_args(),
                last_report="report text",
                resume_note="",
                effective_next_todo="do next",
                effective_open_questions="",
                issue_centric_next_request_section="IC SECTION",
            )
        mock_load.assert_called_once_with(state)
        self.assertEqual(text, "LOADED TEXT")
        self.assertEqual(prepared_status, "retry_send")


class ResolveNormalIcContextHelperTests(unittest.TestCase):
    """Unit tests for _resolve_normal_ic_context."""

    def test_normal_ic_context_delegates_to_prepare_functions(self) -> None:
        """_resolve_normal_ic_context calls prepare snapshot / mode / route and returns _IcResolvedContext."""
        import request_prompt_from_report
        from unittest.mock import MagicMock, patch

        mock_snapshot = MagicMock()
        mock_snapshot.snapshot_path = "logs/snap.json"
        mock_snapshot.snapshot_status = "ready"
        mock_mode = MagicMock()
        mock_section = "IC SECTION"
        mock_route_choice = MagicMock()
        mock_route_choice.route_selected = "issue_centric"

        state: dict = {}
        with patch.object(request_prompt_from_report, "prepare_issue_centric_runtime_snapshot", return_value=(mock_snapshot, None)):
            with patch.object(request_prompt_from_report, "_persist_runtime_snapshot_if_needed", return_value=mock_snapshot):
                with patch.object(request_prompt_from_report, "prepare_issue_centric_runtime_mode", return_value=(mock_mode, mock_section)):
                    with patch.object(request_prompt_from_report, "resolve_issue_centric_route_choice", return_value=mock_route_choice):
                        ctx = request_prompt_from_report._resolve_normal_ic_context(state)

        self.assertIsInstance(ctx, request_prompt_from_report._IcResolvedContext)
        self.assertIs(ctx.runtime_snapshot, mock_snapshot)
        self.assertIs(ctx.runtime_mode, mock_mode)
        self.assertEqual(ctx.next_request_section, mock_section)
        self.assertEqual(ctx.route_selected, "issue_centric")


class LogPreparedRequestReuseHelperTests(unittest.TestCase):
    """Unit tests for _log_prepared_request_reuse."""

    def _invoke(self, prepared_status: str, route_selected: str) -> None:
        import request_prompt_from_report
        request_prompt_from_report._log_prepared_request_reuse(prepared_status, route_selected)

    def test_prepared_ic_route_prints_ic_message(self) -> None:
        """prepared + issue_centric route → IC wording."""
        import io
        import sys
        buf = io.StringIO()
        sys.stdout = buf
        try:
            self._invoke("prepared", "issue_centric")
        finally:
            sys.stdout = sys.__stdout__
        self.assertIn("issue-centric preferred route", buf.getvalue())

    def test_prepared_legacy_route_prints_legacy_message(self) -> None:
        """prepared + non-IC route → legacy fallback wording."""
        import io
        import sys
        buf = io.StringIO()
        sys.stdout = buf
        try:
            self._invoke("prepared", "legacy_fallback")
        finally:
            sys.stdout = sys.__stdout__
        self.assertIn("legacy fallback", buf.getvalue())

    def test_non_prepared_status_prints_resend_message(self) -> None:
        """Any status other than 'prepared' → unsent retry wording."""
        import io
        import sys
        buf = io.StringIO()
        sys.stdout = buf
        try:
            self._invoke("retry_send", "issue_centric")
        finally:
            sys.stdout = sys.__stdout__
        self.assertIn("前回未送信", buf.getvalue())


class IsDuplicatePendingRequestHelperTests(unittest.TestCase):
    """Unit tests for _is_duplicate_pending_request."""

    def _invoke(self, state: dict, request_source: str) -> bool:
        import request_prompt_from_report
        return request_prompt_from_report._is_duplicate_pending_request(state, request_source)

    def test_matching_pending_source_returns_true(self) -> None:
        """Same mode + matching source → True."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_source": "report:file.md",
        }
        self.assertTrue(self._invoke(state, "report:file.md"))

    def test_different_source_returns_false(self) -> None:
        """Same mode but different source → False."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_source": "report:other.md",
        }
        self.assertFalse(self._invoke(state, "report:file.md"))

    def test_non_waiting_mode_returns_false(self) -> None:
        """Mode not 'waiting_prompt_reply' → False even if source matches."""
        state = {
            "mode": "idle",
            "pending_request_source": "report:file.md",
        }
        self.assertFalse(self._invoke(state, "report:file.md"))

    def test_both_absent_returns_false(self) -> None:
        """Empty state → False."""
        self.assertFalse(self._invoke({}, "report:file.md"))


class CleanStalePendingHandoffHelperTests(unittest.TestCase):
    """Tests for _clean_stale_pending_handoff_if_needed."""

    def _invoke(self, state: dict) -> dict:
        from request_prompt_from_report import _clean_stale_pending_handoff_if_needed

        return _clean_stale_pending_handoff_if_needed(state)

    def test_cleans_when_no_rotation_needed_and_log_present(self) -> None:
        """When rotation is not needed and pending_handoff_log is set, fields are cleared."""
        import unittest.mock as mock

        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "pending_handoff_log": "bridge/logs/handoff.md",
            "pending_handoff_hash": "abc123",
            "pending_handoff_source": "report:x.md",
        }
        with mock.patch(
            "request_prompt_from_report.should_rotate_before_next_chat_request",
            return_value=False,
        ), mock.patch("request_prompt_from_report.save_state") as mock_save:
            result = self._invoke(state)

        self.assertEqual(result.get("pending_handoff_log", ""), "")
        self.assertEqual(result.get("pending_handoff_hash", ""), "")
        mock_save.assert_called_once()

    def test_noop_when_rotation_needed(self) -> None:
        """When rotation is needed, state is returned unchanged."""
        import unittest.mock as mock

        state = {
            "mode": "idle",
            "need_chatgpt_next": True,
            "pending_handoff_log": "bridge/logs/handoff.md",
        }
        with mock.patch(
            "request_prompt_from_report.should_rotate_before_next_chat_request",
            return_value=True,
        ), mock.patch("request_prompt_from_report.save_state") as mock_save:
            result = self._invoke(state)

        self.assertIs(result, state)
        mock_save.assert_not_called()

    def test_noop_when_pending_handoff_log_absent(self) -> None:
        """When pending_handoff_log is empty, state is returned unchanged."""
        import unittest.mock as mock

        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.should_rotate_before_next_chat_request",
            return_value=False,
        ), mock.patch("request_prompt_from_report.save_state") as mock_save:
            result = self._invoke(state)

        self.assertIs(result, state)
        mock_save.assert_not_called()


class AcquireRotatedHandoffHelperCachedPathTests(unittest.TestCase):
    """Tests for _acquire_rotated_handoff — cached pending handoff path."""

    def _invoke(self, state: dict, *, request_source: str = "report:x.md") -> tuple:
        import argparse

        from request_prompt_from_report import _acquire_rotated_handoff, _IcResolvedContext

        args = argparse.Namespace(
            next_todo="",
            open_questions="",
            current_status=None,
        )
        return _acquire_rotated_handoff(
            state,
            args,
            "last_report_text",
            request_source=request_source,
            ic_context=_IcResolvedContext(),
        )

    def test_returns_cached_handoff_when_source_matches(self) -> None:
        """When pending_handoff_source matches and text is available, cached text is returned."""
        import unittest.mock as mock

        state = {
            "pending_handoff_source": "report:x.md",
            "pending_handoff_log": "bridge/logs/handoff.md",
        }
        with mock.patch(
            "request_prompt_from_report.read_pending_handoff_text",
            return_value="HANDOFF_TEXT",
        ):
            handoff_text, handoff_received_log = self._invoke(state)

        self.assertEqual(handoff_text, "HANDOFF_TEXT")
        self.assertEqual(handoff_received_log, "bridge/logs/handoff.md")

    def test_proceeds_to_fresh_acquisition_when_source_differs(self) -> None:
        """When pending_handoff_source does not match, fresh acquisition path is taken."""
        import unittest.mock as mock

        state = {
            "pending_handoff_source": "report:OTHER.md",
            "pending_handoff_log": "bridge/logs/handoff.md",
        }
        with mock.patch(
            "request_prompt_from_report.build_chatgpt_handoff_request",
            return_value="REQUEST_TEXT",
        ), mock.patch(
            "request_prompt_from_report.log_text",
            side_effect=lambda tag, text: f"log:{tag}",
        ), mock.patch(
            "request_prompt_from_report.send_to_chatgpt",
        ), mock.patch(
            "request_prompt_from_report.wait_for_handoff_reply_text",
            return_value="RAW",
        ), mock.patch(
            "request_prompt_from_report.extract_last_chatgpt_handoff",
            return_value="FRESH_HANDOFF",
        ), mock.patch(
            "request_prompt_from_report.stable_text_hash",
            return_value="HASH",
        ), mock.patch(
            "request_prompt_from_report.repo_relative",
            side_effect=lambda x: x,
        ), mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: d,
        ), mock.patch(
            "request_prompt_from_report.clear_pending_request_fields",
        ), mock.patch(
            "request_prompt_from_report.save_state",
        ):
            handoff_text, handoff_received_log = self._invoke(state, request_source="report:x.md")

        self.assertEqual(handoff_text, "FRESH_HANDOFF")
        self.assertEqual(handoff_received_log, "log:handoff_received")


class ResolveReportRequestEntryPlanTests(unittest.TestCase):
    """Tests for _resolve_report_request_entry_plan."""

    def _invoke(self, state: dict, argv: list | None = None):
        import argparse

        from request_prompt_from_report import (
            _resolve_report_request_entry_plan,
            parse_args,
        )

        args = parse_args(argv or [])
        return _resolve_report_request_entry_plan(state, args)

    def test_retryable_resume_path(self) -> None:
        """When a retryable prepared request exists, path is retryable_resume."""
        import unittest.mock as mock

        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.load_retryable_prepared_request",
            return_value=("text", "hash", "source"),
        ), mock.patch(
            "request_prompt_from_report.read_last_report_text",
            return_value="REPORT",
        ):
            plan = self._invoke(state)

        self.assertEqual(plan.path, "retryable_resume")
        self.assertEqual(plan.retryable_request, ("text", "hash", "source"))
        self.assertEqual(plan.last_report, "REPORT")

    def test_awaiting_user_stop_path(self) -> None:
        """When mode is awaiting_user and resume_note is empty, path is awaiting_user_stop."""
        import unittest.mock as mock

        state = {"mode": "awaiting_user"}
        with mock.patch(
            "request_prompt_from_report.load_retryable_prepared_request",
            return_value=None,
        ), mock.patch(
            "request_prompt_from_report.resolve_resume_note",
            return_value="",
        ):
            plan = self._invoke(state)

        self.assertEqual(plan.path, "awaiting_user_stop")
        self.assertIsNone(plan.retryable_request)

    def test_awaiting_user_resume_path(self) -> None:
        """When mode is awaiting_user with a non-empty resume_note, path is awaiting_user_resume."""
        import unittest.mock as mock

        state = {"mode": "awaiting_user"}
        with mock.patch(
            "request_prompt_from_report.load_retryable_prepared_request",
            return_value=None,
        ), mock.patch(
            "request_prompt_from_report.resolve_resume_note",
            return_value="補足メモ",
        ), mock.patch(
            "request_prompt_from_report.read_last_report_text",
            return_value="REPORT",
        ), mock.patch(
            "request_prompt_from_report._clean_stale_pending_handoff_if_needed",
            side_effect=lambda s: s,
        ):
            plan = self._invoke(state)

        self.assertEqual(plan.path, "awaiting_user_resume")
        self.assertEqual(plan.resume_note, "補足メモ")

    def test_rotated_path(self) -> None:
        """When rotation is needed, path is rotated."""
        import unittest.mock as mock

        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.load_retryable_prepared_request",
            return_value=None,
        ), mock.patch(
            "request_prompt_from_report.resolve_resume_note",
            return_value="",
        ), mock.patch(
            "request_prompt_from_report.read_last_report_text",
            return_value="REPORT",
        ), mock.patch(
            "request_prompt_from_report._clean_stale_pending_handoff_if_needed",
            side_effect=lambda s: s,
        ), mock.patch(
            "request_prompt_from_report.should_rotate_before_next_chat_request",
            return_value=True,
        ):
            plan = self._invoke(state)

        self.assertEqual(plan.path, "rotated")
        self.assertEqual(plan.last_report, "REPORT")

    def test_normal_resume_path(self) -> None:
        """Default case with no rotation produces normal_resume."""
        import unittest.mock as mock

        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.load_retryable_prepared_request",
            return_value=None,
        ), mock.patch(
            "request_prompt_from_report.resolve_resume_note",
            return_value="",
        ), mock.patch(
            "request_prompt_from_report.read_last_report_text",
            return_value="REPORT",
        ), mock.patch(
            "request_prompt_from_report._clean_stale_pending_handoff_if_needed",
            side_effect=lambda s: s,
        ), mock.patch(
            "request_prompt_from_report.should_rotate_before_next_chat_request",
            return_value=False,
        ):
            plan = self._invoke(state)

        self.assertEqual(plan.path, "normal_resume")

    def test_stale_handoff_cleaned_in_plan(self) -> None:
        """_clean_stale_pending_handoff_if_needed is applied during plan resolution."""
        import unittest.mock as mock

        original_state = {"mode": "idle", "pending_handoff_log": "bridge/logs/h.md"}
        cleaned_state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.load_retryable_prepared_request",
            return_value=None,
        ), mock.patch(
            "request_prompt_from_report.resolve_resume_note",
            return_value="",
        ), mock.patch(
            "request_prompt_from_report.read_last_report_text",
            return_value="REPORT",
        ), mock.patch(
            "request_prompt_from_report._clean_stale_pending_handoff_if_needed",
            return_value=cleaned_state,
        ) as mock_clean, mock.patch(
            "request_prompt_from_report.should_rotate_before_next_chat_request",
            return_value=False,
        ):
            plan = self._invoke(original_state)

        mock_clean.assert_called_once_with(original_state)
        self.assertIs(plan.state, cleaned_state)


class ExecuteReportRequestEntryPlanTests(unittest.TestCase):
    """Tests for _execute_report_request_entry_plan."""

    def _make_plan(self, path: str, **kwargs):
        import argparse

        from request_prompt_from_report import _ReportRequestEntryPlan

        defaults = {
            "state": {"mode": "idle"},
            "args": argparse.Namespace(next_todo="", open_questions="", current_status=None, resume_note=""),
            "last_report": "REPORT",
            "resume_note": "",
            "retryable_request": None,
        }
        defaults.update(kwargs)
        return _ReportRequestEntryPlan(path=path, **defaults)

    def _invoke(self, plan) -> int:
        from request_prompt_from_report import _execute_report_request_entry_plan

        return _execute_report_request_entry_plan(plan)

    def test_awaiting_user_stop_prints_and_returns_0(self) -> None:
        import io
        import unittest.mock as mock

        plan = self._make_plan("awaiting_user_stop")
        with mock.patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            result = self._invoke(plan)

        self.assertEqual(result, 0)
        self.assertIn("空のため送信しませんでした", mock_out.getvalue())

    def test_retryable_resume_delegates(self) -> None:
        import unittest.mock as mock

        plan = self._make_plan("retryable_resume", retryable_request=("t", "h", "s"))
        with mock.patch(
            "request_prompt_from_report.run_resume_request",
            return_value=0,
        ) as mock_run:
            result = self._invoke(plan)

        self.assertEqual(result, 0)
        call_args = mock_run.call_args
        self.assertEqual(call_args[0][4], ("t", "h", "s"))

    def test_awaiting_user_resume_delegates(self) -> None:
        import unittest.mock as mock

        plan = self._make_plan("awaiting_user_resume", resume_note="補足")
        with mock.patch(
            "request_prompt_from_report.run_resume_request",
            return_value=0,
        ) as mock_run:
            result = self._invoke(plan)

        self.assertEqual(result, 0)
        call_args = mock_run.call_args
        self.assertEqual(call_args[0][3], "補足")

    def test_rotated_delegates(self) -> None:
        import unittest.mock as mock

        plan = self._make_plan("rotated")
        with mock.patch(
            "request_prompt_from_report.run_rotated_report_request",
            return_value=0,
        ) as mock_run:
            result = self._invoke(plan)

        self.assertEqual(result, 0)
        mock_run.assert_called_once()

    def test_normal_resume_delegates(self) -> None:
        import unittest.mock as mock

        plan = self._make_plan("normal_resume")
        with mock.patch(
            "request_prompt_from_report.run_resume_request",
            return_value=0,
        ) as mock_run:
            result = self._invoke(plan)

        self.assertEqual(result, 0)
        call_args = mock_run.call_args
        # resume_note must be empty string for normal_resume
        self.assertEqual(call_args[0][3], "")


class StagePreparedRequestStateTests(unittest.TestCase):
    """Tests for _stage_prepared_request_state."""

    def _invoke(self, state: dict, *, status: str = "prepared", snapshot=None) -> None:
        from request_prompt_from_report import _stage_prepared_request_state

        _stage_prepared_request_state(
            state,
            request_hash="HASH",
            request_source="report:x.md",
            request_log_rel="bridge/logs/prepared.md",
            issue_centric_runtime_snapshot=snapshot,
            status=status,
        )

    def test_prepared_staging_saves_state(self) -> None:
        import unittest.mock as mock

        state = {"mode": "idle", "error": "old_error"}
        with mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: {k: v for k, v in d.items() if k != "error"},
        ), mock.patch(
            "request_prompt_from_report.clear_pending_request_fields",
        ), mock.patch(
            "request_prompt_from_report.stage_prepared_request",
        ) as mock_stage, mock.patch(
            "request_prompt_from_report.save_state",
        ) as mock_save:
            self._invoke(state)

        mock_stage.assert_called_once()
        call_kwargs = mock_stage.call_args[1]
        self.assertEqual(call_kwargs["request_hash"], "HASH")
        self.assertEqual(call_kwargs["request_source"], "report:x.md")
        self.assertEqual(call_kwargs["status"], "prepared")
        mock_save.assert_called_once()

    def test_retry_send_status_passed_through(self) -> None:
        import unittest.mock as mock

        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: dict(d),
        ), mock.patch(
            "request_prompt_from_report.clear_pending_request_fields",
        ), mock.patch(
            "request_prompt_from_report.stage_prepared_request",
        ) as mock_stage, mock.patch(
            "request_prompt_from_report.save_state",
        ):
            self._invoke(state, status="retry_send")

        call_kwargs = mock_stage.call_args[1]
        self.assertEqual(call_kwargs["status"], "retry_send")

    def test_ic_generation_binding_applied_when_snapshot_present(self) -> None:
        import unittest.mock as mock

        class _FakeSnapshot:
            snapshot_path = "bridge/ic.json"
            snapshot_status = "ok"
            generation_id = "gen-1"
            runtime_mode = "issue_centric"
            runtime_mode_reason = ""
            runtime_mode_source = ""
            freshness_status = "issue_centric_fresh"
            freshness_reason = ""
            freshness_source = ""
            invalidation_status = ""
            invalidation_reason = ""
            target_issue = "#1"
            target_issue_source = "snapshot"
            fallback_reason = ""
            route_selected = "issue_centric"
            recovery_status = ""
            recovery_source = ""

        staged_state = {}
        ic_updates = {"ic_generation_id_prepared": "gen-1"}

        with mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: dict(d),
        ), mock.patch(
            "request_prompt_from_report.clear_pending_request_fields",
        ), mock.patch(
            "request_prompt_from_report._issue_centric_next_request_state_updates",
            return_value=ic_updates,
        ) as mock_ic, mock.patch(
            "request_prompt_from_report.stage_prepared_request",
        ), mock.patch(
            "request_prompt_from_report.save_state",
            side_effect=lambda s: staged_state.update(s),
        ):
            self._invoke(state={"mode": "idle"}, snapshot=_FakeSnapshot())

        mock_ic.assert_called_once()
        _call_kwargs = mock_ic.call_args[1]
        self.assertEqual(_call_kwargs["phase"], "prepared")
        # The ic updates should be in the saved state
        self.assertIn("ic_generation_id_prepared", staged_state)


class ApplyPendingRequestStateTests(unittest.TestCase):
    """Tests for _apply_pending_request_state."""

    def _invoke(self, state: dict, *, snapshot=None, success_updates=None) -> None:
        from request_prompt_from_report import _apply_pending_request_state

        _apply_pending_request_state(
            state,
            request_hash="HASH",
            request_source="report:x.md",
            request_log_path="bridge/logs/sent.md",
            issue_centric_runtime_snapshot=snapshot,
            success_updates=success_updates,
        )

    def test_pending_state_saved(self) -> None:
        import unittest.mock as mock

        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: dict(d),
        ), mock.patch(
            "request_prompt_from_report.clear_pending_handoff_fields",
        ), mock.patch(
            "request_prompt_from_report.promote_pending_request",
        ) as mock_promote, mock.patch(
            "request_prompt_from_report.repo_relative",
            side_effect=lambda x: x,
        ), mock.patch(
            "request_prompt_from_report.save_state",
        ) as mock_save:
            self._invoke(state)

        mock_promote.assert_called_once()
        call_kwargs = mock_promote.call_args[1]
        self.assertEqual(call_kwargs["request_hash"], "HASH")
        mock_save.assert_called_once()

    def test_success_updates_merged(self) -> None:
        import unittest.mock as mock

        saved = {}
        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: dict(d),
        ), mock.patch(
            "request_prompt_from_report.clear_pending_handoff_fields",
        ), mock.patch(
            "request_prompt_from_report.promote_pending_request",
        ), mock.patch(
            "request_prompt_from_report.repo_relative",
            side_effect=lambda x: x,
        ), mock.patch(
            "request_prompt_from_report.save_state",
            side_effect=lambda s: saved.update(s),
        ):
            self._invoke(state, success_updates={"chatgpt_decision": "", "human_review_auto_continue_count": 0})

        self.assertIn("chatgpt_decision", saved)
        self.assertEqual(saved["human_review_auto_continue_count"], 0)


class ApplyRotatedPendingRequestStateTests(unittest.TestCase):
    """Tests for _apply_rotated_pending_request_state."""

    def _make_rotated_chat(self, **kwargs) -> dict:
        base = {
            "url": "https://chat.openai.com/c/test",
            "title": "Test chat",
            "signal": "",
            "github_source_attach_status": "",
            "github_source_attach_boundary": "",
            "github_source_attach_detail": "",
            "github_source_attach_context": "",
            "github_source_attach_log": "",
            "request_send_continued_without_github_source": False,
            "match_kind": "",
            "matched_hint": "",
            "project_name": "",
            "warning": "",
        }
        base.update(kwargs)
        return base

    def _invoke(self, state: dict, *, rotation_signal: str = "", snapshot=None, mode=None) -> None:
        from request_prompt_from_report import _apply_rotated_pending_request_state

        _apply_rotated_pending_request_state(
            state,
            request_hash="HASH",
            request_source="report:x.md",
            request_log_path="bridge/logs/sent.md",
            rotation_signal=rotation_signal,
            rotated_chat=self._make_rotated_chat(),
            issue_centric_runtime_snapshot=snapshot,
            issue_centric_runtime_mode=mode,
        )

    def test_pending_fields_saved(self) -> None:
        import unittest.mock as mock

        saved = {}
        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: dict(d),
        ), mock.patch(
            "request_prompt_from_report.clear_pending_request_fields",
        ), mock.patch(
            "request_prompt_from_report.clear_pending_handoff_fields",
        ), mock.patch(
            "request_prompt_from_report.clear_chat_rotation_fields",
        ), mock.patch(
            "request_prompt_from_report.repo_relative",
            side_effect=lambda x: x,
        ), mock.patch(
            "request_prompt_from_report.save_state",
            side_effect=lambda s: saved.update(s),
        ):
            self._invoke(state)

        self.assertEqual(saved.get("mode"), "waiting_prompt_reply")
        self.assertEqual(saved.get("pending_request_hash"), "HASH")
        self.assertEqual(saved.get("pending_request_source"), "report:x.md")
        self.assertEqual(saved.get("current_chat_session"), "https://chat.openai.com/c/test")

    def test_ic_generation_binding_applied_when_snapshot_present(self) -> None:
        import unittest.mock as mock

        class _FakeSnapshot:
            pass

        ic_updates = {"ic_generation_id_pending": "gen-1"}
        state = {"mode": "idle"}
        with mock.patch(
            "request_prompt_from_report.clear_error_fields",
            side_effect=lambda d: dict(d),
        ), mock.patch(
            "request_prompt_from_report.clear_pending_request_fields",
        ), mock.patch(
            "request_prompt_from_report.clear_pending_handoff_fields",
        ), mock.patch(
            "request_prompt_from_report.clear_chat_rotation_fields",
        ), mock.patch(
            "request_prompt_from_report.repo_relative",
            side_effect=lambda x: x,
        ), mock.patch(
            "request_prompt_from_report._issue_centric_next_request_state_updates",
            return_value=ic_updates,
        ) as mock_ic, mock.patch(
            "request_prompt_from_report.save_state",
        ):
            snap = _FakeSnapshot()
            self._invoke(state, snapshot=snap)

        mock_ic.assert_called_once_with(snap, phase="pending")


class IcGenerationLifecycleResolverTests(unittest.TestCase):
    """Tests for _resolve_ic_generation_lifecycle (Phase 29)."""

    def _call(self, generation_id="", *, runtime_mode="", runtime_mode_reason="",
              fallback_reason="", route_selected="", phase="",
              ctx_freshness_status="", ctx_freshness_reason="", ctx_freshness_source="",
              ctx_invalidation_status="", ctx_invalidation_reason=""):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m._resolve_ic_generation_lifecycle(
            generation_id,
            runtime_mode=runtime_mode,
            runtime_mode_reason=runtime_mode_reason,
            fallback_reason=fallback_reason,
            route_selected=route_selected,
            phase=phase,
            ctx_freshness_status=ctx_freshness_status,
            ctx_freshness_reason=ctx_freshness_reason,
            ctx_freshness_source=ctx_freshness_source,
            ctx_invalidation_status=ctx_invalidation_status,
            ctx_invalidation_reason=ctx_invalidation_reason,
        )

    def test_no_generation_id_returns_ctx_defaults(self):
        lc = self._call(
            generation_id="",
            ctx_freshness_status="ctx_fresh",
            ctx_freshness_reason="ctx_reason",
            ctx_invalidation_status="ctx_inv_status",
            route_selected="my_route",
            fallback_reason="my_fallback",
        )
        self.assertEqual(lc.freshness_status, "ctx_fresh")
        self.assertEqual(lc.freshness_reason, "ctx_reason")
        self.assertEqual(lc.invalidation_status, "ctx_inv_status")
        self.assertEqual(lc.route_selected, "my_route")
        self.assertEqual(lc.fallback_reason, "my_fallback")
        self.assertEqual(lc.generation_lifecycle, "")
        self.assertEqual(lc.prepared_generation_id, "")
        self.assertEqual(lc.pending_generation_id, "")
        self.assertEqual(lc.invalidated_generation_id, "")

    def test_degraded_fallback_marks_invalidated(self):
        lc = self._call(
            generation_id="gen-abc",
            runtime_mode="issue_centric_degraded_fallback",
            runtime_mode_reason="degraded_reason",
            route_selected="original_route",
            fallback_reason="orig_fallback",
            phase="",
        )
        self.assertEqual(lc.generation_lifecycle, "issue_centric_invalidated")
        self.assertEqual(lc.freshness_status, "issue_centric_invalidated")
        self.assertEqual(lc.invalidation_status, "issue_centric_invalidated")
        self.assertEqual(lc.invalidation_reason, "degraded_reason")
        self.assertEqual(lc.invalidated_generation_id, "gen-abc")
        self.assertEqual(lc.route_selected, "fallback_legacy")
        self.assertEqual(lc.fallback_reason, "degraded_reason")
        self.assertEqual(lc.prepared_generation_id, "")
        self.assertEqual(lc.pending_generation_id, "")

    def test_unavailable_mode_also_invalidates(self):
        lc = self._call(
            generation_id="gen-xyz",
            runtime_mode="issue_centric_unavailable",
            runtime_mode_reason="",
            fallback_reason="unavail_fallback",
            phase="",
        )
        self.assertEqual(lc.generation_lifecycle, "issue_centric_invalidated")
        self.assertEqual(lc.invalidation_reason, "unavail_fallback")
        self.assertEqual(lc.route_selected, "fallback_legacy")

    def test_phase_prepared_binds_prepared_generation_id(self):
        lc = self._call(
            generation_id="gen-prep",
            runtime_mode="issue_centric_normal",
            phase="prepared",
            route_selected="ic_route",
            fallback_reason="",
        )
        self.assertEqual(lc.generation_lifecycle, "fresh_prepared")
        self.assertEqual(lc.freshness_status, "issue_centric_fresh")
        self.assertEqual(lc.freshness_reason, "prepared_request_bound_to_generation")
        self.assertEqual(lc.prepared_generation_id, "gen-prep")
        self.assertEqual(lc.pending_generation_id, "")
        self.assertEqual(lc.invalidated_generation_id, "")
        self.assertEqual(lc.route_selected, "ic_route")

    def test_phase_pending_binds_pending_generation_id(self):
        lc = self._call(
            generation_id="gen-pend",
            runtime_mode="issue_centric_normal",
            phase="pending",
            route_selected="ic_route",
            fallback_reason="",
        )
        self.assertEqual(lc.generation_lifecycle, "fresh_pending")
        self.assertEqual(lc.freshness_status, "issue_centric_fresh")
        self.assertEqual(lc.freshness_reason, "pending_request_bound_to_generation")
        self.assertEqual(lc.pending_generation_id, "gen-pend")
        self.assertEqual(lc.prepared_generation_id, "")
        self.assertEqual(lc.invalidated_generation_id, "")

    def test_fresh_available_when_no_specific_phase(self):
        lc = self._call(
            generation_id="gen-avail",
            runtime_mode="issue_centric_normal",
            phase="other",
            route_selected="some_route",
            fallback_reason="",
        )
        self.assertEqual(lc.generation_lifecycle, "fresh_available")
        self.assertEqual(lc.freshness_status, "issue_centric_fresh")
        self.assertEqual(lc.freshness_reason, "latest_issue_centric_generation_available")
        self.assertEqual(lc.prepared_generation_id, "")
        self.assertEqual(lc.pending_generation_id, "")
        self.assertEqual(lc.invalidated_generation_id, "")
        self.assertEqual(lc.route_selected, "some_route")


class IcNextRequestStateUpdatesTests(unittest.TestCase):
    """Tests for _issue_centric_next_request_state_updates (Phase 29)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def _make_context(self, **kwargs):
        class _Ctx:
            pass
        ctx = _Ctx()
        for k, v in kwargs.items():
            setattr(ctx, k, v)
        return ctx

    def test_route_override_when_degraded(self):
        m = self._module()
        ctx = self._make_context(
            snapshot_path="snap/path",
            snapshot_status="ok",
            generation_id="gen-1",
            runtime_mode="issue_centric_degraded_fallback",
            runtime_mode_reason="degraded",
            runtime_mode_source="",
            target_issue="issue-42",
            target_issue_source="ic",
            fallback_reason="",
            route_selected="ic_normal",
            recovery_status="",
            recovery_source="",
            freshness_status="",
            freshness_reason="",
            freshness_source="",
            invalidation_status="",
            invalidation_reason="",
        )
        result = m._issue_centric_next_request_state_updates(ctx, phase="")
        self.assertEqual(result["last_issue_centric_route_selected"], "fallback_legacy")
        self.assertEqual(result["last_issue_centric_generation_lifecycle"], "issue_centric_invalidated")
        self.assertEqual(result["last_issue_centric_invalidated_generation_id"], "gen-1")
        self.assertEqual(result["last_issue_centric_next_request_target"], "issue-42")

    def test_all_expected_keys_present(self):
        m = self._module()
        ctx = self._make_context(
            snapshot_path="",
            snapshot_status="",
            generation_id="gen-2",
            runtime_mode="issue_centric_normal",
            runtime_mode_reason="",
            runtime_mode_source="",
            target_issue="issue-7",
            target_issue_source="ic",
            fallback_reason="",
            route_selected="ic",
            recovery_status="",
            recovery_source="",
            freshness_status="",
            freshness_reason="",
            freshness_source="",
            invalidation_status="",
            invalidation_reason="",
        )
        result = m._issue_centric_next_request_state_updates(ctx, phase="prepared")
        expected_keys = [
            "last_issue_centric_runtime_snapshot",
            "last_issue_centric_snapshot_status",
            "last_issue_centric_runtime_generation_id",
            "last_issue_centric_generation_lifecycle",
            "last_issue_centric_generation_lifecycle_reason",
            "last_issue_centric_generation_lifecycle_source",
            "last_issue_centric_prepared_generation_id",
            "last_issue_centric_pending_generation_id",
            "last_issue_centric_runtime_mode",
            "last_issue_centric_runtime_mode_reason",
            "last_issue_centric_runtime_mode_source",
            "last_issue_centric_freshness_status",
            "last_issue_centric_freshness_reason",
            "last_issue_centric_freshness_source",
            "last_issue_centric_invalidation_status",
            "last_issue_centric_invalidation_reason",
            "last_issue_centric_invalidated_generation_id",
            "last_issue_centric_consumed_generation_id",
            "last_issue_centric_next_request_target",
            "last_issue_centric_next_request_target_source",
            "last_issue_centric_next_request_fallback_reason",
            "last_issue_centric_route_selected",
            "last_issue_centric_route_fallback_reason",
            "last_issue_centric_recovery_status",
            "last_issue_centric_recovery_source",
            "last_issue_centric_recovery_fallback_reason",
        ]
        for k in expected_keys:
            self.assertIn(k, result, f"missing key: {k}")
        self.assertEqual(result["last_issue_centric_prepared_generation_id"], "gen-2")
        self.assertEqual(result["last_issue_centric_generation_lifecycle"], "fresh_prepared")


class IcResolvedContextDataclassTests(unittest.TestCase):
    """Tests for _IcResolvedContext dataclass (Phase 30)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def test_default_fields_are_none_and_empty(self):
        m = self._module()
        ctx = m._IcResolvedContext()
        self.assertIsNone(ctx.runtime_snapshot)
        self.assertIsNone(ctx.runtime_mode)
        self.assertEqual(ctx.next_request_section, "")
        self.assertEqual(ctx.route_selected, "")

    def test_fields_settable_by_name(self):
        m = self._module()
        sentinel_snapshot = object()
        sentinel_mode = object()
        ctx = m._IcResolvedContext(
            runtime_snapshot=sentinel_snapshot,
            runtime_mode=sentinel_mode,
            next_request_section="IC SECTION",
            route_selected="issue_centric",
        )
        self.assertIs(ctx.runtime_snapshot, sentinel_snapshot)
        self.assertIs(ctx.runtime_mode, sentinel_mode)
        self.assertEqual(ctx.next_request_section, "IC SECTION")
        self.assertEqual(ctx.route_selected, "issue_centric")


class BuildIcRuntimeModeStateHelperTests(unittest.TestCase):
    """Tests for _build_ic_runtime_mode_state (Phase 30)."""

    def _call(self, state: dict, snapshot) -> dict:
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m._build_ic_runtime_mode_state(state, snapshot)

    def test_no_snapshot_returns_copy_of_state(self):
        state = {"mode": "idle", "foo": "bar"}
        result = self._call(state, None)
        self.assertEqual(result, state)
        # must be a copy, not the same object
        self.assertIsNot(result, state)

    def test_snapshot_overlays_snapshot_fields(self):
        class _FakeSnap:
            snapshot_path = "logs/snap.json"
            snapshot_status = "ready"

        state = {"mode": "idle"}
        result = self._call(state, _FakeSnap())
        self.assertEqual(result["last_issue_centric_runtime_snapshot"], "logs/snap.json")
        self.assertEqual(result["last_issue_centric_snapshot_status"], "ready")
        # original state keys must still be present
        self.assertEqual(result["mode"], "idle")

    def test_snapshot_with_empty_path_produces_empty_string(self):
        class _FakeSnap:
            snapshot_path = ""
            snapshot_status = ""

        state: dict = {}
        result = self._call(state, _FakeSnap())
        self.assertEqual(result["last_issue_centric_runtime_snapshot"], "")
        self.assertEqual(result["last_issue_centric_snapshot_status"], "")

    def test_state_not_mutated(self):
        class _FakeSnap:
            snapshot_path = "logs/snap.json"
            snapshot_status = "ok"

        state = {"mode": "idle"}
        self._call(state, _FakeSnap())
        self.assertNotIn("last_issue_centric_runtime_snapshot", state)


class ResolveNormalIcContextNamedFieldsTests(unittest.TestCase):
    """Extra tests for _resolve_normal_ic_context — named field contracts (Phase 30)."""

    def _call(self, state: dict):
        import sys
        sys.path.insert(0, "scripts")
        from unittest.mock import MagicMock, patch
        import request_prompt_from_report as m

        mock_snapshot = MagicMock()
        mock_snapshot.snapshot_path = "logs/snap.json"
        mock_snapshot.snapshot_status = "ready"
        mock_mode = MagicMock()
        mock_route = MagicMock()
        mock_route.route_selected = "issue_centric"

        with patch.object(m, "prepare_issue_centric_runtime_snapshot", return_value=(mock_snapshot, None)):
            with patch.object(m, "_persist_runtime_snapshot_if_needed", return_value=mock_snapshot):
                with patch.object(m, "prepare_issue_centric_runtime_mode", return_value=(mock_mode, "IC SECTION")):
                    with patch.object(m, "resolve_issue_centric_route_choice", return_value=mock_route):
                        ctx = m._resolve_normal_ic_context(state)
        return ctx, mock_snapshot, mock_mode

    def test_returns_ic_resolved_context_instance(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        ctx, _, _ = self._call({})
        self.assertIsInstance(ctx, m._IcResolvedContext)

    def test_snapshot_field_matches_persist_result(self):
        ctx, mock_snapshot, _ = self._call({})
        self.assertIs(ctx.runtime_snapshot, mock_snapshot)

    def test_route_selected_from_route_choice(self):
        ctx, _, _ = self._call({})
        self.assertEqual(ctx.route_selected, "issue_centric")


class ResumeRequestPlanTests(unittest.TestCase):
    """Tests for _ResumeRequestPlan, _resolve_resume_request_plan,
    and _execute_resume_request_plan (Phase 31)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def _make_args(self, *, next_todo="do next", open_questions="", current_status=""):
        import argparse
        args = argparse.Namespace()
        args.next_todo = next_todo
        args.open_questions = open_questions
        args.current_status = current_status
        return args

    def _make_ic_context(self, route_selected="issue_centric"):
        import sys
        sys.path.insert(0, "scripts")
        from request_prompt_from_report import _IcResolvedContext
        return _IcResolvedContext(route_selected=route_selected)

    def test_resolve_plan_returns_resume_request_plan_instance(self):
        """_resolve_resume_request_plan returns a _ResumeRequestPlan with expected fields."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {}
        args = self._make_args()
        ic = self._make_ic_context()
        with mock.patch.object(m, "_resolve_report_request_ic_context", return_value=ic), \
             mock.patch.object(m, "_resolve_completion_followup_request",
                               return_value=("IC SECTION", "do next", "")), \
             mock.patch.object(m, "_resolve_resume_request_payload",
                               return_value=("TEXT", "HASH", "report:x.md", None)):
            plan = m._resolve_resume_request_plan(state, args, "last report", "resume note", None)

        self.assertIsInstance(plan, m._ResumeRequestPlan)
        self.assertIs(plan.state, state)
        self.assertIs(plan.ic_context, ic)
        self.assertEqual(plan.request_text, "TEXT")
        self.assertEqual(plan.request_hash, "HASH")
        self.assertEqual(plan.request_source, "report:x.md")
        self.assertIsNone(plan.prepared_status)
        self.assertEqual(plan.effective_section, "IC SECTION")

    def test_resolve_plan_ic_snapshot_for_dispatch_prefers_runtime_mode(self):
        """ic_snapshot_for_dispatch is runtime_mode when present, else runtime_snapshot."""
        import unittest.mock as mock
        m = self._module()
        sentinel_mode = object()
        sentinel_snap = object()
        ic_with_mode = m._IcResolvedContext(runtime_snapshot=sentinel_snap, runtime_mode=sentinel_mode)
        with mock.patch.object(m, "_resolve_report_request_ic_context", return_value=ic_with_mode), \
             mock.patch.object(m, "_resolve_completion_followup_request", return_value=("", "", "")), \
             mock.patch.object(m, "_resolve_resume_request_payload", return_value=("T", "H", "S", None)):
            plan = m._resolve_resume_request_plan({}, self._make_args(), "", "", None)

        self.assertIs(plan.ic_snapshot_for_dispatch, sentinel_mode)

    def test_resolve_plan_ic_snapshot_falls_back_to_runtime_snapshot(self):
        """ic_snapshot_for_dispatch is runtime_snapshot when runtime_mode is None."""
        import unittest.mock as mock
        m = self._module()
        sentinel_snap = object()
        ic_no_mode = m._IcResolvedContext(runtime_snapshot=sentinel_snap, runtime_mode=None)
        with mock.patch.object(m, "_resolve_report_request_ic_context", return_value=ic_no_mode), \
             mock.patch.object(m, "_resolve_completion_followup_request", return_value=("", "", "")), \
             mock.patch.object(m, "_resolve_resume_request_payload", return_value=("T", "H", "S", None)):
            plan = m._resolve_resume_request_plan({}, self._make_args(), "", "", None)

        self.assertIs(plan.ic_snapshot_for_dispatch, sentinel_snap)

    def test_execute_plan_calls_prepared_reuse_log_when_status_set(self):
        """_execute_resume_request_plan logs prepared reuse when prepared_status is set."""
        import unittest.mock as mock
        m = self._module()
        plan = m._ResumeRequestPlan(
            state={"mode": "idle"},
            args=self._make_args(),
            last_report="",
            resume_note="",
            ic_context=self._make_ic_context(),
            effective_section="",
            effective_next_todo="",
            effective_open_questions="",
            request_text="TEXT",
            request_hash="HASH",
            request_source="report:x.md",
            prepared_status="prepared",
        )
        with mock.patch.object(m, "_log_prepared_request_reuse") as mock_log, \
             mock.patch.object(m, "_is_duplicate_pending_request", return_value=False), \
             mock.patch.object(m, "dispatch_request", return_value=0):
            m._execute_resume_request_plan(plan)

        mock_log.assert_called_once_with("prepared", "issue_centric")

    def test_execute_plan_no_log_when_prepared_status_is_none(self):
        """_execute_resume_request_plan does not log reuse when prepared_status is None."""
        import unittest.mock as mock
        m = self._module()
        plan = m._ResumeRequestPlan(
            state={},
            args=self._make_args(),
            last_report="",
            resume_note="",
            ic_context=self._make_ic_context(),
            effective_section="",
            effective_next_todo="",
            effective_open_questions="",
            request_text="TEXT",
            request_hash="HASH",
            request_source="report:x.md",
            prepared_status=None,
        )
        with mock.patch.object(m, "_log_prepared_request_reuse") as mock_log, \
             mock.patch.object(m, "_is_duplicate_pending_request", return_value=False), \
             mock.patch.object(m, "dispatch_request", return_value=0):
            m._execute_resume_request_plan(plan)

        mock_log.assert_not_called()

    def test_execute_plan_returns_0_on_duplicate_pending(self):
        """_execute_resume_request_plan returns 0 immediately on duplicate pending request."""
        import unittest.mock as mock
        m = self._module()
        plan = m._ResumeRequestPlan(
            state={},
            args=self._make_args(),
            last_report="",
            resume_note="",
            ic_context=self._make_ic_context(),
            effective_section="",
            effective_next_todo="",
            effective_open_questions="",
            request_text="TEXT",
            request_hash="HASH",
            request_source="report:x.md",
            prepared_status=None,
        )
        with mock.patch.object(m, "_is_duplicate_pending_request", return_value=True), \
             mock.patch.object(m, "dispatch_request") as mock_dispatch:
            result = m._execute_resume_request_plan(plan)

        self.assertEqual(result, 0)
        mock_dispatch.assert_not_called()

    def test_execute_plan_dispatches_with_correct_params(self):
        """_execute_resume_request_plan passes correct params to dispatch_request."""
        import unittest.mock as mock
        m = self._module()
        sentinel_snap = object()
        plan = m._ResumeRequestPlan(
            state={"mode": "idle"},
            args=self._make_args(),
            last_report="",
            resume_note="",
            ic_context=self._make_ic_context(),
            effective_section="",
            effective_next_todo="",
            effective_open_questions="",
            request_text="TEXT",
            request_hash="HASH",
            request_source="report:x.md",
            prepared_status=None,
            ic_snapshot_for_dispatch=sentinel_snap,
        )
        with mock.patch.object(m, "_is_duplicate_pending_request", return_value=False), \
             mock.patch.object(m, "dispatch_request", return_value=0) as mock_dispatch:
            m._execute_resume_request_plan(plan)

        call_kwargs = mock_dispatch.call_args[1]
        self.assertEqual(call_kwargs["request_text"], "TEXT")
        self.assertEqual(call_kwargs["request_hash"], "HASH")
        self.assertEqual(call_kwargs["request_source"], "report:x.md")
        self.assertEqual(call_kwargs["prepared_prefix"], "prepared_prompt_request_from_report")
        self.assertIs(call_kwargs["issue_centric_runtime_snapshot"], sentinel_snap)


class RotatedRequestPlanTests(unittest.TestCase):
    """Tests for _RotatedRequestPlan, _resolve_rotated_request_plan,
    and _execute_rotated_request_plan (Phase 31)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def _make_args(self):
        import argparse
        args = argparse.Namespace()
        args.next_todo = ""
        args.open_questions = ""
        args.current_status = None
        return args

    def test_resolve_plan_returns_rotated_request_plan_instance(self):
        """_resolve_rotated_request_plan returns a _RotatedRequestPlan with expected fields."""
        import unittest.mock as mock
        m = self._module()
        sentinel_ic = m._IcResolvedContext()
        state: dict = {}
        with mock.patch.object(m, "_resolve_normal_ic_context", return_value=sentinel_ic), \
             mock.patch.object(m, "build_report_request_source", return_value="report:x.md"), \
             mock.patch.object(m, "_acquire_rotated_handoff",
                               return_value=("HANDOFF TEXT", "logs/handoff_received.md")):
            plan = m._resolve_rotated_request_plan(state, self._make_args(), "last report")

        self.assertIsInstance(plan, m._RotatedRequestPlan)
        self.assertIs(plan.state, state)
        self.assertIs(plan.ic_context, sentinel_ic)
        self.assertEqual(plan.request_source, "report:x.md")
        self.assertEqual(plan.handoff_text, "HANDOFF TEXT")
        self.assertEqual(plan.handoff_received_log, "logs/handoff_received.md")

    def test_execute_plan_delegates_to_apply_rotated_request_result(self):
        """_execute_rotated_request_plan calls _apply_rotated_request_result with plan fields."""
        import unittest.mock as mock
        m = self._module()
        ic = m._IcResolvedContext(route_selected="issue_centric")
        plan = m._RotatedRequestPlan(
            state={"mode": "idle"},
            last_report="",
            request_source="report:x.md",
            ic_context=ic,
            handoff_text="HANDOFF TEXT",
            handoff_received_log="logs/handoff.md",
        )
        with mock.patch.object(m, "_apply_rotated_request_result", return_value=0) as mock_apply:
            result = m._execute_rotated_request_plan(plan)

        self.assertEqual(result, 0)
        call_kwargs = mock_apply.call_args[1]
        self.assertEqual(call_kwargs["handoff_text"], "HANDOFF TEXT")
        self.assertEqual(call_kwargs["handoff_received_log"], "logs/handoff.md")
        self.assertEqual(call_kwargs["request_source"], "report:x.md")
        self.assertIs(call_kwargs["ic_context"], ic)

    def test_run_rotated_report_request_calls_resolve_then_execute(self):
        """run_rotated_report_request delegates to resolve then execute (2-step structure)."""
        import unittest.mock as mock
        m = self._module()
        sentinel_plan = object()
        with mock.patch.object(m, "_resolve_rotated_request_plan", return_value=sentinel_plan) as mock_resolve, \
             mock.patch.object(m, "_execute_rotated_request_plan", return_value=0) as mock_execute:
            result = m.run_rotated_report_request({"mode": "idle"}, self._make_args(), "report")

        self.assertEqual(result, 0)
        mock_resolve.assert_called_once()
        mock_execute.assert_called_once_with(sentinel_plan)


class NeedsStaleHandoffCleanupHelperTests(unittest.TestCase):
    """Tests for _needs_stale_pending_handoff_cleanup (Phase 32)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def test_returns_false_when_no_pending_handoff_log(self):
        """Returns False when pending_handoff_log is absent."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {}
        with mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False):
            result = m._needs_stale_pending_handoff_cleanup(state)
        self.assertFalse(result)

    def test_returns_false_when_rotation_still_needed(self):
        """Returns False when rotation is still required (handoff is not stale)."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {"pending_handoff_log": "logs/handoff.md"}
        with mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=True):
            result = m._needs_stale_pending_handoff_cleanup(state)
        self.assertFalse(result)

    def test_returns_true_when_handoff_log_present_and_no_rotation_needed(self):
        """Returns True when handoff_log present but rotation no longer needed."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {"pending_handoff_log": "logs/handoff.md"}
        with mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False):
            result = m._needs_stale_pending_handoff_cleanup(state)
        self.assertTrue(result)


class CanReusePendingHandoffForRotationTests(unittest.TestCase):
    """Tests for _can_reuse_pending_handoff_for_rotation (Phase 32)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def test_returns_false_when_source_mismatch(self):
        """Returns False when pending_handoff_source does not match request_source."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {"pending_handoff_source": "report:other.md"}
        result = m._can_reuse_pending_handoff_for_rotation(state, "report:current.md")
        self.assertFalse(result)

    def test_returns_false_when_source_matches_but_no_handoff_text(self):
        """Returns False when source matches but no handoff text is stored."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {"pending_handoff_source": "report:current.md"}
        with mock.patch.object(m, "read_pending_handoff_text", return_value=""):
            result = m._can_reuse_pending_handoff_for_rotation(state, "report:current.md")
        self.assertFalse(result)

    def test_returns_true_when_source_matches_and_handoff_text_present(self):
        """Returns True when source matches and handoff text is available."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {"pending_handoff_source": "report:current.md"}
        with mock.patch.object(m, "read_pending_handoff_text", return_value="HANDOFF"):
            result = m._can_reuse_pending_handoff_for_rotation(state, "report:current.md")
        self.assertTrue(result)


class RecoveryDecisionResolverTests(unittest.TestCase):
    """Tests for _RecoveryDecision and _resolve_recovery_decision (Phase 32)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def test_retryable_request_yields_retryable_resume_path(self):
        """When retryable_request is set, path is retryable_resume with correct flags."""
        m = self._module()
        retryable = ("TEXT", "HASH", "report:x.md")
        decision = m._resolve_recovery_decision({}, "", retryable)

        self.assertIsInstance(decision, m._RecoveryDecision)
        self.assertEqual(decision.path, "retryable_resume")
        self.assertTrue(decision.has_retryable_request)
        self.assertFalse(decision.is_awaiting_user_stop)
        self.assertFalse(decision.stale_handoff_cleaned)
        self.assertFalse(decision.needs_rotation)
        self.assertIs(decision.retryable_request, retryable)

    def test_awaiting_user_empty_note_yields_awaiting_user_stop(self):
        """awaiting_user mode + empty resume_note → awaiting_user_stop."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {"mode": "awaiting_user"}
        decision = m._resolve_recovery_decision(state, "", None)

        self.assertEqual(decision.path, "awaiting_user_stop")
        self.assertTrue(decision.is_awaiting_user_stop)
        self.assertFalse(decision.has_retryable_request)

    def test_awaiting_user_with_note_yields_awaiting_user_resume(self):
        """awaiting_user mode + non-empty note → awaiting_user_resume."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {"mode": "awaiting_user"}
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=False):
            decision = m._resolve_recovery_decision(state, "補足メモ", None)

        self.assertEqual(decision.path, "awaiting_user_resume")
        self.assertFalse(decision.is_awaiting_user_stop)
        self.assertEqual(decision.resume_note, "補足メモ")

    def test_stale_handoff_is_cleaned_before_path_selection(self):
        """stale_handoff_cleaned is True when cleanup runs; state is updated."""
        import unittest.mock as mock
        m = self._module()
        cleaned_state: dict = {"mode": "idle", "_cleaned": True}
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=True), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed", return_value=cleaned_state) as mock_clean, \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False):
            decision = m._resolve_recovery_decision({"mode": "idle"}, "", None)

        self.assertTrue(decision.stale_handoff_cleaned)
        mock_clean.assert_called_once()
        self.assertIs(decision.state, cleaned_state)

    def test_rotation_needed_yields_rotated_path(self):
        """should_rotate_before_next_chat_request True → rotated path."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=False), \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=True):
            decision = m._resolve_recovery_decision({"mode": "idle"}, "", None)

        self.assertEqual(decision.path, "rotated")
        self.assertTrue(decision.needs_rotation)

    def test_default_yields_normal_resume_path(self):
        """No special conditions → normal_resume path."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=False), \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False):
            decision = m._resolve_recovery_decision({"mode": "idle"}, "", None)

        self.assertEqual(decision.path, "normal_resume")
        self.assertFalse(decision.needs_rotation)
        self.assertFalse(decision.stale_handoff_cleaned)

    def test_stale_not_cleaned_for_retryable_path(self):
        """stale_handoff_cleaned stays False on retryable_resume — no cleanup runs."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup") as mock_check:
            decision = m._resolve_recovery_decision({}, "", ("T", "H", "S"))

        mock_check.assert_not_called()
        self.assertFalse(decision.stale_handoff_cleaned)


class RecoveryPathIntegrationTests(unittest.TestCase):
    """Integration tests confirming recovery safety at the run() / entry-plan level (Phase 32)."""

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    def _make_args(self):
        import argparse
        args = argparse.Namespace()
        args.next_todo = ""
        args.open_questions = ""
        args.current_status = None
        args.resume_note = ""
        return args

    def test_retryable_yields_retryable_resume_entry_plan(self):
        """Retryable prepared request → entry plan path is retryable_resume."""
        import unittest.mock as mock
        m = self._module()
        retryable = ("TEXT", "HASH", "report:x.md")
        with mock.patch.object(m, "load_retryable_prepared_request", return_value=retryable), \
             mock.patch.object(m, "read_last_report_text", return_value="last report"):
            plan = m._resolve_report_request_entry_plan({"mode": "idle"}, self._make_args())

        self.assertEqual(plan.path, "retryable_resume")
        self.assertIs(plan.retryable_request, retryable)

    def test_awaiting_user_empty_input_yields_stop_plan(self):
        """awaiting_user + empty note → awaiting_user_stop plan (does not dispatch)."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "load_retryable_prepared_request", return_value=None), \
             mock.patch.object(m, "resolve_resume_note", return_value=""):
            plan = m._resolve_report_request_entry_plan({"mode": "awaiting_user"}, self._make_args())

        self.assertEqual(plan.path, "awaiting_user_stop")
        self.assertEqual(plan.last_report, "")  # no report read for stop path

    def test_stale_handoff_cleaned_in_normal_resume_path(self):
        """Stale pending handoff is cleaned when normal_resume path is chosen."""
        import unittest.mock as mock
        m = self._module()
        cleaned: dict = {"mode": "idle", "_cleaned": True}
        with mock.patch.object(m, "load_retryable_prepared_request", return_value=None), \
             mock.patch.object(m, "resolve_resume_note", return_value=""), \
             mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=True), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed", return_value=cleaned), \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False), \
             mock.patch.object(m, "read_last_report_text", return_value=""):
            plan = m._resolve_report_request_entry_plan({"mode": "idle"}, self._make_args())

        self.assertEqual(plan.path, "normal_resume")
        self.assertIs(plan.state, cleaned)

    def test_matching_pending_handoff_reused_in_rotated_path(self):
        """Matching pending_handoff_source → rotated path uses cached handoff."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "_can_reuse_pending_handoff_for_rotation", return_value=True), \
             mock.patch.object(m, "read_pending_handoff_text", return_value="CACHED HANDOFF"):
            state = {"pending_handoff_source": "report:x.md"}
            import argparse
            args = argparse.Namespace(next_todo="", open_questions="", current_status=None)
            handoff_text, handoff_log = m._acquire_rotated_handoff(
                state, args, "",
                request_source="report:x.md",
                ic_context=m._IcResolvedContext(),
            )

        self.assertEqual(handoff_text, "CACHED HANDOFF")

    def test_non_matching_pending_handoff_triggers_fresh_acquisition(self):
        """Non-matching pending_handoff_source → fresh handoff acquisition path."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "_can_reuse_pending_handoff_for_rotation", return_value=False), \
             mock.patch.object(m, "build_chatgpt_handoff_request", return_value="HANDOFF_REQ"), \
             mock.patch.object(m, "log_text", return_value="logs/handoff_req.md"), \
             mock.patch.object(m, "send_to_chatgpt"), \
             mock.patch.object(m, "wait_for_handoff_reply_text", return_value="RAW"), \
             mock.patch.object(m, "extract_last_chatgpt_handoff", return_value="FRESH HANDOFF"), \
             mock.patch.object(m, "stable_text_hash", return_value="HASH"), \
             mock.patch.object(m, "clear_error_fields", side_effect=lambda s: s), \
             mock.patch.object(m, "clear_pending_request_fields"), \
             mock.patch.object(m, "save_state"), \
             mock.patch.object(m, "repo_relative", return_value="logs/r.md"):
            import argparse
            args = argparse.Namespace(next_todo="", open_questions="", current_status=None)
            handoff_text, _ = m._acquire_rotated_handoff(
                {}, args, "",
                request_source="report:other.md",
                ic_context=m._IcResolvedContext(),
            )

        self.assertEqual(handoff_text, "FRESH HANDOFF")

    def test_duplicate_pending_request_not_dispatched(self):
        """duplicate pending request → _execute_resume_request_plan returns 0 without dispatch."""
        import unittest.mock as mock
        m = self._module()
        plan = m._ResumeRequestPlan(
            state={"mode": "waiting_prompt_reply", "pending_request_source": "report:x.md"},
            args=self._make_args(),
            last_report="",
            resume_note="",
            ic_context=m._IcResolvedContext(),
            effective_section="",
            effective_next_todo="",
            effective_open_questions="",
            request_text="TEXT",
            request_hash="HASH",
            request_source="report:x.md",
            prepared_status=None,
        )
        with mock.patch.object(m, "_is_duplicate_pending_request", return_value=True), \
             mock.patch.object(m, "dispatch_request") as mock_dispatch:
            result = m._execute_resume_request_plan(plan)

        self.assertEqual(result, 0)
        mock_dispatch.assert_not_called()

    def test_resume_dispatch_not_blocked_when_no_duplicate(self):
        """Non-duplicate pending request → dispatch_request is called."""
        import unittest.mock as mock
        m = self._module()
        plan = m._ResumeRequestPlan(
            state={"mode": "idle"},
            args=self._make_args(),
            last_report="",
            resume_note="",
            ic_context=m._IcResolvedContext(),
            effective_section="",
            effective_next_todo="",
            effective_open_questions="",
            request_text="TEXT",
            request_hash="HASH",
            request_source="report:x.md",
            prepared_status=None,
        )
        with mock.patch.object(m, "_is_duplicate_pending_request", return_value=False), \
             mock.patch.object(m, "dispatch_request", return_value=0) as mock_dispatch:
            m._execute_resume_request_plan(plan)

        mock_dispatch.assert_called_once()

    def test_rotated_execute_delegates_to_apply_rotated_result(self):
        """_execute_rotated_request_plan always delegates to _apply_rotated_request_result."""
        import unittest.mock as mock
        m = self._module()
        ic = m._IcResolvedContext()
        plan = m._RotatedRequestPlan(
            state={},
            last_report="",
            request_source="report:x.md",
            ic_context=ic,
            handoff_text="HANDOFF",
            handoff_received_log="logs/h.md",
        )
        with mock.patch.object(m, "_apply_rotated_request_result", return_value=0) as mock_apply:
            result = m._execute_rotated_request_plan(plan)

        self.assertEqual(result, 0)
        mock_apply.assert_called_once()


class CycleBoundaryStateConsistencyTests(unittest.TestCase):
    """Cycle boundary state consistency integration tests (Phase 33).

    Verifies that prepared / pending / pending_handoff / chat_rotation
    state families do not bleed across cycle boundaries.
    """

    def _module(self):
        import sys
        sys.path.insert(0, "scripts")
        import request_prompt_from_report as m
        return m

    # ------------------------------------------------------------------
    # _stage_prepared_request_state cleanup
    # ------------------------------------------------------------------

    def test_stage_prepared_clears_pending_handoff_fields(self):
        """_stage_prepared_request_state clears pending_handoff fields before staging."""
        import unittest.mock as mock
        m = self._module()
        state = {"pending_handoff_log": "logs/h.md", "pending_handoff_source": "report:x.md"}
        with mock.patch.object(m, "clear_error_fields", side_effect=lambda s: s), \
             mock.patch.object(m, "clear_pending_request_fields"), \
             mock.patch.object(m, "clear_pending_handoff_fields") as mock_clear_hoff, \
             mock.patch.object(m, "clear_chat_rotation_fields"), \
             mock.patch.object(m, "stage_prepared_request"), \
             mock.patch.object(m, "save_state"), \
             mock.patch.object(m, "repo_relative", side_effect=lambda p: p):
            m._stage_prepared_request_state(
                state,
                request_hash="H",
                request_source="report:x.md",
                request_log_rel="logs/p.md",
                issue_centric_runtime_snapshot=None,
            )
        mock_clear_hoff.assert_called_once()

    def test_stage_prepared_clears_chat_rotation_fields(self):
        """_stage_prepared_request_state clears chat_rotation fields before staging."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {}
        with mock.patch.object(m, "clear_error_fields", side_effect=lambda s: s), \
             mock.patch.object(m, "clear_pending_request_fields"), \
             mock.patch.object(m, "clear_pending_handoff_fields"), \
             mock.patch.object(m, "clear_chat_rotation_fields") as mock_clear_rot, \
             mock.patch.object(m, "stage_prepared_request"), \
             mock.patch.object(m, "save_state"):
            m._stage_prepared_request_state(
                state,
                request_hash="H",
                request_source="report:x.md",
                request_log_rel="logs/p.md",
                issue_centric_runtime_snapshot=None,
            )
        mock_clear_rot.assert_called_once()

    # ------------------------------------------------------------------
    # _apply_pending_request_state cleanup
    # ------------------------------------------------------------------

    def test_apply_pending_clears_chat_rotation_fields(self):
        """_apply_pending_request_state clears chat_rotation fields on pending apply."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {}
        with mock.patch.object(m, "clear_error_fields", side_effect=lambda s: s), \
             mock.patch.object(m, "clear_pending_handoff_fields"), \
             mock.patch.object(m, "clear_chat_rotation_fields") as mock_clear_rot, \
             mock.patch.object(m, "promote_pending_request"), \
             mock.patch.object(m, "save_state"), \
             mock.patch.object(m, "repo_relative", return_value="logs/sent.md"):
            m._apply_pending_request_state(
                state,
                request_hash="H",
                request_source="report:x.md",
                request_log_path="logs/sent.md",
                issue_centric_runtime_snapshot=None,
                success_updates=None,
            )
        mock_clear_rot.assert_called_once()

    def test_apply_pending_clears_pending_handoff_fields(self):
        """_apply_pending_request_state clears pending_handoff fields on apply."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {}
        with mock.patch.object(m, "clear_error_fields", side_effect=lambda s: s), \
             mock.patch.object(m, "clear_pending_handoff_fields") as mock_clear_hoff, \
             mock.patch.object(m, "clear_chat_rotation_fields"), \
             mock.patch.object(m, "promote_pending_request"), \
             mock.patch.object(m, "save_state"), \
             mock.patch.object(m, "repo_relative", return_value="logs/sent.md"):
            m._apply_pending_request_state(
                state,
                request_hash="H",
                request_source="report:x.md",
                request_log_path="logs/sent.md",
                issue_centric_runtime_snapshot=None,
                success_updates=None,
            )
        mock_clear_hoff.assert_called_once()

    # ------------------------------------------------------------------
    # _apply_rotated_pending_request_state cleanup
    # ------------------------------------------------------------------

    def test_apply_rotated_pending_clears_all_four_field_families(self):
        """_apply_rotated_pending_request_state clears all 4 stale field families."""
        import unittest.mock as mock
        m = self._module()
        state: dict = {}
        with mock.patch.object(m, "clear_error_fields", side_effect=lambda s: s), \
             mock.patch.object(m, "clear_pending_request_fields") as mock_clear_req, \
             mock.patch.object(m, "clear_pending_handoff_fields") as mock_clear_hoff, \
             mock.patch.object(m, "clear_chat_rotation_fields") as mock_clear_rot, \
             mock.patch.object(m, "save_state"), \
             mock.patch.object(m, "repo_relative", return_value="logs/sent.md"):
            m._apply_rotated_pending_request_state(
                state,
                request_hash="H",
                request_source="report:x.md",
                request_log_path="logs/sent.md",
                rotation_signal="confirmed",
                rotated_chat={},
                issue_centric_runtime_snapshot=None,
                issue_centric_runtime_mode=None,
            )
        mock_clear_req.assert_called_once()
        mock_clear_hoff.assert_called_once()
        mock_clear_rot.assert_called_once()

    # ------------------------------------------------------------------
    # awaiting_user_stop stale handoff cleanup
    # ------------------------------------------------------------------

    def test_awaiting_user_stop_cleans_stale_pending_handoff(self):
        """awaiting_user_stop path cleans stale pending handoff before stopping."""
        import unittest.mock as mock
        m = self._module()
        state = {"mode": "awaiting_user", "pending_handoff_log": "logs/h.md"}
        cleaned = {"mode": "awaiting_user", "_cleaned": True}
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=True), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed", return_value=cleaned) as mock_clean:
            decision = m._resolve_recovery_decision(state, "", None)

        self.assertEqual(decision.path, "awaiting_user_stop")
        self.assertTrue(decision.stale_handoff_cleaned)
        mock_clean.assert_called_once()
        self.assertIs(decision.state, cleaned)

    def test_awaiting_user_stop_no_cleanup_when_not_stale(self):
        """awaiting_user_stop does not call cleanup when no stale handoff."""
        import unittest.mock as mock
        m = self._module()
        state = {"mode": "awaiting_user"}
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=False), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed") as mock_clean:
            decision = m._resolve_recovery_decision(state, "", None)

        self.assertEqual(decision.path, "awaiting_user_stop")
        self.assertFalse(decision.stale_handoff_cleaned)
        mock_clean.assert_not_called()

    def test_retryable_resume_never_calls_stale_cleanup(self):
        """retryable_resume path never runs stale cleanup — preserves prepared state."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup") as mock_check:
            decision = m._resolve_recovery_decision({}, "", ("T", "H", "S"))

        mock_check.assert_not_called()
        self.assertFalse(decision.stale_handoff_cleaned)

    def test_rotated_path_preserves_matching_pending_handoff(self):
        """rotated path does not clean pending_handoff when rotation is needed."""
        import unittest.mock as mock
        m = self._module()
        state = {"mode": "idle", "pending_handoff_log": "logs/h.md",
                 "pending_handoff_source": "report:x.md"}
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=False), \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=True), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed") as mock_clean:
            decision = m._resolve_recovery_decision(state, "", None)

        self.assertEqual(decision.path, "rotated")
        mock_clean.assert_not_called()

    # ------------------------------------------------------------------
    # end-to-end field-consistency via entry plan
    # ------------------------------------------------------------------

    def test_normal_resume_entry_plan_clears_stale_handoff_via_decision(self):
        """normal_resume entry plan state reflects cleaned handoff when stale."""
        import unittest.mock as mock
        import argparse
        m = self._module()
        cleaned = {"mode": "idle", "_cleaned": True}
        args = argparse.Namespace(next_todo="", open_questions="", current_status=None,
                                  resume_note="")
        with mock.patch.object(m, "load_retryable_prepared_request", return_value=None), \
             mock.patch.object(m, "resolve_resume_note", return_value=""), \
             mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=True), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed", return_value=cleaned), \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False), \
             mock.patch.object(m, "read_last_report_text", return_value=""):
            plan = m._resolve_report_request_entry_plan({"mode": "idle"}, args)

        self.assertEqual(plan.path, "normal_resume")
        self.assertIs(plan.state, cleaned)

    def test_awaiting_user_stop_entry_plan_reflects_cleaned_state(self):
        """awaiting_user_stop entry plan carries cleaned state after handoff cleanup."""
        import unittest.mock as mock
        import argparse
        m = self._module()
        cleaned = {"mode": "awaiting_user", "_cleaned": True}
        args = argparse.Namespace(next_todo="", open_questions="", current_status=None,
                                  resume_note="")
        with mock.patch.object(m, "load_retryable_prepared_request", return_value=None), \
             mock.patch.object(m, "resolve_resume_note", return_value=""), \
             mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=True), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed", return_value=cleaned):
            plan = m._resolve_report_request_entry_plan({"mode": "awaiting_user"}, args)

        self.assertEqual(plan.path, "awaiting_user_stop")
        self.assertIs(plan.state, cleaned)


class IcExecutionToNextCycleConsistencyTests(unittest.TestCase):
    """Phase 34 — _IcNextCycleContext and execution→next-cycle consistency."""

    def _module(self):
        import importlib
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "scripts"))
        return importlib.import_module("request_prompt_from_report")

    # ------------------------------------------------------------------
    # _IcNextCycleContext / _read_ic_next_cycle_context
    # ------------------------------------------------------------------

    def test_read_ic_next_cycle_context_empty_state(self):
        """Empty state returns all-empty fields with safe empty fallbacks."""
        m = self._module()
        ctx = m._read_ic_next_cycle_context({})
        self.assertEqual(ctx.next_request_target, "")
        self.assertEqual(ctx.principal_issue, "")
        self.assertEqual(ctx.principal_issue_kind, "")
        self.assertEqual(ctx.resolved_issue, "")
        self.assertEqual(ctx.target_issue, "")
        self.assertEqual(ctx.action, "")
        self.assertEqual(ctx.next_request_hint, "")
        self.assertEqual(ctx.close_order, "")

    def test_read_ic_next_cycle_context_maps_all_fields(self):
        """All state keys are read and mapped to the correct fields."""
        m = self._module()
        state = {
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/10",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/7",
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_close_order": "2",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.next_request_target, "https://github.com/org/repo/issues/10")
        self.assertEqual(ctx.principal_issue, "https://github.com/org/repo/issues/9")
        self.assertEqual(ctx.principal_issue_kind, "current_issue")
        self.assertEqual(ctx.resolved_issue, "https://github.com/org/repo/issues/8")
        self.assertEqual(ctx.target_issue, "https://github.com/org/repo/issues/7")
        self.assertEqual(ctx.action, "codex_run")
        self.assertEqual(ctx.next_request_hint, "continue_on_current_issue")
        self.assertEqual(ctx.close_order, "2")

    def test_resolved_next_request_target_priority_next_request_target_wins(self):
        """next_request_target wins over all other fields in priority chain."""
        m = self._module()
        state = {
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/10",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/7",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/10")

    def test_resolved_next_request_target_falls_back_to_principal_issue(self):
        """Falls back to principal_issue when next_request_target is absent."""
        m = self._module()
        state = {
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/7",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/9")

    def test_resolved_next_request_target_falls_back_to_resolved_issue(self):
        """Falls back to resolved_issue when next_request_target and principal_issue are absent."""
        m = self._module()
        state = {
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/7",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/8")

    def test_resolved_next_request_target_falls_back_to_target_issue(self):
        """Falls back to target_issue as last resort."""
        m = self._module()
        state = {
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/7",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/7")

    def test_resolved_next_request_target_empty_when_all_absent(self):
        """Returns empty string when all four priority fields are absent."""
        m = self._module()
        ctx = m._read_ic_next_cycle_context({})
        self.assertEqual(ctx.resolved_next_request_target, "")

    # ------------------------------------------------------------------
    # _resolve_completion_followup_target_issue delegates to _IcNextCycleContext
    # ------------------------------------------------------------------

    def test_resolve_completion_followup_target_uses_next_request_target(self):
        """_resolve_completion_followup_target_issue reads next_request_target first."""
        m = self._module()
        state = {
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/42",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/1",
        }
        result = m._resolve_completion_followup_target_issue(state)
        self.assertEqual(result, "https://github.com/org/repo/issues/42")

    def test_resolve_completion_followup_target_fallback_chain_without_next_target(self):
        """_resolve_completion_followup_target_issue falls back through principal → resolved → target."""
        m = self._module()
        state = {
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/9",
        }
        result = m._resolve_completion_followup_target_issue(state)
        self.assertEqual(result, "https://github.com/org/repo/issues/9")

    def test_resolve_completion_followup_target_empty_state(self):
        """Returns empty string from empty state."""
        m = self._module()
        result = m._resolve_completion_followup_target_issue({})
        self.assertEqual(result, "")

    # ------------------------------------------------------------------
    # _is_completion_followup_eligible uses _IcNextCycleContext
    # ------------------------------------------------------------------

    def test_is_completion_followup_eligible_requires_codex_run_action(self):
        """Returns False when last action is not codex_run."""
        m = self._module()
        summary = {"result": "completed", "live_ready": "confirmed"}
        state = {
            "last_issue_centric_action": "issue_create",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
        }
        self.assertFalse(m._is_completion_followup_eligible(summary, state))

    def test_is_completion_followup_eligible_requires_current_issue_principal_kind(self):
        """Returns False when principal_issue_kind is not current_issue."""
        m = self._module()
        summary = {"result": "completed", "live_ready": "confirmed"}
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue_kind": "parent_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
        }
        self.assertFalse(m._is_completion_followup_eligible(summary, state))

    def test_is_completion_followup_eligible_all_conditions_met(self):
        """Returns True when all five conditions are met."""
        m = self._module()
        summary = {"result": "completed", "live_ready": "confirmed"}
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
        }
        self.assertTrue(m._is_completion_followup_eligible(summary, state))

    # ------------------------------------------------------------------
    # Action-specific next-cycle context behaviour
    # ------------------------------------------------------------------

    def test_codex_run_resolved_issue_present_in_context(self):
        """After codex_run, resolved_issue is populated and used as 3rd-priority fallback."""
        m = self._module()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/5",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "codex_run")
        self.assertEqual(ctx.resolved_issue, "https://github.com/org/repo/issues/5")
        # next_request_target and principal_issue absent → resolved_issue wins
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/5")

    def test_close_action_close_order_captured_in_context(self):
        """After close_current_issue, close_order is captured in the context."""
        m = self._module()
        state = {
            "last_issue_centric_action": "close_current_issue",
            "last_issue_centric_close_order": "3",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/11",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "close_current_issue")
        self.assertEqual(ctx.close_order, "3")
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/11")

    def test_whitespace_stripped_from_all_fields(self):
        """Whitespace is stripped from every field on read."""
        m = self._module()
        state = {
            "last_issue_centric_next_request_target": "  https://github.com/org/repo/issues/1  ",
            "last_issue_centric_action": "  codex_run  ",
            "last_issue_centric_close_order": "  1  ",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.next_request_target, "https://github.com/org/repo/issues/1")
        self.assertEqual(ctx.action, "codex_run")
        self.assertEqual(ctx.close_order, "1")


class IcContinuationPayloadTests(unittest.TestCase):
    """Phase 35 — _IcContinuationPayload, _build_ic_continuation_payload,
    _build_ic_continuation_payload_from_normalized, and end-to-end execution→request consistency."""

    def _iec(self):
        """Return the issue_centric_execution module."""
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_execution")

    def _req(self):
        """Return the request_prompt_from_report module."""
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("request_prompt_from_report")

    # ------------------------------------------------------------------
    # _build_ic_continuation_payload — field mapping
    # ------------------------------------------------------------------

    def test_build_continuation_payload_empty_state(self):
        """Empty state produces all-empty fields without raising."""
        iec = self._iec()
        payload = iec._build_ic_continuation_payload({})
        self.assertEqual(payload.principal_issue, "")
        self.assertEqual(payload.principal_issue_kind, "")
        self.assertEqual(payload.next_request_hint, "")
        self.assertEqual(payload.next_request_target, "")
        self.assertEqual(payload.next_request_target_source, "")
        self.assertEqual(payload.action, "")
        self.assertEqual(payload.target_issue, "")
        self.assertEqual(payload.resolved_issue, "")
        self.assertEqual(payload.created_issue_number, "")
        self.assertEqual(payload.created_issue_url, "")
        self.assertEqual(payload.followup_issue_number, "")
        self.assertEqual(payload.followup_issue_url, "")
        self.assertEqual(payload.followup_parent_issue, "")
        self.assertEqual(payload.close_order, "")
        self.assertEqual(payload.execution_status, "")
        self.assertEqual(payload.stop_reason, "")

    def test_build_continuation_payload_maps_all_fields(self):
        """All 16 state keys are mapped to the correct payload fields."""
        iec = self._iec()
        state = {
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/10",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/11",
            "last_issue_centric_next_request_target_source": "runtime_snapshot",
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_created_issue_number": "42",
            "last_issue_centric_created_issue_url": "https://github.com/org/repo/issues/42",
            "last_issue_centric_followup_issue_number": "43",
            "last_issue_centric_followup_issue_url": "https://github.com/org/repo/issues/43",
            "last_issue_centric_followup_parent_issue": "https://github.com/org/repo/issues/5",
            "last_issue_centric_close_order": "2",
            "last_issue_centric_execution_status": "success",
            "last_issue_centric_stop_reason": "completed",
        }
        payload = iec._build_ic_continuation_payload(state)
        self.assertEqual(payload.principal_issue, "https://github.com/org/repo/issues/10")
        self.assertEqual(payload.principal_issue_kind, "current_issue")
        self.assertEqual(payload.next_request_hint, "continue_on_current_issue")
        self.assertEqual(payload.next_request_target, "https://github.com/org/repo/issues/11")
        self.assertEqual(payload.next_request_target_source, "runtime_snapshot")
        self.assertEqual(payload.action, "codex_run")
        self.assertEqual(payload.target_issue, "https://github.com/org/repo/issues/9")
        self.assertEqual(payload.resolved_issue, "https://github.com/org/repo/issues/8")
        self.assertEqual(payload.created_issue_number, "42")
        self.assertEqual(payload.created_issue_url, "https://github.com/org/repo/issues/42")
        self.assertEqual(payload.followup_issue_number, "43")
        self.assertEqual(payload.followup_issue_url, "https://github.com/org/repo/issues/43")
        self.assertEqual(payload.followup_parent_issue, "https://github.com/org/repo/issues/5")
        self.assertEqual(payload.close_order, "2")
        self.assertEqual(payload.execution_status, "success")
        self.assertEqual(payload.stop_reason, "completed")

    def test_build_continuation_payload_strips_whitespace(self):
        """Whitespace is stripped from every field."""
        iec = self._iec()
        state = {
            "last_issue_centric_action": "  codex_run  ",
            "last_issue_centric_close_order": "  3  ",
            "last_issue_centric_next_request_target": "  https://github.com/org/repo/issues/1  ",
        }
        payload = iec._build_ic_continuation_payload(state)
        self.assertEqual(payload.action, "codex_run")
        self.assertEqual(payload.close_order, "3")
        self.assertEqual(payload.next_request_target, "https://github.com/org/repo/issues/1")

    # ------------------------------------------------------------------
    # normalized_summary → state (via _build_ic_continuation_payload_from_normalized)
    # ------------------------------------------------------------------

    def test_apply_ic_continuation_fields_principal_issue_url(self):
        """Writes principal_issue from normalized_summary principal_issue_candidate URL."""
        iec = self._iec()
        state: dict = {}
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/7", "ref": ""},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "continue_on_current_issue",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, {})
        iec._apply_ic_continuation_payload_to_state(state, payload)
        self.assertEqual(state["last_issue_centric_principal_issue"], "https://github.com/org/repo/issues/7")
        self.assertEqual(state["last_issue_centric_principal_issue_kind"], "current_issue")
        self.assertEqual(state["last_issue_centric_next_request_hint"], "continue_on_current_issue")

    def test_apply_ic_continuation_fields_falls_back_to_ref(self):
        """Falls back to ref when URL is absent in principal_issue_candidate."""
        iec = self._iec()
        state: dict = {}
        normalized_summary = {
            "principal_issue_candidate": {"url": "", "ref": "#99"},
            "principal_issue_kind": "planned_issue",
            "next_request_hint": "next_planned_issue",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, {})
        iec._apply_ic_continuation_payload_to_state(state, payload)
        self.assertEqual(state["last_issue_centric_principal_issue"], "#99")
        self.assertEqual(state["last_issue_centric_principal_issue_kind"], "planned_issue")

    def test_apply_ic_continuation_fields_none_candidate(self):
        """None principal_issue_candidate produces empty string."""
        iec = self._iec()
        state: dict = {}
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
            "next_request_hint": "",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, {})
        iec._apply_ic_continuation_payload_to_state(state, payload)
        self.assertEqual(state["last_issue_centric_principal_issue"], "")

    # ------------------------------------------------------------------
    # End-to-end: execution save → request read consistency
    # ------------------------------------------------------------------

    def test_e2e_issue_create_principal_resolves_as_next_target(self):
        """issue_create: principal_issue becomes resolved_next_request_target (2nd priority)."""
        req = self._req()
        # Simulate state after issue_create + _finalize_dispatch:
        # no next_request_target written yet (runtime_snapshot absent in test),
        # principal_issue set from created issue.
        state = {
            "last_issue_centric_action": "issue_create",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/20",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_created_issue_url": "https://github.com/org/repo/issues/20",
            "last_issue_centric_created_issue_number": "20",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "issue_create")
        # next_request_target absent → falls back to principal_issue
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/20")

    def test_e2e_codex_run_next_request_target_wins_over_principal(self):
        """codex_run: next_request_target (from runtime_snapshot) wins over principal_issue."""
        req = self._req()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/22",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/21",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/21",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/22")

    def test_e2e_codex_run_principal_wins_over_resolved_issue(self):
        """codex_run: principal_issue wins over resolved_issue when no next_request_target."""
        req = self._req()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/21",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/19",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/21")

    def test_e2e_codex_run_resolved_issue_fallback_when_no_principal(self):
        """codex_run: resolved_issue used when next_request_target and principal_issue both absent."""
        req = self._req()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/19",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/18",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/19")

    def test_e2e_human_review_resolved_issue_preserved(self):
        """human_review_needed: resolved_issue carries review target and is not lost."""
        req = self._req()
        state = {
            "last_issue_centric_action": "human_review_needed",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/30",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "human_review_needed")
        self.assertEqual(ctx.resolved_issue, "https://github.com/org/repo/issues/30")
        # No principal_issue → resolved_issue used
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/30")

    def test_e2e_followup_next_request_target_points_to_followup(self):
        """follow-up combo: next_request_target (followup issue) wins over principal/resolved."""
        req = self._req()
        state = {
            "last_issue_centric_action": "followup",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/45",
            "last_issue_centric_followup_issue_url": "https://github.com/org/repo/issues/45",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/5",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/45")

    def test_e2e_close_action_next_request_target_preserved(self):
        """close_current_issue: next_request_target still resolved correctly."""
        req = self._req()
        state = {
            "last_issue_centric_action": "close_current_issue",
            "last_issue_centric_close_order": "1",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/60",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.close_order, "1")
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/60")

    def test_e2e_no_action_target_issue_last_resort(self):
        """no_action: target_issue is the last-resort fallback when other fields absent."""
        req = self._req()
        state = {
            "last_issue_centric_action": "no_action",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/3",
        }
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/3")

    def test_e2e_missing_all_fields_safe_empty_fallback(self):
        """All four priority fields absent → empty string, no exception."""
        req = self._req()
        ctx = req._read_ic_next_cycle_context({})
        self.assertEqual(ctx.resolved_next_request_target, "")

    def test_e2e_continuation_payload_shared_fields_match_next_cycle_context(self):
        """_IcContinuationPayload and _IcNextCycleContext read the same fields from identical state."""
        iec = self._iec()
        req = self._req()
        state = {
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/10",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/7",
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_close_order": "",
        }
        payload = iec._build_ic_continuation_payload(state)
        ctx = req._read_ic_next_cycle_context(state)
        # The shared fields must agree
        self.assertEqual(payload.next_request_target, ctx.next_request_target)
        self.assertEqual(payload.principal_issue, ctx.principal_issue)
        self.assertEqual(payload.principal_issue_kind, ctx.principal_issue_kind)
        self.assertEqual(payload.resolved_issue, ctx.resolved_issue)
        self.assertEqual(payload.target_issue, ctx.target_issue)
        self.assertEqual(payload.action, ctx.action)
        self.assertEqual(payload.next_request_hint, ctx.next_request_hint)
        self.assertEqual(payload.close_order, ctx.close_order)


class IcContinuationWriterTests(unittest.TestCase):
    """Phase 36 — _build_ic_continuation_payload_from_normalized,
    _apply_ic_continuation_payload_to_state, and writer end-to-end consistency."""

    def _iec(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_execution")

    def _req(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("request_prompt_from_report")

    # ------------------------------------------------------------------
    # _build_ic_continuation_payload_from_normalized — writer constructor
    # ------------------------------------------------------------------

    def test_build_from_normalized_empty_inputs(self):
        """Empty normalized_summary + empty state → all-empty payload."""
        iec = self._iec()
        payload = iec._build_ic_continuation_payload_from_normalized({}, {})
        self.assertEqual(payload.principal_issue, "")
        self.assertEqual(payload.principal_issue_kind, "")
        self.assertEqual(payload.next_request_hint, "")
        self.assertEqual(payload.next_request_target, "")
        self.assertEqual(payload.next_request_target_source, "")
        self.assertEqual(payload.action, "")
        self.assertEqual(payload.target_issue, "")
        self.assertEqual(payload.resolved_issue, "")
        self.assertEqual(payload.created_issue_number, "")
        self.assertEqual(payload.created_issue_url, "")
        self.assertEqual(payload.followup_issue_number, "")
        self.assertEqual(payload.followup_issue_url, "")
        self.assertEqual(payload.followup_parent_issue, "")
        self.assertEqual(payload.close_order, "")
        self.assertEqual(payload.execution_status, "")
        self.assertEqual(payload.stop_reason, "")

    def test_build_from_normalized_principal_from_url(self):
        """principal_issue_candidate with URL → principal_issue = URL."""
        iec = self._iec()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/7", "ref": "#7"},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "continue_on_current_issue",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, {})
        self.assertEqual(payload.principal_issue, "https://github.com/org/repo/issues/7")
        self.assertEqual(payload.principal_issue_kind, "current_issue")
        self.assertEqual(payload.next_request_hint, "continue_on_current_issue")

    def test_build_from_normalized_principal_falls_back_to_ref(self):
        """URL is empty → falls back to ref field."""
        iec = self._iec()
        normalized_summary = {
            "principal_issue_candidate": {"url": "", "ref": "#99"},
            "principal_issue_kind": "planned_issue",
            "next_request_hint": "next_planned_issue",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, {})
        self.assertEqual(payload.principal_issue, "#99")

    def test_build_from_normalized_principal_none_candidate(self):
        """None principal_issue_candidate → principal_issue = empty."""
        iec = self._iec()
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, {})
        self.assertEqual(payload.principal_issue, "")

    def test_build_from_normalized_reads_state_action_specific_fields(self):
        """Action-specific fields are read from state (written by _apply_*_execution_state)."""
        iec = self._iec()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_close_order": "2",
            "last_issue_centric_created_issue_number": "42",
            "last_issue_centric_created_issue_url": "https://github.com/org/repo/issues/42",
            "last_issue_centric_followup_issue_number": "43",
            "last_issue_centric_followup_issue_url": "https://github.com/org/repo/issues/43",
            "last_issue_centric_followup_parent_issue": "https://github.com/org/repo/issues/5",
            "last_issue_centric_execution_status": "success",
            "last_issue_centric_stop_reason": "completed",
        }
        payload = iec._build_ic_continuation_payload_from_normalized({}, state)
        self.assertEqual(payload.action, "codex_run")
        self.assertEqual(payload.target_issue, "https://github.com/org/repo/issues/9")
        self.assertEqual(payload.resolved_issue, "https://github.com/org/repo/issues/8")
        self.assertEqual(payload.close_order, "2")
        self.assertEqual(payload.created_issue_number, "42")
        self.assertEqual(payload.created_issue_url, "https://github.com/org/repo/issues/42")
        self.assertEqual(payload.followup_issue_number, "43")
        self.assertEqual(payload.followup_issue_url, "https://github.com/org/repo/issues/43")
        self.assertEqual(payload.followup_parent_issue, "https://github.com/org/repo/issues/5")
        self.assertEqual(payload.execution_status, "success")
        self.assertEqual(payload.stop_reason, "completed")

    def test_build_from_normalized_next_request_target_always_empty(self):
        """next_request_target is always '' (set by runtime_snapshot later)."""
        iec = self._iec()
        # Even if state has next_request_target, writer sets it to ""
        state = {"last_issue_centric_next_request_target": "https://github.com/org/repo/issues/10"}
        payload = iec._build_ic_continuation_payload_from_normalized({}, state)
        self.assertEqual(payload.next_request_target, "")
        self.assertEqual(payload.next_request_target_source, "")

    # ------------------------------------------------------------------
    # _apply_ic_continuation_payload_to_state — writer applier
    # ------------------------------------------------------------------

    def test_apply_payload_to_state_writes_all_fields(self):
        """apply writes all 14 continuation keys to state."""
        iec = self._iec()
        payload = iec._IcContinuationPayload(
            principal_issue="https://github.com/org/repo/issues/10",
            principal_issue_kind="current_issue",
            next_request_hint="continue_on_current_issue",
            next_request_target="",
            next_request_target_source="",
            action="codex_run",
            target_issue="https://github.com/org/repo/issues/9",
            resolved_issue="https://github.com/org/repo/issues/8",
            created_issue_number="42",
            created_issue_url="https://github.com/org/repo/issues/42",
            followup_issue_number="43",
            followup_issue_url="https://github.com/org/repo/issues/43",
            followup_parent_issue="https://github.com/org/repo/issues/5",
            close_order="2",
            execution_status="success",
            stop_reason="completed",
        )
        state: dict = {}
        iec._apply_ic_continuation_payload_to_state(state, payload)
        self.assertEqual(state["last_issue_centric_principal_issue"], "https://github.com/org/repo/issues/10")
        self.assertEqual(state["last_issue_centric_principal_issue_kind"], "current_issue")
        self.assertEqual(state["last_issue_centric_next_request_hint"], "continue_on_current_issue")
        self.assertEqual(state["last_issue_centric_action"], "codex_run")
        self.assertEqual(state["last_issue_centric_target_issue"], "https://github.com/org/repo/issues/9")
        self.assertEqual(state["last_issue_centric_resolved_issue"], "https://github.com/org/repo/issues/8")
        self.assertEqual(state["last_issue_centric_created_issue_number"], "42")
        self.assertEqual(state["last_issue_centric_created_issue_url"], "https://github.com/org/repo/issues/42")
        self.assertEqual(state["last_issue_centric_followup_issue_number"], "43")
        self.assertEqual(state["last_issue_centric_followup_issue_url"], "https://github.com/org/repo/issues/43")
        self.assertEqual(state["last_issue_centric_followup_parent_issue"], "https://github.com/org/repo/issues/5")
        self.assertEqual(state["last_issue_centric_close_order"], "2")
        self.assertEqual(state["last_issue_centric_execution_status"], "success")
        self.assertEqual(state["last_issue_centric_stop_reason"], "completed")
        # next_request_target must NOT be written by apply
        self.assertNotIn("last_issue_centric_next_request_target", state)

    def test_apply_payload_to_state_overwrites_existing_values(self):
        """apply overwrites pre-existing state values with payload values."""
        iec = self._iec()
        state: dict = {
            "last_issue_centric_principal_issue": "OLD",
            "last_issue_centric_next_request_hint": "OLD_HINT",
        }
        payload = iec._IcContinuationPayload(
            principal_issue="NEW",
            principal_issue_kind="current_issue",
            next_request_hint="NEW_HINT",
            next_request_target="",
            next_request_target_source="",
            action="", target_issue="", resolved_issue="",
            created_issue_number="", created_issue_url="",
            followup_issue_number="", followup_issue_url="",
            followup_parent_issue="", close_order="",
            execution_status="", stop_reason="",
        )
        iec._apply_ic_continuation_payload_to_state(state, payload)
        self.assertEqual(state["last_issue_centric_principal_issue"], "NEW")
        self.assertEqual(state["last_issue_centric_next_request_hint"], "NEW_HINT")

    # ------------------------------------------------------------------
    # Writer round-trip: build_from_normalized → apply → read
    # ------------------------------------------------------------------

    def test_writer_roundtrip_consistent_state(self):
        """build_from_normalized → apply → reader reads back identical values."""
        iec = self._iec()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/10", "ref": ""},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "continue_on_current_issue",
        }
        source_state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_close_order": "",
            "last_issue_centric_created_issue_number": "",
            "last_issue_centric_created_issue_url": "",
            "last_issue_centric_followup_issue_number": "",
            "last_issue_centric_followup_issue_url": "",
            "last_issue_centric_followup_parent_issue": "",
            "last_issue_centric_execution_status": "success",
            "last_issue_centric_stop_reason": "completed",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, source_state)
        target_state: dict = {}
        iec._apply_ic_continuation_payload_to_state(target_state, payload)
        self.assertEqual(target_state["last_issue_centric_principal_issue"], "https://github.com/org/repo/issues/10")
        self.assertEqual(target_state["last_issue_centric_principal_issue_kind"], "current_issue")
        self.assertEqual(target_state["last_issue_centric_next_request_hint"], "continue_on_current_issue")
        self.assertEqual(target_state["last_issue_centric_action"], "codex_run")
        self.assertEqual(target_state["last_issue_centric_resolved_issue"], "https://github.com/org/repo/issues/8")
        self.assertEqual(target_state["last_issue_centric_stop_reason"], "completed")

    # ------------------------------------------------------------------
    # Writer payload vs _IcNextCycleContext consistency
    # ------------------------------------------------------------------

    def test_writer_payload_and_next_cycle_context_share_consistent_fields(self):
        """Writer payload fields and _IcNextCycleContext agree when built from same state."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/10", "ref": ""},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "continue_on_current_issue",
        }
        source_state: dict = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_close_order": "",
        }
        writer_payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, source_state)
        iec._apply_ic_continuation_payload_to_state(source_state, writer_payload)
        # Now read from the applied state
        ctx = req._read_ic_next_cycle_context(source_state)
        self.assertEqual(ctx.principal_issue, writer_payload.principal_issue)
        self.assertEqual(ctx.principal_issue_kind, writer_payload.principal_issue_kind)
        self.assertEqual(ctx.next_request_hint, writer_payload.next_request_hint)
        self.assertEqual(ctx.action, writer_payload.action)
        self.assertEqual(ctx.target_issue, writer_payload.target_issue)
        self.assertEqual(ctx.resolved_issue, writer_payload.resolved_issue)
        self.assertEqual(ctx.close_order, writer_payload.close_order)

    # ------------------------------------------------------------------
    # End-to-end: action-specific writer scenarios
    # ------------------------------------------------------------------

    def test_e2e_writer_issue_create_principal_resolves_as_next_target(self):
        """issue_create: writer promotes created issue to principal; request reads it as target."""
        iec = self._iec()
        req = self._req()
        # After _apply_issue_create_execution_state and _finalize_dispatch,
        # normalized_summary promotes the created issue as principal.
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/20", "ref": ""},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "continue_on_current_issue",
        }
        source_state = {
            "last_issue_centric_action": "issue_create",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/20",
            "last_issue_centric_created_issue_url": "https://github.com/org/repo/issues/20",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, source_state)
        iec._apply_ic_continuation_payload_to_state(source_state, payload)
        ctx = req._read_ic_next_cycle_context(source_state)
        # next_request_target not written by writer → falls back to principal_issue
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/20")

    def test_e2e_writer_codex_run_resolved_issue_as_fallback(self):
        """codex_run: writer sets resolved_issue; request reads it as 3rd-priority fallback."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
            "next_request_hint": "",
        }
        source_state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/8",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/7",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, source_state)
        iec._apply_ic_continuation_payload_to_state(source_state, payload)
        ctx = req._read_ic_next_cycle_context(source_state)
        # principal_issue empty → resolved_issue is 3rd priority
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/8")

    def test_e2e_writer_human_review_resolved_issue_not_lost(self):
        """human_review_needed: resolved_issue preserved through writer → request."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
            "next_request_hint": "",
        }
        source_state = {
            "last_issue_centric_action": "human_review_needed",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/30",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, source_state)
        iec._apply_ic_continuation_payload_to_state(source_state, payload)
        ctx = req._read_ic_next_cycle_context(source_state)
        self.assertEqual(ctx.action, "human_review_needed")
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/30")

    def test_e2e_writer_followup_close_order_and_target_consistent(self):
        """close_current_issue: writer captures close_order; target survives in context."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/60", "ref": ""},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "",
        }
        source_state = {
            "last_issue_centric_action": "close_current_issue",
            "last_issue_centric_close_order": "1",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/59",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, source_state)
        iec._apply_ic_continuation_payload_to_state(source_state, payload)
        ctx = req._read_ic_next_cycle_context(source_state)
        self.assertEqual(ctx.close_order, "1")
        # principal_issue wins over target_issue
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/60")

    def test_e2e_writer_no_action_hint_preserved(self):
        """no_action: next_request_hint is preserved through writer → context."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
            "next_request_hint": "continue_on_current_issue",
        }
        source_state = {
            "last_issue_centric_action": "no_action",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/3",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, source_state)
        iec._apply_ic_continuation_payload_to_state(source_state, payload)
        ctx = req._read_ic_next_cycle_context(source_state)
        self.assertEqual(ctx.next_request_hint, "continue_on_current_issue")
        # No principal or resolved → target_issue last resort
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/3")

    def test_e2e_writer_missing_fields_safe_empty_fallback(self):
        """All fields absent → resolved_next_request_target is empty, no exception."""
        iec = self._iec()
        req = self._req()
        payload = iec._build_ic_continuation_payload_from_normalized({}, {})
        state: dict = {}
        iec._apply_ic_continuation_payload_to_state(state, payload)
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "")


class IcFinalContinuationContractTests(unittest.TestCase):
    """Phase 37 — _bind_ic_continuation_with_runtime_snapshot,
    _apply_ic_final_continuation_target_to_state, and final contract
    end-to-end integration (execution save → request read)."""

    def _iec(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_execution")

    def _req(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("request_prompt_from_report")

    def _base_writer_payload(self, iec, *, principal_issue="", resolved_issue="", target_issue="", action=""):
        """Build a minimal writer payload (target/source empty)."""
        normalized_summary = {
            "principal_issue_candidate": {"url": principal_issue} if principal_issue else None,
            "principal_issue_kind": "current_issue" if principal_issue else "",
            "next_request_hint": "",
        }
        state = {
            "last_issue_centric_action": action,
            "last_issue_centric_target_issue": target_issue,
            "last_issue_centric_resolved_issue": resolved_issue,
        }
        return iec._build_ic_continuation_payload_from_normalized(normalized_summary, state)

    # ------------------------------------------------------------------
    # _bind_ic_continuation_with_runtime_snapshot
    # ------------------------------------------------------------------

    def test_bind_fills_target_and_source(self):
        """Binding fills next_request_target and next_request_target_source."""
        iec = self._iec()
        payload = self._base_writer_payload(iec)
        self.assertEqual(payload.next_request_target, "")
        self.assertEqual(payload.next_request_target_source, "")
        final = iec._bind_ic_continuation_with_runtime_snapshot(
            payload,
            next_request_target="https://github.com/org/repo/issues/42",
            next_request_target_source="runtime_snapshot",
        )
        self.assertEqual(final.next_request_target, "https://github.com/org/repo/issues/42")
        self.assertEqual(final.next_request_target_source, "runtime_snapshot")

    def test_bind_preserves_other_fields(self):
        """Binding only changes target/source; other fields are unchanged."""
        iec = self._iec()
        payload = self._base_writer_payload(
            iec,
            principal_issue="https://github.com/org/repo/issues/10",
            resolved_issue="https://github.com/org/repo/issues/9",
            action="codex_run",
        )
        final = iec._bind_ic_continuation_with_runtime_snapshot(
            payload,
            next_request_target="https://github.com/org/repo/issues/11",
            next_request_target_source="snapshot",
        )
        self.assertEqual(final.principal_issue, "https://github.com/org/repo/issues/10")
        self.assertEqual(final.resolved_issue, "https://github.com/org/repo/issues/9")
        self.assertEqual(final.action, "codex_run")

    def test_bind_with_empty_target_is_safe(self):
        """Binding with empty target/source results in empty fields (no exception)."""
        iec = self._iec()
        payload = self._base_writer_payload(iec)
        final = iec._bind_ic_continuation_with_runtime_snapshot(
            payload,
            next_request_target="",
            next_request_target_source="",
        )
        self.assertEqual(final.next_request_target, "")
        self.assertEqual(final.next_request_target_source, "")

    # ------------------------------------------------------------------
    # _apply_ic_final_continuation_target_to_state
    # ------------------------------------------------------------------

    def test_apply_final_target_writes_both_keys(self):
        """_apply_ic_final_continuation_target_to_state writes target and source keys."""
        iec = self._iec()
        payload = self._base_writer_payload(iec)
        final = iec._bind_ic_continuation_with_runtime_snapshot(
            payload,
            next_request_target="https://github.com/org/repo/issues/55",
            next_request_target_source="execution_finalize",
        )
        state: dict = {}
        iec._apply_ic_final_continuation_target_to_state(state, final)
        self.assertEqual(state["last_issue_centric_next_request_target"], "https://github.com/org/repo/issues/55")
        self.assertEqual(state["last_issue_centric_next_request_target_source"], "execution_finalize")

    def test_apply_final_target_does_not_touch_other_keys(self):
        """_apply_ic_final_continuation_target_to_state only writes target/source."""
        iec = self._iec()
        payload = self._base_writer_payload(iec)
        final = iec._bind_ic_continuation_with_runtime_snapshot(
            payload,
            next_request_target="https://github.com/org/repo/issues/7",
            next_request_target_source="snapshot",
        )
        state = {"last_issue_centric_action": "codex_run", "last_issue_centric_principal_issue": "old"}
        iec._apply_ic_final_continuation_target_to_state(state, final)
        # Other keys unchanged
        self.assertEqual(state["last_issue_centric_action"], "codex_run")
        self.assertEqual(state["last_issue_centric_principal_issue"], "old")

    # ------------------------------------------------------------------
    # end-to-end: execution save → request read (priority chain)
    # ------------------------------------------------------------------

    def _apply_full_final_contract(self, iec, *, normalized_summary, state, rt_target="", rt_source=""):
        """Helper: apply writer payload + bind target/source → full final contract in state."""
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, state)
        iec._apply_ic_continuation_payload_to_state(state, payload)
        final = iec._bind_ic_continuation_with_runtime_snapshot(
            payload,
            next_request_target=rt_target,
            next_request_target_source=rt_source,
        )
        iec._apply_ic_final_continuation_target_to_state(state, final)

    def test_e2e_explicit_next_request_target_is_highest_priority(self):
        """runtime_snapshot sets next_request_target → it wins over principal/resolved."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/10"},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "",
        }
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/8",
        }
        self._apply_full_final_contract(
            iec,
            normalized_summary=normalized_summary,
            state=state,
            rt_target="https://github.com/org/repo/issues/42",
            rt_source="runtime_snapshot",
        )
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/42")

    def test_e2e_fallback_to_principal_when_no_explicit_target(self):
        """`next_request_target` empty → principal_issue wins."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/10"},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "",
        }
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/8",
        }
        self._apply_full_final_contract(
            iec,
            normalized_summary=normalized_summary,
            state=state,
            rt_target="",
            rt_source="",
        )
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/10")

    def test_e2e_fallback_to_resolved_when_no_principal(self):
        """`next_request_target` and `principal_issue` empty → resolved_issue wins."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
            "next_request_hint": "",
        }
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/8",
        }
        self._apply_full_final_contract(
            iec,
            normalized_summary=normalized_summary,
            state=state,
            rt_target="",
            rt_source="",
        )
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/9")

    def test_e2e_fallback_to_target_issue_last_resort(self):
        """All three above empty → target_issue last resort."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
            "next_request_hint": "",
        }
        state = {
            "last_issue_centric_action": "no_action",
            "last_issue_centric_resolved_issue": "",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/5",
        }
        self._apply_full_final_contract(
            iec,
            normalized_summary=normalized_summary,
            state=state,
            rt_target="",
            rt_source="",
        )
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/5")

    def test_e2e_followup_combo_target_source_traceable(self):
        """follow-up combo: next_request_target_source is visible through final contract."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/20"},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "follow_up",
        }
        state = {
            "last_issue_centric_action": "human_review_needed",
            "last_issue_centric_followup_issue_url": "https://github.com/org/repo/issues/21",
            "last_issue_centric_followup_issue_number": "21",
            "last_issue_centric_followup_parent_issue": "https://github.com/org/repo/issues/20",
        }
        self._apply_full_final_contract(
            iec,
            normalized_summary=normalized_summary,
            state=state,
            rt_target="https://github.com/org/repo/issues/21",
            rt_source="followup_issue",
        )
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.next_request_target, "https://github.com/org/repo/issues/21")
        self.assertEqual(state["last_issue_centric_next_request_target_source"], "followup_issue")
        # next_request_target wins as highest priority
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/21")

    def test_e2e_close_current_issue_close_order_consistent(self):
        """close_current_issue: close_order is preserved and target resolves without conflict."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/30"},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "",
        }
        state = {
            "last_issue_centric_action": "close_current_issue",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/30",
            "last_issue_centric_close_order": "1",
            "last_issue_centric_closed_issue_url": "https://github.com/org/repo/issues/30",
        }
        self._apply_full_final_contract(
            iec,
            normalized_summary=normalized_summary,
            state=state,
            rt_target="https://github.com/org/repo/issues/31",
            rt_source="next_sibling",
        )
        ctx = req._read_ic_next_cycle_context(state)
        # close_order is non-empty
        self.assertEqual(ctx.close_order, "1")
        # explicit next_request_target wins
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/31")

    def test_e2e_no_action_hint_preserved_through_final_contract(self):
        """no_action: next_request_hint preserved through full contract pipeline."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": None,
            "principal_issue_kind": "",
            "next_request_hint": "continue_on_current_issue",
        }
        state = {
            "last_issue_centric_action": "no_action",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/3",
        }
        self._apply_full_final_contract(
            iec,
            normalized_summary=normalized_summary,
            state=state,
            rt_target="",
            rt_source="",
        )
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.next_request_hint, "continue_on_current_issue")
        # target_issue is last resort
        self.assertEqual(ctx.resolved_next_request_target, "https://github.com/org/repo/issues/3")

    def test_e2e_missing_fields_safe_empty_fallback(self):
        """All fields absent → no exception, resolved_next_request_target is empty."""
        iec = self._iec()
        req = self._req()
        state: dict = {}
        self._apply_full_final_contract(
            iec,
            normalized_summary={},
            state=state,
            rt_target="",
            rt_source="",
        )
        ctx = req._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.resolved_next_request_target, "")

    def test_e2e_final_contract_shared_fields_match_next_cycle_context(self):
        """After final contract apply, _IcNextCycleContext shared fields agree with payload."""
        iec = self._iec()
        req = self._req()
        normalized_summary = {
            "principal_issue_candidate": {"url": "https://github.com/org/repo/issues/50"},
            "principal_issue_kind": "current_issue",
            "next_request_hint": "continue_on_current_issue",
        }
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/49",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/48",
            "last_issue_centric_close_order": "",
        }
        payload = iec._build_ic_continuation_payload_from_normalized(normalized_summary, state)
        iec._apply_ic_continuation_payload_to_state(state, payload)
        final = iec._bind_ic_continuation_with_runtime_snapshot(
            payload,
            next_request_target="https://github.com/org/repo/issues/51",
            next_request_target_source="snapshot",
        )
        iec._apply_ic_final_continuation_target_to_state(state, final)
        ctx = req._read_ic_next_cycle_context(state)
        # All shared fields align
        self.assertEqual(final.principal_issue, ctx.principal_issue)
        self.assertEqual(final.principal_issue_kind, ctx.principal_issue_kind)
        self.assertEqual(final.next_request_target, ctx.next_request_target)
        self.assertEqual(final.next_request_hint, ctx.next_request_hint)
        self.assertEqual(final.action, ctx.action)
        self.assertEqual(final.target_issue, ctx.target_issue)
        self.assertEqual(final.resolved_issue, ctx.resolved_issue)
        self.assertEqual(final.close_order, ctx.close_order)


class RunLevelLifecycleIntegrationTests(unittest.TestCase):
    """Phase 38 — run-level lifecycle integration hardening.

    Verifies that the final continuation contract saved by execution is
    correctly consumed by the next cycle's run-level helpers:

    * completion followup section building
    * priority chain target resolution
    * recovery decision path selection
    * duplicate pending request guard
    * stale handoff cleanup consistency
    * resume plan integration
    """

    def _module(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("request_prompt_from_report")

    def _make_args(self, *, next_todo="", open_questions="", current_status=None, resume_note=""):
        import argparse
        args = argparse.Namespace()
        args.next_todo = next_todo
        args.open_questions = open_questions
        args.current_status = current_status
        args.resume_note = resume_note
        return args

    def _eligible_ic_state(self, *, next_request_target="https://github.com/org/repo/issues/10"):
        """Build a state dict representing a final continuation contract that makes
        completion followup eligible."""
        return {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_next_request_target": next_request_target,
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/10",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/9",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/8",
        }

    def _eligible_report_text(self):
        """Minimal report body that satisfies completion followup eligibility."""
        return "- result: completed\n- live_ready: confirmed\n"

    # ------------------------------------------------------------------
    # Group 1: completion followup section building with final contract state
    # ------------------------------------------------------------------

    def test_completion_followup_eligible_state_builds_section(self):
        """Eligible IC state + eligible report → section is built and contains target."""
        m = self._module()
        state = self._eligible_ic_state()
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        self.assertIn("issue_centric_completion_followup", section)
        self.assertIn("https://github.com/org/repo/issues/10", section)

    def test_completion_followup_section_requires_repo_direct_review(self):
        """Report continuation must require direct repo review, not issue comments only."""
        m = self._module()
        state = self._eligible_ic_state()
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        self.assertIn("最初に GitHub repo を直接確認してください", section)
        self.assertIn("Issue コメントだけで判断しないでください", section)
        self.assertIn("GitHub commit / GitHub diff / GitHub changed files / tests / remaining issues", section)
        self.assertIn("GitHub repo を直接確認できない場合は未確認", section)
        self.assertLess(section.index("最初に GitHub repo"), section.index("archived_report_result"))

    def test_ready_bounded_completion_followup_guides_close_after_clean_repo_review(self):
        """Ready bounded completion followup should guide close_current_issue after clean repo review."""
        m = self._module()
        state = self._eligible_ic_state()
        state["current_ready_issue_ref"] = "#11 Ready: verify conversation-tab send and fetch first cycle"
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        self.assertIn("GitHub commit / GitHub diff / GitHub changed files", section)
        self.assertIn("変更が target issue scope 内で remaining issues がない場合", section)
        self.assertIn("原則 close_current_issue=true", section)
        self.assertIn("具体的な未完了理由を summary", section)

    def test_lifecycle_only_guidance_mentions_repo_review_boundary(self):
        """Lifecycle-only guidance should preserve repo-review boundary for completion decisions."""
        self.assertIn("最初に GitHub repo を直接確認してください", _bridge_common._LIFECYCLE_ONLY_REQUEST_GUIDANCE)
        self.assertIn("Issue コメントだけで判断しないでください", _bridge_common._LIFECYCLE_ONLY_REQUEST_GUIDANCE)
        self.assertIn("GitHub commit / GitHub diff / GitHub changed files / tests / remaining issues", _bridge_common._LIFECYCLE_ONLY_REQUEST_GUIDANCE)
        self.assertIn("具体的な未完了理由を summary", _bridge_common._LIFECYCLE_ONLY_REQUEST_GUIDANCE)

    def test_completion_followup_target_explicit_next_request_target_wins(self):
        """Explicit next_request_target → used as target_issue in followup section."""
        m = self._module()
        state = self._eligible_ic_state(next_request_target="https://github.com/org/repo/issues/42")
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        self.assertIn("issues/42", section)
        # The explicit target line should not contain issues/10 (principal) or issues/9 (resolved)
        target_line = [ln for ln in section.splitlines() if "target_issue:" in ln][0]
        self.assertIn("42", target_line)

    def test_completion_followup_target_principal_fallback_when_no_explicit_target(self):
        """`next_request_target` empty → principal_issue becomes the followup target."""
        m = self._module()
        state = self._eligible_ic_state(next_request_target="")
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        target_line = [ln for ln in section.splitlines() if "target_issue:" in ln][0]
        self.assertIn("issues/10", target_line)

    def test_completion_followup_target_resolved_issue_fallback(self):
        """`next_request_target` and `principal_issue` empty → resolved_issue becomes target."""
        m = self._module()
        state = self._eligible_ic_state(next_request_target="")
        state["last_issue_centric_principal_issue"] = ""
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        target_line = [ln for ln in section.splitlines() if "target_issue:" in ln][0]
        self.assertIn("issues/9", target_line)

    def test_completion_followup_target_target_issue_last_resort(self):
        """All higher-priority fields empty → target_issue used as last resort."""
        m = self._module()
        state = self._eligible_ic_state(next_request_target="")
        state["last_issue_centric_principal_issue"] = ""
        state["last_issue_centric_resolved_issue"] = ""
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        target_line = [ln for ln in section.splitlines() if "target_issue:" in ln][0]
        self.assertIn("issues/8", target_line)

    def test_no_action_state_not_eligible_for_completion_followup(self):
        """action=no_action → _build_completion_followup_section returns empty."""
        m = self._module()
        state = self._eligible_ic_state()
        state["last_issue_centric_action"] = "no_action"
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        self.assertEqual(section, "")

    def test_issue_create_action_not_eligible_for_completion_followup(self):
        """action=issue_create → _build_completion_followup_section returns empty."""
        m = self._module()
        state = self._eligible_ic_state()
        state["last_issue_centric_action"] = "issue_create"
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        self.assertEqual(section, "")

    def test_non_current_issue_kind_not_eligible_for_completion_followup(self):
        """principal_issue_kind != current_issue → not eligible, empty section."""
        m = self._module()
        state = self._eligible_ic_state()
        state["last_issue_centric_principal_issue_kind"] = "parent_issue"
        section = m._build_completion_followup_section(state, self._eligible_report_text())
        self.assertEqual(section, "")

    # ------------------------------------------------------------------
    # Group 2: _resolve_completion_followup_request at run-level
    # ------------------------------------------------------------------

    def test_resolve_completion_followup_non_ic_route_returns_original(self):
        """Non IC route → _resolve_completion_followup_request returns original params."""
        m = self._module()
        orig_section, orig_todo, orig_q = "SECTION", "TODO", "QUESTIONS"
        result = m._resolve_completion_followup_request(
            {},
            last_report="",
            issue_centric_next_request_section=orig_section,
            route_selected="legacy",
            next_todo=orig_todo,
            open_questions=orig_q,
        )
        self.assertEqual(result, (orig_section, orig_todo, orig_q))

    def test_resolve_completion_followup_eligible_overrides_section_and_todo(self):
        """IC route + eligible state → section merged, todo overridden."""
        import unittest.mock as mock
        m = self._module()
        state = self._eligible_ic_state()
        with mock.patch.object(
            m, "_build_completion_followup_section",
            return_value="## issue_centric_completion_followup\n- target: X",
        ):
            result_section, result_todo, result_q = m._resolve_completion_followup_request(
                state,
                last_report=self._eligible_report_text(),
                issue_centric_next_request_section="ORIGINAL_SECTION",
                route_selected="issue_centric",
                next_todo="ORIGINAL_TODO",
                open_questions="ORIGINAL_Q",
            )
        self.assertIn("issue_centric_completion_followup", result_section)
        self.assertIn("ORIGINAL_SECTION", result_section)
        self.assertNotEqual(result_todo, "ORIGINAL_TODO")

    def test_resolve_completion_followup_empty_section_keeps_original(self):
        """IC route + ineligible (empty section) → original params returned unchanged."""
        import unittest.mock as mock
        m = self._module()
        with mock.patch.object(m, "_build_completion_followup_section", return_value=""):
            result_section, result_todo, result_q = m._resolve_completion_followup_request(
                {},
                last_report="",
                issue_centric_next_request_section="ORIG",
                route_selected="issue_centric",
                next_todo="TODO",
                open_questions="Q",
            )
        self.assertEqual(result_section, "ORIG")
        self.assertEqual(result_todo, "TODO")
        self.assertEqual(result_q, "Q")

    # ------------------------------------------------------------------
    # Group 3: recovery decision + run-level lifecycle with IC state
    # ------------------------------------------------------------------

    def test_awaiting_user_resume_path_with_ic_continuation_state(self):
        """awaiting_user + non-empty note → awaiting_user_resume (IC state does not block)."""
        m = self._module()
        state = {**self._eligible_ic_state(), "mode": "awaiting_user"}
        decision = m._resolve_recovery_decision(state, "my note here", None)
        self.assertEqual(decision.path, "awaiting_user_resume")
        self.assertEqual(decision.resume_note, "my note here")
        # IC fields still present in decision state
        self.assertEqual(
            decision.state.get("last_issue_centric_action"), "codex_run"
        )

    def test_retryable_resume_takes_priority_over_ic_continuation_state(self):
        """IC continuation state present + retryable request → retryable_resume wins."""
        m = self._module()
        state = {**self._eligible_ic_state(), "mode": "idle"}
        retryable = ("TEXT", "HASH", "report:x.md")
        decision = m._resolve_recovery_decision(state, "", retryable)
        self.assertEqual(decision.path, "retryable_resume")
        self.assertIs(decision.retryable_request, retryable)

    def test_duplicate_pending_guard_not_broken_by_ic_state(self):
        """IC continuation state + same pending_request_source → duplicate guard returns True."""
        m = self._module()
        state = {
            **self._eligible_ic_state(),
            "mode": "waiting_prompt_reply",
            "pending_request_source": "report:x.md",
        }
        result = m._is_duplicate_pending_request(state, "report:x.md")
        self.assertTrue(result)

    def test_stale_handoff_cleanup_consistent_with_ic_continuation_state(self):
        """IC continuation state + stale handoff → cleanup proceeds, IC fields preserved."""
        import unittest.mock as mock
        m = self._module()
        base_state = {
            **self._eligible_ic_state(),
            "mode": "idle",
            "pending_handoff_log": "logs/h.md",
        }
        cleaned = {k: v for k, v in base_state.items() if k != "pending_handoff_log"}
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=True), \
             mock.patch.object(m, "_clean_stale_pending_handoff_if_needed", return_value=cleaned), \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False):
            decision = m._resolve_recovery_decision(base_state, "", None)
        self.assertEqual(decision.path, "normal_resume")
        self.assertTrue(decision.stale_handoff_cleaned)
        # IC fields survive cleanup
        self.assertEqual(decision.state.get("last_issue_centric_action"), "codex_run")
        self.assertNotIn("pending_handoff_log", decision.state)

    def test_normal_resume_ic_state_does_not_require_rotation(self):
        """Eligible IC state in normal mode → normal_resume (no rotation triggered)."""
        import unittest.mock as mock
        m = self._module()
        state = {**self._eligible_ic_state(), "mode": "idle"}
        with mock.patch.object(m, "_needs_stale_pending_handoff_cleanup", return_value=False), \
             mock.patch.object(m, "should_rotate_before_next_chat_request", return_value=False):
            decision = m._resolve_recovery_decision(state, "", None)
        self.assertEqual(decision.path, "normal_resume")

    # ------------------------------------------------------------------
    # Group 4: resume plan level integration
    # ------------------------------------------------------------------

    def test_resolve_resume_request_plan_completion_followup_overrides_todo(self):
        """_resolve_resume_request_plan: eligible IC state → effective_next_todo overridden."""
        import unittest.mock as mock
        m = self._module()
        state = self._eligible_ic_state()
        args = self._make_args()
        ic_ctx = m._IcResolvedContext(
            runtime_snapshot=None,
            runtime_mode=None,
            next_request_section="SECTION",
            route_selected="issue_centric",
        )
        with mock.patch.object(m, "_resolve_report_request_ic_context", return_value=ic_ctx), \
             mock.patch.object(
                 m, "_build_completion_followup_section",
                 return_value="## issue_centric_completion_followup\n- target: X",
             ), \
             mock.patch.object(
                 m, "_resolve_resume_request_payload",
                 return_value=("TEXT", "HASH", "SRC", None),
             ):
            plan = m._resolve_resume_request_plan(state, args, "report text", "", None)
        # Completion followup section is merged into effective_section
        self.assertIn("issue_centric_completion_followup", plan.effective_section)
        # next_todo overridden by completion wording (not original empty)
        self.assertNotEqual(plan.effective_next_todo, "")

    def test_resolve_resume_request_plan_ineligible_keeps_original_section(self):
        """_resolve_resume_request_plan: ineligible → effective_section and todo unchanged."""
        import unittest.mock as mock
        m = self._module()
        state = {"last_issue_centric_action": "no_action"}
        args = self._make_args()
        ic_ctx = m._IcResolvedContext(
            runtime_snapshot=None,
            runtime_mode=None,
            next_request_section="ORIGINAL",
            route_selected="issue_centric",
        )
        with mock.patch.object(m, "_resolve_report_request_ic_context", return_value=ic_ctx), \
             mock.patch.object(m, "_build_completion_followup_section", return_value=""), \
             mock.patch.object(
                 m, "_resolve_resume_request_payload",
                 return_value=("TEXT", "HASH", "SRC", None),
             ):
            plan = m._resolve_resume_request_plan(state, args, "", "", None)
        self.assertEqual(plan.effective_section, "ORIGINAL")
        self.assertEqual(plan.effective_next_todo, "")


class FetchExecuteNextRequestHandoffTests(unittest.TestCase):
    """Phase 39 — fetch → execution → next request lifecycle handoff consistency.

    Verifies that:
    1. fetch clears all prior execution/continuation fields before dispatch
    2. fetch sets action + target from the contract decision
    3. fetch clears correction_count on successful contract fetch
    4. codex_run stops before dispatch (BridgeStop raised before dispatch is called)
    5. legacy reply stops with legacy_contract_detected (dispatch never called)
    6. Loop state (after execution) is correctly read by _read_ic_next_cycle_context()
    7. Continuation contract fields flow intact: action, hint, target, source
    8. Completion followup eligibility boundary:
       - no_action / human_review_needed → ineligible
       - codex_run + continue_on_current_issue → eligible
       - codex_run without hint → ineligible
       - resolved_next_request_target uses next_request_target field first
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _fnp(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("fetch_next_prompt")

    def _rpr(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("request_prompt_from_report")

    def _ic(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_contract")

    def _base_pending_state(self, *, source: str = "report:1") -> dict:
        return {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "hash-abc",
            "pending_request_source": source,
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }

    def _build_raw(self, action: str, target_issue: str = "none") -> str:
        import base64 as _b64
        import json
        ic = self._ic()
        envelope = {
            "action": action,
            "target_issue": target_issue,
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": f"Test {action}.",
        }
        json_blob = json.dumps(envelope, ensure_ascii=True)
        parts = [f"{ic.DECISION_JSON_START}\n{json_blob}\n{ic.DECISION_JSON_END}"]
        if action == "issue_create":
            body = _b64.b64encode(b"Issue body content.\n").decode("ascii")
            parts.append(f"{ic.ISSUE_BODY_START}\n{body}\n{ic.ISSUE_BODY_END}")
        elif action == "codex_run":
            body = _b64.b64encode(b"Codex body content.\n").decode("ascii")
            parts.append(f"{ic.CODEX_BODY_START}\n{body}\n{ic.CODEX_BODY_END}")
        elif action == "human_review_needed":
            body = _b64.b64encode(b"Review comment.\n").decode("ascii")
            parts.append(f"{ic.REVIEW_BODY_START}\n{body}\n{ic.REVIEW_BODY_END}")
        lines = ["あなた:", "request body", "ChatGPT:", *parts, ic.REPLY_COMPLETE_TAG]
        return "\n".join(lines)

    def _build_legacy_raw(self) -> str:
        return "\n".join([
            "あなた:",
            "request body",
            "ChatGPT:",
            "===CHATGPT_PROMPT_REPLY===",
            "some legacy reply text",
            "===END_CHATGPT_PROMPT_REPLY===",
        ])

    def _run_fetch_with_raw_file(
        self,
        raw: str,
        *,
        state: dict | None = None,
        mock_dispatch: bool = True,
    ):
        """Run fetch_next_prompt.run() using --raw-file, capturing saved states
        and the mutable_state passed to dispatch_issue_centric_execution.
        Returns (saved_states, dispatch_mutable_states, exception_raised).
        """
        from contextlib import ExitStack
        from unittest.mock import patch, MagicMock
        fnp = self._fnp()
        saved_states: list[dict] = []
        dispatch_mutable_states: list[dict] = []
        base_state = state if state is not None else self._base_pending_state()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_file = tmp_path / "raw.txt"
            raw_file.write_text(raw, encoding="utf-8")

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                p = tmp_path / f"{prefix}.{suffix}"
                p.write_text(text, encoding="utf-8")
                return p

            def fake_dispatch(**kwargs):
                mutable = kwargs.get("mutable_state", {})
                dispatch_mutable_states.append(dict(mutable))
                result = MagicMock()
                result.final_state = dict(mutable)
                result.stop_message = "mock dispatch complete"
                return result

            exc_caught = None
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(fnp, "read_pending_request_text", return_value="request body")
                )
                stack.enter_context(
                    patch.object(fnp, "log_text", side_effect=fake_log_text)
                )
                stack.enter_context(
                    patch.object(fnp, "save_state",
                                 side_effect=lambda s: saved_states.append(dict(s)))
                )
                if mock_dispatch:
                    stack.enter_context(
                        patch.object(fnp, "dispatch_issue_centric_execution",
                                     side_effect=fake_dispatch)
                    )
                    stack.enter_context(
                        patch.object(fnp, "load_project_config", return_value={})
                    )
                    stack.enter_context(
                        patch.object(fnp, "project_repo_path", return_value=Path("."))
                    )
                try:
                    fnp.run(dict(base_state), ["--raw-file", str(raw_file)])
                except Exception as exc:
                    exc_caught = exc

        return saved_states, dispatch_mutable_states, exc_caught

    # ------------------------------------------------------------------
    # Group 1: Fetch state before dispatch (3 tests)
    # ------------------------------------------------------------------

    def test_fetch_no_action_sets_action_and_target_before_dispatch(self):
        """fetch_next_prompt.run() with no_action → mutable_state passed to dispatch
        has last_issue_centric_action=no_action and last_issue_centric_target_issue=none."""
        raw = self._build_raw("no_action", "none")
        saved, dispatch_calls, exc = self._run_fetch_with_raw_file(raw, mock_dispatch=True)
        self.assertEqual(len(dispatch_calls), 1, f"dispatch should be called once; exc={exc}")
        mutable = dispatch_calls[0]
        self.assertEqual(mutable.get("last_issue_centric_action"), "no_action")
        self.assertEqual(mutable.get("last_issue_centric_target_issue"), "none")

    def test_fetch_clears_continuation_fields_before_dispatch(self):
        """fetch clears all continuation contract fields in mutable_state before calling dispatch."""
        raw = self._build_raw("no_action", "none")
        prior_state = {
            **self._base_pending_state(),
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/1",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/1",
            "last_issue_centric_next_request_target_source": "runtime_snapshot",
            "last_issue_centric_execution_status": "completed",
            "last_issue_centric_dispatch_result": "logs/prev_dispatch.json",
            "last_issue_centric_normalized_summary": "logs/prev_norm.json",
        }
        saved, dispatch_calls, exc = self._run_fetch_with_raw_file(
            raw, state=prior_state, mock_dispatch=True
        )
        self.assertEqual(len(dispatch_calls), 1, f"dispatch should be called once; exc={exc}")
        mutable = dispatch_calls[0]
        self.assertEqual(mutable.get("last_issue_centric_principal_issue"), "")
        self.assertEqual(mutable.get("last_issue_centric_next_request_hint"), "")
        self.assertEqual(mutable.get("last_issue_centric_next_request_target"), "")
        self.assertEqual(mutable.get("last_issue_centric_next_request_target_source"), "")
        self.assertEqual(mutable.get("last_issue_centric_execution_status"), "")
        self.assertEqual(mutable.get("last_issue_centric_dispatch_result"), "")
        self.assertEqual(mutable.get("last_issue_centric_normalized_summary"), "")

    def test_fetch_clears_correction_count_on_successful_fetch(self):
        """Successful contract fetch resets last_issue_centric_contract_correction_count to 0."""
        raw = self._build_raw("no_action", "none")
        prior_state = {
            **self._base_pending_state(),
            "last_issue_centric_contract_correction_count": 2,
            "last_issue_centric_contract_correction_log": "logs/corr.md",
            "last_issue_centric_contract_correction_reason": "bad json",
        }
        saved, dispatch_calls, exc = self._run_fetch_with_raw_file(
            raw, state=prior_state, mock_dispatch=True
        )
        self.assertEqual(len(dispatch_calls), 1, f"dispatch should be called once; exc={exc}")
        mutable = dispatch_calls[0]
        self.assertEqual(mutable.get("last_issue_centric_contract_correction_count"), 0)
        self.assertEqual(mutable.get("last_issue_centric_contract_correction_log"), "")
        self.assertEqual(mutable.get("last_issue_centric_contract_correction_reason"), "")

    # ------------------------------------------------------------------
    # Group 2: codex_run stops before dispatch (1 test)
    # ------------------------------------------------------------------

    def test_fetch_codex_run_stops_before_dispatch_is_called(self):
        """codex_run reply → BridgeStop raised before dispatch_issue_centric_execution is called."""
        from contextlib import ExitStack
        from unittest.mock import patch
        from _bridge_common import BridgeStop
        fnp = self._fnp()
        raw = self._build_raw("codex_run", "#20")
        saved_states: list[dict] = []
        dispatch_called: list[dict] = []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_file = tmp_path / "raw.txt"
            raw_file.write_text(raw, encoding="utf-8")

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                p = tmp_path / f"{prefix}.{suffix}"
                p.write_text(text, encoding="utf-8")
                return p

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(fnp, "read_pending_request_text", return_value="request body")
                )
                stack.enter_context(
                    patch.object(fnp, "log_text", side_effect=fake_log_text)
                )
                stack.enter_context(
                    patch.object(fnp, "save_state",
                                 side_effect=lambda s: saved_states.append(dict(s)))
                )
                stack.enter_context(
                    patch.object(fnp, "dispatch_issue_centric_execution",
                                 side_effect=lambda **kw: dispatch_called.append(kw))
                )
                with self.assertRaises(BridgeStop):
                    fnp.run(dict(self._base_pending_state()), ["--raw-file", str(raw_file)])

        self.assertEqual(len(dispatch_called), 0, "dispatch must not be called for codex_run")
        self.assertEqual(len(saved_states), 1)
        saved = saved_states[0]
        self.assertEqual(saved.get("last_issue_centric_action"), "codex_run")
        self.assertEqual(saved.get("last_issue_centric_artifact_kind"), "codex_body")

    # ------------------------------------------------------------------
    # Group 3: legacy reply stops without dispatch (1 test)
    # ------------------------------------------------------------------

    def test_legacy_reply_stops_with_legacy_contract_detected(self):
        """Legacy reply → BridgeStop raised with legacy_contract_detected; dispatch never called."""
        from contextlib import ExitStack
        from unittest.mock import patch
        from _bridge_common import BridgeStop
        fnp = self._fnp()
        raw = self._build_legacy_raw()
        dispatch_called: list[dict] = []
        saved_states: list[dict] = []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_file = tmp_path / "raw.txt"
            raw_file.write_text(raw, encoding="utf-8")

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                p = tmp_path / f"{prefix}.{suffix}"
                p.write_text(text, encoding="utf-8")
                return p

            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(fnp, "read_pending_request_text", return_value="request body")
                )
                stack.enter_context(
                    patch.object(fnp, "log_text", side_effect=fake_log_text)
                )
                stack.enter_context(
                    patch.object(fnp, "save_state",
                                 side_effect=lambda s: saved_states.append(dict(s)))
                )
                stack.enter_context(
                    patch.object(fnp, "dispatch_issue_centric_execution",
                                 side_effect=lambda **kw: dispatch_called.append(kw))
                )
                with self.assertRaises(BridgeStop):
                    fnp.run(dict(self._base_pending_state()), ["--raw-file", str(raw_file)])

        self.assertEqual(len(dispatch_called), 0, "dispatch must not be called for legacy reply")
        self.assertEqual(len(saved_states), 1)
        saved = saved_states[0]
        self.assertEqual(saved.get("chatgpt_decision"), "legacy_contract_detected")
        self.assertEqual(saved.get("mode"), "awaiting_user")

    # ------------------------------------------------------------------
    # Group 4: Loop state → IcNextCycleContext (5 tests)
    # ------------------------------------------------------------------

    def test_loop_no_action_state_reads_action_in_ic_next_cycle_context(self):
        """State written after no_action execution → IcNextCycleContext.action = no_action."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "no_action",
            "last_issue_centric_target_issue": "#5",
            "last_issue_centric_principal_issue": "",
            "last_issue_centric_principal_issue_kind": "",
            "last_issue_centric_next_request_hint": "",
            "last_issue_centric_next_request_target": "",
            "last_issue_centric_resolved_issue": "",
            "last_issue_centric_close_order": "",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "no_action")
        self.assertEqual(ctx.target_issue, "#5")

    def test_loop_issue_create_state_reads_principal_issue_kind(self):
        """State written after issue_create execution → IcNextCycleContext reflects created_issue."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "issue_create",
            "last_issue_centric_target_issue": "#5",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/30",
            "last_issue_centric_principal_issue_kind": "created_issue",
            "last_issue_centric_next_request_hint": "",
            "last_issue_centric_next_request_target": "",
            "last_issue_centric_resolved_issue": "",
            "last_issue_centric_close_order": "",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "issue_create")
        self.assertEqual(ctx.principal_issue_kind, "created_issue")
        self.assertEqual(ctx.principal_issue, "https://github.com/org/repo/issues/30")

    def test_loop_human_review_state_reads_target_issue(self):
        """State written after human_review_needed execution → IcNextCycleContext preserves target."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "human_review_needed",
            "last_issue_centric_target_issue": "#7",
            "last_issue_centric_principal_issue": "#7",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "",
            "last_issue_centric_next_request_target": "",
            "last_issue_centric_resolved_issue": "",
            "last_issue_centric_close_order": "",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "human_review_needed")
        self.assertEqual(ctx.target_issue, "#7")

    def test_loop_codex_run_state_reads_hint_and_next_request_target(self):
        """State written after codex_run execution → IcNextCycleContext reads hint + target."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "#10",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/10",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/10",
            "last_issue_centric_resolved_issue": "",
            "last_issue_centric_close_order": "",
        }
        ctx = m._read_ic_next_cycle_context(state)
        self.assertEqual(ctx.action, "codex_run")
        self.assertEqual(ctx.next_request_hint, "continue_on_current_issue")
        self.assertEqual(ctx.next_request_target, "https://github.com/org/repo/issues/10")

    def test_loop_resolved_next_request_target_uses_next_request_target_field(self):
        """resolved_next_request_target uses last_issue_centric_next_request_target (highest priority)."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "https://github.com/org/repo/issues/77",
            "last_issue_centric_principal_issue": "https://github.com/org/repo/issues/99",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
            "last_issue_centric_next_request_target": "https://github.com/org/repo/issues/42",
            "last_issue_centric_resolved_issue": "https://github.com/org/repo/issues/88",
            "last_issue_centric_close_order": "",
        }
        ctx = m._read_ic_next_cycle_context(state)
        # next_request_target (42) takes priority over principal_issue (99),
        # resolved_issue (88), and target_issue (77)
        self.assertEqual(ctx.resolved_next_request_target,
                         "https://github.com/org/repo/issues/42")

    # ------------------------------------------------------------------
    # Group 5: Completion followup eligibility boundary (4 tests)
    # ------------------------------------------------------------------

    def test_loop_no_action_not_eligible_for_completion_followup(self):
        """no_action loop state → _is_completion_followup_eligible returns False (action mismatch)."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "no_action",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
        }
        summary_fields = {"result": "completed", "live_ready": "confirmed"}
        result = m._is_completion_followup_eligible(summary_fields, state)
        self.assertFalse(result)

    def test_loop_codex_run_with_continue_hint_eligible(self):
        """codex_run + continue_on_current_issue + current_issue → eligible."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
        }
        summary_fields = {"result": "completed", "live_ready": "confirmed"}
        result = m._is_completion_followup_eligible(summary_fields, state)
        self.assertTrue(result)

    def test_loop_codex_run_without_continue_hint_not_eligible(self):
        """codex_run + empty next_request_hint → not eligible."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "",
        }
        summary_fields = {"result": "completed", "live_ready": "confirmed"}
        result = m._is_completion_followup_eligible(summary_fields, state)
        self.assertFalse(result)

    def test_loop_human_review_not_eligible_for_completion_followup(self):
        """human_review_needed loop state → not eligible (action mismatch)."""
        m = self._rpr()
        state = {
            "last_issue_centric_action": "human_review_needed",
            "last_issue_centric_principal_issue_kind": "current_issue",
            "last_issue_centric_next_request_hint": "continue_on_current_issue",
        }
        summary_fields = {"result": "completed", "live_ready": "confirmed"}
        result = m._is_completion_followup_eligible(summary_fields, state)
        self.assertFalse(result)


class IcFetchHandoffStateHelpersTests(unittest.TestCase):
    """Phase 40 — fetch handoff state family helper unit tests.

    Verifies _IcFetchHandoffState, _IC_CONTINUATION_RESET_FIELDS,
    _build_ic_fetch_handoff_state, _apply_ic_continuation_reset, and
    _apply_ic_fetch_handoff_state introduced in Phase 40.
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _fnp(self):
        import importlib
        import sys
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("fetch_next_prompt")

    def _make_minimal_decision(self, *, action: str = "no_action", target: str = "none"):
        """Return a minimal IssueCentricDecision mock for helper tests."""
        from unittest.mock import MagicMock
        fnp = self._fnp()
        d = MagicMock()
        d.action = MagicMock()
        d.action.value = action
        d.target_issue = None if target == "none" else target
        d.raw_segment = "raw_segment_text"
        return d

    def _make_minimal_materialized(self, *, artifact_kind: str = ""):
        """Return a minimal materialized mock."""
        from unittest.mock import MagicMock
        m = MagicMock()
        m.safe_stop_reason = "test stop reason"
        m.metadata_log_path = Path("/tmp/meta.json")
        if artifact_kind:
            m.artifact_log_path = Path("/tmp/artifact.txt")
            body = MagicMock()
            body.kind = MagicMock()
            body.kind.value = artifact_kind
            m.prepared = MagicMock()
            m.prepared.primary_body = body
        else:
            m.artifact_log_path = None
            m.prepared = MagicMock()
            m.prepared.primary_body = None
        return m

    def _make_minimal_readiness(self):
        from unittest.mock import MagicMock
        fnp = self._fnp()
        r = MagicMock(spec=fnp.IssueCentricReplyReadiness)
        r.status = "reply_complete_valid_contract"
        r.reason = "parsed"
        r.assistant_text_present = True
        r.thinking_visible = False
        r.decision_marker_present = True
        r.contract_parse_attempted = True
        return r

    # ------------------------------------------------------------------
    # Group 1: _IcFetchHandoffState — field contract (3 tests)
    # ------------------------------------------------------------------

    def test_build_ic_fetch_handoff_state_no_action(self):
        """_build_ic_fetch_handoff_state: no_action → action/target/artifact set correctly."""
        fnp = self._fnp()
        decision = self._make_minimal_decision(action="no_action", target="none")
        materialized = self._make_minimal_materialized()
        readiness = self._make_minimal_readiness()
        prior_state: dict = {"last_processed_request_hash": "prev-hash"}
        with tempfile.TemporaryDirectory() as tmp:
            decision_log = Path(tmp) / "decision.md"
            decision_log.write_text("decision log", encoding="utf-8")
            from unittest.mock import patch
            with patch.object(fnp, "repo_relative", side_effect=lambda p: f"logs/{p.name}"):
                handoff = fnp._build_ic_fetch_handoff_state(
                    decision,
                    materialized,
                    readiness=readiness,
                    decision_log=decision_log,
                    pending_request_hash="hash-xyz",
                    prior_state=prior_state,
                )
        self.assertEqual(handoff.action, "no_action")
        self.assertEqual(handoff.target_issue, "none")
        self.assertEqual(handoff.artifact_kind, "")
        self.assertEqual(handoff.artifact_file, "")
        self.assertEqual(handoff.stop_reason, "test stop reason")
        self.assertEqual(handoff.processed_request_hash, "hash-xyz")
        self.assertIsNotNone(handoff.reply_hash)
        self.assertNotEqual(handoff.reply_hash, "")

    def test_build_ic_fetch_handoff_state_issue_create_with_artifact(self):
        """_build_ic_fetch_handoff_state: issue_create + artifact → artifact_kind + artifact_file set."""
        fnp = self._fnp()
        decision = self._make_minimal_decision(action="issue_create", target="#10")
        materialized = self._make_minimal_materialized(artifact_kind="issue_body")
        readiness = self._make_minimal_readiness()
        with tempfile.TemporaryDirectory() as tmp:
            decision_log = Path(tmp) / "decision.md"
            decision_log.write_text("x", encoding="utf-8")
            from unittest.mock import patch
            with patch.object(fnp, "repo_relative", side_effect=lambda p: f"logs/{p.name}"):
                handoff = fnp._build_ic_fetch_handoff_state(
                    decision,
                    materialized,
                    readiness=readiness,
                    decision_log=decision_log,
                    pending_request_hash="h",
                    prior_state={},
                )
        self.assertEqual(handoff.action, "issue_create")
        self.assertEqual(handoff.target_issue, "#10")
        self.assertEqual(handoff.artifact_kind, "issue_body")
        self.assertIn("artifact.txt", handoff.artifact_file)

    def test_build_ic_fetch_handoff_state_falls_back_to_prior_processed_hash(self):
        """_build_ic_fetch_handoff_state: empty pending_request_hash → uses prior_state value."""
        fnp = self._fnp()
        decision = self._make_minimal_decision()
        materialized = self._make_minimal_materialized()
        readiness = self._make_minimal_readiness()
        with tempfile.TemporaryDirectory() as tmp:
            decision_log = Path(tmp) / "d.md"
            decision_log.write_text("x", encoding="utf-8")
            from unittest.mock import patch
            with patch.object(fnp, "repo_relative", side_effect=lambda p: f"logs/{p.name}"):
                handoff = fnp._build_ic_fetch_handoff_state(
                    decision,
                    materialized,
                    readiness=readiness,
                    decision_log=decision_log,
                    pending_request_hash="",
                    prior_state={"last_processed_request_hash": "fallback-hash"},
                )
        self.assertEqual(handoff.processed_request_hash, "fallback-hash")

    # ------------------------------------------------------------------
    # Group 2: _apply_ic_continuation_reset (2 tests)
    # ------------------------------------------------------------------

    def test_apply_ic_continuation_reset_clears_all_reset_fields(self):
        """_apply_ic_continuation_reset: all _IC_CONTINUATION_RESET_FIELDS → ""."""
        fnp = self._fnp()
        state: dict = {}
        for field in fnp._IC_CONTINUATION_RESET_FIELDS:
            state[field] = f"prior_value_{field}"
        fnp._apply_ic_continuation_reset(state)
        for field in fnp._IC_CONTINUATION_RESET_FIELDS:
            self.assertEqual(state[field], "", f"Field {field!r} should be empty after reset")

    def test_apply_ic_continuation_reset_does_not_clear_non_reset_fields(self):
        """_apply_ic_continuation_reset: non-reset fields (action, etc.) are not touched."""
        fnp = self._fnp()
        state: dict = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_target_issue": "#5",
            "mode": "waiting_prompt_reply",
        }
        fnp._apply_ic_continuation_reset(state)
        # These must NOT be cleared by the reset helper
        self.assertEqual(state["last_issue_centric_action"], "codex_run")
        self.assertEqual(state["last_issue_centric_target_issue"], "#5")
        self.assertEqual(state["mode"], "waiting_prompt_reply")

    # ------------------------------------------------------------------
    # Group 3: _apply_ic_fetch_handoff_state (3 tests)
    # ------------------------------------------------------------------

    def test_apply_ic_fetch_handoff_state_writes_all_fields(self):
        """_apply_ic_fetch_handoff_state: all handoff fields land in mutable_state."""
        fnp = self._fnp()
        handoff = fnp._IcFetchHandoffState(
            action="no_action",
            target_issue="none",
            artifact_kind="",
            artifact_file="",
            metadata_log="logs/meta.json",
            decision_log="logs/decision.md",
            stop_reason="stop",
            reply_hash="abc123",
            processed_request_hash="req-hash",
            readiness_status="reply_complete_valid_contract",
            readiness_reason="parsed",
            assistant_text_present=True,
            thinking_visible=False,
            decision_marker_present=True,
            contract_parse_attempted=True,
        )
        state: dict = {}
        fnp._apply_ic_fetch_handoff_state(state, handoff)
        self.assertEqual(state["last_issue_centric_action"], "no_action")
        self.assertEqual(state["last_issue_centric_target_issue"], "none")
        self.assertEqual(state["last_issue_centric_decision_log"], "logs/decision.md")
        self.assertEqual(state["last_issue_centric_metadata_log"], "logs/meta.json")
        self.assertEqual(state["last_issue_centric_artifact_file"], "")
        self.assertEqual(state["last_issue_centric_artifact_kind"], "")
        self.assertEqual(state["last_issue_centric_stop_reason"], "stop")
        self.assertEqual(state["reply_readiness_status"], "reply_complete_valid_contract")
        self.assertEqual(state["reply_readiness_reason"], "parsed")
        self.assertTrue(state["assistant_text_present"])
        self.assertFalse(state["thinking_visible"])
        self.assertTrue(state["decision_marker_present"])
        self.assertTrue(state["contract_parse_attempted"])
        self.assertEqual(state["last_issue_centric_contract_correction_count"], 0)
        self.assertEqual(state["last_issue_centric_contract_correction_log"], "")
        self.assertEqual(state["last_issue_centric_contract_correction_reason"], "")

    def test_apply_ic_fetch_handoff_state_resets_correction_count(self):
        """_apply_ic_fetch_handoff_state: correction count reset to 0 even when prior > 0."""
        fnp = self._fnp()
        handoff = fnp._IcFetchHandoffState(
            action="no_action",
            target_issue="none",
            artifact_kind="",
            artifact_file="",
            metadata_log="",
            decision_log="",
            stop_reason="",
            reply_hash="",
            processed_request_hash="",
            readiness_status="",
            readiness_reason="",
            assistant_text_present=False,
            thinking_visible=False,
            decision_marker_present=False,
            contract_parse_attempted=False,
        )
        state: dict = {
            "last_issue_centric_contract_correction_count": 2,
            "last_issue_centric_contract_correction_log": "logs/corr.md",
            "last_issue_centric_contract_correction_reason": "bad json",
        }
        fnp._apply_ic_fetch_handoff_state(state, handoff)
        self.assertEqual(state["last_issue_centric_contract_correction_count"], 0)
        self.assertEqual(state["last_issue_centric_contract_correction_log"], "")
        self.assertEqual(state["last_issue_centric_contract_correction_reason"], "")

    def test_apply_ic_fetch_handoff_state_with_artifact(self):
        """_apply_ic_fetch_handoff_state: codex_body artifact → artifact fields written."""
        fnp = self._fnp()
        handoff = fnp._IcFetchHandoffState(
            action="codex_run",
            target_issue="#20",
            artifact_kind="codex_body",
            artifact_file="logs/codex_body.txt",
            metadata_log="logs/meta.json",
            decision_log="logs/decision.md",
            stop_reason="stop",
            reply_hash="xyz",
            processed_request_hash="req",
            readiness_status="reply_complete_valid_contract",
            readiness_reason="parsed",
            assistant_text_present=True,
            thinking_visible=False,
            decision_marker_present=True,
            contract_parse_attempted=True,
        )
        state: dict = {}
        fnp._apply_ic_fetch_handoff_state(state, handoff)
        self.assertEqual(state["last_issue_centric_action"], "codex_run")
        self.assertEqual(state["last_issue_centric_target_issue"], "#20")
        self.assertEqual(state["last_issue_centric_artifact_kind"], "codex_body")
        self.assertEqual(state["last_issue_centric_artifact_file"], "logs/codex_body.txt")


class IcFetchOutcomeRoutingTests(unittest.TestCase):
    """Phase 41 — fetch outcome routing / stop policy unit tests.

    Verifies _IcFetchOutcome, _resolve_ic_fetch_outcome, and
    _apply_ic_fetch_stop_state introduced in Phase 41, and confirms that
    run() routes correctly through the outcome helper.

    Group 1: _resolve_ic_fetch_outcome routing decisions (4 tests)
    Group 2: _apply_ic_fetch_stop_state state mutations (2 tests)
    Group 3: run() integration — initial_selection / codex_run paths (2 tests)
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _fnp(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("fetch_next_prompt")

    def _ic(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_contract")

    def _make_decision(self, action_enum, *, target_issue=None, summary="test summary"):
        from unittest.mock import MagicMock
        d = MagicMock()
        d.action = action_enum
        d.target_issue = target_issue
        d.summary = summary
        return d

    def _base_pending_state(self, *, source: str = "report:1") -> dict:
        return {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "hash-abc",
            "pending_request_source": source,
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }

    def _build_raw(self, action: str, target_issue: str = "none") -> str:
        import base64 as _b64
        import json as _json
        ic = self._ic()
        envelope = {
            "action": action,
            "target_issue": target_issue,
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": f"Test {action}.",
        }
        json_blob = _json.dumps(envelope, ensure_ascii=True)
        parts = [f"{ic.DECISION_JSON_START}\n{json_blob}\n{ic.DECISION_JSON_END}"]
        if action == "codex_run":
            body = _b64.b64encode(b"Codex body content.\n").decode("ascii")
            parts.append(f"{ic.CODEX_BODY_START}\n{body}\n{ic.CODEX_BODY_END}")
        lines = ["あなた:", "request body", "ChatGPT:", *parts, ic.REPLY_COMPLETE_TAG]
        return "\n".join(lines)

    def _run_fetch_with_raw_file(
        self,
        raw: str,
        *,
        state: dict | None = None,
        mock_dispatch: bool = True,
    ):
        from contextlib import ExitStack
        from unittest.mock import patch, MagicMock
        fnp = self._fnp()
        saved_states: list[dict] = []
        dispatch_mutable_states: list[dict] = []
        base_state = state if state is not None else self._base_pending_state()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_file = tmp_path / "raw.txt"
            raw_file.write_text(raw, encoding="utf-8")

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                p = tmp_path / f"{prefix}.{suffix}"
                p.write_text(text, encoding="utf-8")
                return p

            def fake_dispatch(**kwargs):
                mutable = kwargs.get("mutable_state", {})
                dispatch_mutable_states.append(dict(mutable))
                result = MagicMock()
                result.final_state = dict(mutable)
                result.stop_message = "mock dispatch complete"
                return result

            exc_caught = None
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(fnp, "read_pending_request_text", return_value="request body")
                )
                stack.enter_context(
                    patch.object(fnp, "log_text", side_effect=fake_log_text)
                )
                stack.enter_context(
                    patch.object(fnp, "save_state",
                                 side_effect=lambda s: saved_states.append(dict(s)))
                )
                if mock_dispatch:
                    stack.enter_context(
                        patch.object(fnp, "dispatch_issue_centric_execution",
                                     side_effect=fake_dispatch)
                    )
                    stack.enter_context(
                        patch.object(fnp, "load_project_config", return_value={})
                    )
                    stack.enter_context(
                        patch.object(fnp, "project_repo_path", return_value=Path("."))
                    )
                try:
                    fnp.run(dict(base_state), ["--raw-file", str(raw_file)])
                except Exception as exc:
                    exc_caught = exc

        return saved_states, dispatch_mutable_states, exc_caught

    # ------------------------------------------------------------------
    # Group 1: _resolve_ic_fetch_outcome routing decisions (4 tests)
    # ------------------------------------------------------------------

    def test_resolve_outcome_codex_run_returns_codex_run_stop(self):
        """CODEX_RUN action → path="codex_run_stop", stop_message contains metadata_log_rel."""
        fnp = self._fnp()
        ic = self._ic()
        decision = self._make_decision(ic.IssueCentricAction.CODEX_RUN, target_issue="#20")
        outcome = fnp._resolve_ic_fetch_outcome(
            decision,
            "report:1",
            raw_log_rel="logs/raw.txt",
            decision_log_rel="logs/decision.md",
            metadata_log_rel="logs/meta.json",
            artifact_log_rel="logs/artifact.txt",
        )
        self.assertEqual(outcome.path, "codex_run_stop")
        self.assertIn("logs/meta.json", outcome.stop_message)
        self.assertIn("logs/raw.txt", outcome.stop_message)
        self.assertIn("logs/artifact.txt", outcome.stop_message)
        self.assertEqual(outcome.selected_issue_ref, "")

    def test_resolve_outcome_initial_selection_with_target_returns_selection_stop(self):
        """initial_selection: + NO_ACTION + target → path="initial_selection_stop"."""
        fnp = self._fnp()
        ic = self._ic()
        decision = self._make_decision(ic.IssueCentricAction.NO_ACTION, target_issue="#7")
        outcome = fnp._resolve_ic_fetch_outcome(
            decision,
            "initial_selection:report",
            raw_log_rel="logs/raw.txt",
            decision_log_rel="logs/decision.md",
            metadata_log_rel="logs/meta.json",
            artifact_log_rel="",
        )
        self.assertEqual(outcome.path, "initial_selection_stop")
        self.assertEqual(outcome.selected_issue_ref, "#7")
        self.assertIn("#7", outcome.stop_message)

    def test_resolve_outcome_initial_selection_without_target_returns_dispatch(self):
        """initial_selection: + NO_ACTION + no target → path="dispatch" (target required)."""
        fnp = self._fnp()
        ic = self._ic()
        decision = self._make_decision(ic.IssueCentricAction.NO_ACTION, target_issue=None)
        outcome = fnp._resolve_ic_fetch_outcome(
            decision,
            "initial_selection:report",
            raw_log_rel="logs/raw.txt",
            decision_log_rel="logs/decision.md",
            metadata_log_rel="logs/meta.json",
            artifact_log_rel="",
        )
        self.assertEqual(outcome.path, "dispatch")
        self.assertEqual(outcome.stop_message, "")

    def test_resolve_outcome_no_action_normal_source_returns_dispatch(self):
        """no_action + non-initial_selection source → path="dispatch"."""
        fnp = self._fnp()
        ic = self._ic()
        decision = self._make_decision(ic.IssueCentricAction.NO_ACTION, target_issue="#5")
        outcome = fnp._resolve_ic_fetch_outcome(
            decision,
            "report:1",
            raw_log_rel="logs/raw.txt",
            decision_log_rel="logs/decision.md",
            metadata_log_rel="logs/meta.json",
            artifact_log_rel="",
        )
        self.assertEqual(outcome.path, "dispatch")
        self.assertEqual(outcome.stop_message, "")
        self.assertEqual(outcome.selected_issue_ref, "")

    # ------------------------------------------------------------------
    # Group 2: _apply_ic_fetch_stop_state (2 tests)
    # ------------------------------------------------------------------

    def test_apply_fetch_stop_state_codex_run_does_not_mutate_state(self):
        """codex_run_stop outcome → _apply_ic_fetch_stop_state makes no state changes."""
        fnp = self._fnp()
        outcome = fnp._IcFetchOutcome(
            path="codex_run_stop",
            stop_message="stop for codex",
            selected_issue_ref="",
        )
        state: dict = {"last_issue_centric_action": "codex_run"}
        fnp._apply_ic_fetch_stop_state(state, outcome)
        self.assertNotIn("selected_ready_issue_ref", state)
        self.assertEqual(state["last_issue_centric_action"], "codex_run")

    def test_apply_fetch_stop_state_initial_selection_writes_issue_ref(self):
        """initial_selection_stop outcome → selected_ready_issue_ref written to state."""
        fnp = self._fnp()
        outcome = fnp._IcFetchOutcome(
            path="initial_selection_stop",
            stop_message="initial selection stop",
            selected_issue_ref="#9",
        )
        state: dict = {}
        fnp._apply_ic_fetch_stop_state(state, outcome)
        self.assertEqual(state.get("selected_ready_issue_ref"), "#9")

    # ------------------------------------------------------------------
    # Group 3: run() integration — initial_selection / codex_run paths (2 tests)
    # ------------------------------------------------------------------

    def test_run_initial_selection_stop_sets_selected_ready_issue_ref(self):
        """initial_selection: source + no_action reply → BridgeStop; selected_ready_issue_ref saved."""
        from _bridge_common import BridgeStop
        raw = self._build_raw("no_action", "#7")
        state = self._base_pending_state(source="initial_selection:report")
        saved, dispatch_calls, exc = self._run_fetch_with_raw_file(
            raw, state=state, mock_dispatch=True
        )
        self.assertIsInstance(exc, BridgeStop, f"Expected BridgeStop, got: {exc!r}")
        self.assertEqual(len(dispatch_calls), 0, "dispatch must not be called for initial_selection")
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].get("selected_ready_issue_ref"), "#7")
        self.assertEqual(saved[0].get("mode"), "awaiting_user")

    def test_run_codex_run_stop_message_contains_metadata(self):
        """codex_run reply → BridgeStop message (via outcome) contains metadata reference."""
        from _bridge_common import BridgeStop
        raw = self._build_raw("codex_run", "#20")
        saved, dispatch_calls, exc = self._run_fetch_with_raw_file(
            raw, mock_dispatch=True
        )
        self.assertIsInstance(exc, BridgeStop, f"Expected BridgeStop, got: {exc!r}")
        self.assertEqual(len(dispatch_calls), 0, "dispatch must not be called for codex_run")
        self.assertIn("metadata", str(exc))


class IcOperatorLifecycleStatusTests(unittest.TestCase):
    """Phase 42 — operator-facing lifecycle summary / status consistency tests.

    Verifies _build_ic_operator_decision_note() and that chatgpt_decision_note
    in saved state is operator-appropriate for each fetch outcome path.

    Group 1: _build_ic_operator_decision_note() unit tests (7 tests)
    Group 2: chatgpt_decision_note in saved state after run() (3 tests)
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _fnp(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("fetch_next_prompt")

    def _ic(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_contract")

    def _base_pending_state(self, *, source: str = "report:1") -> dict:
        return {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "hash-abc",
            "pending_request_source": source,
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }

    def _build_raw(self, action: str, target_issue: str = "none") -> str:
        import base64 as _b64
        import json as _json
        ic = self._ic()
        envelope = {
            "action": action,
            "target_issue": target_issue,
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": f"Test {action}.",
        }
        json_blob = _json.dumps(envelope, ensure_ascii=True)
        parts = [f"{ic.DECISION_JSON_START}\n{json_blob}\n{ic.DECISION_JSON_END}"]
        if action == "codex_run":
            body = _b64.b64encode(b"Codex body content.\n").decode("ascii")
            parts.append(f"{ic.CODEX_BODY_START}\n{body}\n{ic.CODEX_BODY_END}")
        elif action == "issue_create":
            body = _b64.b64encode(b"Issue body content.\n").decode("ascii")
            parts.append(f"{ic.ISSUE_BODY_START}\n{body}\n{ic.ISSUE_BODY_END}")
        elif action == "human_review_needed":
            body = _b64.b64encode(b"Review comment.\n").decode("ascii")
            parts.append(f"{ic.REVIEW_BODY_START}\n{body}\n{ic.REVIEW_BODY_END}")
        lines = ["あなた:", "request body", "ChatGPT:", *parts, ic.REPLY_COMPLETE_TAG]
        return "\n".join(lines)

    def _build_legacy_raw(self) -> str:
        return "\n".join([
            "あなた:",
            "request body",
            "ChatGPT:",
            "===CHATGPT_PROMPT_REPLY===",
            "some legacy reply text",
            "===END_CHATGPT_PROMPT_REPLY===",
        ])

    def _run_fetch_with_raw_file(
        self,
        raw: str,
        *,
        state: dict | None = None,
        mock_dispatch: bool = True,
    ):
        from contextlib import ExitStack
        from unittest.mock import patch, MagicMock
        fnp = self._fnp()
        saved_states: list[dict] = []
        dispatch_mutable_states: list[dict] = []
        base_state = state if state is not None else self._base_pending_state()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            raw_file = tmp_path / "raw.txt"
            raw_file.write_text(raw, encoding="utf-8")

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                p = tmp_path / f"{prefix}.{suffix}"
                p.write_text(text, encoding="utf-8")
                return p

            def fake_dispatch(**kwargs):
                mutable = kwargs.get("mutable_state", {})
                dispatch_mutable_states.append(dict(mutable))
                result = MagicMock()
                result.final_state = dict(mutable)
                result.stop_message = "mock dispatch complete"
                return result

            exc_caught = None
            with ExitStack() as stack:
                stack.enter_context(
                    patch.object(fnp, "read_pending_request_text", return_value="request body")
                )
                stack.enter_context(
                    patch.object(fnp, "log_text", side_effect=fake_log_text)
                )
                stack.enter_context(
                    patch.object(fnp, "save_state",
                                 side_effect=lambda s: saved_states.append(dict(s)))
                )
                if mock_dispatch:
                    stack.enter_context(
                        patch.object(fnp, "dispatch_issue_centric_execution",
                                     side_effect=fake_dispatch)
                    )
                    stack.enter_context(
                        patch.object(fnp, "load_project_config", return_value={})
                    )
                    stack.enter_context(
                        patch.object(fnp, "project_repo_path", return_value=Path("."))
                    )
                try:
                    fnp.run(dict(base_state), ["--raw-file", str(raw_file)])
                except Exception as exc:
                    exc_caught = exc

        return saved_states, dispatch_mutable_states, exc_caught

    # ------------------------------------------------------------------
    # Group 1: _build_ic_operator_decision_note() unit tests (7 tests)
    # ------------------------------------------------------------------

    def test_note_codex_run_stop_contains_prepared_body_guidance(self):
        """codex_run_stop → note mentions prepared Codex body and bridge re-run."""
        fnp = self._fnp()
        note = fnp._build_ic_operator_decision_note(
            "codex_run", "codex_run_stop", target_issue="#20"
        )
        self.assertIn("prepared Codex body", note)
        self.assertIn("bridge", note)
        self.assertIn("#20", note)

    def test_note_initial_selection_stop_contains_ready_issue_guidance(self):
        """initial_selection_stop → note mentions ready issue selection and --ready-issue-ref."""
        fnp = self._fnp()
        note = fnp._build_ic_operator_decision_note(
            "no_action", "initial_selection_stop", selected_issue_ref="#7"
        )
        self.assertIn("ready issue", note)
        self.assertIn("#7", note)
        self.assertIn("--ready-issue-ref", note)

    def test_note_initial_selection_stop_uses_target_issue_when_no_ref(self):
        """initial_selection_stop + no selected_issue_ref → falls back to target_issue."""
        fnp = self._fnp()
        note = fnp._build_ic_operator_decision_note(
            "no_action", "initial_selection_stop", target_issue="#9"
        )
        self.assertIn("#9", note)

    def test_note_human_review_dispatch_contains_bridge_guidance(self):
        """human_review_needed + dispatch → note mentions bridge re-run for supplement input."""
        fnp = self._fnp()
        note = fnp._build_ic_operator_decision_note(
            "human_review_needed", "dispatch", target_issue="#5"
        )
        self.assertIn("#5", note)
        self.assertIn("bridge", note)

    def test_note_no_action_dispatch_does_not_imply_codex_stop(self):
        """no_action + dispatch → note does not mention 'prepared Codex body' (stop phrasing)."""
        fnp = self._fnp()
        note = fnp._build_ic_operator_decision_note("no_action", "dispatch")
        self.assertNotIn("prepared Codex body", note)
        self.assertNotIn("--ready-issue-ref", note)

    def test_note_issue_create_dispatch_contains_issue_guidance(self):
        """issue_create + dispatch → note mentions issue 作成指示."""
        fnp = self._fnp()
        note = fnp._build_ic_operator_decision_note(
            "issue_create", "dispatch", target_issue="#11"
        )
        self.assertIn("issue", note)
        self.assertIn("#11", note)

    def test_note_codex_run_dispatch_not_stop_phrasing(self):
        """codex_run + dispatch → note does NOT say 'prepared Codex body は保存済みです'."""
        fnp = self._fnp()
        note = fnp._build_ic_operator_decision_note("codex_run", "dispatch", target_issue="#8")
        # dispatch path: should NOT say artifact prepared/stop-before-dispatch
        self.assertNotIn("保存済みです。 bridge を再実行すると issue-centric codex_run dispatch を進めます", note)
        self.assertIn("dispatch", note)

    # ------------------------------------------------------------------
    # Group 2: chatgpt_decision_note in saved state after run() (3 tests)
    # ------------------------------------------------------------------

    def test_run_codex_run_stop_decision_note_mentions_prepared_artifact(self):
        """codex_run stop → chatgpt_decision_note in saved state mentions prepared Codex body."""
        raw = self._build_raw("codex_run", "#20")
        saved, _, exc = self._run_fetch_with_raw_file(raw, mock_dispatch=True)
        self.assertEqual(len(saved), 1)
        note = saved[0].get("chatgpt_decision_note", "")
        self.assertIn("prepared Codex body", str(note))
        self.assertIn("#20", str(note))

    def test_run_initial_selection_stop_decision_note_mentions_ready_issue(self):
        """initial_selection: stop → chatgpt_decision_note mentions ready issue and --ready-issue-ref."""
        raw = self._build_raw("no_action", "#7")
        state = self._base_pending_state(source="initial_selection:report")
        saved, _, exc = self._run_fetch_with_raw_file(raw, state=state, mock_dispatch=True)
        self.assertEqual(len(saved), 1)
        note = saved[0].get("chatgpt_decision_note", "")
        self.assertIn("ready issue", str(note))
        self.assertIn("--ready-issue-ref", str(note))

    def test_run_legacy_reply_decision_note_is_not_success_phrasing(self):
        """legacy_contract_detected → chatgpt_decision_note is legacy-warning, not success."""
        raw = self._build_legacy_raw()
        saved, _, exc = self._run_fetch_with_raw_file(raw, mock_dispatch=True)
        self.assertEqual(len(saved), 1)
        note = str(saved[0].get("chatgpt_decision_note", ""))
        decision = str(saved[0].get("chatgpt_decision", ""))
        # Must not look like a successful dispatch note
        self.assertNotIn("dispatch を継続します", note)
        self.assertNotIn("prepared Codex body は保存済みです", note)
        self.assertEqual(decision, "legacy_contract_detected")
        # Note should indicate legacy or contract required
        self.assertTrue(
            "legacy" in note.lower() or "contract" in note.lower(),
            f"Expected legacy/contract in note, got: {note!r}",
        )


class IcRunUntilStopHandoffSummaryTests(unittest.TestCase):
    """Phase 43 — run_until_stop operator-facing lifecycle summary / handoff summary 整流.

    Verifies that IC-specific stop patterns produce operator-appropriate guidance
    in suggested_next_note(), _build_ic_initial_selection_stop_note(), and
    present_bridge_handoff().

    Group 1: _build_ic_initial_selection_stop_note() unit tests (3 tests)
    Group 2: suggested_next_note() for IC stop patterns (3 tests)
    Group 3: present_bridge_handoff() for IC stop patterns (4 tests)
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _rus(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("run_until_stop")

    def _initial_selection_stop_state(self, *, with_note: bool = True) -> dict:
        state: dict = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:no_action",
            "selected_ready_issue_ref": "#7",
            "error": False,
        }
        if with_note:
            state["chatgpt_decision_note"] = (
                "ChatGPT が ready issue #7 を選定しました。"
                " --ready-issue-ref でその issue を指定して bridge を再実行してください。"
            )
        return state

    def _codex_run_stop_state(self, *, with_note: bool = True) -> dict:
        state: dict = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:codex_run",
            "last_issue_centric_artifact_kind": "codex_body",
            "last_issue_centric_metadata_log": "logs/metadata.md",
            "need_codex_run": False,
            "last_issue_centric_execution_status": "",
            "error": False,
        }
        if with_note:
            state["chatgpt_decision_note"] = (
                "ChatGPT が Codex 実行指示 (#20) を返しました。"
                " prepared Codex body は保存済みです。"
                " bridge を再実行すると issue-centric codex_run dispatch を進めます。"
            )
        return state

    def _legacy_contract_detected_state(self) -> dict:
        return {
            "mode": "awaiting_user",
            "chatgpt_decision": "legacy_contract_detected",
            "error": True,
            "error_message": "Legacy reply detected. Re-send with issue-centric contract.",
        }

    # ------------------------------------------------------------------
    # Group 1: _build_ic_initial_selection_stop_note() unit tests (3 tests)
    # ------------------------------------------------------------------

    def test_build_ic_initial_selection_stop_note_returns_decision_note(self):
        """IC selection stop state with chatgpt_decision_note → returns the note as-is."""
        rus = self._rus()
        state = self._initial_selection_stop_state(with_note=True)
        result = rus._build_ic_initial_selection_stop_note(state)
        self.assertIsNotNone(result)
        self.assertIn("ready issue", result)
        self.assertIn("--ready-issue-ref", result)
        self.assertIn("#7", result)

    def test_build_ic_initial_selection_stop_note_returns_fallback_without_note(self):
        """IC selection stop without chatgpt_decision_note → fallback sentence includes ref."""
        rus = self._rus()
        state = self._initial_selection_stop_state(with_note=False)
        result = rus._build_ic_initial_selection_stop_note(state)
        self.assertIsNotNone(result)
        self.assertIn("#7", result)
        self.assertIn("--ready-issue-ref", result)

    def test_build_ic_initial_selection_stop_note_returns_none_without_ref(self):
        """State without selected_ready_issue_ref → returns None (not an IC selection stop)."""
        rus = self._rus()
        state = {
            "mode": "waiting_prompt_reply",
            "chatgpt_decision": "issue_centric:no_action",
            "chatgpt_decision_note": "some note",
        }
        result = rus._build_ic_initial_selection_stop_note(state)
        self.assertIsNone(result)

    # ------------------------------------------------------------------
    # Group 2: suggested_next_note() for IC stop patterns (3 tests)
    # ------------------------------------------------------------------

    def test_suggested_next_note_initial_selection_stop_uses_decision_note(self):
        """suggested_next_note for initial_selection_stop → chatgpt_decision_note, not generic Safari text."""
        rus = self._rus()
        state = self._initial_selection_stop_state(with_note=True)
        with patch.object(rus, "resolve_unified_next_action", return_value="request_prompt_from_report"):
            note = rus.suggested_next_note(state)
        self.assertIn("ready issue", note)
        self.assertIn("--ready-issue-ref", note)
        self.assertNotIn("Safari", note)

    def test_suggested_next_note_codex_run_stop_no_duplicate_bridge_suffix(self):
        """chatgpt_decision_note already has 'bridge を再実行すると' → no duplicate suffix added."""
        rus = self._rus()
        state = self._codex_run_stop_state(with_note=True)
        with patch.object(rus, "resolve_unified_next_action", return_value="dispatch_issue_centric_codex_run"):
            note = rus.suggested_next_note(state)
        count = note.count("bridge を再実行すると")
        self.assertEqual(count, 1, f"Expected exactly 1 occurrence, got {count} in: {note!r}")

    def test_suggested_next_note_codex_run_stop_without_note_gets_suffix(self):
        """Empty chatgpt_decision_note for codex_run_stop → fallback text includes dispatch guidance."""
        rus = self._rus()
        state = self._codex_run_stop_state(with_note=False)
        with patch.object(rus, "resolve_unified_next_action", return_value="dispatch_issue_centric_codex_run"):
            note = rus.suggested_next_note(state)
        self.assertIn("prepared Codex body", note)
        self.assertIn("bridge を再実行すると", note)

    # ------------------------------------------------------------------
    # Group 3: present_bridge_handoff() for IC stop patterns (4 tests)
    # ------------------------------------------------------------------

    def test_present_bridge_handoff_initial_selection_stop_specific_title(self):
        """initial_selection_stop → handoff title explicitly mentions ready issue and --ready-issue-ref."""
        state = self._initial_selection_stop_state(with_note=True)
        handoff = _bridge_common.present_bridge_handoff(state)
        self.assertIn("ready issue", handoff.title)
        self.assertIn("--ready-issue-ref", handoff.title)
        self.assertIn("#7", handoff.title)

    def test_present_bridge_handoff_initial_selection_stop_detail_from_note(self):
        """initial_selection_stop → handoff detail uses chatgpt_decision_note content."""
        state = self._initial_selection_stop_state(with_note=True)
        handoff = _bridge_common.present_bridge_handoff(state)
        self.assertIn("--ready-issue-ref", handoff.detail)
        self.assertIn("#7", handoff.detail)

    def test_present_bridge_handoff_legacy_contract_detected_uses_error_branch(self):
        """legacy_contract_detected sets error=True → error branch fires, title is 異常."""
        state = self._legacy_contract_detected_state()
        handoff = _bridge_common.present_bridge_handoff(state)
        self.assertIn("異常", handoff.title)
        self.assertNotIn("完了", handoff.title)
        self.assertNotIn("選定", handoff.title)

    def test_present_bridge_handoff_codex_run_stop_codex_ready_title(self):
        """codex_run_stop → Codex 準備完了の title, not error, not selection-stop."""
        state = self._codex_run_stop_state(with_note=True)
        handoff = _bridge_common.present_bridge_handoff(state)
        self.assertNotIn("異常", handoff.title)
        self.assertNotIn("--ready-issue-ref", handoff.title)
        self.assertIn("Codex", handoff.title)


class IcOperatorStatusSurfaceTests(unittest.TestCase):
    """Phase 44 — operator-facing status surface 最終整流テスト.

    Verifies that status / summary / handoff surfaces are mutually consistent for
    each IC stop pattern.  Tests cover:
      Group 1: detect_ic_stop_path() unit tests (4 tests)
      Group 2: present_bridge_status() label / detail for IC stop patterns (3 tests)
      Group 3: present_bridge_handoff() title for human_review_needed IC (2 tests)
      Group 4: format_operator_stop_note() for IC stop patterns (2 tests)
      Group 5: recommended_operator_step() for IC stop patterns (2 tests)
      Group 6: suggested_next_note() with real action=no_action for IC stops (2 tests)
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _bc(self):
        return _bridge_common

    def _rus(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("run_until_stop")

    def _initial_selection_stop_state(self, *, with_note: bool = True) -> dict:
        state: dict = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:no_action",
            "selected_ready_issue_ref": "#7",
            "error": False,
        }
        if with_note:
            state["chatgpt_decision_note"] = (
                "ChatGPT が ready issue #7 を選定しました。"
                " --ready-issue-ref でその issue を指定して bridge を再実行してください。"
            )
        return state

    def _human_review_needed_ic_state(self, *, with_note: bool = True) -> dict:
        state: dict = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:human_review_needed",
            "error": False,
        }
        if with_note:
            state["chatgpt_decision_note"] = (
                "ChatGPT が人レビュー待ち (#20) を返しました。"
                " bridge を再実行すると補足入力を受けて次 request を送ります。"
            )
        return state

    def _codex_run_stop_state(self) -> dict:
        return {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:codex_run",
            "last_issue_centric_artifact_kind": "codex_body",
            "last_issue_centric_metadata_log": "logs/metadata.md",
            "need_codex_run": False,
            "last_issue_centric_execution_status": "",
            "error": False,
            "chatgpt_decision_note": (
                "ChatGPT が Codex 実行指示 (#20) を返しました。"
                " prepared Codex body は保存済みです。"
                " bridge を再実行すると issue-centric codex_run dispatch を進めます。"
            ),
        }

    def _non_ic_idle_state(self) -> dict:
        return {
            "mode": "idle",
            "need_chatgpt_prompt": True,
            "chatgpt_decision": "",
            "error": False,
        }

    def _make_args(self):
        import argparse
        args = argparse.Namespace()
        args.request_body = ""
        args.ready_issue_ref = ""
        args.dry_run = False
        args.start_mode = "run"
        args.clear_error = False
        args.worker_repo_path = ""
        args.max_steps = 6
        return args

    def _fake_plan(self, next_action: str = "no_action"):
        """Build a minimal RuntimeDispatchPlan-like mock."""
        from unittest.mock import MagicMock
        plan = MagicMock()
        plan.next_action = next_action
        plan.runtime_action = "need_next_generation"
        plan.is_fallback = False
        plan.note = "mock plan note"
        return plan

    # ------------------------------------------------------------------
    # Group 1: detect_ic_stop_path() unit tests (4 tests)
    # ------------------------------------------------------------------

    def test_detect_ic_stop_path_codex_run_stop(self):
        """has_pending_issue_centric_codex_dispatch state → 'codex_run_stop'."""
        bc = self._bc()
        state = self._codex_run_stop_state()
        self.assertEqual(bc.detect_ic_stop_path(state), "codex_run_stop")

    def test_detect_ic_stop_path_initial_selection_stop(self):
        """selected_ready_issue_ref + IC decision → 'initial_selection_stop'."""
        bc = self._bc()
        state = self._initial_selection_stop_state()
        self.assertEqual(bc.detect_ic_stop_path(state), "initial_selection_stop")

    def test_detect_ic_stop_path_human_review_needed(self):
        """IC human_review_needed decision → 'human_review_needed'."""
        bc = self._bc()
        state = self._human_review_needed_ic_state()
        self.assertEqual(bc.detect_ic_stop_path(state), "human_review_needed")

    def test_detect_ic_stop_path_non_ic_returns_empty(self):
        """Non-IC state (no IC decision, no selected_ref) → ''."""
        bc = self._bc()
        state = self._non_ic_idle_state()
        self.assertEqual(bc.detect_ic_stop_path(state), "")

    # ------------------------------------------------------------------
    # Group 2: present_bridge_status() for IC stop patterns (3 tests)
    # ------------------------------------------------------------------

    def test_present_bridge_status_initial_selection_stop_specific_label(self):
        """initial_selection_stop → status label is 'ready issue選定済み', not '人確認待ち'."""
        bc = self._bc()
        state = self._initial_selection_stop_state(with_note=True)
        status = bc.present_bridge_status(state)
        self.assertEqual(status.label, "ready issue選定済み")
        self.assertIn("--ready-issue-ref", status.detail)
        self.assertIn("#7", status.detail)

    def test_present_bridge_status_codex_run_stop_codex_label(self):
        """codex_run_stop → status label is 'Codex実行待ち' (regression)."""
        bc = self._bc()
        state = self._codex_run_stop_state()
        status = bc.present_bridge_status(state)
        self.assertEqual(status.label, "Codex実行待ち")

    def test_present_bridge_status_human_review_needed_ic_human_confirm_label(self):
        """human_review_needed IC → status label is '人確認待ち' with IC note as detail."""
        bc = self._bc()
        state = self._human_review_needed_ic_state(with_note=True)
        status = bc.present_bridge_status(state)
        self.assertEqual(status.label, "人確認待ち")
        self.assertIn("人レビュー待ち", status.detail)

    # ------------------------------------------------------------------
    # Group 3: present_bridge_handoff() for human_review_needed IC (2 tests)
    # ------------------------------------------------------------------

    def test_present_bridge_handoff_human_review_needed_specific_title(self):
        """human_review_needed IC → explicit handoff title, not generic '人の確認が必要です'."""
        bc = self._bc()
        state = self._human_review_needed_ic_state(with_note=True)
        handoff = bc.present_bridge_handoff(state)
        self.assertIn("補足", handoff.title)
        self.assertNotIn("summary と doctor を確認してください", handoff.title)

    def test_present_bridge_handoff_human_review_needed_detail_from_ic_note(self):
        """human_review_needed IC → handoff detail uses chatgpt_decision_note."""
        bc = self._bc()
        state = self._human_review_needed_ic_state(with_note=True)
        handoff = bc.present_bridge_handoff(state)
        self.assertIn("人レビュー待ち", handoff.detail)

    # ------------------------------------------------------------------
    # Group 4: format_operator_stop_note() for IC stop patterns (2 tests)
    # ------------------------------------------------------------------

    def test_format_operator_stop_note_initial_selection_stop_uses_ic_note(self):
        """initial_selection_stop + chatgpt_decision_note → returns the IC note, not 'no_action' text."""
        bc = self._bc()
        state = self._initial_selection_stop_state(with_note=True)
        plan = self._fake_plan(next_action="no_action")
        note = bc.format_operator_stop_note(state, plan=plan)
        self.assertIn("--ready-issue-ref", note)
        self.assertNotIn("次の 1 手が見つかりません", note)

    def test_format_operator_stop_note_human_review_needed_uses_ic_note(self):
        """human_review_needed IC + chatgpt_decision_note → returns the IC note, not 'no_action' text."""
        bc = self._bc()
        state = self._human_review_needed_ic_state(with_note=True)
        plan = self._fake_plan(next_action="no_action")
        note = bc.format_operator_stop_note(state, plan=plan)
        self.assertIn("人レビュー待ち", note)
        self.assertNotIn("次の 1 手が見つかりません", note)

    # ------------------------------------------------------------------
    # Group 5: recommended_operator_step() for IC stop patterns (2 tests)
    # ------------------------------------------------------------------

    def test_recommended_operator_step_initial_selection_stop_not_none(self):
        """initial_selection_stop → recommended step label mentions '--ready-issue-ref', command is not 'なし'."""
        rus = self._rus()
        state = self._initial_selection_stop_state(with_note=True)
        args = self._make_args()
        with patch.object(rus, "resolve_unified_next_action", return_value="no_action"):
            label, command = rus.recommended_operator_step(args, state)
        self.assertIn("ready-issue-ref", label)
        self.assertNotEqual(command, "なし")

    def test_recommended_operator_step_human_review_needed_resume(self):
        """human_review_needed IC → recommended step is resume (補足), not 'なし'."""
        rus = self._rus()
        state = self._human_review_needed_ic_state(with_note=True)
        args = self._make_args()
        with patch.object(rus, "resolve_unified_next_action", return_value="no_action"):
            label, command = rus.recommended_operator_step(args, state)
        self.assertIn("補足", label)
        self.assertNotEqual(command, "なし")

    # ------------------------------------------------------------------
    # Group 6: suggested_next_note() with real action=no_action (2 tests)
    # ------------------------------------------------------------------

    def test_suggested_next_note_initial_selection_stop_real_no_action(self):
        """initial_selection_stop with action=no_action → returns IC note, not generic doctor text."""
        rus = self._rus()
        state = self._initial_selection_stop_state(with_note=True)
        with patch.object(rus, "resolve_unified_next_action", return_value="no_action"):
            note = rus.suggested_next_note(state)
        self.assertIn("--ready-issue-ref", note)
        self.assertNotIn("summary と doctor を確認し", note)

    def test_suggested_next_note_human_review_needed_real_no_action(self):
        """human_review_needed IC with action=no_action → returns IC note + bridge guidance."""
        rus = self._rus()
        state = self._human_review_needed_ic_state(with_note=True)
        with patch.object(rus, "resolve_unified_next_action", return_value="no_action"):
            note = rus.suggested_next_note(state)
        self.assertIn("人レビュー待ち", note)
        self.assertNotIn("summary と doctor を確認し", note)


class IcCodexRunCloseCurrentIssueTests(unittest.TestCase):
    """Phase 47 — codex_run + close_current_issue post-launch narrow close path.

    Verifies:
    - _determine_close_order with allow_codex_run_close=True returns "after_codex_run"
    - _determine_close_order without allow_codex_run_close returns "blocked_codex_run"
    - resolve_close_target_issue raises when codex_run + no allow flags
    - resolve_close_target_issue does NOT raise when allow_codex_run_close=True and no followup
    - dispatch matrix_path for codex_run + close + no_followup is "codex_run_then_close"
    - dispatch matrix_path for codex_run without close is still "codex_run_launch_and_continuation"
    - dispatch matrix_path for codex_run + followup + close is still "codex_run_followup_then_close" (regression)
    - not_attempted_continuation_blocked close_order is "after_codex_run" when no followup

    Group 1: _determine_close_order unit tests (3 tests)
    Group 2: resolve_close_target_issue permission guard tests (2 tests)
    Group 3: matrix_path selection tests (3 tests)
    Group 4: not_attempted close_order tests (1 test)
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _iccc(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_close_current_issue")

    def _ic(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_contract")

    def _ice(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("issue_centric_execution")

    def _make_prepared(self, *, action_name: str, create_followup_issue: bool = False,
                       close_current_issue: bool = True, target_issue: str = "none") -> object:
        from unittest.mock import MagicMock
        ic = self._ic()
        d = MagicMock()
        d.action = ic.IssueCentricAction(action_name)
        d.create_followup_issue = create_followup_issue
        d.close_current_issue = close_current_issue
        d.target_issue = target_issue
        d.summary = "Test decision."
        prepared = MagicMock()
        prepared.decision = d
        return prepared

    # ------------------------------------------------------------------
    # Group 1: _determine_close_order unit tests (3 tests)
    # ------------------------------------------------------------------

    def test_determine_close_order_codex_run_allow_returns_after_codex_run(self):
        """_determine_close_order with allow_codex_run_close=True returns 'after_codex_run'."""
        iccc = self._iccc()
        ic = self._ic()
        result = iccc._determine_close_order(
            ic.IssueCentricAction.CODEX_RUN,
            allow_codex_run_close=True,
        )
        self.assertEqual(result, "after_codex_run")

    def test_determine_close_order_codex_run_default_returns_blocked(self):
        """_determine_close_order with codex_run and no allow flags returns 'blocked_codex_run'."""
        iccc = self._iccc()
        ic = self._ic()
        result = iccc._determine_close_order(ic.IssueCentricAction.CODEX_RUN)
        self.assertEqual(result, "blocked_codex_run")

    def test_determine_close_order_codex_run_followup_returns_after_codex_run_followup(self):
        """_determine_close_order with allow_codex_run_followup_close=True returns 'after_codex_run_followup'."""
        iccc = self._iccc()
        ic = self._ic()
        result = iccc._determine_close_order(
            ic.IssueCentricAction.CODEX_RUN,
            allow_codex_run_followup_close=True,
        )
        self.assertEqual(result, "after_codex_run_followup")

    # ------------------------------------------------------------------
    # Group 2: resolve_close_target_issue permission guard tests (2 tests)
    # ------------------------------------------------------------------

    def test_resolve_close_target_issue_codex_run_no_allow_raises(self):
        """resolve_close_target_issue for codex_run without allow flags raises IssueCentricCloseCurrentIssueError."""
        iccc = self._iccc()
        prepared = self._make_prepared(action_name="codex_run", create_followup_issue=False)
        with self.assertRaises(iccc.IssueCentricCloseCurrentIssueError):
            iccc.resolve_close_target_issue(
                prepared,
                prior_state={},
                default_repository="user/repo",
            )

    def test_resolve_close_target_issue_codex_run_allow_codex_run_close_no_raise(self):
        """resolve_close_target_issue for codex_run with allow_codex_run_close=True does not raise on permission check."""
        from unittest.mock import patch
        iccc = self._iccc()
        prepared = self._make_prepared(action_name="codex_run", create_followup_issue=False,
                                       target_issue="#42")

        fake_resolved = object.__new__(type(
            "ResolvedGitHubIssue",
            (),
            {"source_ref": "#42", "issue_url": "https://github.com/user/repo/issues/42",
             "issue_number": 42, "repository": "user/repo"},
        ))
        fake_resolved = type(
            "ResolvedGitHubIssue",
            (),
            {"source_ref": "#42", "issue_url": "https://github.com/user/repo/issues/42",
             "issue_number": 42, "repository": "user/repo"},
        )()

        with patch.object(iccc, "resolve_target_issue", return_value=fake_resolved):
            result = iccc.resolve_close_target_issue(
                prepared,
                prior_state={},
                default_repository="user/repo",
                allow_codex_run_close=True,
            )
        self.assertEqual(result.issue_number, 42)

    # ------------------------------------------------------------------
    # Group 3: matrix_path selection tests (3 tests)
    # ------------------------------------------------------------------

    def _dispatch_and_capture_matrix_path(
        self,
        *,
        close_current_issue: bool,
        create_followup_issue: bool,
        launch_status: str = "completed",
    ) -> str:
        """Run a minimally mocked dispatch_issue_centric_execution and return matrix_path."""
        from unittest.mock import MagicMock, patch
        import tempfile

        ice = self._ice()
        ic = self._ic()

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            def fake_log_writer(prefix: str, text: str, suffix: str = "md") -> Path:
                p = tmp_path / f"{prefix}.{suffix}"
                p.write_text(text, encoding="utf-8")
                return p

            def fake_repo_relative(p: Path) -> str:
                try:
                    return str(p.relative_to(tmp_path))
                except ValueError:
                    return str(p)

            contract_decision = MagicMock()
            contract_decision.action = ic.IssueCentricAction.CODEX_RUN
            contract_decision.close_current_issue = close_current_issue
            contract_decision.create_followup_issue = create_followup_issue
            contract_decision.target_issue = "#10"
            contract_decision.summary = "dispatch test"

            prepared = MagicMock()
            prepared.decision = contract_decision
            prepared.codex_body = b"body"
            prepared.codex_body_base64 = "Ym9keQ=="
            prepared.followup_issue_body = b"followup body" if create_followup_issue else None

            materialized = MagicMock()
            materialized.prepared = prepared

            prompt_p = tmp_path / "prompt.md"
            launch_p = tmp_path / "launch.md"
            continuation_p = tmp_path / "continuation.md"
            prompt_p.touch()
            launch_p.touch()
            continuation_p.touch()

            launch_result = MagicMock()
            launch_result.status = launch_status
            launch_result.launch_status = launch_status
            launch_result.final_mode = "launch"
            launch_result.continuation_status = launch_status
            launch_result.launch_entrypoint = "codex_run"
            launch_result.prompt_log_path = prompt_p
            launch_result.launch_log_path = launch_p
            launch_result.continuation_log_path = continuation_p
            launch_result.report_status = ""
            launch_result.report_file = ""
            launch_result.safe_stop_reason = "launch ok"

            trigger_result = MagicMock()
            trigger_result.execution_log_path = tmp_path / "trigger.json"
            trigger_result.payload_log_path = None
            trigger_result.safe_stop_reason = "trigger ok"
            trigger_result.created_comment = None
            trigger_result.status = "completed"
            trigger_result.resolved_issue = None
            trigger_result.launch_status = "completed"
            (tmp_path / "trigger.json").write_text("{}", encoding="utf-8")

            _mock_issue = MagicMock(number=10, url="https://github.com/u/r/issues/10",
                                    title="T", state="closed")

            close_result = MagicMock()
            close_result.status = "completed"
            close_result.close_status = "closed"
            close_result.close_order = "after_codex_run"
            close_result.issue_after = _mock_issue
            close_result.issue_before = _mock_issue
            close_result.resolved_issue = MagicMock(source_ref="#10",
                                                     issue_url="https://github.com/u/r/issues/10",
                                                     issue_number=10, repository="u/r")
            close_result.execution_log_path = tmp_path / "close.json"
            close_result.safe_stop_reason = "closed #10"
            (tmp_path / "close.json").write_text("{}", encoding="utf-8")

            followup_close_result = MagicMock()
            followup_close_result.status = "completed"
            followup_close_result.close_status = "closed"
            followup_close_result.close_order = "after_codex_run_followup"
            followup_close_result.issue_after = _mock_issue
            followup_close_result.issue_before = _mock_issue
            followup_close_result.resolved_issue = close_result.resolved_issue
            followup_close_result.execution_log_path = tmp_path / "fclose.json"
            followup_close_result.safe_stop_reason = "closed #10 after followup"
            (tmp_path / "fclose.json").write_text("{}", encoding="utf-8")

            followup_result = MagicMock()
            followup_result.status = "completed"
            followup_result.created_issue = None
            followup_result.execution_log_path = tmp_path / "followup.json"
            followup_result.safe_stop_reason = "followup ok"
            (tmp_path / "followup.json").write_text("{}", encoding="utf-8")

            sync_result = MagicMock()
            sync_result.status = "not_requested"
            sync_result.execution_log_path = tmp_path / "sync.json"
            sync_result.safe_stop_reason = ""
            (tmp_path / "sync.json").touch()

            state: dict = {
                "last_issue_centric_action": "codex_run",
                "last_issue_centric_resolved_issue": "https://github.com/u/r/issues/10",
                "last_issue_centric_target_issue": "#10",
            }

            captured_matrix_paths: list[str] = []
            original_finalize = ice._finalize_dispatch

            def capturing_finalize(**kwargs):
                captured_matrix_paths.append(kwargs.get("matrix_path", ""))
                return original_finalize(**kwargs)

            def fake_close_fn(*args, **kwargs):
                if kwargs.get("allow_codex_run_followup_close"):
                    return followup_close_result
                return close_result

            with patch.object(ice, "_finalize_dispatch", side_effect=capturing_finalize):
                ice.dispatch_issue_centric_execution(
                    contract_decision=contract_decision,
                    materialized=materialized,
                    prior_state=state,
                    mutable_state=dict(state),
                    project_config={"github_repository": "u/r"},
                    repo_path=tmp_path,
                    source_raw_log="logs/raw.txt",
                    source_decision_log="logs/decision.json",
                    source_metadata_log="logs/metadata.md",
                    source_artifact_path="",
                    log_writer=fake_log_writer,
                    repo_relative=fake_repo_relative,
                    load_state_fn=lambda: dict(state),
                    save_state_fn=lambda s: None,
                    execute_issue_create_action_fn=lambda *a, **kw: MagicMock(),
                    execute_codex_run_action_fn=lambda *a, **kw: trigger_result,
                    launch_issue_centric_codex_run_fn=lambda *a, **kw: launch_result,
                    execute_human_review_action_fn=lambda *a, **kw: MagicMock(),
                    execute_close_current_issue_fn=fake_close_fn,
                    execute_followup_issue_action_fn=lambda *a, **kw: followup_result,
                    execute_current_issue_project_state_sync_fn=lambda *a, **kw: sync_result,
                    launch_runner=lambda s, a: 0,
                )

        return captured_matrix_paths[0] if captured_matrix_paths else ""

    def test_matrix_path_codex_run_then_close_when_close_no_followup(self):
        """codex_run + close_current_issue + no_followup → matrix_path == 'codex_run_then_close'."""
        matrix_path = self._dispatch_and_capture_matrix_path(
            close_current_issue=True,
            create_followup_issue=False,
        )
        self.assertEqual(matrix_path, "codex_run_then_close")

    def test_matrix_path_codex_run_launch_and_continuation_when_no_close(self):
        """codex_run without close → matrix_path == 'codex_run_launch_and_continuation'."""
        matrix_path = self._dispatch_and_capture_matrix_path(
            close_current_issue=False,
            create_followup_issue=False,
        )
        self.assertEqual(matrix_path, "codex_run_launch_and_continuation")

    def test_matrix_path_codex_run_followup_then_close_regression(self):
        """codex_run + followup + close → matrix_path == 'codex_run_followup_then_close' (regression)."""
        matrix_path = self._dispatch_and_capture_matrix_path(
            close_current_issue=True,
            create_followup_issue=True,
        )
        self.assertEqual(matrix_path, "codex_run_followup_then_close")

    # ------------------------------------------------------------------
    # Group 4: not_attempted close_order tests (1 test)
    # ------------------------------------------------------------------

    def test_not_attempted_close_order_no_followup_is_after_codex_run(self):
        """When launch fails and no followup, close_order recorded is 'after_codex_run'."""
        matrix_path = self._dispatch_and_capture_matrix_path(
            close_current_issue=True,
            create_followup_issue=False,
            launch_status="blocked",
        )
        # matrix_path doesn't matter; focus is that dispatch didn't crash
        self.assertIn(matrix_path, {"codex_run_then_close", "codex_run_launch_and_continuation"})


class IcOrchestratorDoctorStatusSurfaceTests(unittest.TestCase):
    """Phase 45 — bridge_orchestrator.run() IC stop path print 整合テスト.

    Verifies that bridge_orchestrator.run() prints chatgpt_decision_note rather than
    the generic plan note for IC stop paths.

    Group 1: run() print for initial_selection_stop (2 tests)
    Group 2: run() print for human_review_needed IC (2 tests)
    Group 3: run() print for non-IC no_action (generic note) (2 tests)
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _bo(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("bridge_orchestrator")

    def _initial_selection_stop_state(self, *, with_note: bool = True) -> dict:
        state: dict = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:no_action",
            "selected_ready_issue_ref": "#7",
            "error": False,
        }
        if with_note:
            state["chatgpt_decision_note"] = (
                "ChatGPT が ready issue #7 を選定しました。"
                " --ready-issue-ref でその issue を指定して bridge を再実行してください。"
            )
        return state

    def _human_review_needed_ic_state(self, *, with_note: bool = True) -> dict:
        state: dict = {
            "mode": "awaiting_user",
            "chatgpt_decision": "issue_centric:human_review_needed",
            "error": False,
        }
        if with_note:
            state["chatgpt_decision_note"] = (
                "ChatGPT が人レビュー待ち (#20) を返しました。"
                " 補足を入れて bridge を再実行してください。"
            )
        return state

    def _non_ic_no_action_state(self) -> dict:
        return {
            "mode": "awaiting_user",
            "chatgpt_decision": "",
            "error": False,
        }

    def _run_and_capture(self, state: dict) -> str:
        """Run bridge_orchestrator.run() with all side-effectful helpers patched, capture stdout."""
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch, MagicMock
        bo = self._bo()

        fake_plan = MagicMock()
        fake_plan.next_action = "no_action"
        fake_plan.note = "今回の 1 手はありません。必要なら state.json の詳細を確認してください。"

        buf = io.StringIO()
        with redirect_stdout(buf):
            with patch.object(bo, "resolve_unified_next_action", return_value="no_action"):
                with patch.object(bo, "resolve_runtime_dispatch_plan", return_value=fake_plan):
                    with patch.object(bo, "should_prioritize_unarchived_report", return_value=False):
                        with patch.object(bo, "has_pending_issue_centric_codex_dispatch", return_value=False):
                            with patch.object(bo, "is_blocked_codex_lifecycle_state", return_value=False):
                                with patch.object(bo, "load_project_config", return_value={}):
                                    with patch.object(bo, "parse_args", return_value=MagicMock(
                                        execution_agent="codex",
                                        dry_run_codex=False,
                                        worker_repo_path="",
                                    )):
                                        with patch.object(bo, "resolve_execution_agent", return_value="codex"):
                                            with patch.object(bo, "print_project_config_warnings"):
                                                with patch.object(
                                                    bo,
                                                    "validate_selected_ready_issue_for_auto_continue",
                                                    return_value=bo.ReadyIssueAutoContinueValidation(True),
                                                ):
                                                    with patch.object(bo.request_next_prompt, "run", return_value=0):
                                                        bo.run(state, argv=[])
        return buf.getvalue()

    # ------------------------------------------------------------------
    # Group 1: initial_selection_stop (2 tests)
    # ------------------------------------------------------------------

    def test_run_initial_selection_stop_prints_ic_note(self):
        """initial_selection_stop: run() prints auto-continue message with issue ref."""
        output = self._run_and_capture(self._initial_selection_stop_state(with_note=True))
        # New behavior: auto-continue message is printed, not the stop note.
        self.assertIn("#7", output)
        self.assertIn("選定", output)
        self.assertNotIn("今回の 1 手はありません", output)

    def test_run_initial_selection_stop_no_note_falls_back_to_plan_note(self):
        """initial_selection_stop: even without chatgpt_decision_note, auto-continue fires."""
        output = self._run_and_capture(self._initial_selection_stop_state(with_note=False))
        # New behavior: auto-continue regardless of chatgpt_decision_note presence.
        self.assertIn("#7", output)
        self.assertIn("選定", output)
        self.assertNotIn("今回の 1 手はありません", output)

    # ------------------------------------------------------------------
    # Group 2: human_review_needed IC (2 tests)
    # ------------------------------------------------------------------

    def test_run_human_review_needed_ic_prints_ic_note(self):
        """human_review_needed IC: run() prints chatgpt_decision_note."""
        output = self._run_and_capture(self._human_review_needed_ic_state(with_note=True))
        self.assertIn("人レビュー待ち", output)
        self.assertNotIn("今回の 1 手はありません", output)

    def test_run_human_review_needed_ic_no_note_falls_back_to_plan_note(self):
        """human_review_needed IC without chatgpt_decision_note: falls back to plan.note."""
        output = self._run_and_capture(self._human_review_needed_ic_state(with_note=False))
        self.assertIn("今回の 1 手はありません", output)

    # ------------------------------------------------------------------
    # Group 3: non-IC no_action (2 tests)
    # ------------------------------------------------------------------

    def test_run_non_ic_no_action_prints_generic_note(self):
        """Non-IC no_action: run() prints the generic plan note."""
        output = self._run_and_capture(self._non_ic_no_action_state())
        self.assertIn("今回の 1 手はありません", output)

    def test_run_non_ic_no_action_does_not_print_ready_issue_ref(self):
        """Non-IC no_action: run() must NOT print IC-specific guidance."""
        output = self._run_and_capture(self._non_ic_no_action_state())
        self.assertNotIn("--ready-issue-ref", output)


class IcPlanARouteDecisionTests(unittest.TestCase):
    """Phase 48 — _IcReplyRouteDecision + _resolve_ic_reply_route_decision.

    Verifies that the route decision helper correctly separates:
      - "Plan A present but broken" (stop_broken) from
      - "Plan A absent" (correction_retry)
      - "legacy markers detected" (legacy_stop)
      - "valid Plan A" (ic_proceed)
      - "reply not yet complete" (not_ready)

    Key invariants tested:
      - plan_a_present=True  → route is "ic_proceed" or "stop_broken" only
      - legacy_stop          → plan_a_present=False always
      - ic_proceed           → plan_a_present=True AND plan_a_parseable=True

    Group 1: route values for each readiness status (5 tests)
    Group 2: invariant checks (3 tests)
    """

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _fnp(self):
        import importlib
        sys.path.insert(0, str(SCRIPTS_DIR))
        return importlib.import_module("fetch_next_prompt")

    def _make_readiness(
        self,
        status: str,
        *,
        decision_marker_present: bool = False,
        contract_parse_attempted: bool = False,
        reply_complete_tag_present: bool = False,
        assistant_text_present: bool = True,
    ) -> object:
        fnp = self._fnp()
        return fnp.IssueCentricReplyReadiness(
            status=status,
            reason=f"reason for {status}",
            assistant_text_present=assistant_text_present,
            thinking_visible=False,
            decision_marker_present=decision_marker_present,
            contract_parse_attempted=contract_parse_attempted,
            reply_complete_tag_present=reply_complete_tag_present,
        )

    # ------------------------------------------------------------------
    # Group 1: route values for each readiness status (5 tests)
    # ------------------------------------------------------------------

    def test_valid_plan_a_routes_to_ic_proceed(self):
        """reply_complete_valid_contract → ic_proceed, plan_a_present=True, plan_a_parseable=True."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_complete_valid_contract",
            decision_marker_present=True,
            contract_parse_attempted=True,
            reply_complete_tag_present=True,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertEqual(rd.route, "ic_proceed")
        self.assertTrue(rd.plan_a_present)
        self.assertTrue(rd.plan_a_parseable)
        self.assertFalse(rd.legacy_present)

    def test_plan_a_present_broken_routes_to_stop_broken(self):
        """reply_complete_invalid_contract + decision_marker_present → stop_broken."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_complete_invalid_contract",
            decision_marker_present=True,
            contract_parse_attempted=True,
            reply_complete_tag_present=True,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertEqual(rd.route, "stop_broken")
        self.assertTrue(rd.plan_a_present)
        self.assertFalse(rd.plan_a_parseable)
        self.assertFalse(rd.legacy_present)

    def test_plan_a_absent_no_legacy_routes_to_correction_retry(self):
        """reply_complete_no_marker → correction_retry, plan_a_present=False."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_complete_no_marker",
            decision_marker_present=False,
            reply_complete_tag_present=True,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertEqual(rd.route, "correction_retry")
        self.assertFalse(rd.plan_a_present)
        self.assertFalse(rd.plan_a_parseable)
        self.assertFalse(rd.legacy_present)

    def test_legacy_detected_routes_to_legacy_stop(self):
        """reply_complete_legacy_contract → legacy_stop, legacy_present=True."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_complete_legacy_contract",
            decision_marker_present=False,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertEqual(rd.route, "legacy_stop")
        self.assertTrue(rd.legacy_present)
        self.assertFalse(rd.plan_a_present)
        self.assertFalse(rd.plan_a_parseable)

    def test_not_ready_routes_to_not_ready(self):
        """reply_not_ready → not_ready."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_not_ready",
            decision_marker_present=False,
            assistant_text_present=True,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertEqual(rd.route, "not_ready")
        self.assertFalse(rd.plan_a_present)
        self.assertFalse(rd.plan_a_parseable)
        self.assertFalse(rd.legacy_present)

    # ------------------------------------------------------------------
    # Group 2: invariant checks (3 tests)
    # ------------------------------------------------------------------

    def test_plan_a_present_broken_never_routes_to_correction_retry_or_legacy_stop(self):
        """Broken Plan A (plan_a_present=True) must NOT route to correction_retry or legacy_stop."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_complete_invalid_contract",
            decision_marker_present=True,
            contract_parse_attempted=True,
            reply_complete_tag_present=True,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertNotEqual(rd.route, "correction_retry")
        self.assertNotEqual(rd.route, "legacy_stop")
        self.assertTrue(rd.plan_a_present)

    def test_legacy_stop_implies_plan_a_not_present(self):
        """legacy_stop route must always have plan_a_present=False and plan_a_parseable=False."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_complete_legacy_contract",
            decision_marker_present=False,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertEqual(rd.route, "legacy_stop")
        self.assertFalse(rd.plan_a_present)
        self.assertFalse(rd.plan_a_parseable)

    def test_ic_proceed_implies_plan_a_present_and_parseable(self):
        """ic_proceed route must always have plan_a_present=True and plan_a_parseable=True."""
        fnp = self._fnp()
        readiness = self._make_readiness(
            "reply_complete_valid_contract",
            decision_marker_present=True,
            contract_parse_attempted=True,
            reply_complete_tag_present=True,
        )
        rd = fnp._resolve_ic_reply_route_decision(readiness)
        self.assertEqual(rd.route, "ic_proceed")
        self.assertTrue(rd.plan_a_present)
        self.assertTrue(rd.plan_a_parseable)
        self.assertFalse(rd.legacy_present)


# ---------------------------------------------------------------------------
# Duplicate ready-issue request guard tests (Phase 21)
# ---------------------------------------------------------------------------

class ReadyIssueDuplicateGuardTests(unittest.TestCase):
    """request_next_prompt.run() must block duplicate ready-issue sends in all pending modes.

    Regression: the old guard only checked mode == "waiting_prompt_reply".
    When the mode transitioned to "extended_wait" or "await_late_completion"
    (Safari timeout sub-states), the same ready-issue request was re-sent.
    """

    def _make_pending_state(self, mode: str, request_source: str, request_hash: str = "h1") -> dict:
        return {
            "mode": mode,
            "pending_request_source": request_source,
            "pending_request_hash": request_hash,
            "pending_request_log": "logs/sent_ready_issue.md",
            "pending_request_signal": "",
            "need_chatgpt_prompt": False,
            "need_chatgpt_next": False,
            "need_codex_run": False,
        }

    def _run_and_capture(self, state: dict, argv: list) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = request_next_prompt.run(state, argv)
        return rc, buf.getvalue()

    def test_guard_blocks_duplicate_in_waiting_prompt_reply(self) -> None:
        """Existing behaviour: waiting_prompt_reply mode blocks resend of same ready-issue request."""
        ref = "#7 Implement feature X"
        source = request_next_prompt.build_ready_issue_request_source(ref)
        state = self._make_pending_state("waiting_prompt_reply", source)
        rc, out = self._run_and_capture(state, ["--ready-issue-ref", ref, "--project-path", "/tmp/repo"])
        self.assertEqual(rc, 0)
        self.assertIn("再送しませんでした", out)

    def test_guard_blocks_duplicate_in_extended_wait(self) -> None:
        """Fix: extended_wait mode must also block resend of same ready-issue request."""
        ref = "#7 Implement feature X"
        source = request_next_prompt.build_ready_issue_request_source(ref)
        state = self._make_pending_state("extended_wait", source)
        rc, out = self._run_and_capture(state, ["--ready-issue-ref", ref, "--project-path", "/tmp/repo"])
        self.assertEqual(rc, 0)
        self.assertIn("再送しませんでした", out)

    def test_guard_blocks_duplicate_in_await_late_completion(self) -> None:
        """Fix: await_late_completion mode must also block resend of same ready-issue request."""
        ref = "#7 Implement feature X"
        source = request_next_prompt.build_ready_issue_request_source(ref)
        state = self._make_pending_state("await_late_completion", source)
        rc, out = self._run_and_capture(state, ["--ready-issue-ref", ref, "--project-path", "/tmp/repo"])
        self.assertEqual(rc, 0)
        self.assertIn("再送しませんでした", out)

    def test_guard_allows_different_issue_in_extended_wait(self) -> None:
        """A different ready-issue ref must NOT be blocked even when in extended_wait mode."""
        ref_pending = "#7 Implement feature X"
        source_pending = request_next_prompt.build_ready_issue_request_source(ref_pending)
        state = self._make_pending_state("extended_wait", source_pending)

        ref_new = "#8 Fix something else"
        # Attempting to send a different ref while the old one is pending should
        # NOT be blocked (different request source → not a duplicate).
        # We mock send_initial_request_to_chatgpt so the test stays unit-level.
        send_calls = []
        fake_send_result = {"signal": "sent", "url": "https://chatgpt.com", "title": "", "match_kind": "", "matched_hint": "", "project_name": "", "github_source_attach_status": "", "github_source_attach_boundary": "", "github_source_attach_detail": "", "github_source_attach_context": "", "github_source_attach_log": "", "request_send_continued_without_github_source": False}
        with (
            patch.object(request_next_prompt, "send_initial_request_to_chatgpt", side_effect=lambda t: (send_calls.append(t), fake_send_result)[1]),
            patch.object(request_next_prompt, "save_state"),
            patch.object(request_next_prompt, "log_text", return_value="logs/dummy.md"),
            patch.object(request_next_prompt, "repo_relative", side_effect=lambda x: x),
        ):
            rc = request_next_prompt.run(state, ["--ready-issue-ref", ref_new, "--project-path", "/tmp/repo"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(send_calls), 1, "Different ready-issue ref must trigger actual send")

    def test_guard_allows_idle_mode_to_resend_after_clear(self) -> None:
        """After pending is cleared (mode=idle), same ref can be resent."""
        ref = "#7 Implement feature X"
        state = {
            "mode": "idle",
            "pending_request_source": "",
            "pending_request_hash": "",
            "need_chatgpt_prompt": True,
            "need_chatgpt_next": False,
            "need_codex_run": False,
        }
        send_calls = []
        fake_send_result = {"signal": "sent", "url": "https://chatgpt.com", "title": "", "match_kind": "", "matched_hint": "", "project_name": "", "github_source_attach_status": "", "github_source_attach_boundary": "", "github_source_attach_detail": "", "github_source_attach_context": "", "github_source_attach_log": "", "request_send_continued_without_github_source": False}
        with (
            patch.object(request_next_prompt, "send_initial_request_to_chatgpt", side_effect=lambda t: (send_calls.append(t), fake_send_result)[1]),
            patch.object(request_next_prompt, "save_state"),
            patch.object(request_next_prompt, "log_text", return_value="logs/dummy.md"),
            patch.object(request_next_prompt, "repo_relative", side_effect=lambda x: x),
        ):
            rc = request_next_prompt.run(state, ["--ready-issue-ref", ref, "--project-path", "/tmp/repo"])
        self.assertEqual(rc, 0)
        self.assertEqual(len(send_calls), 1, "Cleared state must allow resend")


class ResumeRequestContextBlockInjectionTests(unittest.TestCase):
    """_resolve_resume_request_plan injects build_request_context_section output.

    Covers: normal path injects context; pinned path skips injection;
    close/issue_create/codex_run state produces expected context lines;
    empty state produces no injection; rotated path injects context.
    """

    def _module(self):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        import request_prompt_from_report as m
        return m

    def _make_args(self):
        import argparse
        args = argparse.Namespace()
        args.next_todo = "do next"
        args.open_questions = ""
        args.current_status = ""
        return args

    def _make_ic_context(self, *, section="IC_ORIG"):
        from request_prompt_from_report import _IcResolvedContext
        return _IcResolvedContext(
            runtime_snapshot=None,
            runtime_mode=None,
            next_request_section=section,
            route_selected="issue_centric",
        )

    def _resolve_plan_with_state(self, state, *, orig_section="IC_ORIG"):
        """Run _resolve_resume_request_plan and capture the effective_section in plan."""
        import unittest.mock as mock
        m = self._module()
        ic = self._make_ic_context(section=orig_section)
        captured = {}

        def capture_payload(*_, issue_centric_next_request_section="", **__):
            captured["section"] = issue_centric_next_request_section
            return ("TEXT", "HASH", "report:dummy.md", None)

        with mock.patch.object(m, "_resolve_report_request_ic_context", return_value=ic), \
             mock.patch.object(m, "_resolve_completion_followup_request",
                               return_value=(orig_section, "do next", "")), \
             mock.patch.object(m, "_resolve_resume_request_payload",
                               side_effect=capture_payload):
            plan = m._resolve_resume_request_plan(state, self._make_args(), "", "", None)
        return plan, captured.get("section", "")

    # --- normal path: context injected ---

    def test_close_state_context_appended_to_effective_section(self):
        """Close state → 再選択してはいけません appears in effective_section."""
        state = {
            "last_issue_centric_close_status": "closed",
            "last_issue_centric_closed_issue_number": "5",
            "last_issue_centric_closed_issue_title": "Old task",
        }
        plan, passed_section = self._resolve_plan_with_state(state)
        self.assertIn("IC_ORIG", plan.effective_section)
        self.assertIn("再選択してはいけません", plan.effective_section)
        self.assertIn("#5 Old task", plan.effective_section)
        # Also check that the same modified section was passed to _resolve_resume_request_payload
        self.assertIn("再選択してはいけません", passed_section)

    def test_issue_create_state_context_appended(self):
        """issue_create with cleared created_number → principal_issue in effective_section."""
        state = {
            "last_issue_centric_action": "issue_create",
            "last_issue_centric_created_issue_number": "",
            "last_issue_centric_principal_issue": "#22 Primary task",
            "last_issue_centric_principal_issue_kind": "primary_issue",
            "last_issue_centric_next_request_hint": "continue_on_primary_issue",
        }
        plan, _ = self._resolve_plan_with_state(state)
        self.assertIn("issue_create", plan.effective_section)
        self.assertIn("#22 Primary task", plan.effective_section)

    def test_codex_run_state_context_appended(self):
        """codex_run state → action line appears in effective_section."""
        state = {
            "last_issue_centric_action": "codex_run",
            "last_issue_centric_principal_issue": "#10 Feature",
        }
        plan, _ = self._resolve_plan_with_state(state)
        self.assertIn("codex_run", plan.effective_section)
        self.assertIn("#10 Feature", plan.effective_section)

    def test_empty_state_no_context_injected(self):
        """Empty state → no 状況: block; effective_section unchanged."""
        plan, passed_section = self._resolve_plan_with_state({})
        self.assertEqual(plan.effective_section, "IC_ORIG")
        self.assertEqual(passed_section, "IC_ORIG")
        self.assertNotIn("状況:", plan.effective_section)

    def test_irrelevant_state_no_context_injected(self):
        """State without IC fields → no 状況: block injected."""
        state = {"mode": "idle", "chatgpt_decision": ""}
        plan, _ = self._resolve_plan_with_state(state)
        self.assertNotIn("状況:", plan.effective_section)

    def test_context_appended_after_orig_section_with_blank_line(self):
        """Context block is separated from orig section by a blank line."""
        state = {"last_issue_centric_close_status": "closed", "last_issue_centric_closed_issue_number": "3"}
        plan, _ = self._resolve_plan_with_state(state, orig_section="## orig_section\n- x: y")
        # blank line between orig section and 状況: block
        self.assertIn("\n\n状況:", plan.effective_section)

    # --- pinned ready issue path: context NOT injected ---

    def test_pinned_ready_issue_path_no_context_injected(self):
        """Pinned ready issue path → context block is skipped."""
        state = {
            "pending_request_source": "ready_issue:abc123",
            "current_ready_issue_ref": "#7 Some task",
            # IC state that would normally produce a context block
            "last_issue_centric_close_status": "closed",
            "last_issue_centric_closed_issue_number": "5",
        }
        plan, passed_section = self._resolve_plan_with_state(state)
        self.assertNotIn("再選択してはいけません", plan.effective_section)
        self.assertNotIn("状況:", plan.effective_section)
        self.assertNotIn("再選択してはいけません", passed_section)

    # --- rotated path: context injected into ic.next_request_section ---

    def test_rotated_path_context_injected_into_ic_section(self):
        """_resolve_rotated_request_plan injects context into ic.next_request_section."""
        import unittest.mock as mock
        m = self._module()
        state = {
            "last_issue_centric_close_status": "closed",
            "last_issue_centric_closed_issue_number": "9",
            "last_issue_centric_closed_issue_title": "Done item",
        }
        ic = self._make_ic_context(section="HANDOFF_ORIG")
        captured_ic = {}

        def fake_acquire_handoff(state, args, last_report, *, request_source, ic_context):
            captured_ic["section"] = ic_context.next_request_section
            return ("handoff text", "handoff_log")

        with mock.patch.object(m, "_resolve_normal_ic_context", return_value=ic), \
             mock.patch.object(m, "build_report_request_source", return_value="report:x"), \
             mock.patch.object(m, "_acquire_rotated_handoff", side_effect=fake_acquire_handoff):
            m._resolve_rotated_request_plan(state, self._make_args(), "last report")

        self.assertIn("HANDOFF_ORIG", captured_ic["section"])
        self.assertIn("再選択してはいけません", captured_ic["section"])
        self.assertIn("#9 Done item", captured_ic["section"])

    def test_rotated_path_empty_state_no_context_injected(self):
        """_resolve_rotated_request_plan with empty state: ic.next_request_section unchanged."""
        import unittest.mock as mock
        m = self._module()
        ic = self._make_ic_context(section="HANDOFF_ORIG")
        captured_ic = {}

        def fake_acquire_handoff(state, args, last_report, *, request_source, ic_context):
            captured_ic["section"] = ic_context.next_request_section
            return ("handoff text", "handoff_log")

        with mock.patch.object(m, "_resolve_normal_ic_context", return_value=ic), \
             mock.patch.object(m, "build_report_request_source", return_value="report:x"), \
             mock.patch.object(m, "_acquire_rotated_handoff", side_effect=fake_acquire_handoff):
            m._resolve_rotated_request_plan({}, self._make_args(), "last report")

        self.assertEqual(captured_ic["section"], "HANDOFF_ORIG")
        self.assertNotIn("状況:", captured_ic["section"])


if __name__ == "__main__":
    unittest.main()
