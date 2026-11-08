from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.prepare_corpus import clean_stem, main


class PrepareCorpusTests(unittest.TestCase):
    def write_paired_csv(self, path: Path, rows: list[dict[str, str]]) -> None:
        columns = [
            "doi", "title", "year", "journal", "article_type",
            "cited_by_count", "landing_page_url", "abstract",
            "local_pdf_path", "local_si_info", "pdf_status", "si_status",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    def create_pdf(self, path: Path, text: str = "Figure 1. Cycling performance\nTable S1. Additional data") -> None:
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")
        path.parent.mkdir(parents=True, exist_ok=True)
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), text)
        document.save(path)
        document.close()

    def test_normalizes_stems_without_scanning_the_directory(self) -> None:
        self.assertEqual(clean_stem("paper_SI_001.pdf"), "paper")
        self.assertEqual(clean_stem("paper_supporting_information.md"), "paper")
        self.assertEqual(clean_stem("SI_paper_supplementary.xlsx"), "paper")

    def test_consumes_only_csv_listed_pdf_and_si_and_expands_zip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "chem-literature-output"
            output = Path(temp) / "feature-create-prepared"
            (root / "papers_pdf").mkdir(parents=True)
            (root / "papers_si").mkdir(parents=True)
            main_pdf = root / "papers_pdf" / "10_1234_example.pdf"
            self.create_pdf(main_pdf)
            si_zip = root / "papers_si" / "10_1234_example_SI.zip"
            with zipfile.ZipFile(si_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "Supporting Information/Table_S1.md",
                    "Supplementary Table S1\nAdditional capacity: 2.5 mAh g-1",
                )
                archive.writestr(
                    "Supporting Information/raw_data.csv",
                    "Cycle,Capacity\n1,220\n2,215\n",
                )
            # An unrelated file in the same folder must not be discovered.
            rogue_pdf = root / "papers_pdf" / "not_in_paired_csv.pdf"
            self.create_pdf(rogue_pdf, "Unlisted paper")

            paired_csv = root / "research_corpus_paired.csv"
            self.write_paired_csv(paired_csv, [{
                "doi": "10.1234/example",
                "title": "A paired research paper",
                "year": "2025",
                "journal": "Journal of Example Chemistry",
                "article_type": "Original Research",
                "cited_by_count": "12",
                "landing_page_url": "https://doi.org/10.1234/example",
                "abstract": "An abstract.",
                "local_pdf_path": "papers_pdf/10_1234_example.pdf",
                "local_si_info": "papers_si/10_1234_example_SI.zip",
                "pdf_status": "downloaded",
                "si_status": "downloaded",
            }])

            code = main(["--paired-csv", str(paired_csv), "--output", str(output)])
            self.assertEqual(code, 0)
            manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_kind"], "chem-literature-research_corpus_paired.csv")
            self.assertEqual(manifest["upstream_row_count"], 1)
            self.assertEqual(manifest["paper_count"], 1)
            paper = manifest["papers"][0]
            self.assertEqual(paper["doi"], "10.1234/example")
            self.assertEqual(paper["title"], "A paired research paper")
            self.assertEqual(paper["pair_status"], "paired_files_verified")
            self.assertEqual(len(paper["main_source_ids"]), 1)
            self.assertGreaterEqual(len(paper["si_source_ids"]), 3)

            paths = [source["relative_path"] for source in manifest["sources"]]
            self.assertFalse(any("not_in_paired_csv" in path for path in paths))
            si_text_sources = [
                source for source in manifest["sources"]
                if source["source_type"] == "si_text" and source.get("extracted_text_path")
            ]
            self.assertTrue(si_text_sources)
            extracted = (
                output / si_text_sources[0]["extracted_text_path"]
            ).read_text(encoding="utf-8")
            self.assertIn("2.5 mAh g-1", extracted)

            main_source = next(
                source for source in manifest["sources"]
                if source["source_role"] == "main"
            )
            self.assertEqual(main_source["parse_status"], "parsed")
            self.assertTrue(main_source["visual_pages"])
            visual = output / "visual_assets" / main_source["visual_pages"][0]["relative_path"]
            self.assertTrue(visual.is_file())

    def test_missing_si_path_blocks_pair_without_replacing_from_directory_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "chem-literature-output"
            output = Path(temp) / "prepared"
            (root / "papers_pdf").mkdir(parents=True)
            (root / "papers_si").mkdir(parents=True)
            main_pdf = root / "papers_pdf" / "paper.pdf"
            self.create_pdf(main_pdf)
            # A physically present SI is deliberately not listed in the CSV.
            (root / "papers_si" / "unlisted_si.txt").write_text(
                "Unlisted supplementary material", encoding="utf-8"
            )
            paired_csv = root / "research_corpus_paired.csv"
            self.write_paired_csv(paired_csv, [{
                "doi": "10.5678/missing-si",
                "title": "Missing SI row",
                "year": "2024",
                "journal": "Example",
                "article_type": "Original Research",
                "local_pdf_path": "papers_pdf/paper.pdf",
                "local_si_info": "",
            }])
            code = main(["--paired-csv", str(paired_csv), "--output", str(output)])
            self.assertEqual(code, 0)
            manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["papers"][0]["pair_status"], "blocked_pair_integrity")
            self.assertFalse(any("unlisted_si" in item["relative_path"] for item in manifest["sources"]))

    def test_missing_physical_file_is_retained_as_blocked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "chem-literature-output"
            root.mkdir()
            output = Path(temp) / "prepared"
            paired_csv = root / "research_corpus_paired.csv"
            self.write_paired_csv(paired_csv, [{
                "doi": "10.9012/missing",
                "title": "Missing physical files",
                "year": "2023",
                "journal": "Example",
                "article_type": "Original Research",
                "local_pdf_path": "papers_pdf/missing.pdf",
                "local_si_info": "papers_si/missing.zip",
            }])
            code = main(["--paired-csv", str(paired_csv), "--output", str(output)])
            self.assertEqual(code, 0)
            manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["papers"][0]["pair_status"], "blocked_pair_integrity")
            self.assertTrue(any(source["parse_status"] == "missing_file" for source in manifest["sources"]))

    def test_rejects_non_chem_literature_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bad_csv = root / "arbitrary.csv"
            bad_csv.write_text("filename,path\npaper,paper.pdf\n", encoding="utf-8")
            code = main([
                "--paired-csv", str(bad_csv),
                "--output", str(root / "prepared"),
            ])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
