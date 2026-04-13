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
        with self.assertRaisesRegex(
            issue_centric_transport.IssueCentricBodyDecodeError,
            "not valid UTF-8",
        ):
            issue_centric_transport.decode_issue_centric_decision(decision)

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
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
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
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
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
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                "No contract markers here yet.",
            ]
        )
        with self.assertRaises(fetch_next_prompt.IssueCentricReplyInvalid) as ctx:
            fetch_next_prompt.parse_issue_centric_reply_for_fetch(raw, after_text="request body")
        self.assertIn("issue-centric decision markers are missing", str(ctx.exception))

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

    def test_fetch_run_stops_immediately_for_invalid_issue_centric_contract(self) -> None:
        state = {
            "mode": "waiting_prompt_reply",
            "pending_request_hash": "request-hash",
            "pending_request_source": "ready_issue:#2",
            "pending_request_log": "logs/request.md",
            "pending_request_signal": "",
            "last_processed_request_hash": "",
            "last_processed_reply_hash": "",
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
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
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


class PlanAFetchPrimaryPathTests(unittest.TestCase):
    """Tests for Plan A BODY base64 transport as the primary fetch path.

    Verifies that:
    - wait_for_plan_a_or_prompt_reply_text succeeds on Plan A contract reply (primary path)
    - wait_for_plan_a_or_prompt_reply_text succeeds on visible DOM text reply (safety fallback)
    - fetch_next_prompt.run() passes a plan_a_extractor to wait_for_plan_a_or_prompt_reply_text
    - fetch_next_prompt.run() falls through to visible DOM text path when Plan A is absent
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

    def test_fetch_run_uses_wait_for_plan_a_or_prompt_reply_text(self) -> None:
        """fetch_next_prompt.run() must call wait_for_plan_a_or_prompt_reply_text (not wait_for_prompt_reply_text)."""
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
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", wait_mock),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: None),
            ):
                try:
                    fetch_next_prompt.run(dict(state), [])
                except Exception:
                    pass

            # Must have called wait_for_plan_a_or_prompt_reply_text with plan_a_extractor kwarg
            self.assertTrue(wait_mock.called)
            call_kwargs = wait_mock.call_args.kwargs
            self.assertIn("plan_a_extractor", call_kwargs)
            self.assertTrue(callable(call_kwargs["plan_a_extractor"]))

    def test_fetch_run_falls_back_to_visible_dom_path_when_plan_a_absent(self) -> None:
        """When Plan A contract is absent, fetch_next_prompt.run() falls through to visible DOM text path."""
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
                patch.object(fetch_next_prompt, "wait_for_plan_a_or_prompt_reply_text", return_value=raw),
                patch.object(fetch_next_prompt, "log_text", side_effect=fake_log_text),
                patch.object(fetch_next_prompt, "save_state", side_effect=lambda s: saved_states.append(dict(s))),
                patch.object(fetch_next_prompt, "write_text", side_effect=lambda p, t: None),
            ):
                result = fetch_next_prompt.run(dict(state), [])

            # Visible DOM text path: codex_prompt → mode=ready_for_codex
            self.assertEqual(result, 0)
            saved = saved_states[0]
            self.assertEqual(saved["mode"], "ready_for_codex")
            self.assertTrue(bool(saved.get("need_codex_run")))


class WaitForPlanAOrPromptReplyTextTests(unittest.TestCase):
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

        self.assertEqual(readiness.status, "reply_complete_no_marker")
        self.assertTrue(readiness.assistant_text_present)
        self.assertFalse(readiness.thinking_visible)
        self.assertFalse(readiness.decision_marker_present)

    def test_classifies_invalid_contract_when_decision_json_is_broken(self) -> None:
        raw = "\n".join(
            [
                "あなた:",
                "request body",
                "ChatGPT:",
                issue_centric_contract.DECISION_JSON_START,
                "not json",
                issue_centric_contract.DECISION_JSON_END,
            ]
        )

        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw,
            after_text="request body",
        )

        self.assertEqual(readiness.status, "reply_complete_invalid_contract")
        self.assertTrue(readiness.decision_marker_present)
        self.assertTrue(readiness.contract_parse_attempted)

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

    def test_legacy_visible_text_reply_still_uses_fallback_path(self) -> None:
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
            result = bridge_common.wait_for_plan_a_or_prompt_reply_text(
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
                bridge_common.wait_for_plan_a_or_prompt_reply_text(
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
        raw = self._make_raw("了解しました。次の変更を進めます。")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        self.assertEqual(readiness.status, "reply_complete_no_marker")
        self.assertTrue(readiness.assistant_final_content_present)
        self.assertFalse(readiness.assistant_meta_only)

    def test_mixed_meta_and_content_lines_is_invalid_stop(self) -> None:
        raw = self._make_raw("Thought for 5s", "了解しました。次の変更を進めます。")
        readiness = fetch_next_prompt.classify_issue_centric_reply_readiness(
            raw, after_text="request body"
        )
        # Has actual content + no marker → invalid stop
        self.assertEqual(readiness.status, "reply_complete_no_marker")
        self.assertTrue(readiness.assistant_final_content_present)
        self.assertFalse(readiness.assistant_meta_only)

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


if __name__ == "__main__":
    unittest.main()
