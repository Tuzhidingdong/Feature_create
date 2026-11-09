from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from feature_create.sampling import create_corpus_sample
from feature_create.sampling_quality import (
    SamplingQualityError,
    build_sampling_quality_report,
)


FIELDS = [
    "doi", "title", "year", "journal", "article_type",
    "local_pdf_path", "local_si_info", "preprocessing_path",
    "preprocessing_status", "abstract",
]


def make_rows(count: int) -> list[dict[str, str]]:
    return [
        {
            "doi": f"10.1234/quality-{index:04d}",
            "title": f"Paper {index}",
            "year": str(2020 + index % 2),
            "journal": f"Journal {index % 4}",
            "article_type": "Research Article" if index % 5 else "Review",
            "local_pdf_path": f"pdf/paper-{index}.pdf",
            "local_si_info": "",
            "preprocessing_path": f"preprocessed/paper-{index}",
            "preprocessing_status": "COMPLETE",
            "abstract": f"Abstract {index}",
        }
        for index in range(count)
    ]


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class SamplingQualityTests(unittest.TestCase):
    def _fixture(self, root: Path, count: int = 100) -> tuple[Path, dict]:
        source = root / "research_corpus_paired.csv"
        write_csv(source, make_rows(count))
        result = create_corpus_sample(
            source, root / "sampling", fraction="0.1",
            seed="quality-test-seed", stratify_by=("year",),
        )
        return source, result

    def test_reports_paper_level_distribution_and_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, result = self._fixture(root)
            report = build_sampling_quality_report(
                source,
                Path(result["sampling_manifest"]),
                root / "quality-report",
            )
            self.assertEqual(100, report["sample"]["eligible_population_paper_count"])
            self.assertEqual(10, report["sample"]["selected_paper_count"])
            self.assertAlmostEqual(0.1, report["sample"]["actual_fraction_of_eligible_population"])
            self.assertEqual(
                ["year", "journal", "article_type", "preprocessing_status"],
                [item["column"] for item in report["dimensions"]],
            )
            year = report["dimensions"][0]
            self.assertEqual(2, year["population_category_count"])
            self.assertEqual(10, sum(item["sample_paper_count"] for item in year["categories"]))
            self.assertTrue(Path(report["report_files"]["json"]).is_file())
            self.assertTrue(Path(report["report_files"]["markdown"]).is_file())
            saved = json.loads(Path(report["report_files"]["json"]).read_text(encoding="utf-8"))
            self.assertEqual("descriptive_sampling_distribution_diagnostics", saved["report_type"])
            self.assertIn("does not prove scientific", Path(report["report_files"]["markdown"]).read_text(encoding="utf-8"))

    def test_duplicate_csv_rows_are_counted_as_one_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_rows = make_rows(20)
            duplicate = dict(source_rows[0])
            duplicate["abstract"] = "second row for the same paper"
            source = root / "research_corpus_paired.csv"
            write_csv(source, source_rows + [duplicate])
            result = create_corpus_sample(source, root / "sampling", fraction="0.2", seed="dupe", stratify_by=())
            report = build_sampling_quality_report(source, Path(result["sampling_manifest"]), root / "report")
            self.assertEqual(20, report["sample"]["eligible_population_paper_count"])
            self.assertEqual(4, report["sample"]["selected_paper_count"])
            self.assertEqual(21, report["source"]["source_row_count"])

    def test_rejects_changed_source_csv(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, result = self._fixture(root)
            with source.open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(SamplingQualityError, "Source CSV SHA-256"):
                build_sampling_quality_report(source, Path(result["sampling_manifest"]), root / "report")

    def test_rejects_incremental_csv_when_cumulative_sample_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, first = self._fixture(root)
            second = create_corpus_sample(
                source, root / "sampling-round-20", fraction="0.2",
                seed="quality-test-seed", stratify_by=("year",),
                previous_manifest=Path(first["sampling_manifest"]),
            )
            with self.assertRaisesRegex(SamplingQualityError, "cumulative sampled"):
                build_sampling_quality_report(
                    source,
                    Path(second["sampling_manifest"]),
                    root / "report",
                    sample_csv=Path(second["incremental_csv"]),
                )

    def test_rejects_manifest_or_sample_identity_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, result = self._fixture(root)
            sample = Path(result["sampled_csv"])
            with sample.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            rows[0]["doi"] = "10.1234/not-the-selected-paper"
            write_csv(sample, rows)
            with self.assertRaisesRegex(SamplingQualityError, "Sample CSV SHA-256"):
                build_sampling_quality_report(source, Path(result["sampling_manifest"]), root / "report")

    def test_custom_dimension_and_unknown_dimension(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, result = self._fixture(root)
            report = build_sampling_quality_report(
                source, Path(result["sampling_manifest"]), root / "report",
                dimensions=("journal",),
            )
            self.assertEqual(["journal"], [item["column"] for item in report["dimensions"]])
            with self.assertRaisesRegex(SamplingQualityError, "Unknown report dimensions"):
                build_sampling_quality_report(
                    source, Path(result["sampling_manifest"]), root / "bad-report",
                    dimensions=("not_a_column",),
                )


if __name__ == "__main__":
    unittest.main()
