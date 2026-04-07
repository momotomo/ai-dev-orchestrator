from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import issue_centric_contract  # noqa: E402
import issue_centric_execution  # noqa: E402
import issue_centric_transport  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class TempLogWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.counter = 0

    def __call__(self, prefix: str, content: str, suffix: str = "md") -> Path:
        self.counter += 1
        path = self.root / f"{self.counter:02d}_{prefix}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return path


def build_decision(
    *,
    action: issue_centric_contract.IssueCentricAction,
    target_issue: str | None = None,
    close_current_issue: bool = False,
    create_followup_issue: bool = False,
    issue_text: str | None = None,
    review_text: str | None = None,
    followup_text: str | None = None,
) -> issue_centric_contract.IssueCentricDecision:
    return issue_centric_contract.IssueCentricDecision(
        action=action,
        target_issue=target_issue,
        close_current_issue=close_current_issue,
        create_followup_issue=create_followup_issue,
        summary="Dispatcher test decision",
        issue_body_base64=(b64(issue_text) if issue_text is not None else None),
        codex_body_base64=None,
        review_base64=(b64(review_text) if review_text is not None else None),
        followup_issue_body_base64=(b64(followup_text) if followup_text is not None else None),
        raw_json="{}",
        raw_segment="segment",
    )


def materialized_from_decision(
    decision: issue_centric_contract.IssueCentricDecision,
    *,
    root: Path,
) -> SimpleNamespace:
    prepared = issue_centric_transport.decode_issue_centric_decision(decision)
    metadata = root / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    artifact = root / "artifact.md"
    artifact.write_text("artifact", encoding="utf-8")
    return SimpleNamespace(
        prepared=prepared,
        metadata_log_path=metadata,
        artifact_log_path=artifact,
        safe_stop_reason="prepared",
    )


def fake_issue(number: int, *, state: str = "open") -> SimpleNamespace:
    return SimpleNamespace(
        number=number,
        url=f"https://github.com/example/repo/issues/{number}",
        title=f"Issue {number}",
        repository="example/repo",
        node_id=f"ISSUE_{number}",
        state=state,
    )


def fake_comment(comment_id: int, issue_number: int) -> SimpleNamespace:
    return SimpleNamespace(
        comment_id=comment_id,
        url=f"https://github.com/example/repo/issues/{issue_number}#issuecomment-{comment_id}",
        issue_number=issue_number,
    )


class IssueCentricExecutionDispatcherTests(unittest.TestCase):
    def base_state(self) -> dict[str, object]:
        return {
            "last_issue_centric_action": "",
            "last_issue_centric_target_issue": "",
            "last_issue_centric_stop_reason": "",
            "chatgpt_decision_note": "",
            "last_issue_centric_dispatch_result": "",
            "last_issue_centric_close_order": "",
            "last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20",
            "last_issue_centric_execution_status": "",
            "last_issue_centric_execution_log": "",
            "last_issue_centric_created_issue_number": "",
            "last_issue_centric_created_issue_url": "",
            "last_issue_centric_created_issue_title": "",
            "last_issue_centric_project_sync_status": "",
            "last_issue_centric_project_url": "",
            "last_issue_centric_project_item_id": "",
            "last_issue_centric_project_state_field": "",
            "last_issue_centric_project_state_value": "",
            "last_issue_centric_followup_status": "",
            "last_issue_centric_followup_log": "",
            "last_issue_centric_followup_parent_issue": "",
            "last_issue_centric_close_status": "",
            "last_issue_centric_close_log": "",
            "last_issue_centric_closed_issue_number": "",
            "last_issue_centric_closed_issue_url": "",
            "last_issue_centric_closed_issue_title": "",
            "last_issue_centric_review_status": "",
            "last_issue_centric_review_log": "",
            "last_issue_centric_review_comment_id": "",
            "last_issue_centric_review_comment_url": "",
            "last_issue_centric_review_close_policy": "",
        }

    def dispatch(
        self,
        *,
        decision: issue_centric_contract.IssueCentricDecision,
        materialized: SimpleNamespace,
        root: Path,
        mutable_state: dict[str, object] | None = None,
        execute_issue_create_action_fn=None,
        execute_codex_run_action_fn=None,
        launch_issue_centric_codex_run_fn=None,
        execute_human_review_action_fn=None,
        execute_close_current_issue_fn=None,
        execute_followup_issue_action_fn=None,
    ) -> issue_centric_execution.IssueCentricDispatchResult:
        saved_states: list[dict[str, object]] = []
        log_writer = TempLogWriter(root)
        state = self.base_state() if mutable_state is None else mutable_state
        state.update(
            {
                "last_issue_centric_action": decision.action.value,
                "last_issue_centric_target_issue": decision.target_issue or "none",
            }
        )
        return issue_centric_execution.dispatch_issue_centric_execution(
            contract_decision=decision,
            materialized=materialized,
            prior_state={"last_issue_centric_resolved_issue": "https://github.com/example/repo/issues/20"},
            mutable_state=state,
            project_config={"github_repository": "example/repo", "github_project_url": "", "worker_repo_path": "."},
            repo_path=REPO_ROOT,
            source_raw_log="logs/raw.txt",
            source_decision_log="logs/decision.md",
            source_metadata_log="logs/metadata.json",
            source_artifact_path="logs/artifact.md",
            log_writer=log_writer,
            repo_relative=lambda path: path.name,
            load_state_fn=lambda: dict(saved_states[-1]) if saved_states else dict(state),
            save_state_fn=lambda s: saved_states.append(dict(s)),
            execute_issue_create_action_fn=execute_issue_create_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("issue_create should not run"))),
            execute_codex_run_action_fn=execute_codex_run_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("codex_run should not run"))),
            launch_issue_centric_codex_run_fn=launch_issue_centric_codex_run_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("launch should not run"))),
            execute_human_review_action_fn=execute_human_review_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("review should not run"))),
            execute_close_current_issue_fn=execute_close_current_issue_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("close should not run"))),
            execute_followup_issue_action_fn=execute_followup_issue_action_fn or (lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("followup should not run"))),
            launch_runner=lambda state, argv=None: 0,
        )

    def test_dispatcher_blocks_followup_for_non_no_action_combo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                create_followup_issue=True,
                issue_text="# Title\n\nBody\n",
                followup_text="# Follow-up\n\nBody\n",
            )
            result = self.dispatch(
                decision=decision,
                materialized=materialized_from_decision(decision, root=root),
                root=root,
            )

            self.assertEqual(result.matrix_path, "blocked_followup_combo")
            self.assertEqual(result.final_status, "blocked")
            self.assertEqual([step.name for step in result.steps], ["unsupported_followup_combo"])
            self.assertTrue(result.final_state["last_issue_centric_dispatch_result"])

    def test_dispatcher_runs_issue_create_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
                target_issue="#20",
                close_current_issue=True,
                issue_text="# New issue\n\nBody\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_issue_create(*args, **kwargs):
                calls.append("issue_create")
                log_path = root / "issue_create.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    execution_log_path=log_path,
                    created_issue=fake_issue(71),
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="created issue",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_issue_create",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_issue_create_action_fn=fake_issue_create,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["issue_create", "close"])
            self.assertEqual(result.matrix_path, "issue_create_then_close")
            self.assertEqual([step.name for step in result.steps], ["issue_create", "close_current_issue"])
            self.assertEqual(result.final_state["last_issue_centric_closed_issue_number"], "20")

    def test_dispatcher_runs_human_review_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED,
                target_issue="#20",
                close_current_issue=True,
                review_text="## Review\n\n- OK\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_review(*args, **kwargs):
                calls.append("review")
                log_path = root / "review.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    review_status="completed",
                    close_policy="after_review_close_if_review_succeeds",
                    resolved_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_comment=fake_comment(3001, 20),
                    execution_log_path=log_path,
                    safe_stop_reason="review posted",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_human_review",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed after review",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_human_review_action_fn=fake_review,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["review", "close"])
            self.assertEqual(result.matrix_path, "human_review_then_close")
            self.assertEqual(result.final_state["last_issue_centric_review_comment_id"], "3001")
            self.assertEqual(result.final_state["last_issue_centric_close_order"], "after_human_review")

    def test_dispatcher_runs_no_action_followup_then_close_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            decision = build_decision(
                action=issue_centric_contract.IssueCentricAction.NO_ACTION,
                target_issue="#20",
                close_current_issue=True,
                create_followup_issue=True,
                followup_text="# Follow-up\n\nBody\n",
            )
            materialized = materialized_from_decision(decision, root=root)
            calls: list[str] = []

            def fake_followup(*args, **kwargs):
                calls.append("followup")
                log_path = root / "followup.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    followup_status="completed",
                    parent_issue=SimpleNamespace(issue_url="https://github.com/example/repo/issues/20"),
                    created_issue=fake_issue(72),
                    execution_log_path=log_path,
                    project_sync_status="issue_only_fallback",
                    project_url="",
                    project_item_id="",
                    project_state_field_name="",
                    project_state_value_name="",
                    safe_stop_reason="follow-up created",
                )

            def fake_close(*args, **kwargs):
                calls.append("close")
                log_path = root / "close.json"
                log_path.write_text("{}", encoding="utf-8")
                return SimpleNamespace(
                    status="completed",
                    close_status="closed",
                    close_order="after_no_action",
                    execution_log_path=log_path,
                    issue_before=fake_issue(20),
                    issue_after=fake_issue(20, state="closed"),
                    safe_stop_reason="closed current issue",
                )

            result = self.dispatch(
                decision=decision,
                materialized=materialized,
                root=root,
                execute_followup_issue_action_fn=fake_followup,
                execute_close_current_issue_fn=fake_close,
            )

            self.assertEqual(calls, ["followup", "close"])
            self.assertEqual(result.matrix_path, "no_action_followup_then_close")
            self.assertEqual([step.name for step in result.steps], ["followup_issue_create", "close_current_issue"])


if __name__ == "__main__":
    unittest.main()
