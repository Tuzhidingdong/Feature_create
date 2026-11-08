from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_task_queue import (
    group_units,
    make_source_units,
    split_text_preserving_content,
    table_row_segments,
    text_page_segments,
)
from scripts.audit_records import aggregate_source_scope_status, coverage_for_task
from scripts.validate_evidence import validate_locator_scope, verify_quote_in_assigned_scope
from scripts.build_task_queue import main as build_task_queue_main


class SemanticChunkingTests(unittest.TestCase):
    def test_end_to_end_queue_writes_chunk_scope_hashes_and_preserves_upstream_attachments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            prepared = root / "prepared"
            extracted = prepared / "extracted_text"
            extracted.mkdir(parents=True)
            main_text = "# Main Analysis\nDOI: 10.1000/end-to-end\n\n"
            for page in range(1, 6):
                main_text += (
                    f"## Page {page} (MAIN)\n"
                    + (f"Sample P{page} capacity retention result. " * 20)
                    + "\n---\n"
                )
            (extracted / "main.txt").write_text(main_text, encoding="utf-8")

            image_path = root / "figure-1.png"
            image_path.write_bytes(b"already extracted image")
            raw_pdf = root / "raw-original.pdf"
            raw_pdf.write_bytes(b"%PDF-original")
            evidence_index = root / "evidence_index.json"
            evidence_index.write_text('{"package_version":"1.1.0"}', encoding="utf-8")
            qc_report = root / "PREPROCESSING_QC_REPORT.md"
            qc_report.write_text("# QC\nStatus: COMPLETE\n", encoding="utf-8")

            sources = [{
                "source_id": "SRC-MAIN",
                "paper_id": "10.1000/end-to-end",
                "source_role": "main",
                "source_type": "main_text",
                "component": "main_text",
                "relative_path": "preprocessing/main_analysis.md",
                "parse_status": "upstream_preprocessed_text",
                "sha256": "main-source-hash",
                "extracted_text_path": "extracted_text/main.txt",
                "page_numbers": [1, 2, 3, 4, 5],
            }, {
                "source_id": "SRC-FIG-1",
                "paper_id": "10.1000/end-to-end",
                "source_role": "main",
                "source_type": "main_figure",
                "component": "main_figures",
                "relative_path": "preprocessing/extracted_figures/figure-1.png",
                "attachment_path": str(image_path),
                "sha256": "figure-sha256",
                "parse_status": "upstream_extracted_visual_asset",
                "extracted_text_path": "extracted_text/figure-descriptor.txt",
                "is_visual_asset": True,
                "page_number": 2,
                "figure_id": "MAIN_FIG_P2_01",
                "bbox": [10, 20, 300, 400],
                "asset_id": "ASSET-FIG-1",
                "asset_type": "figure",
            }]
            (extracted / "figure-descriptor.txt").write_text(
                "ASSET_ID: ASSET-FIG-1\nFIGURE_ID: MAIN_FIG_P2_01\nPAGE: 2\n",
                encoding="utf-8",
            )
            manifest = {
                "manifest_version": "0.3",
                "source_kind": "chem-literature-preprocessed-research_corpus_paired.csv",
                "corpus_fingerprint": "corpus-hash",
                "upstream_artifact_sha256": "csv-hash",
                "upstream_row_count": 1,
                "sources": sources,
                "papers": [{
                    "paper_id": "10.1000/end-to-end",
                    "doi": "10.1000/end-to-end",
                    "title": "Long paper with preprocessed inputs",
                    "year": "2026",
                    "journal": "Example Journal",
                    "article_type": "Original Research",
                    "abstract": "Abstract context.",
                    "pair_status": "paired_files_verified",
                    "input_status": "ready",
                    "preprocessing_status": "COMPLETE",
                    "preprocessing_path": "papers/10.1000.end-to-end/01_preprocessing",
                    "processing_summary": {"status": "COMPLETE"},
                    "pair_warnings": [],
                    "unparsed_material": [],
                    "evidence_index_copy": str(evidence_index),
                    "evidence_index_sha256": "evidence-hash",
                    "qc_report_path": str(qc_report),
                    "qc_report_sha256": "qc-hash",
                    "raw_source_inventory": [{
                        "role": "main_raw_pdf",
                        "absolute_path": str(raw_pdf),
                        "worker_attachment": False,
                        "reparsed_downstream": False,
                    }],
                    "source_ids": ["SRC-MAIN", "SRC-FIG-1"],
                }],
            }
            manifest_path = prepared / "source_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "dispatch"

            exit_code = build_task_queue_main([
                "--manifest", str(manifest_path),
                "--output", str(output),
                "--max-bundle-chars", "2600",
                "--max-command-chars", "7600",
                "--max-attachments", "5",
            ])
            self.assertEqual(exit_code, 0)
            queue = json.loads((output / "task_queue.json").read_text(encoding="utf-8"))
            self.assertGreater(queue["chunk_count"], 1)
            self.assertEqual(queue["task_count"], queue["chunk_count"] * 2)
            self.assertEqual(queue["pending_count"], queue["task_count"])

            for chunk_id in {task["chunk_id"] for task in queue["tasks"]}:
                paired = [task for task in queue["tasks"] if task["chunk_id"] == chunk_id]
                self.assertEqual({task["worker_role"] for task in paired}, {"source_discovery", "feature_audit"})
                self.assertEqual(paired[0]["source_scopes"], paired[1]["source_scopes"])

            image_attachment_seen = False
            figure_bundle_seen = False
            for task in queue["tasks"]:
                attachments = task["attachment_paths"]
                self.assertIn(str(evidence_index), attachments)
                self.assertIn(str(qc_report), attachments)
                self.assertNotIn(str(raw_pdf), attachments)
                self.assertTrue(all(not path.lower().endswith(".pdf") for path in attachments))
                if str(image_path) in attachments:
                    image_attachment_seen = True
                bundle = Path(task["bundle_path"]).read_text(encoding="utf-8")
                self.assertIn("Do not request or perform a second PDF/SI parsing pass", bundle)
                if "MAIN_FIG_P2_01" in bundle:
                    figure_bundle_seen = True
                for scope in task["source_scopes"]:
                    scope_file = Path(scope["scope_text_path"])
                    self.assertTrue(scope_file.is_file())
                    raw = scope_file.read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), scope["scope_text_sha256"])
                    self.assertIn("[PRIMARY SCOPE]", raw.decode("utf-8"))
            self.assertTrue(image_attachment_seen)
            self.assertTrue(figure_bundle_seen)

    def test_text_split_reconstructs_source_without_loss_or_overlap(self) -> None:
        source = ("alpha beta gamma\n\n" * 40) + "last marker Ω"
        parts = split_text_preserving_content(source, 47)
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(parts), source)
        self.assertIn("last marker Ω", parts[-1])

    def test_page_chunks_keep_upstream_page_locators(self) -> None:
        text = (
            "# Main Analysis\nDOI: 10.1000/example\n\n"
            "## Page 1 (MAIN)\n" + ("Page one unique sentence. " * 18) + "\n---\n"
            "## Page 2 (MAIN)\n" + ("Page two unique sentence. " * 18) + "\n---\n"
            "## Page 3 (MAIN)\nThird page final marker.\n"
        )
        source = {
            "source_id": "SRC-MAIN-1",
            "component": "main_text",
            "page_numbers": [1, 2, 3],
        }
        scopes = text_page_segments(text, source, 180)
        self.assertEqual("".join(scope["text"] for scope in scopes), text)
        self.assertEqual({1, 2, 3}, {scope["page_number"] for scope in scopes})
        self.assertTrue(all(scope["scope_id"].startswith("SCP-") for scope in scopes))
        self.assertTrue(any(scope["scope_type"] == "page_text_segment" for scope in scopes))

    def test_table_row_chunks_preserve_original_rows_and_repeat_header_as_context(self) -> None:
        rows = [["Sample", "Capacity", "Retention"]]
        rows.extend([[f"S{i}", f"{300+i} mAh g-1", f"{90-i/10:.1f}%"] for i in range(1, 31)])
        source = {
            "source_id": "SRC-TABLE-1",
            "source_type": "main_table",
            "component": "main_tables",
            "source_role": "main",
            "table_id": "MAIN_TAB_P5_01",
            "table_data": {
                "table_id": "MAIN_TAB_P5_01",
                "doc_type": "MAIN",
                "page_number": 5,
                "bbox": [10, 20, 300, 400],
                "caption": "Electrochemical results",
                "rows": rows,
            },
        }
        scopes = table_row_segments(source, 600)
        self.assertGreater(len(scopes), 1)
        primary_rows = []
        for scope in scopes:
            primary_rows.extend(range(scope["row_start"], scope["row_end"] + 1))
            rendered = scope["text"]
            self.assertIn("TABLE_ID: MAIN_TAB_P5_01", rendered)
            if scope["row_start"] > 1:
                self.assertEqual([1], scope["context_rows"])
                self.assertIn("CONTEXT ROW (repeated for column-header interpretation) | ROW 1", rendered)
            for row_number in range(scope["row_start"], scope["row_end"] + 1):
                self.assertIn(f"PRIMARY ROW | ROW {row_number} |", rendered)
        self.assertEqual(list(range(1, len(rows) + 1)), primary_rows)

    def test_task_grouping_chunks_all_pages_without_changing_source_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text_dir = root / "extracted_text"
            text_dir.mkdir()
            text = "# Main Analysis\nDOI: 10.1000/chunk\n\n"
            for page in range(1, 9):
                text += f"## Page {page} (MAIN)\n"
                text += (f"Page {page} unique evidence marker. " * 35) + "\n---\n"
            (text_dir / "main.txt").write_text(text, encoding="utf-8")

            source = {
                "source_id": "SRC-STABLE-MAIN",
                "paper_id": "10.1000/chunk",
                "source_role": "main",
                "source_type": "main_text",
                "component": "main_text",
                "relative_path": "preprocessing/main_analysis.md",
                "parse_status": "upstream_preprocessed_text",
                "sha256": "source-hash",
                "extracted_text_path": "extracted_text/main.txt",
                "page_numbers": list(range(1, 9)),
            }
            units = make_source_units([source], root, max_unit_chars=1500)
            self.assertEqual("".join(unit["text"] for unit in units), text)

            paper = {
                "paper_id": "10.1000/chunk",
                "doi": "10.1000/chunk",
                "title": "Chunking test",
                "year": "2026",
                "journal": "Test Journal",
                "article_type": "Original Research",
                "preprocessing_status": "COMPLETE",
                "input_status": "ready",
                "processing_summary": {},
                "pair_warnings": [],
                "unparsed_material": [],
                "upstream_csv_rows": [],
            }
            paper_dir = root / "tasks"
            groups = group_units(
                paper,
                units,
                paper_dir,
                {"source_discovery": "opencode-go/free-a", "feature_audit": "opencode-go/free-b"},
                max_bundle_chars=3300,
                max_command_chars=7600,
                max_attachments=12,
            )
            self.assertGreater(len(groups), 1)
            grouped_scope_ids = [
                unit["scope"]["scope_id"] for group in groups for unit in group
            ]
            self.assertEqual(len(grouped_scope_ids), len(set(grouped_scope_ids)))
            self.assertEqual(
                set(grouped_scope_ids),
                {unit["scope"]["scope_id"] for unit in units},
            )
            self.assertTrue(all(unit["source"]["source_id"] == "SRC-STABLE-MAIN" for group in groups for unit in group))

    def test_source_is_partial_until_every_chunk_scope_is_complete(self) -> None:
        expected = {"SRC-1": {"scope-a", "scope-b", "scope-c"}}
        partial = {"scope-a": "fully_reviewed", "scope-b": "fully_reviewed"}
        result = aggregate_source_scope_status(
            expected, partial, "fully_reviewed", "partially_reviewed"
        )
        self.assertEqual({"SRC-1": "partially_reviewed"}, result)
        complete = {
            "scope-a": "fully_reviewed",
            "scope-b": "fully_reviewed",
            "scope-c": "fully_reviewed",
        }
        result = aggregate_source_scope_status(
            expected, complete, "fully_reviewed", "partially_reviewed"
        )
        self.assertEqual({"SRC-1": "fully_reviewed"}, result)

    def test_coverage_for_task_keeps_multiple_scopes_of_same_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            (output_dir / "response.json").write_text(
                (
                    '{"coverage":['
                    '{"source_id":"SRC-1","scope_id":"SCP-1","status":"fully_reviewed"},'
                    '{"source_id":"SRC-1","scope_id":"SCP-2","status":"partially_reviewed"}]}'
                ),
                encoding="utf-8",
            )
            task = {
                "worker_role": "source_discovery",
                "output_dir": str(output_dir),
                "source_scopes": [
                    {"source_id": "SRC-1", "scope_id": "SCP-1"},
                    {"source_id": "SRC-1", "scope_id": "SCP-2"},
                ],
            }
            self.assertEqual(
                {"SCP-1": "fully_reviewed", "SCP-2": "partially_reviewed"},
                coverage_for_task(task),
            )


    def test_evidence_locator_must_be_inside_assigned_original_row_range(self) -> None:
        source = {"table_data": {"rows": [[str(i)] for i in range(1, 51)]}}
        scopes = [{
            "scope_id": "SCP-TABLE",
            "scope_type": "table_rows",
            "row_start": 20,
            "row_end": 30,
            "context_rows": [1, 2, 3],
        }]
        self.assertIsNone(validate_locator_scope(
            source, {"row_index_1based": 25, "column_index_1based": 1}, scopes
        ))
        self.assertIsNone(validate_locator_scope(
            source, {"row_index_1based": 2, "column_index_1based": 1}, scopes
        ))
        self.assertIn("outside this chunk", validate_locator_scope(
            source, {"row_index_1based": 15, "column_index_1based": 1}, scopes
        ))

    def test_text_quote_must_occur_in_exact_assigned_segment_or_its_local_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            scope_path = Path(temp) / "scope.txt"
            scope_path.write_text(
                "[CONTEXT BEFORE]\nPrevious sample identity.\n"
                "[PRIMARY SCOPE]\nCapacity retention reached 94.2%.\n",
                encoding="utf-8",
            )
            task = {
                "source_scopes": [{
                    "source_id": "SRC-TEXT",
                    "scope_id": "SCP-TEXT",
                    "scope_type": "page_text_segment",
                    "page_number": 7,
                    "scope_text_path": str(scope_path),
                }]
            }
            ok, _ = verify_quote_in_assigned_scope(
                "SRC-TEXT", {"page_number": 7}, "Previous sample identity.", task
            )
            self.assertTrue(ok)
            ok, note = verify_quote_in_assigned_scope(
                "SRC-TEXT", {"page_number": 8}, "Capacity retention reached 94.2%.", task
            )
            self.assertFalse(ok)
            self.assertIn("No assigned source scope matches", note)


if __name__ == "__main__":
    unittest.main()
