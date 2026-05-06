"""Tests for issue_centric_ci_gate.py."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import issue_centric_ci_gate as ci_gate  # noqa: E402


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------


class TestExtractCiRunIds(unittest.TestCase):
    def test_extracts_single_run_url(self) -> None:
        text = "See https://github.com/owner/repo/actions/runs/1234567890 for details."
        result = ci_gate.extract_ci_run_ids_from_text(text)
        self.assertEqual(result, [("owner/repo", "1234567890")])

    def test_extracts_multiple_run_urls(self) -> None:
        text = (
            "First: https://github.com/owner/repo/actions/runs/100\n"
            "Second: https://github.com/owner/repo/actions/runs/200"
        )
        result = ci_gate.extract_ci_run_ids_from_text(text)
        self.assertEqual(result, [("owner/repo", "100"), ("owner/repo", "200")])

    def test_no_run_urls(self) -> None:
        text = "No CI URLs in this text."
        result = ci_gate.extract_ci_run_ids_from_text(text)
        self.assertEqual(result, [])

    def test_ignores_non_run_urls(self) -> None:
        text = "https://github.com/owner/repo/issues/42 is not a run URL."
        result = ci_gate.extract_ci_run_ids_from_text(text)
        self.assertEqual(result, [])

    def test_handles_hyphenated_repo(self) -> None:
        text = "https://github.com/my-org/my-repo/actions/runs/999"
        result = ci_gate.extract_ci_run_ids_from_text(text)
        self.assertEqual(result, [("my-org/my-repo", "999")])


class TestExtractCommitShas(unittest.TestCase):
    def test_extracts_sha(self) -> None:
        sha = "a" * 40
        text = f"Pushed commit {sha} to main."
        result = ci_gate.extract_commit_shas_from_text(text)
        self.assertIn(sha, result)

    def test_extracts_uppercase_sha(self) -> None:
        sha = "B" * 40
        text = f"Commit {sha}"
        result = ci_gate.extract_commit_shas_from_text(text)
        self.assertIn(sha, result)

    def test_no_sha(self) -> None:
        text = "No SHA here."
        result = ci_gate.extract_commit_shas_from_text(text)
        self.assertEqual(result, [])

    def test_partial_sha_not_matched(self) -> None:
        # 39 chars — should NOT match
        partial = "a" * 39
        text = f"Short ref {partial} is not a full SHA."
        result = ci_gate.extract_commit_shas_from_text(text)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# CIRunStatus helper methods
# ---------------------------------------------------------------------------


def make_run(
    status: str,
    conclusion: str | None = None,
    run_id: str = "12345",
    repository: str = "owner/repo",
) -> ci_gate.CIRunStatus:
    return ci_gate.CIRunStatus(
        run_id=run_id,
        repository=repository,
        status=status,
        conclusion=conclusion,
        html_url=f"https://github.com/{repository}/actions/runs/{run_id}",
        head_sha="a" * 40,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        name="CI",
    )


class TestCIRunStatusMethods(unittest.TestCase):
    def test_is_pending_queued(self) -> None:
        run = make_run("queued")
        self.assertTrue(run.is_pending())
        self.assertFalse(run.is_success())
        self.assertFalse(run.is_failure())

    def test_is_pending_in_progress(self) -> None:
        run = make_run("in_progress")
        self.assertTrue(run.is_pending())

    def test_is_success(self) -> None:
        run = make_run("completed", "success")
        self.assertFalse(run.is_pending())
        self.assertTrue(run.is_success())
        self.assertFalse(run.is_failure())

    def test_is_failure_failure(self) -> None:
        run = make_run("completed", "failure")
        self.assertFalse(run.is_pending())
        self.assertFalse(run.is_success())
        self.assertTrue(run.is_failure())

    def test_is_failure_cancelled(self) -> None:
        run = make_run("completed", "cancelled")
        self.assertTrue(run.is_failure())

    def test_neutral_is_success(self) -> None:
        run = make_run("completed", "neutral")
        self.assertTrue(run.is_success())


# ---------------------------------------------------------------------------
# evaluate_ci_gate — mocked GitHub API
# ---------------------------------------------------------------------------


class TestEvaluateCiGate(unittest.TestCase):
    """Tests for evaluate_ci_gate() with mocked GitHub API."""

    def _call(
        self,
        *,
        report_text: str = "",
        prior_state: dict | None = None,
        fetch_by_id: ci_gate.CIRunStatus | Exception | None = None,
        fetch_latest: ci_gate.CIRunStatus | None | Exception | None = None,
    ) -> ci_gate.CIGateResult:
        state = prior_state or {}

        def _fake_fetch_by_id(repo: str, run_id: str, token: str) -> ci_gate.CIRunStatus:
            if isinstance(fetch_by_id, Exception):
                raise fetch_by_id
            if fetch_by_id is None:
                raise ci_gate.CIGateError("no run")
            return fetch_by_id

        def _fake_fetch_latest(
            repo: str, token: str, *, branch: str = "", commit_sha: str = "", per_page: int = 5
        ) -> ci_gate.CIRunStatus | None:
            if isinstance(fetch_latest, Exception):
                raise fetch_latest
            return fetch_latest

        with (
            patch.object(ci_gate, "fetch_ci_run_by_id", side_effect=_fake_fetch_by_id),
            patch.object(ci_gate, "fetch_latest_ci_run", side_effect=_fake_fetch_latest),
            patch.object(ci_gate, "fetch_ci_run_failed_jobs_summary", return_value="job=test failure"),
        ):
            return ci_gate.evaluate_ci_gate(
                report_text=report_text,
                repository="owner/repo",
                token="fake-token",
                prior_state=state,
            )

    # --- verdict: waiting_ci ---

    def test_in_progress_run_from_report_url(self) -> None:
        run = make_run("in_progress", run_id="99")
        report = "CI: https://github.com/owner/repo/actions/runs/99"
        result = self._call(report_text=report, fetch_by_id=run)
        self.assertEqual(result.verdict, "waiting_ci")
        self.assertEqual(result.run_id, "99")
        self.assertIsNotNone(result.run_status)

    def test_queued_run_from_report_url(self) -> None:
        run = make_run("queued", run_id="88")
        report = "Run: https://github.com/owner/repo/actions/runs/88"
        result = self._call(report_text=report, fetch_by_id=run)
        self.assertEqual(result.verdict, "waiting_ci")

    def test_in_progress_recheck_from_prior_run_id(self) -> None:
        run = make_run("in_progress", run_id="55")
        state = {"ci_gate_run_id": "55", "ci_gate_attempt_count": 2}
        result = self._call(prior_state=state, fetch_by_id=run)
        self.assertEqual(result.verdict, "waiting_ci")
        self.assertEqual(result.attempt_count, 3)

    # --- verdict: success ---

    def test_success_run_from_report_url(self) -> None:
        run = make_run("completed", "success", run_id="77")
        report = "CI: https://github.com/owner/repo/actions/runs/77"
        result = self._call(report_text=report, fetch_by_id=run)
        self.assertEqual(result.verdict, "success")
        self.assertEqual(result.run_id, "77")

    def test_success_from_latest_run(self) -> None:
        run = make_run("completed", "success", run_id="66")
        result = self._call(fetch_latest=run)
        self.assertEqual(result.verdict, "success")

    # --- verdict: failure ---

    def test_failure_run_from_report_url(self) -> None:
        run = make_run("completed", "failure", run_id="44")
        report = "CI: https://github.com/owner/repo/actions/runs/44"
        result = self._call(report_text=report, fetch_by_id=run)
        self.assertEqual(result.verdict, "failure")
        self.assertEqual(result.failure_detail, "job=test failure")

    def test_cancelled_run(self) -> None:
        run = make_run("completed", "cancelled", run_id="33")
        report = "Run: https://github.com/owner/repo/actions/runs/33"
        result = self._call(report_text=report, fetch_by_id=run)
        self.assertEqual(result.verdict, "failure")

    # --- verdict: skipped ---

    def test_skipped_when_no_run_found(self) -> None:
        result = self._call(fetch_latest=None)
        self.assertEqual(result.verdict, "skipped")

    def test_skipped_when_fetch_latest_fails_and_no_sha_in_report(self) -> None:
        result = self._call(
            fetch_latest=ci_gate.CIGateError("api down"),
        )
        self.assertEqual(result.verdict, "skipped")

    # --- verdict: indeterminate ---

    def test_indeterminate_when_max_attempts_exceeded(self) -> None:
        run = make_run("in_progress", run_id="11")
        state = {
            "ci_gate_run_id": "11",
            "ci_gate_attempt_count": ci_gate.CI_GATE_MAX_ATTEMPT_COUNT,
        }
        result = self._call(prior_state=state, fetch_by_id=run)
        self.assertEqual(result.verdict, "indeterminate")
        self.assertIn("maximum", result.note)

    def test_indeterminate_when_fetch_by_id_fails_for_prior_run(self) -> None:
        state = {"ci_gate_run_id": "11", "ci_gate_attempt_count": 1}
        result = self._call(
            prior_state=state,
            fetch_by_id=ci_gate.CIGateError("not found"),
        )
        self.assertEqual(result.verdict, "indeterminate")

    def test_indeterminate_unknown_conclusion(self) -> None:
        run = make_run("completed", "startup_failure", run_id="22")
        report = "CI: https://github.com/owner/repo/actions/runs/22"
        result = self._call(report_text=report, fetch_by_id=run)
        self.assertEqual(result.verdict, "indeterminate")

    # --- commit SHA fallback ---

    def test_uses_commit_sha_when_no_run_url(self) -> None:
        sha = "b" * 40
        run = make_run("completed", "success", run_id="321")
        report = f"Pushed commit {sha} to main."

        def _fake_fetch_latest(
            repo: str,
            token: str,
            *,
            branch: str = "",
            commit_sha: str = "",
            per_page: int = 5,
        ) -> ci_gate.CIRunStatus | None:
            if commit_sha:
                return run
            return None

        with (
            patch.object(ci_gate, "fetch_latest_ci_run", side_effect=_fake_fetch_latest),
            patch.object(ci_gate, "fetch_ci_run_failed_jobs_summary", return_value=""),
        ):
            result = ci_gate.evaluate_ci_gate(
                report_text=report,
                repository="owner/repo",
                token="fake",
                prior_state={},
            )
        self.assertEqual(result.verdict, "success")
        self.assertEqual(result.run_id, "321")

    # --- attempt_count incremented ---

    def test_attempt_count_incremented(self) -> None:
        run = make_run("completed", "success", run_id="1")
        report = "CI: https://github.com/owner/repo/actions/runs/1"
        result = self._call(
            report_text=report,
            prior_state={"ci_gate_attempt_count": 4},
            fetch_by_id=run,
        )
        self.assertEqual(result.attempt_count, 5)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


class TestCiGateStateHelpers(unittest.TestCase):
    def test_apply_ci_gate_state(self) -> None:
        run = make_run("in_progress", run_id="42")
        result = ci_gate.CIGateResult(
            verdict="waiting_ci",
            run_id="42",
            commit_sha="a" * 40,
            checked_at="2026-01-01T00:00:00Z",
            attempt_count=3,
            run_status=run,
            note="waiting",
        )
        state: dict = {}
        ci_gate.apply_ci_gate_state(state, result, current_issue="#7")
        self.assertEqual(state["ci_gate_status"], "waiting_ci")
        self.assertEqual(state["ci_gate_run_id"], "42")
        self.assertEqual(state["ci_gate_commit_sha"], "a" * 40)
        self.assertEqual(state["ci_gate_checked_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(state["ci_gate_attempt_count"], 3)
        self.assertEqual(state["ci_gate_current_issue"], "#7")

    def test_clear_ci_gate_state(self) -> None:
        state = {
            "ci_gate_status": "waiting_ci",
            "ci_gate_run_id": "99",
            "ci_gate_commit_sha": "a" * 40,
            "ci_gate_checked_at": "2026-01-01T00:00:00Z",
            "ci_gate_attempt_count": 5,
            "ci_gate_current_issue": "#3",
        }
        ci_gate.clear_ci_gate_state(state)
        self.assertEqual(state["ci_gate_status"], "")
        self.assertEqual(state["ci_gate_run_id"], "")
        self.assertEqual(state["ci_gate_attempt_count"], 0)
        self.assertEqual(state["ci_gate_current_issue"], "")

    def test_is_waiting_ci_true(self) -> None:
        state = {"ci_gate_status": "waiting_ci"}
        self.assertTrue(ci_gate.is_waiting_ci(state))

    def test_is_waiting_ci_false(self) -> None:
        for val in ("", "success", "failure", "skipped", "indeterminate"):
            with self.subTest(val=val):
                state = {"ci_gate_status": val}
                self.assertFalse(ci_gate.is_waiting_ci(state))


# ---------------------------------------------------------------------------
# CI failure continuation body builder
# ---------------------------------------------------------------------------


class TestBuildCiFailureContinuationBody(unittest.TestCase):
    def test_contains_required_sections(self) -> None:
        body = ci_gate.build_ci_failure_continuation_body(
            issue_ref="#42",
            run_id="12345",
            run_url="https://github.com/owner/repo/actions/runs/12345",
            conclusion="failure",
            failure_detail="job='test' conclusion=failure failed_steps=[pytest]",
            repository="owner/repo",
        )
        self.assertIn("#42", body)
        self.assertIn("12345", body)
        self.assertIn("failure", body)
        self.assertIn("pytest", body)
        self.assertIn("Task", body)
        self.assertIn("Do NOT close", body)

    def test_no_failure_detail_omits_section(self) -> None:
        body = ci_gate.build_ci_failure_continuation_body(
            issue_ref="#1",
            run_id="1",
            run_url="",
            conclusion="cancelled",
            failure_detail="",
            repository="owner/repo",
        )
        self.assertNotIn("Failure Summary", body)

    def test_no_run_url_omits_url_line(self) -> None:
        body = ci_gate.build_ci_failure_continuation_body(
            issue_ref="#1",
            run_id="1",
            run_url="",
            conclusion="failure",
            failure_detail="",
            repository="owner/repo",
        )
        self.assertNotIn("**URL**:", body)

    def test_run_url_present_when_given(self) -> None:
        url = "https://github.com/owner/repo/actions/runs/99"
        body = ci_gate.build_ci_failure_continuation_body(
            issue_ref="#1",
            run_id="99",
            run_url=url,
            conclusion="failure",
            failure_detail="",
            repository="owner/repo",
        )
        self.assertIn(url, body)


# ---------------------------------------------------------------------------
# _WAIT_CI state with DEFAULT_STATE fields
# ---------------------------------------------------------------------------


class TestDefaultStateFields(unittest.TestCase):
    """Verify that _bridge_common DEFAULT_STATE has all CI gate fields."""

    def test_default_state_has_ci_gate_fields(self) -> None:
        import _bridge_common as bc
        state = bc.DEFAULT_STATE
        expected_fields = {
            "ci_gate_status",
            "ci_gate_run_id",
            "ci_gate_commit_sha",
            "ci_gate_checked_at",
            "ci_gate_attempt_count",
            "ci_gate_current_issue",
        }
        for field in expected_fields:
            with self.subTest(field=field):
                self.assertIn(field, state)

    def test_ci_gate_status_default_empty(self) -> None:
        import _bridge_common as bc
        self.assertEqual(bc.DEFAULT_STATE["ci_gate_status"], "")

    def test_ci_gate_attempt_count_default_zero(self) -> None:
        import _bridge_common as bc
        self.assertEqual(bc.DEFAULT_STATE["ci_gate_attempt_count"], 0)


# ---------------------------------------------------------------------------
# bridge_orchestrator CI gate integration (lightweight, no real HTTP)
# ---------------------------------------------------------------------------


class TestBridgeOrchestratorCiGateIntegration(unittest.TestCase):
    """Smoke tests for bridge_orchestrator CI gate hooks (no real I/O)."""

    def _make_state(self, ci_status: str = "", **extra: object) -> dict:
        import _bridge_common as bc
        state = dict(bc.DEFAULT_STATE)
        state["ci_gate_status"] = ci_status
        state.update(extra)
        return state

    def _make_project_config(self, repo: str = "") -> dict:
        return {"github_repository": repo}

    def test_is_waiting_ci_gate_entry(self) -> None:
        state = self._make_state("waiting_ci", ci_gate_run_id="10", ci_gate_attempt_count=1)
        self.assertTrue(ci_gate.is_waiting_ci(state))

    def test_not_waiting_ci_when_status_empty(self) -> None:
        state = self._make_state("")
        self.assertFalse(ci_gate.is_waiting_ci(state))

    def test_gate_disabled_when_no_repository(self) -> None:
        import bridge_orchestrator as bo
        state = self._make_state("")
        result = bo._run_ci_gate_check(state, self._make_project_config(repo=""))
        self.assertIsNone(result)

    def test_ci_gate_before_report_request_returns_none_when_no_repo(self) -> None:
        """_handle_ci_gate_before_report_request returns None (proceed) when gate disabled."""
        import argparse
        import bridge_orchestrator as bo
        state = self._make_state("")
        args = argparse.Namespace()
        result = bo._handle_ci_gate_before_report_request(
            state, self._make_project_config(repo=""), args
        )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
