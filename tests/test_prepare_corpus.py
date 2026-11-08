from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts.prepare_corpus import clean_stem, main


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PrepareCorpusTests(unittest.TestCase):
    def make_fixture(self, base: Path, status: str = "COMPLETE") -> tuple[Path, Path, Path]:
        root = base / "chem-literature"
        package = root / "runs" / "preprocessed" / "10_1234_example" / "01_preprocessing"
        (root / "papers_pdf").mkdir(parents=True)
        (root / "papers_si").mkdir(parents=True)
        (package / "extracted_figures" / "MAIN").mkdir(parents=True)
        (package / "extracted_tables").mkdir(parents=True)
        (package / "page_text").mkdir(parents=True)

        main_pdf = root / "papers_pdf" / "paper.pdf"
        main_pdf.write_bytes(b"%PDF-1.7\nnot parsed by Feature_create")
        si_zip = root / "papers_si" / "paper_SI.zip"
        with zipfile.ZipFile(si_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("SI/raw_data.csv", "Cycle,Capacity\n1,220\n")

        (package / "main_analysis.md").write_text(
            "# Main Analysis\n\n## Page 1 (MAIN)\n\nDischarge capacity was 220 mAh g-1.\n",
            encoding="utf-8",
        )
        (package / "si_analysis.md").write_text(
            "# Supporting Information\n\n## Page 1 (SI)\n\nAdditional capacity retention was 91%.\n",
            encoding="utf-8",
        )
        table_md = package / "extracted_tables" / "MAIN_TAB_P1_01.md"
        table_csv = package / "extracted_tables" / "MAIN_TAB_P1_01.csv"
        table_md.write_text(
            "### MAIN_TAB_P1_01\n\n| Metric | Value |\n|---|---|\n| Capacity | 220 mAh g-1 |\n",
            encoding="utf-8",
        )
        table_csv.write_text("Metric,Value\nCapacity,220 mAh g-1\n", encoding="utf-8")
        figure = package / "extracted_figures" / "MAIN" / "MAIN_FIG_001.png"
        figure.write_bytes(b"synthetic upstream extracted figure bytes")
        (package / "PREPROCESSING_QC_REPORT.md").write_text(
            "# QC\n\n- Status: " + status + "\n", encoding="utf-8"
        )

        package_index = {
            "package_version": "1.1.0",
            "paper_id": "10_1234_example",
            "metadata": {
                "doi": "10.1234/example", "title": "A paired paper",
                "has_si": True, "publication_year": 2025,
            },
            "inventory": {
                "main_pdf": {
                    "path": str(main_pdf), "sha256": digest(main_pdf),
                    "size_bytes": main_pdf.stat().st_size, "page_count": 1,
                },
                "si_pdf": None,
                "source_supplementary_files": [{
                    "path": str(si_zip), "filename": si_zip.name, "format": "ZIP",
                    "size_bytes": si_zip.stat().st_size, "sha256": digest(si_zip),
                }],
                "supplementary_files": [{
                    "path": str(si_zip), "filename": si_zip.name, "sha256": digest(si_zip),
                    "processing_status": "PARSED",
                }],
            },
            "pages": [
                {"doc_type": "MAIN", "page_index": 0, "page_number": 1, "char_count": 80,
                 "is_scanned": False, "blocks": [{"bbox": [10, 20, 100, 40], "text": "Discharge capacity was 220 mAh g-1."}]},
                {"doc_type": "SI", "page_index": 0, "page_number": 1, "char_count": 50,
                 "is_scanned": False, "blocks": [{"bbox": [10, 20, 100, 40], "text": "Capacity retention was 91%."}]},
            ],
            "tables": [{
                "table_id": "MAIN_TAB_P1_01", "doc_type": "MAIN", "page_number": 1,
                "bbox": [10, 40, 300, 160], "rows": [
                    ["Metric", "Value"], ["Capacity", "220 mAh g-1"]
                ], "row_count": 2, "col_count": 2, "caption": "Capacity results",
                "md_file": "extracted_tables/MAIN_TAB_P1_01.md",
                "csv_file": "extracted_tables/MAIN_TAB_P1_01.csv",
            }],
            "figures": [{
                "figure_id": "MAIN_FIG_001", "doc_type": "MAIN", "page_number": 1,
                "bbox": [20, 200, 400, 500], "caption": "Figure 1. Cycling performance.",
            }],
            "si_references": [{
                "ref_text": "Figure S1", "doc_type": "MAIN", "page_number": 1,
                "bbox": [10, 200, 120, 220],
            }],
            "media_assets": [{
                "asset_id": "ASSET_00001", "doc_type": "MAIN", "asset_type": "figure",
                "source_file": main_pdf.name, "source_sha256": digest(main_pdf),
                "relative_path": "extracted_figures/MAIN/MAIN_FIG_001.png",
                "sha256": digest(figure), "extraction_method": "upstream_test",
                "page_number": 1, "bbox": [20, 200, 400, 500],
                "figure_id": "MAIN_FIG_001", "table_id": None,
                "caption": "Figure 1. Cycling performance.",
                "width": 100, "height": 80, "mime_type": "image/png",
                "is_tiny_icon": False,
            }],
            "processing_summary": {
                "total_main_pages": 1, "total_si_pages": 1,
                "total_tables_detected": 1, "total_figures_detected": 1,
                "total_si_references": 1, "scanned_pages_detected": 0,
                "images_extracted": 1, "grobid_status": "DISABLED",
                "warnings": [], "status": status,
            },
        }
        (package / "evidence_index.json").write_text(
            json.dumps(package_index, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        paired_csv = root / "research_corpus_paired.csv"
        with paired_csv.open("w", encoding="utf-8-sig", newline="") as handle:
            columns = [
                "doi", "title", "year", "journal", "article_type", "landing_page_url",
                "abstract", "local_pdf_path", "local_si_info",
                "preprocessing_path", "preprocessing_status", "preprocessing_qc_report",
            ]
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            writer.writerow({
                "doi": "10.1234/example", "title": "A paired paper", "year": "2025",
                "journal": "Example Journal", "article_type": "Original Research",
                "landing_page_url": "https://doi.org/10.1234/example",
                "abstract": "An abstract.",
                "local_pdf_path": "papers_pdf/paper.pdf",
                "local_si_info": "papers_si/paper_SI.zip",
                "preprocessing_path": "runs/preprocessed/10_1234_example/01_preprocessing",
                "preprocessing_status": status,
                "preprocessing_qc_report": "runs/preprocessed/10_1234_example/01_preprocessing/PREPROCESSING_QC_REPORT.md",
            })
        return root, package, paired_csv

    def test_clean_stem_compatibility(self) -> None:
        self.assertEqual(clean_stem("paper_SI_001.pdf"), "paper")
        self.assertEqual(clean_stem("paper_supporting_information.md"), "paper")

    def test_indexes_upstream_package_without_reparsing_or_unpacking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, package, paired_csv = self.make_fixture(base)
            output = base / "feature-create-prepared"
            self.assertEqual(main([
                "--paired-csv", str(paired_csv),
                "--corpus-root", str(root),
                "--output", str(output),
            ]), 0)
            manifest = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["source_kind"], "chem-literature-preprocessed-research_corpus_paired.csv")
            self.assertFalse(manifest["parsing_performed_by_feature_create"])
            paper = manifest["papers"][0]
            self.assertEqual(paper["input_status"], "ready")
            self.assertEqual(paper["preprocessing_status"], "COMPLETE")
            self.assertEqual(paper["pair_status"], "paired_files_verified")
            self.assertEqual(len(paper["raw_source_inventory"]), 2)

            sources = manifest["sources"]
            self.assertTrue(any(x["source_type"] == "main_text" for x in sources))
            self.assertTrue(any(x["source_type"] == "si_text" for x in sources))
            table_source = next(x for x in sources if x["source_type"] == "main_table")
            self.assertEqual(table_source["table_data"]["rows"][1][1], "220 mAh g-1")
            self.assertTrue(any(x["source_type"] == "main_figure_caption" for x in sources))
            image_source = next(x for x in sources if x["is_visual_asset"])
            self.assertTrue(Path(image_source["attachment_path"]).is_file())
            self.assertTrue(Path(paper["evidence_index_copy"]).is_file())
            self.assertFalse((output / "si_archives").exists())
            self.assertFalse((output / "visual_assets").exists())
            self.assertFalse(any(Path(x["absolute_path"]).suffix.lower() == ".pdf" for x in sources if x.get("absolute_path")))

    def test_scanned_page_warning_is_preserved_and_not_marked_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, package, paired_csv = self.make_fixture(base, "WARNING_SCANNED_PAGES")
            index_path = package / "evidence_index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["processing_summary"]["scanned_pages_detected"] = 1
            index["processing_summary"]["warnings"] = ["OCR was not performed."]
            index_path.write_text(json.dumps(index), encoding="utf-8")
            output = base / "prepared"
            self.assertEqual(main(["--paired-csv", str(paired_csv), "--output", str(output)]), 0)
            paper = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))["papers"][0]
            self.assertEqual(paper["preprocessing_status"], "WARNING_SCANNED_PAGES")
            self.assertEqual(paper["input_status"], "ready_with_warnings")
            self.assertTrue(any("OCR was not performed" in x for x in paper["pair_warnings"]))

    def test_missing_or_mismatched_upstream_package_blocks_preprocessing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root, package, paired_csv = self.make_fixture(base)
            # Corrupt the package's recorded main hash without touching the source PDF.
            index_path = package / "evidence_index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["inventory"]["main_pdf"]["sha256"] = "0" * 64
            index_path.write_text(json.dumps(index), encoding="utf-8")
            output = base / "prepared"
            self.assertEqual(main(["--paired-csv", str(paired_csv), "--output", str(output)]), 0)
            paper = json.loads((output / "source_manifest.json").read_text(encoding="utf-8"))["papers"][0]
            self.assertEqual(paper["input_status"], "blocked_pair_integrity")
            self.assertEqual(paper["pair_status"], "blocked_pair_integrity")

    def test_rejects_csv_without_preprocessing_handoff_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            csv_path = root / "research_corpus_paired.csv"
            csv_path.write_text(
                "doi,title,local_pdf_path,local_si_info\n10.1234/x,Paper,p.pdf,s.zip\n",
                encoding="utf-8",
            )
            code = main(["--paired-csv", str(csv_path), "--output", str(root / "prepared")])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
