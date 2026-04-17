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


if __name__ == "__main__":
    unittest.main()
