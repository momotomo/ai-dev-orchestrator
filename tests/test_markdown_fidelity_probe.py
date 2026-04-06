from __future__ import annotations

import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "markdown_fidelity"
sys.path.insert(0, str(SCRIPTS_DIR))

import markdown_fidelity_probe  # noqa: E402


def read_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


class MarkdownFidelityProbeTests(unittest.TestCase):
    def test_visible_text_fixture_is_lossy_for_markdown_markers(self) -> None:
        summary = markdown_fidelity_probe.analyze_markdown_text(read_fixture("visible_reply.txt"))
        self.assertFalse(summary.has_heading_marker)
        self.assertFalse(summary.has_exact_dash_list)
        self.assertFalse(summary.has_markdown_list)
        self.assertFalse(summary.has_inline_code)
        self.assertFalse(summary.has_fenced_code)
        self.assertTrue(summary.has_blank_line_pair)

    def test_copied_variants_keep_markdown_structure_but_not_exact_dash_list(self) -> None:
        variant_a = markdown_fidelity_probe.analyze_markdown_text(read_fixture("copied_reply_variant_a.md"))
        variant_b = markdown_fidelity_probe.analyze_markdown_text(read_fixture("copied_reply_variant_b.md"))

        for summary in (variant_a, variant_b):
            self.assertTrue(summary.has_heading_marker)
            self.assertFalse(summary.has_exact_dash_list)
            self.assertTrue(summary.has_markdown_list)
            self.assertTrue(summary.has_inline_code)
            self.assertTrue(summary.has_fenced_code)
            self.assertTrue(summary.has_blank_line_pair)

    def test_copied_variants_are_not_byte_identical(self) -> None:
        variant_a = markdown_fidelity_probe.analyze_markdown_text(read_fixture("copied_reply_variant_a.md"))
        variant_b = markdown_fidelity_probe.analyze_markdown_text(read_fixture("copied_reply_variant_b.md"))
        self.assertNotEqual(variant_a.sha256, variant_b.sha256)


if __name__ == "__main__":
    unittest.main()
