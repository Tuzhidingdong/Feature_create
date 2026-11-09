from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from feature_create.sampling import SamplingError, create_corpus_sample
from scripts.prepare_corpus import CorpusError, load_sampling_provenance


FIELDS = [
    "doi", "title", "year", "journal", "article_type",
    "local_pdf_path", "local_si_info", "preprocessing_path",
    "preprocessing_status", "abstract",
]


def make_rows(count: int) -> list[dict[str, str]]:
    return [
        {
            "doi": f"10.1234/paper-{index:04d}",
            "title": f"Paper {index}",
            "year": str(2020 + index % 3),
            "journal": f"Journal {index % 4}",
            "article_type": "Research Article",
            "local_pdf_path": f"pdf/paper-{index}.pdf",
            "local_si_info": f"si/paper-{index}.zip",
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


def selected_dois(csv_path: Path) -> list[str]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [row["doi"] for row in csv.DictReader(handle)]


class CorpusSamplingTests(unittest.TestCase):
    def test_sampling_is_repeatable_and_selects_exact_rounded_fraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            rows = make_rows(100)
            write_csv(source, rows)

            first = create_corpus_sample(source, root / "run-a", fraction="0.1", seed="fixed-seed")
            second = create_corpus_sample(source, root / "run-b", fraction="0.1", seed="fixed-seed")

            self.assertEqual(10, first["selected_paper_count"])
            self.assertEqual(10, second["selected_paper_count"])
            self.assertEqual(
                selected_dois(Path(first["sampled_csv"])),
                selected_dois(Path(second["sampled_csv"])),
            )
            self.assertEqual(
                Path(first["sampled_csv"]).read_bytes(),
                Path(second["sampled_csv"]).read_bytes(),
            )

    def test_stratified_sampling_preserves_proportions_across_years(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            rows = make_rows(60)
            write_csv(source, rows)

            result = create_corpus_sample(
                source, root / "sample", fraction="0.1",
                seed="year-strata", stratify_by=("year",),
            )
            manifest = __import__("json").loads(Path(result["sampling_manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(6, result["selected_paper_count"])
            self.assertEqual(
                {2},
                {item["selected_paper_count"] for item in manifest["stratum_summary"]},
            )
            self.assertEqual(3, len(manifest["stratum_summary"]))

    def test_duplicate_doi_rows_are_selected_or_excluded_as_one_paper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            rows = make_rows(20)
            duplicate = dict(rows[0])
            duplicate["abstract"] = "Another CSV row for the same DOI"
            rows.append(duplicate)
            write_csv(source, rows)

            result = create_corpus_sample(
                source, root / "sample", fraction="0.2",
                seed="duplicate-check", stratify_by=(),
            )
            manifest = __import__("json").loads(Path(result["sampling_manifest"]).read_text(encoding="utf-8"))
            decisions = {item["doi"]: item for item in manifest["paper_decisions"]}
            self.assertEqual(20, result["source_paper_count"])
            self.assertEqual(4, result["selected_paper_count"])
            self.assertEqual(2, decisions["10.1234/paper-0000"]["row_count"])
            selected_rows = selected_dois(Path(result["sampled_csv"]))
            self.assertEqual(5 if decisions["10.1234/paper-0000"]["selected"] else 4, len(selected_rows))

    def test_unusable_preprocessing_rows_are_excluded_before_sampling(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            rows = make_rows(10)
            rows[0]["preprocessing_status"] = "FAILED"
            rows[1]["preprocessing_path"] = ""
            write_csv(source, rows)

            result = create_corpus_sample(
                source, root / "sample", fraction="0.25", seed="eligible-only", stratify_by=(),
            )
            manifest = __import__("json").loads(Path(result["sampling_manifest"]).read_text(encoding="utf-8"))
            self.assertEqual(10, result["source_paper_count"])
            self.assertEqual(8, result["eligible_paper_count"])
            self.assertEqual(2, result["ineligible_paper_count"])
            self.assertEqual(2, result["selected_paper_count"])
            self.assertEqual(2, len([item for item in manifest["paper_decisions"] if not item["eligible"]]))

    def test_prepare_corpus_verifies_sampled_csv_hash_and_preserves_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            write_csv(source, make_rows(20))
            result = create_corpus_sample(source, root / "sample", fraction="0.2", seed="manifest")
            sampled_csv = Path(result["sampled_csv"])
            manifest = Path(result["sampling_manifest"])

            provenance = load_sampling_provenance(manifest, sampled_csv)
            self.assertEqual(4, provenance["selected_paper_count"])
            self.assertEqual(result["sampled_csv_sha256"], provenance["sampled_csv_sha256"])
            self.assertEqual("0.2", provenance["requested_fraction"])

            sampled_csv.write_text(sampled_csv.read_text(encoding="utf-8-sig") + "\n", encoding="utf-8")
            with self.assertRaises(CorpusError):
                load_sampling_provenance(manifest, sampled_csv)

    def test_prepare_corpus_rejects_tampered_selection_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            write_csv(source, make_rows(20))
            result = create_corpus_sample(
                source, root / "sample", fraction="0.2", seed="tamper-ledger", stratify_by=(),
            )
            sampled_csv = Path(result["sampled_csv"])
            manifest_path = Path(result["sampling_manifest"])
            manifest = __import__("json").loads(manifest_path.read_text(encoding="utf-8"))
            manifest["paper_decisions"][0]["selected"] = not manifest["paper_decisions"][0]["selected"]
            manifest_path.write_text(
                __import__("json").dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            with self.assertRaises(CorpusError):
                load_sampling_provenance(manifest_path, sampled_csv)

    def test_selection_is_independent_of_source_row_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = make_rows(100)
            first_csv = root / "original.csv"
            reversed_csv = root / "reversed.csv"
            write_csv(first_csv, original)
            write_csv(reversed_csv, list(reversed(original)))

            first = create_corpus_sample(
                first_csv, root / "sample-a", fraction="0.1", seed="row-order", stratify_by=("year",),
            )
            second = create_corpus_sample(
                reversed_csv, root / "sample-b", fraction="0.1", seed="row-order", stratify_by=("year",),
            )
            self.assertEqual(
                set(selected_dois(Path(first["sampled_csv"]))),
                set(selected_dois(Path(second["sampled_csv"]))),
            )

    def test_existing_sample_outputs_are_not_silently_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            write_csv(source, make_rows(30))
            output = root / "sample"
            first = create_corpus_sample(source, output, fraction="0.1", seed="first")
            manifest_path = Path(first["sampling_manifest"])
            original_manifest = manifest_path.read_bytes()

            with self.assertRaises(SamplingError):
                create_corpus_sample(source, output, fraction="0.2", seed="second")

            self.assertEqual(original_manifest, manifest_path.read_bytes())

    def test_no_doi_papers_use_title_year_journal_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            rows = make_rows(20)
            rows[0]["doi"] = ""
            duplicate = dict(rows[0])
            duplicate["abstract"] = "Second row with the same metadata identity"
            rows.append(duplicate)
            write_csv(source, rows)

            result = create_corpus_sample(
                source, root / "sample", fraction="0.2", seed="metadata-fallback", stratify_by=(),
            )
            manifest = __import__("json").loads(Path(result["sampling_manifest"]).read_text(encoding="utf-8"))
            metadata_decisions = [
                item for item in manifest["paper_decisions"]
                if item["paper_key"].startswith("metadata:")
            ]
            self.assertEqual(1, len(metadata_decisions))
            self.assertEqual(2, metadata_decisions[0]["row_count"])

    def test_invalid_fraction_and_missing_identity_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "research_corpus_paired.csv"
            write_csv(source, make_rows(3))
            with self.assertRaises(SamplingError):
                create_corpus_sample(source, root / "bad-fraction", fraction="1.1")
            rows = make_rows(3)
            rows[0]["doi"] = ""
            rows[0]["title"] = ""
            write_csv(source, rows)
            with self.assertRaises(SamplingError):
                create_corpus_sample(source, root / "bad-identity")


if __name__ == "__main__":
    unittest.main()
