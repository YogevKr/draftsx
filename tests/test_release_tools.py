from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "update_homebrew_formula.py"


spec = importlib.util.spec_from_file_location("update_homebrew_formula", SCRIPT_PATH)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)


class UpdateHomebrewFormulaTests(unittest.TestCase):
    def test_update_formula_text_rewrites_release_url_and_sha(self) -> None:
        original = """class Draftsx < Formula
  include Language::Python::Virtualenv

  desc "CLI and rebuildable local index for Drafts.app"
  homepage "https://github.com/YogevKr/draftsx"
  url "https://github.com/YogevKr/draftsx/releases/download/v0.1.1/draftsx-0.1.1.tar.gz"
  sha256 "oldsha"
  license "MIT"

  depends_on "python@3.13"
end
"""

        updated = module.update_formula_text(
            original,
            version="0.2.0",
            sha256="abc123",
        )

        self.assertIn('url "https://github.com/YogevKr/draftsx/releases/download/v0.2.0/draftsx-0.2.0.tar.gz"', updated)
        self.assertIn('sha256 "abc123"', updated)
        self.assertIn('license "MIT"', updated)

    def test_update_formula_text_rejects_wrong_formula(self) -> None:
        with self.assertRaises(ValueError):
            module.update_formula_text("class Other < Formula\nend\n", version="0.2.0", sha256="abc123")
