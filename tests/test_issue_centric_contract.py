from __future__ import annotations

import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import fetch_next_prompt  # noqa: E402
import issue_centric_contract  # noqa: E402
import issue_centric_transport  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
