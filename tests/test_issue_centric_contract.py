from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_next_prompt  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_transport  # noqa: E402
import _bridge_common as bridge_common  # noqa: E402
from _bridge_common import BridgeError, BridgeStop  # noqa: E402


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def block(name: str, payload: str) -> str:
    markers = {
        "json": (
            issue_centric_contract.DECISION_JSON_START,
            issue_centric_contract.DECISION_JSON_END,
        ),
        "issue": (
            issue_centric_contract.ISSUE_BODY_START,
            issue_centric_contract.ISSUE_BODY_END,
        ),
        "codex": (
            issue_centric_contract.CODEX_BODY_START,
            issue_centric_contract.CODEX_BODY_END,
        ),
        "review": (
            issue_centric_contract.REVIEW_BODY_START,
            issue_centric_contract.REVIEW_BODY_END,
        ),
        "followup": (
            issue_centric_contract.FOLLOWUP_ISSUE_BODY_START,
            issue_centric_contract.FOLLOWUP_ISSUE_BODY_END,
        ),
    }
    start_marker, end_marker = markers[name]
    return f"{start_marker}\n{payload}\n{end_marker}"


def build_raw_reply(
    envelope: dict[str, object],
    *,
    parts: list[str] | None = None,
    after_text: str = "request body",
    extra_before: str = "",
    extra_after: str = "",
) -> str:
    json_block = block("json", json.dumps(envelope, ensure_ascii=True, indent=2))
    contract_parts = parts or [json_block]
    lines = [
        "あなた:",
        after_text,
        "ChatGPT:",
    ]
    if extra_before:
        lines.append(extra_before)
    lines.extend(contract_parts)
    if extra_after:
        lines.append(extra_after)
    # Terminal completion tag — bridge waits for this before attempting parse.
    lines.append(issue_centric_contract.REPLY_COMPLETE_TAG)
    return "\n".join(lines)


class TempLogWriter:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.counter = 0

    def __call__(self, prefix: str, content: str, suffix: str = "md") -> Path:
        self.counter += 1
        path = self.root / f"{self.counter:02d}_{prefix}.{suffix}"
        path.write_text(content, encoding="utf-8")
        return path


class IssueCentricContractParserTests(unittest.TestCase):
    def test_parses_no_action_without_body(self) -> None:
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "No next action is required.",
            }
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.NO_ACTION)
        self.assertIsNone(decision.target_issue)
        self.assertIsNone(decision.issue_body_base64)
        self.assertIsNone(decision.codex_body_base64)
        self.assertIsNone(decision.review_base64)

    def test_parses_human_review_needed_with_optional_review_block(self) -> None:
        review_payload = b64("Review notes")
        raw = build_raw_reply(
            {
                "action": "human_review_needed",
                "target_issue": "#55",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Human review is required.",
            },
            parts=[
                block("review", review_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "human_review_needed",
                            "target_issue": "#55",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Human review is required.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
            extra_before="Short preface outside the contract.",
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.HUMAN_REVIEW_NEEDED)
        self.assertEqual(decision.target_issue, "#55")
        self.assertEqual(decision.review_base64, review_payload)

    def test_parses_no_action_with_followup_issue_body_when_flag_is_true(self) -> None:
        followup_payload = b64("# Follow-up title\n\nBody\n")
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "#55",
                "close_current_issue": False,
                "create_followup_issue": True,
                "summary": "Create one follow-up issue.",
            },
            parts=[
                block("followup", followup_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "no_action",
                            "target_issue": "#55",
                            "close_current_issue": False,
                            "create_followup_issue": True,
                            "summary": "Create one follow-up issue.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.NO_ACTION)
        self.assertEqual(decision.target_issue, "#55")
        self.assertTrue(decision.create_followup_issue)
        self.assertEqual(decision.followup_issue_body_base64, followup_payload)

    def test_parses_issue_create_with_issue_body_only(self) -> None:
        issue_payload = b64("Issue body")
        raw = build_raw_reply(
            {
                "action": "issue_create",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Create the next issue.",
            },
            parts=[
                block("issue", issue_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "issue_create",
                            "target_issue": "none",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Create the next issue.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.ISSUE_CREATE)
        self.assertEqual(decision.issue_body_base64, issue_payload)
        self.assertIsNone(decision.codex_body_base64)

    def test_parses_codex_run_with_codex_body_only(self) -> None:
        codex_payload = b64("Codex body")
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#123",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Run Codex on the existing issue.",
            },
            parts=[
                block("codex", codex_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": "#123",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Run Codex on the existing issue.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.CODEX_RUN)
        self.assertEqual(decision.target_issue, "#123")
        self.assertEqual(decision.codex_body_base64, codex_payload)

    def test_parses_near_miss_codex_run_with_preface_and_missing_optional_flags(self) -> None:
        codex_payload = b64("Implement the bounded rehearsal task.\n")
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": 2,
                "summary": "Add rehearsal marker and README completion note.",
            },
            parts=[
                block("codex", codex_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": 2,
                            "summary": "Add rehearsal marker and README completion note.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
            extra_before="短い前置きが marker の前にあります。",
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.CODEX_RUN)
        self.assertEqual(decision.target_issue, "2")
        self.assertFalse(decision.close_current_issue)
        self.assertFalse(decision.create_followup_issue)
        self.assertEqual(decision.codex_body_base64, codex_payload)

    def test_rejects_codex_run_with_target_issue_none(self) -> None:
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Invalid codex run.",
            },
            parts=[
                block("codex", b64("Codex body")),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": "none",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Invalid codex run.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "target_issue=none"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_issue_create_without_issue_body(self) -> None:
        raw = build_raw_reply(
            {
                "action": "issue_create",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Missing issue body.",
            }
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "requires CHATGPT_ISSUE_BODY"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_codex_run_without_codex_body(self) -> None:
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#123",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Missing codex body.",
            }
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "requires CHATGPT_CODEX_BODY"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_no_action_with_unexpected_body(self) -> None:
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Unexpected body.",
            },
            parts=[
                block("review", b64("Unexpected")),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "no_action",
                            "target_issue": "none",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Unexpected body.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "no_action must not include body blocks"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_followup_flag_without_followup_body(self) -> None:
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "#55",
                "close_current_issue": False,
                "create_followup_issue": True,
                "summary": "Missing follow-up body.",
            }
        )
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "requires CHATGPT_FOLLOWUP_ISSUE_BODY",
        ):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_followup_body_when_flag_is_false(self) -> None:
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "#55",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Unexpected follow-up body.",
            },
            parts=[
                block("followup", b64("# Follow-up title\n\nBody\n")),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "no_action",
                            "target_issue": "#55",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Unexpected follow-up body.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "allowed only when create_followup_issue=true",
        ):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_human_review_needed_with_invalid_body_combination(self) -> None:
        raw = build_raw_reply(
            {
                "action": "human_review_needed",
                "target_issue": "#77",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Invalid human review block mix.",
            },
            parts=[
                block("codex", b64("Not allowed")),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "human_review_needed",
                            "target_issue": "#77",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Invalid human review block mix.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "may include CHATGPT_REVIEW only"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_unknown_action(self) -> None:
        raw = build_raw_reply(
            {
                "action": "launch_rocket",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Unknown action.",
            }
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "action is unknown"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_missing_required_json_field(self) -> None:
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
            }
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "summary must be a string"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_duplicate_body_block(self) -> None:
        payload = b64("Issue body")
        raw = build_raw_reply(
            {
                "action": "issue_create",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Duplicate issue body.",
            },
            parts=[
                block("issue", payload),
                block("issue", payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "issue_create",
                            "target_issue": "none",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Duplicate issue body.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "must not appear more than once"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_empty_body_block(self) -> None:
        raw = build_raw_reply(
            {
                "action": "issue_create",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Empty issue body.",
            },
            parts=[
                block("issue", "   \n   "),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "issue_create",
                            "target_issue": "none",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Empty issue body.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "present but empty"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_invalid_summary_type(self) -> None:
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": 123,
            }
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "summary must be a string"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_invalid_boolean_type(self) -> None:
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "none",
                "close_current_issue": "false",
                "create_followup_issue": False,
                "summary": "Invalid bool.",
            }
        )
        with self.assertRaisesRegex(issue_centric_contract.IssueCentricContractError, "close_current_issue must be a boolean"):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_rejects_integer_optional_flags_even_after_near_miss_relaxation(self) -> None:
        for field_name, field_value in (("close_current_issue", 1), ("create_followup_issue", 0)):
            with self.subTest(field_name=field_name, field_value=field_value):
                raw = build_raw_reply(
                    {
                        "action": "no_action",
                        "target_issue": "none",
                        "close_current_issue": False,
                        "create_followup_issue": False,
                        "summary": "Invalid bool-like integer.",
                    }
                )
                envelope = {
                    "action": "no_action",
                    "target_issue": "none",
                    "close_current_issue": False,
                    "create_followup_issue": False,
                    "summary": "Invalid bool-like integer.",
                }
                envelope[field_name] = field_value
                raw = build_raw_reply(envelope)
                with self.assertRaisesRegex(
                    issue_centric_contract.IssueCentricContractError,
                    f"{field_name} must be a boolean",
                ):
                    issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_accepts_body_blocks_in_any_order_and_ignores_extra_text(self) -> None:
        issue_payload = b64("Issue body")
        raw = build_raw_reply(
            {
                "action": "issue_create",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Create from shuffled contract blocks.",
            },
            parts=[
                block("issue", issue_payload),
                "Some extra explanation that should be ignored.",
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "issue_create",
                            "target_issue": "none",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Create from shuffled contract blocks.",
                        },
                        ensure_ascii=True,
                        indent=2,
                    ),
                ),
            ],
            extra_before="Intro text before the contract.",
            extra_after="Trailing note after the contract.",
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.issue_body_base64, issue_payload)
        self.assertFalse(decision.create_followup_issue)

    def test_normalizes_multiline_base64_payload(self) -> None:
        payload = b64("Codex body payload")
        multiline_payload = f"{payload[:8]}  \n  {payload[8:16]}\n{payload[16:]}"
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#123",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Multiline base64 payload.",
            },
            parts=[
                block("codex", multiline_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": "#123",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Multiline base64 payload.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertEqual(decision.codex_body_base64, payload)

    def test_maybe_parse_returns_none_when_contract_is_absent(self) -> None:
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "===CHATGPT_PROMPT_REPLY===",
                "Legacy prompt body",
                "===END_REPLY===",
            ]
        )
        self.assertIsNone(issue_centric_contract.maybe_parse_issue_centric_reply(raw, after_text="request body"))


class IssueCentricTargetIssueFormatTests(unittest.TestCase):
    """Tests for target_issue format validation (added in #42)."""

    def _make_reply(self, target_issue: str, action: str = "no_action") -> str:
        return build_raw_reply(
            {
                "action": action,
                "target_issue": target_issue,
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Format validation test.",
            }
        )

    # --- valid formats ---

    def test_accepts_hash_prefixed_number(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("#42"), after_text="request body"
        )
        self.assertEqual(decision.target_issue, "#42")

    def test_accepts_bare_number(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("42"), after_text="request body"
        )
        self.assertEqual(decision.target_issue, "42")

    def test_accepts_cross_repo_reference(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("owner/repo#42"), after_text="request body"
        )
        self.assertEqual(decision.target_issue, "owner/repo#42")

    def test_accepts_none_sentinel(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("none"), after_text="request body"
        )
        self.assertIsNone(decision.target_issue)

    def test_accepts_none_sentinel_case_insensitive(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("NONE"), after_text="request body"
        )
        self.assertIsNone(decision.target_issue)

    def test_accepts_none_sentinel_with_surrounding_spaces(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("  none  "), after_text="request body"
        )
        self.assertIsNone(decision.target_issue)

    # --- invalid formats ---

    def test_rejects_freetext_target_issue(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("not-a-number"), after_text="request body"
            )

    def test_rejects_double_hash_target_issue(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("##42"), after_text="request body"
            )

    def test_accepts_full_github_issue_url(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("https://github.com/org/repo/issues/42"),
            after_text="request body",
        )
        self.assertEqual(decision.target_issue, "https://github.com/org/repo/issues/42")

    def test_accepts_full_github_issue_url_large_number(self) -> None:
        url = "https://github.com/owner/my-repo/issues/1234"
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply(url), after_text="request body"
        )
        self.assertEqual(decision.target_issue, url)

    # --- invalid URL variants ---

    def test_rejects_github_url_with_trailing_path(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("https://github.com/org/repo/issues/42/files"),
                after_text="request body",
            )

    def test_rejects_non_github_url(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("https://gitlab.com/org/repo/issues/42"),
                after_text="request body",
            )

    def test_rejects_hash_only(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("#"), after_text="request body"
            )

    def test_rejects_alphabetic_after_hash(self) -> None:
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("#abc"), after_text="request body"
            )

    # --- cross-repo: extra slash variants (added in #42 review follow-up) ---

    def test_rejects_cross_repo_with_extra_path_segment(self) -> None:
        """owner/repo/extra#42 must be rejected (only 2-segment owner/repo allowed)."""
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("owner/repo/extra#42"), after_text="request body"
            )

    def test_rejects_cross_repo_with_double_slash(self) -> None:
        """owner//repo#42 must be rejected (empty segment is not valid)."""
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("owner//repo#42"), after_text="request body"
            )

    def test_rejects_cross_repo_with_trailing_junk_after_number(self) -> None:
        """owner/repo#42/extra must be rejected."""
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("owner/repo#42/extra"), after_text="request body"
            )

    def test_rejects_cross_repo_with_alphabetic_issue_number(self) -> None:
        """owner/repo#abc must be rejected."""
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("owner/repo#abc"), after_text="request body"
            )

    # --- ref with trailing title (normalization, added for live-run failure case) ---

    def test_normalizes_hash_ref_with_title(self) -> None:
        """'#3 Ready: ...' normalizes to '#3' (actual live-run failure case)."""
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply(
                "#3 Ready: verify GitHub attach confirmed path with second rehearsal note"
            ),
            after_text="request body",
        )
        self.assertEqual(decision.target_issue, "#3")

    def test_normalizes_cross_repo_ref_with_title(self) -> None:
        """'owner/repo#42 Some title' normalizes to 'owner/repo#42'."""
        decision = issue_centric_contract.parse_issue_centric_reply(
            self._make_reply("owner/repo#42 Some PR title"),
            after_text="request body",
        )
        self.assertEqual(decision.target_issue, "owner/repo#42")

    def test_rejects_issue_keyword_with_number(self) -> None:
        """'issue 3' has no leading issue ref → still invalid."""
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("issue 3"), after_text="request body"
            )

    def test_rejects_ref_embedded_mid_string(self) -> None:
        """'Ready: #3' has ref in the middle, not at start → still invalid."""
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError,
            "target_issue has an invalid format",
        ):
            issue_centric_contract.parse_issue_centric_reply(
                self._make_reply("Ready: #3"), after_text="request body"
            )

    def test_integration_with_full_raw_reply_ref_with_title(self) -> None:
        """Integration: target_issue='#3 Ready: ...' normalizes to '#3' end-to-end."""
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "#3 Ready: verify GitHub attach confirmed path with second rehearsal note",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Normalized from ref-with-title.",
            },
            after_text="request body",
        )
        decision = issue_centric_contract.parse_issue_centric_reply(
            raw, after_text="request body"
        )
        self.assertEqual(decision.target_issue, "#3")


class IssueCentricTransportTests(unittest.TestCase):
    def materialize(
        self,
        decision: issue_centric_contract.IssueCentricDecision,
    ) -> issue_centric_transport.MaterializedIssueCentricDecision:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_writer = TempLogWriter(root)
            materialized = issue_centric_transport.materialize_issue_centric_decision(
                decision,
                log_writer=log_writer,
                repo_relative=lambda path: path.name,
            )
            body_path = materialized.artifact_log_path
            metadata_path = materialized.metadata_log_path
            if body_path is not None:
                self.assertTrue(body_path.exists())
            self.assertTrue(metadata_path.exists())
            return materialized

    def test_materializes_issue_create_body_artifact(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            build_raw_reply(
                {
                    "action": "issue_create",
                    "target_issue": "none",
                    "close_current_issue": False,
                    "create_followup_issue": False,
                    "summary": "Create a new issue from the decoded body.",
                },
                parts=[
                    block("issue", b64("## New issue\n\n- item\n")),
                    block(
                        "json",
                        json.dumps(
                            {
                                "action": "issue_create",
                                "target_issue": "none",
                                "close_current_issue": False,
                                "create_followup_issue": False,
                                "summary": "Create a new issue from the decoded body.",
                            },
                            ensure_ascii=True,
                        ),
                    ),
                ],
            ),
            after_text="request body",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_writer = TempLogWriter(root)
            materialized = issue_centric_transport.materialize_issue_centric_decision(
                decision,
                log_writer=log_writer,
                repo_relative=lambda path: path.name,
            )

            self.assertEqual(
                materialized.prepared.primary_body.kind,
                issue_centric_transport.IssueCentricArtifactKind.ISSUE_BODY,
            )
            self.assertEqual(
                materialized.artifact_log_path.read_text(encoding="utf-8"),
                "## New issue\n\n- item\n",
            )
            self.assertEqual(
                materialized.metadata["prepared_artifact"]["kind"],
                "issue_body",
            )

    def test_materializes_followup_issue_body_artifact_for_no_action(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            build_raw_reply(
                {
                    "action": "no_action",
                    "target_issue": "#55",
                    "close_current_issue": False,
                    "create_followup_issue": True,
                    "summary": "Prepare one follow-up issue.",
                },
                parts=[
                    block("followup", b64("# Follow-up title\n\nBody\n")),
                    block(
                        "json",
                        json.dumps(
                            {
                                "action": "no_action",
                                "target_issue": "#55",
                                "close_current_issue": False,
                                "create_followup_issue": True,
                                "summary": "Prepare one follow-up issue.",
                            },
                            ensure_ascii=True,
                        ),
                    ),
                ],
            ),
            after_text="request body",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialized = issue_centric_transport.materialize_issue_centric_decision(
                decision,
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
            )

            self.assertEqual(
                materialized.prepared.primary_body.kind,
                issue_centric_transport.IssueCentricArtifactKind.FOLLOWUP_ISSUE_BODY,
            )
            self.assertEqual(
                materialized.artifact_log_path.read_text(encoding="utf-8"),
                "# Follow-up title\n\nBody\n",
            )
            self.assertEqual(
                materialized.metadata["pending_runtime_action"],
                "followup_issue_dispatch",
            )

    def test_materializes_codex_body_artifact(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            build_raw_reply(
                {
                    "action": "codex_run",
                    "target_issue": "#123",
                    "close_current_issue": False,
                    "create_followup_issue": False,
                    "summary": "Run Codex from the decoded body.",
                },
                parts=[
                    block("codex", b64("Run Codex with this body.\n")),
                    block(
                        "json",
                        json.dumps(
                            {
                                "action": "codex_run",
                                "target_issue": "#123",
                                "close_current_issue": False,
                                "create_followup_issue": False,
                                "summary": "Run Codex from the decoded body.",
                            },
                            ensure_ascii=True,
                        ),
                    ),
                ],
            ),
            after_text="request body",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialized = issue_centric_transport.materialize_issue_centric_decision(
                decision,
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
            )

            self.assertEqual(
                materialized.prepared.primary_body.kind,
                issue_centric_transport.IssueCentricArtifactKind.CODEX_BODY,
            )
            self.assertEqual(
                materialized.artifact_log_path.read_text(encoding="utf-8"),
                "Run Codex with this body.\n",
            )
            self.assertEqual(
                materialized.metadata["pending_runtime_action"],
                "codex_run_dispatch",
            )

    def test_materializes_human_review_artifact(self) -> None:
        decision = issue_centric_contract.parse_issue_centric_reply(
            build_raw_reply(
                {
                    "action": "human_review_needed",
                    "target_issue": "#55",
                    "close_current_issue": False,
                    "create_followup_issue": False,
                    "summary": "Review notes are required.",
                },
                parts=[
                    block("review", b64("## Review\n\nNeeds follow-up.\n")),
                    block(
                        "json",
                        json.dumps(
                            {
                                "action": "human_review_needed",
                                "target_issue": "#55",
                                "close_current_issue": False,
                                "create_followup_issue": False,
                                "summary": "Review notes are required.",
                            },
                            ensure_ascii=True,
                        ),
                    ),
                ],
            ),
            after_text="request body",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialized = issue_centric_transport.materialize_issue_centric_decision(
                decision,
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
            )

            self.assertEqual(
                materialized.prepared.primary_body.kind,
                issue_centric_transport.IssueCentricArtifactKind.REVIEW,
            )
            self.assertEqual(
                materialized.artifact_log_path.read_text(encoding="utf-8"),
                "## Review\n\nNeeds follow-up.\n",
            )

    def test_materializes_multiline_base64_payload(self) -> None:
        payload = b64("```python\nprint('ok')\n```\n")
        multiline = f"{payload[:10]}\n{payload[10:20]}\n{payload[20:]}"
        decision = issue_centric_contract.parse_issue_centric_reply(
            build_raw_reply(
                {
                    "action": "codex_run",
                    "target_issue": "#123",
                    "close_current_issue": False,
                    "create_followup_issue": False,
                    "summary": "Multiline payload.",
                },
                parts=[
                    block("codex", multiline),
                    block(
                        "json",
                        json.dumps(
                            {
                                "action": "codex_run",
                                "target_issue": "#123",
                                "close_current_issue": False,
                                "create_followup_issue": False,
                                "summary": "Multiline payload.",
                            },
                            ensure_ascii=True,
                        ),
                    ),
                ],
            ),
            after_text="request body",
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialized = issue_centric_transport.materialize_issue_centric_decision(
                decision,
                log_writer=TempLogWriter(root),
                repo_relative=lambda path: path.name,
            )
            self.assertEqual(
                materialized.artifact_log_path.read_text(encoding="utf-8"),
                "```python\nprint('ok')\n```\n",
            )

    def test_rejects_invalid_base64_at_transport_stage(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
            target_issue=None,
            close_current_issue=False,
            create_followup_issue=False,
            summary="Invalid base64 payload.",
            issue_body_base64="!!not-base64!!",
            codex_body_base64=None,
            review_base64=None,
            followup_issue_body_base64=None,
            raw_json="{}",
            raw_segment="segment",
        )
        with self.assertRaisesRegex(
            issue_centric_transport.IssueCentricBodyDecodeError,
            "not valid base64",
        ):
            issue_centric_transport.decode_issue_centric_decision(decision)

    def test_rejects_invalid_utf8_at_transport_stage(self) -> None:
        # New behavior (post-fix): valid base64 that decodes to non-UTF-8 bytes
        # no longer raises IssueCentricBodyDecodeError.  Instead a WARNING is printed
        # and the text is decoded with errors='replace' (U+FFFD for bad bytes).
        # This ensures that AI-model encoding errors don't hard-stop the run.
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.CODEX_RUN,
            target_issue="#123",
            close_current_issue=False,
            create_followup_issue=False,
            summary="Invalid UTF-8 payload.",
            issue_body_base64=None,
            codex_body_base64=base64.b64encode(b"\xff").decode("ascii"),
            review_base64=None,
            followup_issue_body_base64=None,
            raw_json="{}",
            raw_segment="segment",
        )
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            prepared = issue_centric_transport.decode_issue_centric_decision(decision)
        output = buf.getvalue()
        # Warning must be visible
        self.assertIn("WARNING", output)
        self.assertIn("non-UTF-8", output)
        # Decoded text contains replacement character, not empty
        self.assertIn("\ufffd", prepared.codex_body.decoded_text)

    def test_rejects_empty_decoded_payload(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
            target_issue=None,
            close_current_issue=False,
            create_followup_issue=False,
            summary="Empty decoded payload.",
            issue_body_base64="",
            codex_body_base64=None,
            review_base64=None,
            followup_issue_body_base64=None,
            raw_json="{}",
            raw_segment="segment",
        )
        with self.assertRaisesRegex(
            issue_centric_transport.IssueCentricBodyDecodeError,
            "decodes to empty text",
        ):
            issue_centric_transport.decode_issue_centric_decision(decision)

    def test_rejects_forbidden_body_before_decode(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue=None,
            close_current_issue=False,
            create_followup_issue=False,
            summary="Forbidden body.",
            issue_body_base64=b64("Should not decode"),
            codex_body_base64=None,
            review_base64=None,
            followup_issue_body_base64=None,
            raw_json="{}",
            raw_segment="segment",
        )
        with self.assertRaisesRegex(
            issue_centric_transport.IssueCentricContractError,
            "no_action must not include body blocks",
        ):
            issue_centric_transport.decode_issue_centric_decision(decision)


class IssueCentricDispatcherPreparedPayloadTests(unittest.TestCase):
    """Tests that the minimal dispatcher returns expected prepared state / artifact per action (#42)."""

    def _decode(
        self,
        action: str,
        target_issue: str,
        *,
        issue_body: str | None = None,
        codex_body: str | None = None,
        review: str | None = None,
        followup_body: str | None = None,
    ) -> issue_centric_transport.PreparedIssueCentricDecision:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction(action),
            target_issue=None if target_issue == "none" else target_issue,
            close_current_issue=False,
            create_followup_issue=False,
            summary="Dispatch payload test.",
            issue_body_base64=b64(issue_body) if issue_body else None,
            codex_body_base64=b64(codex_body) if codex_body else None,
            review_base64=b64(review) if review else None,
            followup_issue_body_base64=b64(followup_body) if followup_body else None,
            raw_json="{}",
            raw_segment="segment",
        )
        return issue_centric_transport.decode_issue_centric_decision(decision)

    # --- issue_create ---

    def test_issue_create_has_issue_body_as_primary(self) -> None:
        prepared = self._decode(
            "issue_create", "none", issue_body="# New issue\n\nBody text."
        )
        self.assertIsNotNone(prepared.issue_body)
        self.assertEqual(
            prepared.issue_body.kind,
            issue_centric_transport.IssueCentricArtifactKind.ISSUE_BODY,
        )
        self.assertIsNone(prepared.codex_body)
        self.assertEqual(prepared.primary_body, prepared.issue_body)
        self.assertEqual(prepared.pending_runtime_action, "issue_create_mutation")

    def test_issue_create_decoded_text_is_accessible(self) -> None:
        prepared = self._decode(
            "issue_create", "none", issue_body="# Title\n\nContent."
        )
        self.assertEqual(prepared.issue_body.decoded_text, "# Title\n\nContent.")

    # --- codex_run ---

    def test_codex_run_has_codex_body_as_primary(self) -> None:
        prepared = self._decode(
            "codex_run", "#42", codex_body="Run this task."
        )
        self.assertIsNotNone(prepared.codex_body)
        self.assertEqual(
            prepared.codex_body.kind,
            issue_centric_transport.IssueCentricArtifactKind.CODEX_BODY,
        )
        self.assertIsNone(prepared.issue_body)
        self.assertEqual(prepared.primary_body, prepared.codex_body)
        self.assertEqual(prepared.pending_runtime_action, "codex_run_dispatch")

    def test_codex_run_decoded_text_is_accessible(self) -> None:
        prepared = self._decode("codex_run", "#42", codex_body="Implement X.\n")
        self.assertEqual(prepared.codex_body.decoded_text, "Implement X.\n")

    # --- no_action ---

    def test_no_action_has_no_primary_body(self) -> None:
        prepared = self._decode("no_action", "none")
        self.assertIsNone(prepared.primary_body)
        self.assertIsNone(prepared.issue_body)
        self.assertIsNone(prepared.codex_body)
        self.assertEqual(prepared.pending_runtime_action, "decision_finalize")

    # --- human_review_needed ---

    def test_human_review_needed_has_review_body_as_primary(self) -> None:
        prepared = self._decode(
            "human_review_needed", "#77", review="## Review\n\nApproved."
        )
        self.assertIsNotNone(prepared.review_body)
        self.assertEqual(
            prepared.review_body.kind,
            issue_centric_transport.IssueCentricArtifactKind.REVIEW,
        )
        self.assertIsNone(prepared.codex_body)
        self.assertEqual(prepared.primary_body, prepared.review_body)
        self.assertEqual(prepared.pending_runtime_action, "human_review_dispatch")

    def test_human_review_without_review_body_has_no_primary(self) -> None:
        prepared = self._decode("human_review_needed", "#77")
        self.assertIsNone(prepared.review_body)
        self.assertIsNone(prepared.primary_body)
        self.assertEqual(prepared.pending_runtime_action, "human_review_dispatch")

    # --- metadata ---

    def test_dispatch_payload_metadata_chars_match_decoded_text(self) -> None:
        body_text = "# Issue title\n\nBody paragraph.\n"
        prepared = self._decode("issue_create", "none", issue_body=body_text)
        meta = prepared.issue_body.payload_metadata()
        self.assertEqual(meta["decoded_text_chars"], len(body_text))
        self.assertIn("decoded_text_sha256", meta)
        self.assertIn("normalized_payload_sha256", meta)


class FetchNextPromptContractStopTests(unittest.TestCase):
    def test_fetch_next_prompt_stops_safely_when_new_contract_is_detected(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "report:1",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "none",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "No further action.",
            }
        )

        saved_states: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            ):
                with self.assertRaisesRegex(
                    BridgeStop,
                    "BODY base64 transport の prepared artifact まで作成しました",
                ):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(len(saved_states), 1)
            saved = saved_states[0]
            self.assertEqual(saved["mode"], "awaiting_user")
            self.assertEqual(saved["chatgpt_decision"], "issue_centric:no_action")
            self.assertEqual(saved["last_issue_centric_action"], "no_action")
            self.assertEqual(saved["last_issue_centric_target_issue"], "none")
            self.assertEqual(saved["last_issue_centric_artifact_file"], "")
            self.assertTrue(str(saved["last_issue_centric_metadata_log"]).endswith(".json"))

    def test_fetch_next_prompt_prepares_codex_artifact_then_stops(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#20",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#20",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Dispatch the prepared Codex body later.",
            },
            parts=[
                block("codex", b64("Prepared Codex body\n")),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": "#20",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Dispatch the prepared Codex body later.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )
        saved_states: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            ):
                with self.assertRaisesRegex(BridgeStop, "artifact: .*prepared_issue_centric_codex_body"):
                    fetch_next_prompt.run(dict(state), [])

            saved = saved_states[0]
            self.assertEqual(saved["last_issue_centric_action"], "codex_run")
            self.assertEqual(saved["last_issue_centric_target_issue"], "#20")
            self.assertEqual(saved["last_issue_centric_artifact_kind"], "codex_body")
            artifact_path = temp_root / Path(str(saved["last_issue_centric_artifact_file"])).name
            self.assertTrue(artifact_path.exists())
            self.assertEqual(artifact_path.read_text(encoding="utf-8"), "Prepared Codex body\n")
            metadata_path = temp_root / Path(str(saved["last_issue_centric_metadata_log"])).name
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(metadata["pending_runtime_action"], "codex_run_dispatch")
            self.assertEqual(metadata["prepared_artifact"]["kind"], "codex_body")


class FetchNextPromptIssueCentricContractParsingTests(unittest.TestCase):
    def _build_near_miss_raw(self) -> str:
        codex_payload = b64("Implement the bounded rehearsal task.\n")
        return build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": 2,
                "summary": "Add rehearsal marker and README completion note.",
            },
            parts=[
                block("codex", codex_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": 2,
                            "summary": "Add rehearsal marker and README completion note.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
            extra_before="短い自然文の前置きです。",
        )

    def test_parse_issue_centric_reply_for_fetch_waits_when_marker_is_absent(self) -> None:
        # No terminal tag → no decision markers → reply_not_ready (not invalid).
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "No contract markers here yet.",
            ]
        )
        with self.assertRaises(fetch_next_prompt.IssueCentricReplyNotReady) as ctx:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(raw, after_text="request body")
        self.assertEqual(ctx.exception.reply_readiness_status, "reply_not_ready")
        self.assertFalse(ctx.exception.reply_complete_tag_present)

    def test_parse_issue_centric_reply_for_fetch_stops_when_marker_is_present_but_invalid(self) -> None:
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#2",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Missing codex body should stay invalid.",
            },
            extra_before="marker はあるが contract は不正です。",
        )
        with self.assertRaises(fetch_next_prompt.IssueCentricReplyInvalid) as cm:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(raw, after_text="request body")
        self.assertIn("requires CHATGPT_CODEX_BODY", str(cm.exception))

    def test_parse_issue_centric_reply_for_fetch_accepts_near_miss_contract(self) -> None:
        decision = fetch_next_prompt.parse_issue_centric_reply_for_fetch(
            self._build_near_miss_raw(),
            after_text="request body",
        )
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.CODEX_RUN)
        self.assertEqual(decision.target_issue, "2")
        self.assertFalse(decision.close_current_issue)
        self.assertFalse(decision.create_followup_issue)

    def test_validate_ready_issue_target_binding_accepts_matching_target(self) -> None:
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.NO_ACTION,
            target_issue="#8",
            close_current_issue=True,
            create_followup_issue=False,
            summary="close the current ready issue",
            issue_body_base64=None,
            codex_body_base64=None,
            review_base64=None,
            followup_issue_body_base64=None,
            raw_json="{}",
            raw_segment="segment",
        )
        with patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo"}):
            error = fetch_next_prompt._validate_ready_issue_target_binding(
                decision,
                state={"current_ready_issue_ref": "#8 Ready: verify parent update comment after narrow child close"},
                pending_request_source="ready_issue:hash",
            )
        self.assertIsNone(error)

    def test_fetch_run_stops_immediately_for_invalid_issue_centric_contract(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#2",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
            # exhausted correction retries so stop_for_invalid_issue_centric_contract is reached
            "last_issue_centric_contract_correction_count": 2,
        }
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#2",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Still invalid because the codex body is missing.",
            },
            extra_before="marker は見つかったが contract が不正です。",
        )
        saved_states: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            ):
                with self.assertRaisesRegex(BridgeError, "issue-centric contract reply が不正でした"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(len(saved_states), 1)
            saved = saved_states[0]
            self.assertEqual(saved["mode"], "awaiting_user")
            self.assertTrue(bool(saved["error"]))
            self.assertEqual(saved["chatgpt_decision"], "issue_centric_invalid_contract")
            self.assertIn("raw_chatgpt_prompt_dump", str(saved["error_message"]))
            self.assertIn("invalid_issue_centric_contract", str(saved["error_message"]))

    def test_fetch_run_rejects_stale_target_issue_for_ready_issue_request(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#8",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "current_ready_issue_ref": "#8 Ready: verify parent update comment after narrow child close",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
            # exhausted correction retries so stop_for_invalid_issue_centric_contract is reached
            "last_issue_centric_contract_correction_count": 2,
        }
        raw = build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "#7",
                "close_current_issue": True,
                "create_followup_issue": False,
                "summary": "stale issue should be rejected",
            },
            extra_before="current ready issue と違う stale target が返っています。",
        )
        saved_states: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo"}),
                patch.object(fetch_next_prompt, "dispatch_issue_centric_execution", side_effect=AssertionError("dispatch should not run")),
            ):
                with self.assertRaisesRegex(BridgeError, "issue-centric contract reply が不正でした"):
                    fetch_next_prompt.run(dict(state), [])

            self.assertEqual(len(saved_states), 1)
            saved = saved_states[0]
            self.assertEqual(saved["mode"], "awaiting_user")
            self.assertTrue(bool(saved["error"]))
            self.assertEqual(saved["chatgpt_decision"], "issue_centric_invalid_contract")
            self.assertIn("#8", str(saved["chatgpt_decision_note"]))
            self.assertIn("#7", str(saved["chatgpt_decision_note"]))


class PlanAFetchPrimaryPathTests(unittest.TestCase):
    """Tests for Plan A BODY base64 transport as the primary fetch path.

    Verifies that:
    - wait_for_issue_centric_reply_text succeeds on Plan A contract reply (IC-only path)
    - fetch_next_prompt.run() passes a plan_a_extractor to wait_for_issue_centric_reply_text
    - fetch_next_prompt.run() raises BridgeStop for legacy visible-text replies (explicit stop)
    """

    def _build_plan_a_only_raw(self) -> str:
        """Build a raw reply containing only a Plan A contract (no visible-text markers)."""
        envelope = {
            "action": "no_action",
            "target_issue": "none",
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": "Plan A only reply.",
        }
        return build_raw_reply(envelope, after_text="request body")

    def _build_visible_dom_only_raw(self) -> str:
        """Build a raw reply containing only a visible DOM text (CHATGPT_PROMPT_REPLY) -- no Plan A contract."""
        return "\n".join([
            "あなた:",
            "request body",
            "ChatGPT:",
            "===CHATGPT_PROMPT_REPLY===",
            "## Codex prompt body\n",
            "===END_REPLY===",
        ])

    def test_plan_a_extractor_succeeds_when_contract_present(self) -> None:
        """plan_a_extractor must not raise when a valid Plan A contract is present."""
        import _bridge_common as bc

        raw = self._build_plan_a_only_raw()

        def plan_a_extractor(r: str, after: str | None) -> None:
            result = issue_centric_contract.maybe_parse_issue_centric_reply(r, after_text=after)
            if result is None:
                raise BridgeError("not found")

        # Must not raise -- Plan A contract is present
        plan_a_extractor(raw, "request body")

    def test_plan_a_extractor_raises_when_contract_absent(self) -> None:
        """plan_a_extractor must raise BridgeError when no Plan A contract is present."""
        raw = self._build_visible_dom_only_raw()

        def plan_a_extractor(r: str, after: str | None) -> None:
            result = issue_centric_contract.maybe_parse_issue_centric_reply(r, after_text=after)
            if result is None:
                raise BridgeError("not found")

        with self.assertRaises(BridgeError):
            plan_a_extractor(raw, "request body")

    def test_fetch_run_uses_wait_for_issue_centric_reply_text(self) -> None:
        """fetch_next_prompt.run() must call wait_for_issue_centric_reply_text (not wait_for_prompt_reply_text)."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "hash1",
            "pending_request_source": "report:f",
            "pending_request_log": "logs/r.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = self._build_plan_a_only_raw()
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            wait_mock = MagicMock(return_value=raw)
            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", wait_mock),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: None),
            ):
                try:
                    fetch_next_prompt.run(dict(state), [])
                except Exception:
                    pass

            # Must have called wait_for_issue_centric_reply_text with plan_a_extractor kwarg
            self.assertTrue(wait_mock.called)
            call_kwargs = wait_mock.call_args.kwargs
            self.assertIn("plan_a_extractor", call_kwargs)
            self.assertTrue(callable(call_kwargs["plan_a_extractor"]))

    def test_fetch_run_stops_explicitly_when_legacy_reply_detected(self) -> None:
        """When only a legacy CHATGPT_PROMPT_REPLY is present, fetch_next_prompt.run() raises BridgeStop (explicit stop)."""
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "hash2",
            "pending_request_source": "report:g",
            "pending_request_log": "logs/r.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        raw = self._build_visible_dom_only_raw()
        saved_states: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)

            def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
                path = temp_root / f"{prefix}.{suffix}"
                path.write_text(text, encoding="utf-8")
                return path

            with (
                patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
                patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "write_text", side_effect=lambda p, t: None),
            ):
                with self.assertRaises(BridgeStop) as cm:
                    fetch_next_prompt.run(dict(state), [])

            # State must reflect the explicit legacy stop (error, not success)
            self.assertTrue(len(saved_states) > 0)
            saved = saved_states[-1]
            self.assertEqual(saved["mode"], "awaiting_user")
            self.assertTrue(saved.get("error"))
            self.assertIn("legacy", str(cm.exception).lower())


class IssueCentricReplyWaitTests(unittest.TestCase):
    """Tests for wait_for_issue_centric_reply_text() and the IC reply readiness classifier.

    Also covers the legacy visible-text detect-only safety net:
    legacy markers are classified and trigger an explicit stop — there is no
    legacy success path.  Normal operation never reaches the legacy gate.
    """
    def test_classifies_thinking_reply_as_not_ready(self) -> None:
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "",
                "思考中",
                "",
                "じっくり思考",
                "",
                "ChatGPT の回答は必ずしも正しいとは限りません。重要な情報は確認するようにしてください。cookie の設定を参照してください。",
            ]
        )

        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw,
            after_text="request body",
        )

        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_text_present)
        self.assertTrue(readiness.thinking_visible)
        self.assertFalse(readiness.decision_marker_present)
        self.assertFalse(readiness.contract_parse_attempted)

    def test_parse_for_fetch_raises_not_ready_for_thinking_reply(self) -> None:
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "",
                "思考中",
                "",
                "じっくり思考",
            ]
        )

        with self.assertRaises(fetch_next_prompt.IssueCentricReplyNotReady) as ctx:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(raw, after_text="request body")

        self.assertEqual(ctx.exception.reply_readiness_status, "reply_not_ready")
        self.assertTrue(ctx.exception.thinking_visible)
        self.assertFalse(ctx.exception.decision_marker_present)

    def test_classifies_completed_reply_without_marker_as_invalid_stop(self) -> None:
        # No terminal tag → reply_not_ready regardless of content (terminal tag
        # gate takes precedence over UI-state inference).
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "",
                "了解しました。次の変更を進めます。",
            ]
        )

        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw,
            after_text="request body",
        )

        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_text_present)
        self.assertFalse(readiness.thinking_visible)
        self.assertFalse(readiness.reply_complete_tag_present)

    def test_classifies_invalid_contract_when_decision_json_is_broken(self) -> None:
        # Terminal tag present + broken JSON → reply_complete_invalid_contract.
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.DECISION_JSON_START,
                "not json",
                issue_centric_contract.DECISION_JSON_END,
                issue_centric_contract.REPLY_COMPLETE_TAG,
            ]
        )

        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw,
            after_text="request body",
        )

        self.assertEqual(readiness.status, "reply_complete_invalid_contract")
        self.assertTrue(readiness.decision_marker_present)
        self.assertTrue(readiness.contract_parse_attempted)
        self.assertTrue(readiness.reply_complete_tag_present)

    def test_classifies_valid_contract_and_returns_decision(self) -> None:
        codex_payload = b64("Codex body")
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#123",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Run Codex.",
            },
            parts=[
                block("codex", codex_payload),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": "#123",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Run Codex.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )

        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw,
            after_text="request body",
        )

        self.assertEqual(readiness.status, "reply_complete_valid_contract")
        self.assertIsNotNone(readiness.decision)
        self.assertEqual(
            readiness.decision.action,
            issue_centric_contract.IssueCentricAction.CODEX_RUN,
        )

    def test_legacy_visible_text_reply_classified_and_stopped(self) -> None:
        """Legacy markers are detect-only: classified as reply_complete_legacy_contract,
        never routed through a success path.  parse_issue_centric_reply_for_fetch()
        raises BridgeError when called directly with such a reply (run() stops
        earlier via an explicit BridgeStop before this function is reached).
        """
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "===CHATGPT_PROMPT_REPLY===",
                "Next phase prompt",
                "===END_REPLY===",
            ]
        )

        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw,
            after_text="request body",
        )

        self.assertEqual(readiness.status, "reply_complete_legacy_contract")
        with self.assertRaises(BridgeError):
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(raw, after_text="request body")

    def test_wait_continues_when_reply_is_still_thinking(self) -> None:
        raw_not_ready = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "",
                "思考中",
                "",
                "じっくり思考",
            ]
        )
        raw_valid = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#2",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Run Codex.",
            },
            parts=[
                block("codex", b64("Codex body")),
                block(
                    "json",
                    json.dumps(
                        {
                            "action": "codex_run",
                            "target_issue": "#2",
                            "close_current_issue": False,
                            "create_followup_issue": False,
                            "summary": "Run Codex.",
                        },
                        ensure_ascii=True,
                    ),
                ),
            ],
        )

        class _DummyPage:
            def __init__(self) -> None:
                self.wait_calls = 0

            def wait_for_timeout(self, _: int) -> None:
                self.wait_calls += 1

        page = _DummyPage()
        events: list[bridge_common.ChatGPTWaitEvent] = []

        @contextmanager
        def fake_open_chatgpt_page(**_: object):
            yield None, page, {"poll_interval_seconds": 0}, {"url": "https://chatgpt.com/c/demo", "title": "ChatGPT"}

        with (
            patch.object(bridge_common, "open_chatgpt_page", fake_open_chatgpt_page),
            patch.object(
                bridge_common,
                "read_chatgpt_conversation_dom",
                side_effect=[raw_not_ready, raw_valid],
            ),
        ):
            result = bridge_common.wait_for_issue_centric_reply_text(
                plan_a_extractor=(
                    lambda raw_text, after_text: fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                        raw_text,
                        after_text=after_text,
                    )
                ),
                request_text="request body",
                stage_callback=events.append,
            )

        self.assertEqual(result, raw_valid)
        self.assertEqual(page.wait_calls, 1)
        self.assertTrue(events)
        self.assertEqual(events[0].name, "reply_not_ready")
        self.assertEqual(events[0].details["reply_readiness_status"], "reply_not_ready")
        self.assertTrue(events[0].details["thinking_visible"])

    def test_propagates_non_bridgeerror_from_plan_a_extractor(self) -> None:
        raw = build_raw_reply(
            {
                "action": "codex_run",
                "target_issue": "#2",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "Missing codex body should surface as invalid.",
            }
        )

        def fake_wait_for_chatgpt_reply_text(**kwargs: object) -> str:
            extractor = kwargs["extractor"]
            return extractor(raw, "request body")

        def plan_a_extractor(_: str, __: str | None) -> None:
            raise fetch_next_prompt.IssueCentricReplyInvalid(
                "requires CHATGPT_CODEX_BODY",
                raw_text=raw,
            )

        with patch.object(
            bridge_common,
            "_wait_for_chatgpt_reply_text",
            side_effect=fake_wait_for_chatgpt_reply_text,
        ):
            with self.assertRaises(fetch_next_prompt.IssueCentricReplyInvalid):
                bridge_common.wait_for_issue_centric_reply_text(
                    plan_a_extractor=plan_a_extractor,
                    request_text="request body",
                )


class MetaOnlyReplyNotReadyTests(unittest.TestCase):
    """Verify that meta-only UI labels in the assistant area are classified as
    reply_not_ready and never trigger an invalid stop."""

    def _make_raw(self, *assistant_lines: str) -> str:
        return "\n".join(
            ["あなた:", "request body", "ChatGPT:", ""] + list(assistant_lines)
        )

    # ------------------------------------------------------------------
    # classify_issue_centric_reply_readiness
    # ------------------------------------------------------------------

    def test_thought_for_39s_is_not_ready(self) -> None:
        raw = self._make_raw("Thought for 39s", "じっくり思考", "GitHub")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_text_present)
        self.assertTrue(readiness.assistant_meta_only)
        self.assertFalse(readiness.assistant_final_content_present)

    def test_thought_for_120_seconds_is_not_ready(self) -> None:
        raw = self._make_raw("Thought for 120 seconds", "GitHub")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)
        self.assertFalse(readiness.assistant_final_content_present)

    def test_github_pill_only_is_not_ready(self) -> None:
        raw = self._make_raw("GitHub")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)

    def test_deep_research_label_is_not_ready(self) -> None:
        raw = self._make_raw("Deep research")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)

    def test_thinking_marker_only_is_not_ready(self) -> None:
        raw = self._make_raw("思考中")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)

    def test_actual_reply_body_with_no_marker_is_invalid_stop(self) -> None:
        # No terminal tag → reply_not_ready (terminal tag gate supersedes
        # content-presence inference).
        raw = self._make_raw("了解しました。次の変更を進めます。")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_final_content_present)
        self.assertFalse(readiness.assistant_meta_only)
        self.assertFalse(readiness.reply_complete_tag_present)

    def test_mixed_meta_and_content_lines_is_invalid_stop(self) -> None:
        # No terminal tag → reply_not_ready (terminal tag gate supersedes
        # content-presence inference even when content lines are present).
        raw = self._make_raw("Thought for 5s", "了解しました。次の変更を進めます。")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_final_content_present)
        self.assertFalse(readiness.assistant_meta_only)
        self.assertFalse(readiness.reply_complete_tag_present)

    def test_assistant_meta_only_flag_set_correctly(self) -> None:
        raw_meta = self._make_raw("Thought for 1s", "GitHub")
        r = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw_meta, after_text="request body"
        )
        self.assertTrue(r.assistant_meta_only)
        self.assertFalse(r.assistant_final_content_present)

        raw_content = self._make_raw("Here is the plan.")
        r2 = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw_content, after_text="request body"
        )
        self.assertFalse(r2.assistant_meta_only)
        self.assertTrue(r2.assistant_final_content_present)

    # ------------------------------------------------------------------
    # parse_issue_centric_reply_for_fetch raises IssueCentricReplyNotReady
    # ------------------------------------------------------------------

    def test_parse_for_fetch_raises_not_ready_for_thought_for_seconds(self) -> None:
        raw = self._make_raw("Thought for 39s", "じっくり思考", "GitHub")
        with self.assertRaises(fetch_next_prompt.IssueCentricReplyNotReady) as ctx:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )
        self.assertEqual(ctx.exception.reply_readiness_status, "reply_not_ready")
        self.assertTrue(ctx.exception.assistant_meta_only)
        self.assertFalse(ctx.exception.assistant_final_content_present)

    def test_parse_for_fetch_does_not_raise_invalid_for_meta_only(self) -> None:
        raw = self._make_raw("GitHub", "Thought for 5s")
        try:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )
            self.fail("expected IssueCentricReplyNotReady")
        except fetch_next_prompt.IssueCentricReplyNotReady:
            pass  # correct
        except fetch_next_prompt.IssueCentricReplyInvalid:
            self.fail("meta-only dump must not raise IssueCentricReplyInvalid")


class PartialBodyBlockNotReadyTests(unittest.TestCase):
    """Verify that a partial issue-centric body block (start marker present,
    end marker absent) is classified as reply_not_ready and never triggers
    an invalid stop."""

    _DECISION_COMPLETE = "\n".join(
        [
            "===CHATGPT_DECISION_JSON===",
            '{"action":"codex_run","target_issue":"#3","summary":"test"}',
            "===END_DECISION_JSON===",
        ]
    )

    def _make_raw(self, *assistant_lines: str) -> str:
        return "\n".join(
            ["あなた:", "request body", "ChatGPT:", ""] + list(assistant_lines)
        )

    # ------------------------------------------------------------------
    # _detect_partial_body_blocks
    # ------------------------------------------------------------------

    def test_detect_open_codex_body_block(self) -> None:
        segment = "===CHATGPT_CODEX_BODY===\naGVsbG8="
        open_b, closed_b = fetch_next_prompt._detect_partial_body_blocks(segment)
        self.assertIn("===CHATGPT_CODEX_BODY===", open_b)
        self.assertEqual(closed_b, [])

    def test_detect_closed_codex_body_block(self) -> None:
        segment = "===CHATGPT_CODEX_BODY===\naGVsbG8=\n===END_CODEX_BODY==="
        open_b, closed_b = fetch_next_prompt._detect_partial_body_blocks(segment)
        self.assertEqual(open_b, [])
        self.assertIn("===CHATGPT_CODEX_BODY===", closed_b)

    def test_detect_open_issue_body_block(self) -> None:
        segment = "===CHATGPT_ISSUE_BODY===\naGVsbG8="
        open_b, closed_b = fetch_next_prompt._detect_partial_body_blocks(segment)
        self.assertIn("===CHATGPT_ISSUE_BODY===", open_b)
        self.assertEqual(closed_b, [])

    def test_detect_open_review_body_block(self) -> None:
        segment = "===CHATGPT_REVIEW===\naGVsbG8="
        open_b, closed_b = fetch_next_prompt._detect_partial_body_blocks(segment)
        self.assertIn("===CHATGPT_REVIEW===", open_b)
        self.assertEqual(closed_b, [])

    def test_detect_no_body_blocks(self) -> None:
        segment = "===CHATGPT_DECISION_JSON===\n{}\n===END_DECISION_JSON==="
        open_b, closed_b = fetch_next_prompt._detect_partial_body_blocks(segment)
        self.assertEqual(open_b, [])
        self.assertEqual(closed_b, [])

    # ------------------------------------------------------------------
    # classify_issue_centric_reply_readiness — partial CODEX_BODY
    # ------------------------------------------------------------------

    def test_partial_codex_body_is_not_ready(self) -> None:
        """DECISION_JSON complete + CODEX_BODY start only → reply_not_ready."""
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_CODEX_BODY===",
            "aGVsbG8=",
            "じっくり思考",
            "GitHub",
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.partial_body_block_detected)
        self.assertTrue(readiness.body_block_start_present)
        self.assertFalse(readiness.body_block_end_present)
        self.assertIn("===CHATGPT_CODEX_BODY===", readiness.open_body_blocks)

    def test_partial_codex_body_partial_body_block_detected_flag(self) -> None:
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_CODEX_BODY===",
            "dGVzdA==",
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertTrue(readiness.partial_body_block_detected)
        self.assertFalse(readiness.body_block_end_present)

    def test_partial_codex_body_does_not_trigger_invalid_stop(self) -> None:
        """A partial CODEX_BODY must not be classified as reply_complete_invalid_contract."""
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_CODEX_BODY===",
            "aGVsbG8=",
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertNotEqual(readiness.status, "reply_complete_invalid_contract")

    def test_partial_issue_body_is_not_ready(self) -> None:
        """DECISION_JSON complete + ISSUE_BODY start only → reply_not_ready."""
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_ISSUE_BODY===",
            "cGFydGlhbA==",
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.partial_body_block_detected)
        self.assertIn("===CHATGPT_ISSUE_BODY===", readiness.open_body_blocks)

    def test_partial_review_body_is_not_ready(self) -> None:
        """DECISION_JSON complete + REVIEW start only → reply_not_ready."""
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_REVIEW===",
            "cmV2aWV3",
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.partial_body_block_detected)

    def test_completed_codex_body_still_goes_to_parse(self) -> None:
        """Complete CODEX_BODY block pairing + terminal tag must pass through to contract parse."""
        import base64
        codex_payload = base64.b64encode(b"do the thing").decode()
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_CODEX_BODY===",
            codex_payload,
            "===END_CODEX_BODY===",
            issue_centric_contract.REPLY_COMPLETE_TAG,
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        # Must not be partial; contract parse should be attempted.
        self.assertFalse(readiness.partial_body_block_detected)
        self.assertTrue(readiness.body_block_start_present)
        self.assertTrue(readiness.body_block_end_present)
        self.assertTrue(readiness.reply_complete_tag_present)
        # If parse succeeded: valid; if parse failed: invalid. Either is fine
        # as long as partial_body_block_detected is False.
        self.assertIn(
            readiness.status,
            ["reply_complete_valid_contract", "reply_complete_invalid_contract"],
        )

    # ------------------------------------------------------------------
    # parse_issue_centric_reply_for_fetch — partial CODEX_BODY
    # ------------------------------------------------------------------

    def test_parse_for_fetch_raises_not_ready_for_partial_codex_body(self) -> None:
        """parse_for_fetch must raise IssueCentricReplyNotReady, not Invalid."""
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_CODEX_BODY===",
            "aGVsbG8=",
            "じっくり思考",
            "GitHub",
        )
        with self.assertRaises(fetch_next_prompt.IssueCentricReplyNotReady) as ctx:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )
        self.assertEqual(ctx.exception.reply_readiness_status, "reply_not_ready")
        self.assertTrue(ctx.exception.partial_body_block_detected)

    def test_parse_for_fetch_does_not_raise_invalid_for_partial_codex_body(self) -> None:
        """Partial CODEX_BODY must never raise IssueCentricReplyInvalid."""
        raw = self._make_raw(
            self._DECISION_COMPLETE,
            "===CHATGPT_CODEX_BODY===",
            "aGVsbG8=",
        )
        try:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )
            self.fail("expected IssueCentricReplyNotReady")
        except fetch_next_prompt.IssueCentricReplyNotReady:
            pass  # correct
        except fetch_next_prompt.IssueCentricReplyInvalid:
            self.fail("partial body block must not raise IssueCentricReplyInvalid")

    # ------------------------------------------------------------------
    # confirm meta-only and no-marker paths are still intact
    # ------------------------------------------------------------------

    def test_meta_only_still_not_ready(self) -> None:
        raw = self._make_raw("じっくり思考", "GitHub")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertFalse(readiness.partial_body_block_detected)

    def test_completed_no_marker_still_invalid_stop(self) -> None:
        # No terminal tag → reply_not_ready (terminal tag gate).
        raw = self._make_raw("This is a complete reply with no markers at all.")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertFalse(readiness.partial_body_block_detected)
        self.assertFalse(readiness.reply_complete_tag_present)


class RunningAppMetaOnlyTests(unittest.TestCase):
    """Verify that tool-call status labels (Running app request / response,
    Received app response) are treated as meta-only UI labels and never
    trigger an invalid stop."""

    def _make_raw(self, *assistant_lines: str) -> str:
        return "\n".join(
            ["あなた:", "request body", "ChatGPT:", ""] + list(assistant_lines)
        )

    # ------------------------------------------------------------------
    # classify_issue_centric_reply_readiness
    # ------------------------------------------------------------------

    def test_running_app_request_alone_is_not_ready(self) -> None:
        raw = self._make_raw("Running app request")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)
        self.assertFalse(readiness.assistant_final_content_present)

    def test_running_app_response_alone_is_not_ready(self) -> None:
        raw = self._make_raw("Running app response")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)

    def test_received_app_response_alone_is_not_ready(self) -> None:
        raw = self._make_raw("Received app response")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)

    def test_running_app_request_with_thinking_is_not_ready(self) -> None:
        """Running app request + じっくり思考 → reply_not_ready."""
        raw = self._make_raw("Running app request", "じっくり思考")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.assistant_meta_only)
        self.assertFalse(readiness.assistant_final_content_present)

    def test_running_app_with_real_content_is_not_meta_only(self) -> None:
        """Running app request + real sentence → final content present."""
        raw = self._make_raw(
            "Running app request",
            "This is actual reply content from ChatGPT.",
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertFalse(readiness.assistant_meta_only)
        self.assertTrue(readiness.assistant_final_content_present)

    def test_completed_no_marker_still_invalid_stop(self) -> None:
        """No terminal tag → reply_not_ready (terminal tag gate supersedes content-presence)."""
        raw = self._make_raw("This is a complete reply with no markers at all.")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertFalse(readiness.reply_complete_tag_present)

    # ------------------------------------------------------------------
    # parse_issue_centric_reply_for_fetch
    # ------------------------------------------------------------------

    def test_parse_for_fetch_raises_not_ready_for_running_app_request(self) -> None:
        raw = self._make_raw("Running app request", "じっくり思考")
        with self.assertRaises(fetch_next_prompt.IssueCentricReplyNotReady) as ctx:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )
        self.assertEqual(ctx.exception.reply_readiness_status, "reply_not_ready")
        self.assertTrue(ctx.exception.assistant_meta_only)

    def test_parse_for_fetch_does_not_raise_invalid_for_running_app(self) -> None:
        """Running app labels must never raise IssueCentricReplyInvalid."""
        raw = self._make_raw("Running app request", "Received app response")
        try:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )
            self.fail("expected IssueCentricReplyNotReady")
        except fetch_next_prompt.IssueCentricReplyNotReady:
            pass  # correct
        except fetch_next_prompt.IssueCentricReplyInvalid:
            self.fail("tool-call labels must not raise IssueCentricReplyInvalid")

    # ------------------------------------------------------------------
    # partial body block path is not disrupted
    # ------------------------------------------------------------------

    def test_partial_body_block_still_not_ready(self) -> None:
        """Partial CODEX_BODY with Running app label still not-ready."""
        decision = "\n".join([
            "===CHATGPT_DECISION_JSON===",
            '{"action":"codex_run","target_issue":"#3","summary":"t"}',
            "===END_DECISION_JSON===",
        ])
        raw = self._make_raw(decision, "===CHATGPT_CODEX_BODY===", "Running app request")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertTrue(readiness.partial_body_block_detected)


class ReplyCompleteTagGateTests(unittest.TestCase):
    """Verify the primary terminal tag gate behaviour.

    The bridge must not attempt parse / validate until
    ===CHATGPT_REPLY_COMPLETE=== appears at the end of the assistant turn.
    """

    _COMPLETE = issue_centric_contract.REPLY_COMPLETE_TAG

    _VALID_DECISION = "\n".join([
        "===CHATGPT_DECISION_JSON===",
        '{"action":"no_action","target_issue":"none","close_current_issue":false,"create_followup_issue":false,"summary":"ok"}',
        "===END_DECISION_JSON===",
    ])

    def _make_raw(self, *assistant_lines: str) -> str:
        return "\n".join(
            ["あなた:", "request body", "ChatGPT:", ""] + list(assistant_lines)
        )

    # ------------------------------------------------------------------
    # 1. 完了タグなし + meta-only text → reply_not_ready
    # ------------------------------------------------------------------

    def test_no_tag_meta_only_thought_for_seconds_is_not_ready(self) -> None:
        """完了タグなし + Thought for 39s / じっくり思考 / GitHub → reply_not_ready."""
        raw = self._make_raw("Thought for 39s", "じっくり思考", "GitHub")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertFalse(readiness.reply_complete_tag_present)
        self.assertTrue(readiness.assistant_meta_only)
        self.assertFalse(readiness.assistant_final_content_present)

    # ------------------------------------------------------------------
    # 2. 完了タグなし + partial CODEX_BODY → reply_not_ready
    # ------------------------------------------------------------------

    def test_no_tag_partial_codex_body_is_not_ready(self) -> None:
        """完了タグなし + partial CODEX_BODY → reply_not_ready."""
        raw = self._make_raw(
            self._VALID_DECISION,
            "===CHATGPT_CODEX_BODY===",
            "aGVsbG8=",
        )
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertFalse(readiness.reply_complete_tag_present)
        self.assertTrue(readiness.partial_body_block_detected)

    # ------------------------------------------------------------------
    # 3. 完了タグあり + 正常な issue-centric contract → valid
    # ------------------------------------------------------------------

    def test_with_tag_valid_no_action_contract_is_valid(self) -> None:
        """完了タグあり + 正常な no_action contract → reply_complete_valid_contract."""
        raw = self._make_raw(self._VALID_DECISION, self._COMPLETE)
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_complete_valid_contract")
        self.assertTrue(readiness.reply_complete_tag_present)
        self.assertIsNotNone(readiness.decision)

    def test_with_tag_valid_contract_parse_returns_decision(self) -> None:
        """完了タグあり + valid → parse_issue_centric_reply_for_fetch が decision を返す."""
        raw = self._make_raw(self._VALID_DECISION, self._COMPLETE)
        decision = fetch_next_prompt.parse_issue_centric_reply_for_fetch(
            raw, after_text="request body"
        )
        self.assertEqual(decision.action, issue_centric_contract.IssueCentricAction.NO_ACTION)

    # ------------------------------------------------------------------
    # 4. 完了タグあり + DECISION_JSON 欠落 → invalid
    # ------------------------------------------------------------------

    def test_with_tag_missing_decision_json_is_invalid(self) -> None:
        """完了タグあり + DECISION_JSON なし → reply_complete_no_marker."""
        raw = self._make_raw("This is the reply text.", self._COMPLETE)
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_complete_no_marker")
        self.assertTrue(readiness.reply_complete_tag_present)
        self.assertFalse(readiness.decision_marker_present)

    def test_with_tag_missing_decision_json_raises_invalid(self) -> None:
        """完了タグあり + DECISION_JSON なし → parse_for_fetch は IssueCentricReplyInvalid を raise."""
        raw = self._make_raw("This is the reply text.", self._COMPLETE)
        with self.assertRaises(fetch_next_prompt.IssueCentricReplyInvalid):
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )

    # ------------------------------------------------------------------
    # 5. 途中メタ表示 (Running app / Received app) + 完了タグなし → not invalid
    # ------------------------------------------------------------------

    def test_no_tag_running_app_request_is_not_ready_not_invalid(self) -> None:
        """Running app request + 完了タグなし → reply_not_ready, never invalid."""
        raw = self._make_raw("Running app request", "じっくり思考")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertFalse(readiness.reply_complete_tag_present)
        # Must not be any kind of reply_complete_*
        self.assertFalse(readiness.status.startswith("reply_complete"))

    def test_no_tag_received_app_response_is_not_ready(self) -> None:
        """Received app response + 完了タグなし → reply_not_ready."""
        raw = self._make_raw("Received app response", "Thought for 1m 10s")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_not_ready")
        self.assertFalse(readiness.reply_complete_tag_present)

    def test_no_tag_running_app_never_raises_invalid(self) -> None:
        """Running app labels + 完了タグなし → IssueCentricReplyNotReady, never IssueCentricReplyInvalid."""
        raw = self._make_raw("Running app request", "Running app response", "Received app response")
        try:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(
                raw, after_text="request body"
            )
            self.fail("expected IssueCentricReplyNotReady")
        except fetch_next_prompt.IssueCentricReplyNotReady:
            pass  # correct
        except fetch_next_prompt.IssueCentricReplyInvalid:
            self.fail("tool-call labels without terminal tag must not raise IssueCentricReplyInvalid")


class Base64WhitespaceToleranceTests(unittest.TestCase):
    """Whitespace inside base64 payload blocks must be tolerated.

    LLM responses sometimes insert spaces or newlines inside a base64 block.
    The contract and transport layers must strip whitespace before validation,
    while truly invalid payloads (non-base64 characters) still raise errors.
    """

    def _envelope(self, action: str = "codex_run") -> dict[str, object]:
        if action == "issue_create":
            return {
                "action": "issue_create",
                "target_issue": "#1 test",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "ws tolerance test",
            }
        return {
            "action": "codex_run",
            "target_issue": "#1 test",
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": "ws tolerance test",
        }

    def test_codex_body_with_intra_line_spaces_is_accepted(self) -> None:
        """CHATGPT_CODEX_BODY with spaces inside a line must parse successfully."""
        clean = b64("hello world task")
        # Insert a space in the middle of the base64 string
        spaced = clean[:8] + " " + clean[8:]
        raw = build_raw_reply(
            self._envelope(),
            parts=[
                block("codex", spaced),
                block("json", json.dumps(self._envelope(), ensure_ascii=True)),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertIsNotNone(decision)
        assert decision is not None
        decoded = base64.b64decode(decision.codex_body_base64 + "==", validate=False)
        self.assertEqual(decoded.decode("utf-8"), "hello world task")

    def test_codex_body_with_newlines_inside_block_is_accepted(self) -> None:
        """CHATGPT_CODEX_BODY with extra newlines inside the block must parse."""
        clean = b64("hello world task")
        # Split the base64 string across multiple lines (LLM word-wrap behavior)
        split = clean[:6] + "\n" + clean[6:12] + "\n" + clean[12:]
        raw = build_raw_reply(
            self._envelope(),
            parts=[
                block("codex", split),
                block("json", json.dumps(self._envelope(), ensure_ascii=True)),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertIsNotNone(decision)

    def test_codex_body_with_tabs_inside_block_is_accepted(self) -> None:
        """CHATGPT_CODEX_BODY with tab characters inside must parse."""
        clean = b64("tabbed content")
        tabbed = clean[:4] + "\t" + clean[4:]
        raw = build_raw_reply(
            self._envelope(),
            parts=[
                block("codex", tabbed),
                block("json", json.dumps(self._envelope(), ensure_ascii=True)),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertIsNotNone(decision)

    def test_issue_body_with_intra_line_spaces_is_accepted(self) -> None:
        """CHATGPT_ISSUE_BODY with spaces inside a line must parse successfully."""
        clean = b64("issue body content")
        spaced = clean[:5] + " " + clean[5:]
        raw = build_raw_reply(
            self._envelope("issue_create"),
            parts=[
                block("issue", spaced),
                block("json", json.dumps(self._envelope("issue_create"), ensure_ascii=True)),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertIsNotNone(decision)

    def test_truly_invalid_payload_still_raises(self) -> None:
        """Non-base64 characters (e.g. '!', '@') still raise IssueCentricContractError."""
        invalid_payload = "!!not-valid-base64!!"
        raw = build_raw_reply(
            self._envelope(),
            parts=[
                block("codex", invalid_payload),
                block("json", json.dumps(self._envelope(), ensure_ascii=True)),
            ],
        )
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError, "not valid base64"
        ):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_normalize_base64_payload_removes_all_whitespace(self) -> None:
        """_normalize_base64_payload (via extract_body_block) removes space, tab, CR, LF."""
        from issue_centric_contract import _normalize_base64_payload
        clean = b64("whitespace test")
        dirty = " " + clean[:4] + "\t" + clean[4:8] + "\r\n" + clean[8:]
        result = _normalize_base64_payload(dirty, name="TEST")
        self.assertEqual(result, clean)


class FollowupIssueBodyFallbackTests(unittest.TestCase):
    """issue_create + create_followup_issue=True + CHATGPT_FOLLOWUP_ISSUE_BODY (no CHATGPT_ISSUE_BODY).

    Root cause: _validate_decision() required CHATGPT_ISSUE_BODY unconditionally for
    issue_create, even when create_followup_issue=True and CHATGPT_FOLLOWUP_ISSUE_BODY
    was provided. The fix: allow issue_body_base64=None when create_followup_issue=True
    and followup_issue_body_base64 is present.
    """

    def _followup_envelope(self) -> dict[str, object]:
        return {
            "action": "issue_create",
            "target_issue": "#1 PromptWeave",
            "close_current_issue": False,
            "create_followup_issue": True,
            "summary": "Create child issue via followup body",
        }

    def test_issue_create_followup_body_only_is_accepted(self) -> None:
        """issue_create + create_followup_issue=True + CHATGPT_FOLLOWUP_ISSUE_BODY only → accepted."""
        envelope = self._followup_envelope()
        followup_b64 = b64("# Child Issue\n\nBody of the follow-up issue.")
        raw = build_raw_reply(
            envelope,
            parts=[
                block("followup", followup_b64),
                block("json", json.dumps(envelope, ensure_ascii=True)),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertIsNone(decision.issue_body_base64)
        self.assertEqual(decision.followup_issue_body_base64, followup_b64)
        self.assertTrue(decision.create_followup_issue)

    def test_issue_create_issue_body_only_still_accepted(self) -> None:
        """issue_create + CHATGPT_ISSUE_BODY only (no create_followup_issue) → still accepted."""
        envelope: dict[str, object] = {
            "action": "issue_create",
            "target_issue": "#2 Other",
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": "Normal issue create",
        }
        issue_b64 = b64("# Main Issue\n\nBody.")
        raw = build_raw_reply(
            envelope,
            parts=[
                block("issue", issue_b64),
                block("json", json.dumps(envelope, ensure_ascii=True)),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.issue_body_base64, issue_b64)
        self.assertIsNone(decision.followup_issue_body_base64)

    def test_issue_create_no_body_at_all_is_rejected(self) -> None:
        """issue_create with neither CHATGPT_ISSUE_BODY nor CHATGPT_FOLLOWUP_ISSUE_BODY → error."""
        envelope: dict[str, object] = {
            "action": "issue_create",
            "target_issue": "#3 Missing",
            "close_current_issue": False,
            "create_followup_issue": False,
            "summary": "No body at all",
        }
        raw = build_raw_reply(
            envelope,
            parts=[block("json", json.dumps(envelope, ensure_ascii=True))],
        )
        with self.assertRaisesRegex(
            issue_centric_contract.IssueCentricContractError, "requires CHATGPT_ISSUE_BODY"
        ):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_issue_create_create_followup_true_no_followup_body_is_rejected(self) -> None:
        """issue_create + create_followup_issue=True but NO body blocks at all → error."""
        envelope = self._followup_envelope()
        raw = build_raw_reply(
            envelope,
            parts=[block("json", json.dumps(envelope, ensure_ascii=True))],
        )
        # create_followup_issue=True requires CHATGPT_FOLLOWUP_ISSUE_BODY fires first
        with self.assertRaises(issue_centric_contract.IssueCentricContractError):
            issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")

    def test_issue_create_issue_body_takes_priority_over_followup_body(self) -> None:
        """When both CHATGPT_ISSUE_BODY and CHATGPT_FOLLOWUP_ISSUE_BODY are present, issue_body wins."""
        envelope = self._followup_envelope()
        issue_b64 = b64("# Primary Issue\n\nThis is the primary body.")
        followup_b64 = b64("# Child Issue\n\nThis is the child body.")
        raw = build_raw_reply(
            envelope,
            parts=[
                block("issue", issue_b64),
                block("followup", followup_b64),
                block("json", json.dumps(envelope, ensure_ascii=True)),
            ],
        )
        decision = issue_centric_contract.parse_issue_centric_reply(raw, after_text="request body")
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.issue_body_base64, issue_b64)
        self.assertEqual(decision.followup_issue_body_base64, followup_b64)

    def test_primary_body_returns_followup_body_when_issue_body_absent(self) -> None:
        """transport primary_body falls back to followup_issue_body when issue_body=None."""
        followup_b64 = b64("# Child Issue\n\nFallback body.")
        decision = issue_centric_contract.IssueCentricDecision(
            action=issue_centric_contract.IssueCentricAction.ISSUE_CREATE,
            target_issue="#1",
            close_current_issue=False,
            create_followup_issue=True,
            summary="fallback test",
            issue_body_base64=None,
            codex_body_base64=None,
            review_base64=None,
            followup_issue_body_base64=followup_b64,
            raw_json="",
            raw_segment="",
        )
        prepared = issue_centric_transport.decode_issue_centric_decision(decision)
        self.assertIsNone(prepared.issue_body)
        self.assertIsNotNone(prepared.followup_issue_body)
        # primary_body must fall back to followup_issue_body
        self.assertIsNotNone(prepared.primary_body)
        assert prepared.primary_body is not None
        self.assertEqual(prepared.primary_body.decoded_text, "# Child Issue\n\nFallback body.")


# ---------------------------------------------------------------------------
# ContractCorrectionRetryBehaviorTests
# ---------------------------------------------------------------------------

class ContractCorrectionRetryBehaviorTests(unittest.TestCase):
    """Integration-level tests for the correction-retry loop in fetch_next_prompt.run().

    Verifies that:
    - A retryable invalid-contract triggers send_to_chatgpt on the 1st attempt
    - The saved correction count reaches 1 after the 1st send
    - A second retryable hit (count=1) triggers send_to_chatgpt again
    - The saved correction count reaches 2 after the 2nd send
    - When count is already 2 (== _MAX_CONTRACT_CORRECTIONS), no send occurs and
      a hard stop (BridgeError) fires instead
    - ready_issue_binding_error (count=0) also triggers send_to_chatgpt
    - ready_issue_binding_error correction request uses binding-mismatch wording
    - reply_not_ready does NOT trigger correction — just raises BridgeError
    - A valid contract bypasses all correction logic and proceeds normally
    """

    # Helper: a raw reply text that carries the completion tag but omits the
    # decision markers, causing reply_complete_no_marker.
    _NO_MARKER_RAW = (
        "あなた:\nrequest body\nChatGPT:\n"
        "何かコメントをここに書きました。\n"
        + issue_centric_contract.REPLY_COMPLETE_TAG
    )

    # Helper: a raw reply text that has no completion tag → reply_not_ready
    _NOT_READY_RAW = "あなた:\nrequest body\nChatGPT:\nまだ考え中です。"

    def _base_state(self, *, correction_count: int = 0) -> dict[str, object]:
        state: dict[str, object] = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "hash-abc",
            "pending_request_source": "ready_issue:#5",
            "pending_request_log": "logs/req.md",
            "pending_request_signal": "",
            "current_ready_issue_ref": "#5 Ready: implement feature X",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
        }
        if correction_count:
            state["last_issue_centric_contract_correction_count"] = correction_count
        return state

    def _make_patched_context(self, tmp: str, raw: str, saved_states: list, sent_texts: list):
        """Return a context manager that patches all external calls in run()."""
        from pathlib import Path
        from unittest.mock import patch

        temp_root = Path(tmp)

        def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
            path = temp_root / f"{prefix}.{suffix}"
            path.write_text(text, encoding="utf-8")
            return path

        def fake_send(text: str) -> None:
            sent_texts.append(text)

        return (
            patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
            patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", return_value=raw),
            patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
            patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            patch.object(fetch_next_prompt, "send_to_chatgpt", side_effect=fake_send),
            patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo"}),
        )

    # ------------------------------------------------------------------
    # Generic invalid contract (reply_complete_no_marker) retry
    # ------------------------------------------------------------------

    def test_first_retry_sends_correction_request(self) -> None:
        """1st attempt (count=0): send_to_chatgpt called, count saved as 1, BridgeStop raised."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._NO_MARKER_RAW, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop):
                    fetch_next_prompt.run(self._base_state(correction_count=0), [])
        self.assertEqual(len(sent_texts), 1, "send_to_chatgpt should be called once")
        self.assertEqual(len(saved_states), 1)
        self.assertEqual(saved_states[0]["last_issue_centric_contract_correction_count"], 1)
        self.assertEqual(saved_states[0]["mode"], "waiting_prompt_reply")

    def test_second_retry_sends_correction_request(self) -> None:
        """2nd attempt (count=1): send_to_chatgpt called, count saved as 2, BridgeStop raised."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._NO_MARKER_RAW, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop):
                    fetch_next_prompt.run(self._base_state(correction_count=1), [])
        self.assertEqual(len(sent_texts), 1, "send_to_chatgpt should be called once")
        self.assertEqual(len(saved_states), 1)
        self.assertEqual(saved_states[0]["last_issue_centric_contract_correction_count"], 2)

    def test_third_attempt_hard_stops_without_send(self) -> None:
        """3rd attempt (count=2 == _MAX): no send, BridgeError (hard stop) raised."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._NO_MARKER_RAW, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaisesRegex(BridgeError, "issue-centric contract reply が不正でした"):
                    fetch_next_prompt.run(self._base_state(correction_count=2), [])
        self.assertEqual(len(sent_texts), 0, "send_to_chatgpt must NOT be called on hard stop")
        self.assertEqual(len(saved_states), 1)
        self.assertEqual(saved_states[0]["mode"], "awaiting_user")

    # ------------------------------------------------------------------
    # reply_not_ready — must NOT enter correction loop
    # ------------------------------------------------------------------

    def test_reply_not_ready_does_not_send_correction(self) -> None:
        """reply_not_ready: no correction sent, raises BridgeError about incomplete reply."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._NOT_READY_RAW, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeError):
                    fetch_next_prompt.run(self._base_state(correction_count=0), [])
        self.assertEqual(len(sent_texts), 0, "send_to_chatgpt must NOT be called for reply_not_ready")

    # ------------------------------------------------------------------
    # ready_issue_binding_error retry
    # ------------------------------------------------------------------

    def _stale_target_raw(self) -> str:
        """A valid-format contract reply but with target_issue=#99 (stale, doesn't match #5)."""
        return build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "#99",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "stale issue",
            }
        )

    def test_binding_mismatch_first_retry_sends_correction(self) -> None:
        """ready_issue_binding_error with count=0: send_to_chatgpt called, count saved as 1."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._stale_target_raw(), saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop):
                    fetch_next_prompt.run(self._base_state(correction_count=0), [])
        self.assertEqual(len(sent_texts), 1, "binding mismatch: send_to_chatgpt should be called")
        self.assertEqual(saved_states[0]["last_issue_centric_contract_correction_count"], 1)

    def test_binding_mismatch_correction_uses_binding_wording(self) -> None:
        """The correction request for binding mismatch must mention target_issue fix."""
        sent_texts: list[str] = []
        saved_states: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._stale_target_raw(), saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop):
                    fetch_next_prompt.run(self._base_state(correction_count=0), [])
        self.assertEqual(len(sent_texts), 1)
        correction_text = sent_texts[0]
        # binding mismatch wording must include target_issue fix instruction
        self.assertIn("target_issue", correction_text)
        self.assertIn("#5", correction_text)  # current ready issue ref
        self.assertNotIn("===CHATGPT_REPLY_COMPLETE===", correction_text)
        # must NOT be generic wording (generic starts with "前回の返答に issue-centric contract の不正")
        self.assertNotIn("issue-centric contract の不正がありました", correction_text)

    def test_binding_mismatch_hard_stop_at_max_corrections(self) -> None:
        """ready_issue_binding_error with count=2: no send, BridgeError raised."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._stale_target_raw(), saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaisesRegex(BridgeError, "issue-centric contract reply が不正でした"):
                    fetch_next_prompt.run(self._base_state(correction_count=2), [])
        self.assertEqual(len(sent_texts), 0)
        self.assertEqual(saved_states[0]["mode"], "awaiting_user")

    # ------------------------------------------------------------------
    # Early exception route (IssueCentricReplyInvalid raised by wait_for_issue_centric_reply_text)
    # ------------------------------------------------------------------

    def _make_early_exc_patched_context(self, tmp: str, exc: Exception, saved_states: list, sent_texts: list):
        """Return patches where wait_for_issue_centric_reply_text raises exc directly."""
        from pathlib import Path
        from unittest.mock import patch

        temp_root = Path(tmp)

        def fake_log_text(prefix: str, text: str, suffix: str = "md") -> Path:
            path = temp_root / f"{prefix}.{suffix}"
            path.write_text(text, encoding="utf-8")
            return path

        def fake_send(text: str) -> None:
            sent_texts.append(text)

        return (
            patch.object(fetch_next_prompt, "read_pending_request_text", return_value="request body"),
            patch.object(fetch_next_prompt, "wait_for_issue_centric_reply_text", side_effect=exc),
            patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
            patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
            patch.object(fetch_next_prompt, "send_to_chatgpt", side_effect=fake_send),
            patch.object(fetch_next_prompt, "load_project_config", return_value={"github_repository": "example/repo"}),
        )

    def _make_early_invalid_exc(self) -> "fetch_next_prompt.IssueCentricReplyInvalid":
        """Build an IssueCentricReplyInvalid that looks like reply_complete_no_marker."""
        from fetch_next_prompt import IssueCentricReplyReadiness

        raw = self._NO_MARKER_RAW
        readiness = IssueCentricReplyReadiness(
            status="reply_complete_no_marker",
            reason="completion tag present but issue-centric decision markers are missing.",
            assistant_text_present=True,
            assistant_final_content_present=True,
            assistant_meta_only=False,
            thinking_visible=False,
            decision_marker_present=False,
            reply_complete_tag_present=True,
            body_block_start_present=False,
            body_block_end_present=False,
            partial_body_block_detected=False,
            open_body_blocks=set(),
            contract_parse_attempted=False,
            decision=None,
        )
        return fetch_next_prompt.IssueCentricReplyInvalid(
            readiness.reason,
            raw_text=raw,
            readiness=readiness,
        )

    def test_early_exception_first_retry_sends_correction(self) -> None:
        """IssueCentricReplyInvalid from wait_for_plan_a: count=0 → send_to_chatgpt, count→1."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        exc = self._make_early_invalid_exc()
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_early_exc_patched_context(tmp, exc, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop):
                    fetch_next_prompt.run(self._base_state(correction_count=0), [])
        self.assertEqual(len(sent_texts), 1, "send_to_chatgpt must be called on early exception retry")
        self.assertEqual(saved_states[0]["last_issue_centric_contract_correction_count"], 1)
        self.assertEqual(saved_states[0]["mode"], "waiting_prompt_reply")

    def test_early_exception_second_retry_sends_correction(self) -> None:
        """IssueCentricReplyInvalid from wait_for_plan_a: count=1 → send_to_chatgpt, count→2."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        exc = self._make_early_invalid_exc()
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_early_exc_patched_context(tmp, exc, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaises(BridgeStop):
                    fetch_next_prompt.run(self._base_state(correction_count=1), [])
        self.assertEqual(len(sent_texts), 1)
        self.assertEqual(saved_states[0]["last_issue_centric_contract_correction_count"], 2)

    def test_early_exception_hard_stop_at_max_count(self) -> None:
        """IssueCentricReplyInvalid from wait_for_plan_a: count=2 → no send, hard BridgeError."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        exc = self._make_early_invalid_exc()
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_early_exc_patched_context(tmp, exc, saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                with self.assertRaisesRegex(BridgeError, "issue-centric contract reply が不正でした"):
                    fetch_next_prompt.run(self._base_state(correction_count=2), [])
        self.assertEqual(len(sent_texts), 0, "no send on hard stop")
        self.assertEqual(saved_states[0]["mode"], "awaiting_user")

    # ------------------------------------------------------------------
    # Valid contract clears correction state
    # ------------------------------------------------------------------

    def _valid_no_action_raw(self) -> str:
        """A fully valid no_action contract for issue #5 (matches current_ready_issue_ref)."""
        return build_raw_reply(
            {
                "action": "no_action",
                "target_issue": "#5",
                "close_current_issue": False,
                "create_followup_issue": False,
                "summary": "No action needed.",
            }
        )

    def test_valid_contract_clears_correction_state(self) -> None:
        """After a valid contract is processed, correction count/log/reason are cleared in state."""
        saved_states: list[dict] = []
        sent_texts: list[str] = []
        state = self._base_state(correction_count=1)
        state["last_issue_centric_contract_correction_log"] = "logs/prev_correction.md"
        state["last_issue_centric_contract_correction_reason"] = "some old reason"
        with tempfile.TemporaryDirectory() as tmp:
            patches = self._make_patched_context(tmp, self._valid_no_action_raw(), saved_states, sent_texts)
            with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
                # valid contract raises BridgeStop for no_action (awaiting_user save then stop)
                try:
                    fetch_next_prompt.run(dict(state), [])
                except (BridgeStop, BridgeError):
                    pass
        # At least one save_state must have occurred with correction fields cleared
        self.assertGreater(len(saved_states), 0)
        last_saved = saved_states[-1]
        self.assertEqual(last_saved.get("last_issue_centric_contract_correction_count"), 0)
        self.assertEqual(last_saved.get("last_issue_centric_contract_correction_log"), "")
        self.assertEqual(last_saved.get("last_issue_centric_contract_correction_reason"), "")


