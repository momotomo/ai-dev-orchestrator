#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from issue_centric_contract import (
    IssueCentricAction,
    IssueCentricContractError,
    IssueCentricDecision,
    validate_issue_centric_decision,
)


class IssueCentricTransportError(IssueCentricContractError):
    """Raised when a validated contract cannot be prepared for downstream use."""


class IssueCentricBodyDecodeError(IssueCentricTransportError):
    """Raised when a contract body payload cannot be decoded to UTF-8 text."""


class IssueCentricArtifactKind(str, Enum):
    ISSUE_BODY = "issue_body"
    CODEX_BODY = "codex_body"
    REVIEW = "review"
    FOLLOWUP_ISSUE_BODY = "followup_issue_body"


_BLOCK_NAMES = {
    IssueCentricArtifactKind.ISSUE_BODY: "CHATGPT_ISSUE_BODY",
    IssueCentricArtifactKind.CODEX_BODY: "CHATGPT_CODEX_BODY",
    IssueCentricArtifactKind.REVIEW: "CHATGPT_REVIEW",
    IssueCentricArtifactKind.FOLLOWUP_ISSUE_BODY: "CHATGPT_FOLLOWUP_ISSUE_BODY",
}

_ARTIFACT_LOG_PREFIXES = {
    IssueCentricArtifactKind.ISSUE_BODY: "prepared_issue_centric_issue_body",
    IssueCentricArtifactKind.CODEX_BODY: "prepared_issue_centric_codex_body",
    IssueCentricArtifactKind.REVIEW: "prepared_issue_centric_review_body",
    IssueCentricArtifactKind.FOLLOWUP_ISSUE_BODY: "prepared_issue_centric_followup_issue_body",
}


@dataclass(frozen=True)
class IssueCentricDecodedBody:
    kind: IssueCentricArtifactKind
    block_name: str
    raw_base64: str
    normalized_base64: str
    decoded_text: str

    def payload_metadata(self, *, decoded_path: str | None = None) -> dict[str, object]:
        return {
            "present": True,
            "raw_payload_chars": len(self.raw_base64),
            "normalized_payload_chars": len(self.normalized_base64),
            "normalized_payload_sha256": hashlib.sha256(
                self.normalized_base64.encode("utf-8")
            ).hexdigest(),
            "decoded_text_chars": len(self.decoded_text),
            "decoded_text_sha256": hashlib.sha256(self.decoded_text.encode("utf-8")).hexdigest(),
            "decoded_text_path": decoded_path or "",
        }


@dataclass(frozen=True)
class PreparedIssueCentricDecision:
    decision: IssueCentricDecision
    issue_body: IssueCentricDecodedBody | None
    codex_body: IssueCentricDecodedBody | None
    review_body: IssueCentricDecodedBody | None
    followup_issue_body: IssueCentricDecodedBody | None

    @property
    def primary_body(self) -> IssueCentricDecodedBody | None:
        if self.decision.action is IssueCentricAction.ISSUE_CREATE:
            return self.issue_body
        if self.decision.action is IssueCentricAction.CODEX_RUN:
            return self.codex_body
        if self.decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED:
            return self.review_body
        if self.decision.create_followup_issue:
            return self.followup_issue_body
        return None

    @property
    def pending_runtime_action(self) -> str:
        if self.decision.action is IssueCentricAction.ISSUE_CREATE:
            return "issue_create_mutation"
        if self.decision.action is IssueCentricAction.CODEX_RUN:
            return "codex_run_dispatch"
        if self.decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED:
            return "human_review_dispatch"
        if self.decision.create_followup_issue:
            return "followup_issue_dispatch"
        return "decision_finalize"

    @property
    def safe_stop_reason(self) -> str:
        if self.decision.action is IssueCentricAction.ISSUE_CREATE:
            return (
                "issue_create execution is not implemented yet. "
                "The decoded issue body has been prepared for the future GitHub issue-create step."
            )
        if self.decision.action is IssueCentricAction.CODEX_RUN:
            return (
                "codex_run execution is not implemented yet. "
                "The decoded Codex body has been prepared for the future issue comment registration / Codex launch step."
            )
        if self.decision.action is IssueCentricAction.HUMAN_REVIEW_NEEDED:
            if self.review_body is not None:
                return (
                    "human_review_needed review execution is available as a narrow slice. "
                    "The decoded review body has been prepared for the target-issue review comment step."
                )
            return (
                "human_review_needed review execution is available as a narrow slice, "
                "but this decision does not include CHATGPT_REVIEW."
            )
        if self.decision.action is IssueCentricAction.NO_ACTION and self.decision.create_followup_issue:
            return (
                "no_action + create_followup_issue execution is available as a narrow slice. "
                "The decoded follow-up issue body has been prepared for the follow-up issue create step."
            )
        return (
            "no_action has been validated and recorded. "
            "The bridge does not execute the new contract end-to-end yet, so it stops after preparing metadata."
        )

    def metadata_dict(
        self,
        *,
        artifact_path: str | None,
        raw_log_path: str | None,
        decision_log_path: str | None,
    ) -> dict[str, object]:
        body_metadata: dict[str, object] = {}
        decoded_lookup = {
            IssueCentricArtifactKind.ISSUE_BODY: self.issue_body,
            IssueCentricArtifactKind.CODEX_BODY: self.codex_body,
            IssueCentricArtifactKind.REVIEW: self.review_body,
            IssueCentricArtifactKind.FOLLOWUP_ISSUE_BODY: self.followup_issue_body,
        }
        primary = self.primary_body
        for kind, block_name in _BLOCK_NAMES.items():
            decoded = decoded_lookup[kind]
            if decoded is None:
                raw_payload = self._raw_payload(kind)
                body_metadata[block_name] = {
                    "present": raw_payload is not None,
                    "raw_payload_chars": len(raw_payload or ""),
                    "decoded_text_path": "",
                }
                continue
            body_metadata[block_name] = decoded.payload_metadata(
                decoded_path=artifact_path if primary is decoded else ""
            )

        return {
            "action": self.decision.action.value,
            "target_issue": self.decision.target_issue or "none",
            "close_current_issue": self.decision.close_current_issue,
            "create_followup_issue": self.decision.create_followup_issue,
            "summary": self.decision.summary,
            "body_blocks": body_metadata,
            "prepared_artifact": (
                {
                    "kind": primary.kind.value,
                    "block_name": primary.block_name,
                    "path": artifact_path or "",
                }
                if primary is not None
                else None
            ),
            "pending_runtime_action": self.pending_runtime_action,
            "pending_runtime_status": "not_yet_implemented",
            "safe_stop_reason": self.safe_stop_reason,
            "raw_response_log": raw_log_path or "",
            "decision_log": decision_log_path or "",
        }

    def _raw_payload(self, kind: IssueCentricArtifactKind) -> str | None:
        if kind is IssueCentricArtifactKind.ISSUE_BODY:
            return self.decision.issue_body_base64
        if kind is IssueCentricArtifactKind.CODEX_BODY:
            return self.decision.codex_body_base64
        if kind is IssueCentricArtifactKind.REVIEW:
            return self.decision.review_base64
        return self.decision.followup_issue_body_base64


@dataclass(frozen=True)
class MaterializedIssueCentricDecision:
    prepared: PreparedIssueCentricDecision
    metadata_log_path: Path
    artifact_log_path: Path | None
    metadata: dict[str, object]

    @property
    def safe_stop_reason(self) -> str:
        return self.prepared.safe_stop_reason


def decode_issue_centric_decision(decision: IssueCentricDecision) -> PreparedIssueCentricDecision:
    validate_issue_centric_decision(decision)
    issue_body = _decode_optional_body(
        IssueCentricArtifactKind.ISSUE_BODY,
        decision.issue_body_base64,
    )
    codex_body = _decode_optional_body(
        IssueCentricArtifactKind.CODEX_BODY,
        decision.codex_body_base64,
    )
    review_body = _decode_optional_body(
        IssueCentricArtifactKind.REVIEW,
        decision.review_base64,
    )
    followup_issue_body = _decode_optional_body(
        IssueCentricArtifactKind.FOLLOWUP_ISSUE_BODY,
        decision.followup_issue_body_base64,
    )
    return PreparedIssueCentricDecision(
        decision=decision,
        issue_body=issue_body,
        codex_body=codex_body,
        review_body=review_body,
        followup_issue_body=followup_issue_body,
    )


def materialize_issue_centric_decision(
    decision: IssueCentricDecision,
    *,
    log_writer: Callable[[str, str, str], Path],
    repo_relative: Callable[[Path], str],
    raw_log_path: Path | None = None,
    decision_log_path: Path | None = None,
) -> MaterializedIssueCentricDecision:
    prepared = decode_issue_centric_decision(decision)
    primary = prepared.primary_body
    artifact_log_path = None
    artifact_path_rel = None
    if primary is not None:
        artifact_log_path = log_writer(
            _ARTIFACT_LOG_PREFIXES[primary.kind],
            primary.decoded_text,
            "md",
        )
        artifact_path_rel = repo_relative(artifact_log_path)

    metadata = prepared.metadata_dict(
        artifact_path=artifact_path_rel,
        raw_log_path=repo_relative(raw_log_path) if raw_log_path is not None else None,
        decision_log_path=repo_relative(decision_log_path) if decision_log_path is not None else None,
    )
    metadata_log_path = log_writer(
        f"prepared_issue_centric_{decision.action.value}_metadata",
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        "json",
    )
    return MaterializedIssueCentricDecision(
        prepared=prepared,
        metadata_log_path=metadata_log_path,
        artifact_log_path=artifact_log_path,
        metadata=metadata,
    )


def _decode_optional_body(
    kind: IssueCentricArtifactKind,
    raw_payload: str | None,
) -> IssueCentricDecodedBody | None:
    if raw_payload is None:
        return None

    normalized = "".join(line.strip() for line in raw_payload.splitlines() if line.strip())
    if not normalized:
        raise IssueCentricBodyDecodeError(
            f"{_BLOCK_NAMES[kind]} payload decodes to empty text."
        )

    padded = normalized + ("=" * ((4 - len(normalized) % 4) % 4))
    try:
        raw_bytes = base64.b64decode(padded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise IssueCentricBodyDecodeError(
            f"{_BLOCK_NAMES[kind]} payload is not valid base64: {exc}"
        ) from exc

    try:
        decoded_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise IssueCentricBodyDecodeError(
            f"{_BLOCK_NAMES[kind]} payload is not valid UTF-8: {exc}"
        ) from exc

    if decoded_text == "":
        raise IssueCentricBodyDecodeError(
            f"{_BLOCK_NAMES[kind]} payload decodes to empty text."
        )

    return IssueCentricDecodedBody(
        kind=kind,
        block_name=_BLOCK_NAMES[kind],
        raw_base64=raw_payload,
        normalized_base64=normalized,
        decoded_text=decoded_text,
    )
