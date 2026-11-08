from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_task_queue import main


ROOT = Path(__file__).resolve().parents[1]


class BuildTaskQueueTests(unittest.TestCase):
    def test_worker_task_ids_change_when_upstream_paper_metadata_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            prepared = base / "prepared"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            (extracted / "source.txt").write_text("Stable source text.", encoding="utf-8")
            source = {
                "source_id": "SRC-1",
                "paper_id": "10.1234/stable",
                "source_role": "main",
                "source_type": "main_text",
                "relative_path": "papers_pdf/paper.pdf",
                "absolute_path": str(base / "paper.pdf"),
                "parse_status": "parsed",
                "sha256": "fixed-source-hash",
                "extracted_text_path": "extracted_text/source.txt",
            }

            def build(title: str, run_name: str) -> list[str]:
                manifest = {
                    "manifest_version": "0.2",
                    "source_kind": "chem-literature-research_corpus_paired.csv",
                    "corpus_fingerprint": "same-file-fingerprint",
                    "upstream_artifact_sha256": "csv-hash-" + title,
                    "sources": [source],
                    "papers": [{
                        "paper_id": "10.1234/stable",
                        "doi": "10.1234/stable",
                        "title": title,
                        "year": "2025",
                        "journal": "Example Journal",
                        "article_type": "Original Research",
                        "abstract": "An abstract.",
                        "pair_status": "paired_files_verified",
                        "source_ids": ["SRC-1"],
                    }],
                }
                manifest_path = prepared / f"{run_name}_manifest.json"
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                output = base / run_name
                self.assertEqual(main(["--manifest", str(manifest_path), "--output", str(output)]), 0)
                queue = json.loads((output / "task_queue.json").read_text(encoding="utf-8"))
                return sorted(task["task_id"] for task in queue["tasks"])

            original_ids = build("Original title", "dispatch_original")
            revised_ids = build("Updated title", "dispatch_revised")
            self.assertNotEqual(original_ids, revised_ids)

    def test_blocks_papers_that_failed_upstream_pair_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            prepared = base / "prepared"
            run = base / "dispatch"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            (extracted / "source.txt").write_text("Main text marker.", encoding="utf-8")
            source = {
                "source_id": "SRC-1",
                "paper_id": "PAPER-1",
                "source_role": "main",
                "source_type": "main_text",
                "relative_path": "papers_pdf/paper.pdf",
                "absolute_path": str(base / "paper.pdf"),
                "parse_status": "parsed",
                "sha256": "abc123",
                "extracted_text_path": "extracted_text/source.txt",
            }
            manifest = {
                "manifest_version": "0.2",
                "source_kind": "chem-literature-research_corpus_paired.csv",
                "corpus_fingerprint": "fingerprint",
                "sources": [source],
                "papers": [{
                    "paper_id": "PAPER-1",
                    "doi": None,
                    "title": "A paper with missing SI",
                    "pair_status": "blocked_pair_integrity",
                    "source_ids": ["SRC-1"],
                }],
            }
            manifest_path = prepared / "source_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            exit_code = main([
                "--manifest", str(manifest_path),
                "--output", str(run),
            ])
            self.assertEqual(exit_code, 0)
            queue = json.loads((run / "task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["blocked_count"], 2)
            self.assertTrue(all(task["task_status"] == "blocked_pair_integrity" for task in queue["tasks"]))

    def test_reads_extracted_text_from_manifest_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            prepared = base / "prepared"
            run = base / "separate_run"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            text_path = extracted / "source.txt"
            text_path.write_text("Unique source text marker 123.45", encoding="utf-8")
            source = {
                "source_id": "SRC-1",
                "paper_id": "PAPER-1",
                "source_role": "main",
                "source_type": "main_text",
                "relative_path": "papers_pdf/paper.md",
                "absolute_path": str(base / "paper.md"),
                "parse_status": "parsed",
                "sha256": "abc123",
                "extracted_text_path": "extracted_text/source.txt",
            }
            manifest = {
                "manifest_version": "0.1",
                "corpus_fingerprint": "fingerprint",
                "sources": [source],
                "papers": [{
                    "paper_id": "PAPER-1",
                    "doi": None,
                    "title": "Test paper from paired corpus",
                    "year": "2025",
                    "journal": "Example Journal",
                    "article_type": "Original Research",
                    "pair_status": "paired_files_verified",
                    "source_ids": ["SRC-1"],
                }],
            }
            manifest_path = prepared / "source_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            exit_code = main([
                "--manifest", str(manifest_path),
                "--output", str(run),
            ])
            self.assertEqual(exit_code, 0)
            queue = json.loads((run / "task_queue.json").read_text(encoding="utf-8"))
            self.assertEqual(queue["task_count"], 2)
            task_input = next((run / "tasks").rglob("task_input.md"))
            self.assertIn("Unique source text marker 123.45", task_input.read_text(encoding="utf-8"))


    def test_attaches_preprocessed_images_and_evidence_index_but_not_raw_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            prepared = base / "prepared"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            (extracted / "main.txt").write_text("Capacity retention was 91%.", encoding="utf-8")
            image_path = base / "figure.png"
            image_path.write_bytes(b"already extracted image")
            raw_pdf = base / "original.pdf"
            raw_pdf.write_bytes(b"%PDF-raw source")
            evidence_index = base / "evidence_index.json"
            evidence_index.write_text('{"package_version":"1.1.0","tables":[]}', encoding="utf-8")
            qc_report = base / "PREPROCESSING_QC_REPORT.md"
            qc_report.write_text("# QC\\n- Status: COMPLETE", encoding="utf-8")

            source = {
                "source_id": "SRC-PRE-1",
                "paper_id": "10.1234/preprocessed",
                "source_role": "main",
                "source_type": "main_figure",
                "component": "main_figures",
                "relative_path": "preprocessing/extracted_figures/figure.png",
                "absolute_path": str(image_path),
                "attachment_path": str(image_path),
                "parse_status": "upstream_extracted_visual_asset",
                "sha256": "figure-hash",
                "extracted_text_path": "extracted_text/main.txt",
                "is_visual_asset": True,
            }
            manifest = {
                "manifest_version": "0.3",
                "source_kind": "chem-literature-preprocessed-research_corpus_paired.csv",
                "corpus_fingerprint": "preprocessed-fingerprint",
                "sources": [source],
                "papers": [{
                    "paper_id": "10.1234/preprocessed",
                    "doi": "10.1234/preprocessed",
                    "title": "Preprocessed paper",
                    "year": "2025",
                    "journal": "Example",
                    "article_type": "Original Research",
                    "pair_status": "paired_files_verified",
                    "input_status": "ready_with_warnings",
                    "preprocessing_status": "WARNING_SCANNED_PAGES",
                    "processing_summary": {"scanned_pages_detected": 1},
                    "pair_warnings": ["One page is scanned; OCR was not performed."],
                    "unparsed_material": [],
                    "evidence_index_copy": str(evidence_index),
                    "evidence_index_sha256": "evidence-hash",
                    "qc_report_path": str(qc_report),
                    "qc_report_sha256": "qc-hash",
                    "raw_source_inventory": [{
                        "role": "main_raw_pdf", "absolute_path": str(raw_pdf),
                        "worker_attachment": False, "reparsed_downstream": False,
                    }],
                    "source_ids": ["SRC-PRE-1"],
                }],
            }
            manifest_path = prepared / "source_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = base / "dispatch"
            self.assertEqual(main(["--manifest", str(manifest_path), "--output", str(output)]), 0)
            queue = json.loads((output / "task_queue.json").read_text(encoding="utf-8"))
            for task in queue["tasks"]:
                attachments = task["attachment_paths"]
                self.assertIn(str(image_path), attachments)
                self.assertIn(task["chunk_evidence_index_path"], attachments)
                self.assertIn(task["paper_chunk_index_path"], attachments)
                self.assertNotIn(str(evidence_index), attachments)
                self.assertIn(str(qc_report), attachments)
                self.assertFalse(any(path.lower().endswith((".pdf", ".zip", ".docx", ".xlsx")) for path in attachments))
                self.assertEqual(task["preprocessing_status"], "WARNING_SCANNED_PAGES")
                task_input = Path(task["bundle_path"]).read_text(encoding="utf-8")
                self.assertIn("OCR was not performed", task_input)
                self.assertIn("Do not request or perform a second PDF/SI parsing pass", task_input)
                chunk_index = json.loads(Path(task["chunk_evidence_index_path"]).read_text(encoding="utf-8"))
                self.assertEqual("evidence-hash", chunk_index["upstream_evidence_index_sha256"])



    def test_large_paper_is_split_into_multiple_chunk_scoped_worker_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            prepared = base / "prepared"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            text = (
                "# Main Analysis\nDOI: 10.1234/large\n\n"
                "## Page 1 (MAIN)\n\n"
                + ("Page one paragraph: capacity retention 91 percent. " * 120)
                + "\n\n## Page 2 (MAIN)\n\n"
                + ("Page two paragraph: coulombic efficiency 99 percent. " * 120)
            )
            (extracted / "main.txt").write_text(text, encoding="utf-8")
            source = {
                "source_id": "SRC-LARGE-MAIN",
                "paper_id": "10.1234/large",
                "source_role": "main",
                "source_type": "main_text",
                "component": "main_text",
                "relative_path": "preprocessing/main_analysis.md",
                "parse_status": "upstream_preprocessed_text",
                "sha256": "synthetic-text-hash",
                "extracted_text_path": "extracted_text/main.txt",
            }
            manifest = {
                "manifest_version": "0.3",
                "source_kind": "chem-literature-preprocessed-research_corpus_paired.csv",
                "corpus_fingerprint": "large-paper-fingerprint",
                "sources": [source],
                "papers": [{
                    "paper_id": "10.1234/large",
                    "doi": "10.1234/large",
                    "title": "Large synthetic paper",
                    "year": "2026",
                    "journal": "Example",
                    "article_type": "Original Research",
                    "pair_status": "paired_files_verified",
                    "input_status": "ready",
                    "source_ids": ["SRC-LARGE-MAIN"],
                }],
            }
            manifest_path = prepared / "source_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = base / "dispatch"

            self.assertEqual(main([
                "--manifest", str(manifest_path),
                "--output", str(output),
                "--max-bundle-chars", "10000",
            ]), 0)

            queue = json.loads((output / "task_queue.json").read_text(encoding="utf-8"))
            chunks = {task["chunk_id"] for task in queue["tasks"]}
            self.assertGreaterEqual(len(chunks), 2)
            self.assertEqual(len(chunks) * 2, len(queue["tasks"]))
            self.assertTrue(all(task["task_status"] == "pending" for task in queue["tasks"]))
            self.assertTrue(all(task["source_ids"] == ["SRC-LARGE-MAIN"] for task in queue["tasks"]))
            self.assertTrue(all(task["chunk_evidence_index_path"] for task in queue["tasks"]))
            self.assertTrue(all(task["paper_chunk_index_path"] for task in queue["tasks"]))
            self.assertTrue(all(task["source_unit_ids"]["SRC-LARGE-MAIN"] for task in queue["tasks"]))
            self.assertTrue(all(task["chunk_count"] == len(chunks) for task in queue["tasks"]))
            self.assertTrue(all(task["source_scopes"] for task in queue["tasks"]))
            self.assertTrue(all(task["scope_count"] == len(task["source_scopes"]) for task in queue["tasks"]))
            worker_a_tasks = [task for task in queue["tasks"] if task["worker_role"] == "source_discovery"]
            scopes = [scope for task in worker_a_tasks for scope in task["source_scopes"]]
            self.assertEqual(len(scopes), len({scope["scope_id"] for scope in scopes}))
            self.assertEqual({scope["source_id"] for scope in scopes}, {"SRC-LARGE-MAIN"})
            self.assertEqual(
                {scope["unit_id"] for scope in scopes},
                {unit_id for task in worker_a_tasks for unit_id in task["unit_ids"]},
            )

            paper_index_path = Path(queue["tasks"][0]["paper_chunk_index_path"])
            paper_index = json.loads(paper_index_path.read_text(encoding="utf-8"))
            boundaries = paper_index["cross_chunk_text_boundaries"]
            self.assertGreater(len(boundaries), 1)
            self.assertTrue(all(item["source_id"] == "SRC-LARGE-MAIN" for item in boundaries))
            for boundary in boundaries:
                self.assertGreater(
                    boundary["from_segment"]["source_text_offset_end"],
                    boundary["from_segment"]["source_text_offset_start"],
                )
                self.assertEqual(
                    boundary["from_segment"]["source_text_offset_end"],
                    boundary["to_segment"]["source_text_offset_start"],
                )
                self.assertNotEqual(
                    boundary["from_segment"]["chunk_id"],
                    boundary["to_segment"]["chunk_id"],
                )


if __name__ == "__main__":
    unittest.main()
