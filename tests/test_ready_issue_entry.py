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


if __name__ == "__main__":
    unittest.main()
