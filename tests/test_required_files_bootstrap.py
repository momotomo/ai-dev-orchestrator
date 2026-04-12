from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import required_files_bootstrap  # noqa: E402


class RequiredFilesBootstrapTests(unittest.TestCase):
    def test_bootstrap_artifact_materializes_real_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            prompt_artifact_path = temp_root / "artifacts" / "required-files-bootstrap.md"
            scratch_repo_path = temp_root / "scratch-target-repo"

            build_result = subprocess.run(
                [
                    sys.executable,
                    "scripts/required_files_bootstrap.py",
                    "build-prompt",
                    "--target-repo-name",
                    "scratch-target-repo",
                    "--target-repo-path",
                    str(scratch_repo_path),
                    "--output",
                    str(prompt_artifact_path),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(Path(build_result.stdout.strip()), prompt_artifact_path.resolve())
            self.assertTrue(prompt_artifact_path.exists())

            materialize_result = subprocess.run(
                [
                    sys.executable,
                    "scripts/required_files_bootstrap.py",
                    "materialize",
                    "--prompt",
                    str(prompt_artifact_path),
                    "--target-repo-path",
                    str(scratch_repo_path),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            written_paths = {
                Path(line.strip()).resolve().relative_to(scratch_repo_path.resolve()).as_posix()
                for line in materialize_result.stdout.splitlines()
                if line.strip()
            }
            self.assertEqual(
                written_paths,
                {"AGENTS.md", ".github/copilot-instructions.md"},
            )

            validate_result = subprocess.run(
                [
                    sys.executable,
                    "scripts/required_files_bootstrap.py",
                    "validate",
                    "--target-repo-path",
                    str(scratch_repo_path),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(Path(validate_result.stdout.strip()), scratch_repo_path.resolve())

            prompt_text = prompt_artifact_path.read_text(encoding="utf-8")
            self.assertIn("Required Files Bootstrap Pack", prompt_text)
            self.assertIn("PATH: AGENTS.md", prompt_text)
            self.assertIn("PATH: .github/copilot-instructions.md", prompt_text)

            pack = required_files_bootstrap.load_required_files_pack()
            for spec in pack.required_files:
                file_path = scratch_repo_path / spec.path
                self.assertTrue(file_path.exists())
                content = file_path.read_text(encoding="utf-8")
                for anchor in spec.minimum_anchors:
                    self.assertIn(anchor, content)

    def test_smoke_test_writes_inspectable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_root = Path(tmp) / "smoke-test"
            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/required_files_bootstrap.py",
                    "smoke-test",
                    "--target-repo-name",
                    "smoke-test-repo",
                    "--workspace-root",
                    str(workspace_root),
                ],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            summary = json.loads(result.stdout)
            self.assertEqual(
                Path(summary["prompt_artifact_path"]),
                (workspace_root / "required-files-bootstrap-prompt.md").resolve(),
            )
            self.assertEqual(
                Path(summary["scratch_repo_path"]),
                (workspace_root / "scratch-target-repo").resolve(),
            )
            self.assertEqual(
                set(summary["written_files"]),
                {"AGENTS.md", ".github/copilot-instructions.md"},
            )
            self.assertEqual(
                Path(summary["summary_path"]),
                (workspace_root / "smoke-test-summary.json").resolve(),
            )


if __name__ == "__main__":
    unittest.main()
