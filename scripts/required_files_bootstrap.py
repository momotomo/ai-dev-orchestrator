#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT_DIR / "bridge" / "required_files_bootstrap"
PACK_DEFINITION_PATH = PACK_DIR / "required_files_pack.json"
PROMPT_TEMPLATE_PATH = PACK_DIR / "copilot_bootstrap_prompt_template.md"
DEFAULT_SMOKE_WORKSPACE = ROOT_DIR / "logs" / "required_files_bootstrap_smoke"
BOOTSTRAP_FILE_START = "===BOOTSTRAP_FILE_START==="
BOOTSTRAP_FILE_END = "===BOOTSTRAP_FILE_END==="
BOOTSTRAP_CONTENT_START = "===BOOTSTRAP_CONTENT_START==="
BOOTSTRAP_CONTENT_END = "===BOOTSTRAP_CONTENT_END==="


@dataclass(frozen=True)
class RequiredFileSpec:
    path: str
    purpose: str
    minimum_anchors: tuple[str, ...]
    template_path: Path


@dataclass(frozen=True)
class RequiredFilesPack:
    pack_id: str
    title: str
    description: str
    required_files: tuple[RequiredFileSpec, ...]


@dataclass(frozen=True)
class BootstrapArtifactFile:
    path: str
    purpose: str
    minimum_anchors: tuple[str, ...]
    content: str


def load_required_files_pack() -> RequiredFilesPack:
    payload = json.loads(PACK_DEFINITION_PATH.read_text(encoding="utf-8"))
    required_files: list[RequiredFileSpec] = []
    for entry in payload["required_files"]:
        required_files.append(
            RequiredFileSpec(
                path=str(entry["path"]),
                purpose=str(entry["purpose"]),
                minimum_anchors=tuple(str(anchor) for anchor in entry["minimum_anchors"]),
                template_path=PACK_DIR / str(entry["template"]),
            )
        )
    return RequiredFilesPack(
        pack_id=str(payload["pack_id"]),
        title=str(payload["title"]),
        description=str(payload["description"]),
        required_files=tuple(required_files),
    )


def render_required_file_content(spec: RequiredFileSpec, target_repo_name: str) -> str:
    content = spec.template_path.read_text(encoding="utf-8")
    content = content.replace("{{TARGET_REPO_NAME}}", target_repo_name)
    return content.rstrip() + "\n"


def render_required_files_summary(pack: RequiredFilesPack) -> str:
    lines: list[str] = []
    for spec in pack.required_files:
        anchors = ", ".join(f"`{anchor}`" for anchor in spec.minimum_anchors)
        lines.append(f"- `{spec.path}`: {spec.purpose}")
        lines.append(f"  Required anchors: {anchors}")
    return "\n".join(lines)


def render_file_sections(pack: RequiredFilesPack, target_repo_name: str) -> str:
    sections: list[str] = []
    for spec in pack.required_files:
        sections.append(BOOTSTRAP_FILE_START)
        sections.append(f"PATH: {spec.path}")
        sections.append(f"PURPOSE: {spec.purpose}")
        for anchor in spec.minimum_anchors:
            sections.append(f"ANCHOR: {anchor}")
        sections.append(BOOTSTRAP_CONTENT_START)
        sections.append(render_required_file_content(spec, target_repo_name).rstrip())
        sections.append(BOOTSTRAP_CONTENT_END)
        sections.append(BOOTSTRAP_FILE_END)
        sections.append("")
    return "\n".join(sections).rstrip()


def render_bootstrap_prompt(
    *,
    target_repo_name: str,
    target_repo_path: Path,
    pack: RequiredFilesPack | None = None,
) -> str:
    pack = pack or load_required_files_pack()
    template = PROMPT_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("__TARGET_REPO_NAME__", target_repo_name)
        .replace("__TARGET_REPO_PATH__", str(target_repo_path))
        .replace("__REQUIRED_FILES_SUMMARY__", render_required_files_summary(pack))
        .replace("__FILE_SECTIONS__", render_file_sections(pack, target_repo_name))
    ).rstrip() + "\n"


def write_bootstrap_prompt_artifact(
    *,
    target_repo_name: str,
    target_repo_path: Path,
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_bootstrap_prompt(
            target_repo_name=target_repo_name,
            target_repo_path=target_repo_path,
        ),
        encoding="utf-8",
    )
    return output_path


def parse_bootstrap_prompt_artifact(prompt_text: str) -> tuple[BootstrapArtifactFile, ...]:
    files: list[BootstrapArtifactFile] = []
    lines = prompt_text.splitlines()
    index = 0
    while index < len(lines):
        if lines[index] != BOOTSTRAP_FILE_START:
            index += 1
            continue

        index += 1
        if index >= len(lines) or not lines[index].startswith("PATH: "):
            raise ValueError("bootstrap artifact is missing PATH metadata")
        path = lines[index][len("PATH: ") :]

        index += 1
        if index >= len(lines) or not lines[index].startswith("PURPOSE: "):
            raise ValueError("bootstrap artifact is missing PURPOSE metadata")
        purpose = lines[index][len("PURPOSE: ") :]

        index += 1
        anchors: list[str] = []
        while index < len(lines) and lines[index].startswith("ANCHOR: "):
            anchors.append(lines[index][len("ANCHOR: ") :])
            index += 1

        if index >= len(lines) or lines[index] != BOOTSTRAP_CONTENT_START:
            raise ValueError("bootstrap artifact is missing content start marker")

        index += 1
        content_lines: list[str] = []
        while index < len(lines) and lines[index] != BOOTSTRAP_CONTENT_END:
            content_lines.append(lines[index])
            index += 1

        if index >= len(lines):
            raise ValueError("bootstrap artifact is missing content end marker")

        index += 1
        if index >= len(lines) or lines[index] != BOOTSTRAP_FILE_END:
            raise ValueError("bootstrap artifact is missing file end marker")

        files.append(
            BootstrapArtifactFile(
                path=path,
                purpose=purpose,
                minimum_anchors=tuple(anchors),
                content="\n".join(content_lines).rstrip() + "\n",
            )
        )
        index += 1
    return tuple(files)


def materialize_bootstrap_artifact(
    *,
    prompt_artifact_path: Path,
    target_repo_path: Path,
) -> tuple[Path, ...]:
    target_repo_path.mkdir(parents=True, exist_ok=True)
    prompt_text = prompt_artifact_path.read_text(encoding="utf-8")
    artifact_files = parse_bootstrap_prompt_artifact(prompt_text)
    if not artifact_files:
        raise ValueError("bootstrap artifact did not contain any file payloads")

    written_paths: list[Path] = []
    for artifact_file in artifact_files:
        destination = target_repo_path / artifact_file.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(artifact_file.content, encoding="utf-8")
        written_paths.append(destination)
    return tuple(written_paths)


def validate_required_files(
    *,
    target_repo_path: Path,
    pack: RequiredFilesPack | None = None,
) -> None:
    pack = pack or load_required_files_pack()
    for spec in pack.required_files:
        file_path = target_repo_path / spec.path
        if not file_path.exists():
            raise ValueError(f"required file is missing: {file_path}")
        content = file_path.read_text(encoding="utf-8")
        for anchor in spec.minimum_anchors:
            if anchor not in content:
                raise ValueError(f"required anchor is missing from {file_path}: {anchor}")


def run_smoke_test(*, workspace_root: Path, target_repo_name: str) -> dict[str, object]:
    prompt_artifact_path = workspace_root / "required-files-bootstrap-prompt.md"
    scratch_repo_path = workspace_root / "scratch-target-repo"
    summary_path = workspace_root / "smoke-test-summary.json"

    workspace_root.mkdir(parents=True, exist_ok=True)
    write_bootstrap_prompt_artifact(
        target_repo_name=target_repo_name,
        target_repo_path=scratch_repo_path,
        output_path=prompt_artifact_path,
    )
    written_paths = materialize_bootstrap_artifact(
        prompt_artifact_path=prompt_artifact_path,
        target_repo_path=scratch_repo_path,
    )
    validate_required_files(target_repo_path=scratch_repo_path)

    summary = {
        "pack_id": load_required_files_pack().pack_id,
        "prompt_artifact_path": str(prompt_artifact_path),
        "scratch_repo_path": str(scratch_repo_path),
        "written_files": [str(path.relative_to(scratch_repo_path)) for path in written_paths],
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    summary["summary_path"] = str(summary_path)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and materialize the ai-dev-orchestrator required-files bootstrap pack."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_prompt_parser = subparsers.add_parser(
        "build-prompt",
        help="Write the Copilot-facing bootstrap prompt artifact.",
    )
    build_prompt_parser.add_argument("--target-repo-name", required=True)
    build_prompt_parser.add_argument("--target-repo-path", required=True)
    build_prompt_parser.add_argument("--output", required=True)

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="Create the required files in a target repo from a bootstrap prompt artifact.",
    )
    materialize_parser.add_argument("--prompt", required=True)
    materialize_parser.add_argument("--target-repo-path", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate that a target repo contains the canonical required files and anchors.",
    )
    validate_parser.add_argument("--target-repo-path", required=True)

    smoke_test_parser = subparsers.add_parser(
        "smoke-test",
        help="Build an inspectable prompt artifact, materialize it into a scratch repo, and validate the result.",
    )
    smoke_test_parser.add_argument("--target-repo-name", required=True)
    smoke_test_parser.add_argument(
        "--workspace-root",
        default=str(DEFAULT_SMOKE_WORKSPACE),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "build-prompt":
        output_path = write_bootstrap_prompt_artifact(
            target_repo_name=args.target_repo_name,
            target_repo_path=Path(args.target_repo_path).resolve(),
            output_path=Path(args.output).resolve(),
        )
        print(output_path)
        return 0

    if args.command == "materialize":
        written_paths = materialize_bootstrap_artifact(
            prompt_artifact_path=Path(args.prompt).resolve(),
            target_repo_path=Path(args.target_repo_path).resolve(),
        )
        for path in written_paths:
            print(path)
        return 0

    if args.command == "validate":
        validate_required_files(target_repo_path=Path(args.target_repo_path).resolve())
        print(Path(args.target_repo_path).resolve())
        return 0

    if args.command == "smoke-test":
        summary = run_smoke_test(
            workspace_root=Path(args.workspace_root).resolve(),
            target_repo_name=args.target_repo_name,
        )
        print(json.dumps(summary, indent=2))
        return 0

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
