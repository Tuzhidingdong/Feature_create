from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.prepare_corpus import clean_stem, main


class PrepareCorpusTests(unittest.TestCase):
    def test_normalizes_supplementary_filename_suffixes(self) -> None:
        self.assertEqual(clean_stem("paper_SI_001.pdf"), "paper")
        self.assertEqual(clean_stem("paper_supporting_information.md"), "paper")
        self.assertEqual(clean_stem("SI_paper_supplementary.xlsx"), "paper")

    def test_groups_doi_less_si_with_unique_main_stem(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "corpus"
            output = Path(temp) / "prepared"
            root.mkdir()
            (root / "paper.md").write_text(
                "Title\nDOI: 10.1234/example.paper\nMain results: 1.25 mAh g-1",
                encoding="utf-8",
            )
            (root / "paper_SI_001.md").write_text(
                "Supplementary Table S1\nAdditional result: 2.5",
                encoding="utf-8",
            )

            exit_code = main(["--input", str(root), "--output", str(output)])
            self.assertEqual(exit_code, 0)
            manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["paper_count"], 1)
            self.assertEqual(len(manifest["papers"][0]["source_ids"]), 2)
            self.assertEqual(manifest["papers"][0]["doi"], "10.1234/example.paper")
            si_sources = [s for s in manifest["sources"] if s["source_role"] == "si"]
            self.assertEqual(len(si_sources), 1)
            self.assertEqual(si_sources[0]["doi_match_method"], "unique_normalized_filename_stem")


if __name__ == "__main__":
    unittest.main()
