from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.build_task_queue import main


ROOT = Path(__file__).resolve().parents[1]


class BuildTaskQueueTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
