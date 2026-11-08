from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.build_task_queue import (
    group_units,
    make_source_units,
    render_table_rows,
    split_text_preserving_content,
    table_row_segments,
    text_page_segments,
)
from scripts.audit_records import aggregate_source_scope_status, coverage_for_task
from scripts.validate_evidence import validate_locator_scope, verify_quote_in_assigned_scope


class SemanticChunkingTests(unittest.TestCase):
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
