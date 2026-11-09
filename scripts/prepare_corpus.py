"""Build a source manifest from chem-literature's already-preprocessed packages.

This adapter intentionally does NOT parse PDFs, unpack SI archives, extract tables,
or render images. Those operations belong exclusively to chem-literature.
Feature_create validates the upstream package and indexes its existing outputs.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CorpusError(RuntimeError):
    pass


ALLOWED_PREPROCESSING_STATUSES = {
    "COMPLETE", "WARNING_SCANNED_PAGES", "PARTIAL_SI_UNSUPPORTED", "PARTIAL_NO_SI"
}
IMAGE_ASSET_TYPES = {"figure", "table_image", "embedded_image", "composite_figure", "page_render"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_id(prefix: str, value: str, length: int = 16) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:length]}"


def normalize_doi(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    raw = raw.rstrip(".,;)]}>")
    return raw or None


def clean_stem(stem: str) -> str:
    """Compatibility helper retained for existing callers; never used for discovery."""
    value = stem.casefold()
    value = re.sub(r"\.(pdf|md|txt|xml|csv|xlsx|html?|docx)$", "", value)
    value = re.sub(
        r"([_-](si|supp|supplement|supplementary|supporting|supporting_information|supplementary_information)([_-].*)?)$",
        "", value,
    )
    value = re.sub(r"^(si|supp|supplement|supplementary|supporting)[_-]+", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "unnamed"


def parse_path_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip().strip('"') for item in value if str(item).strip()]
    raw = str(value).strip().strip('"')
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip().strip('"') for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    if ";" in raw:
        return [item.strip().strip('"') for item in raw.split(";") if item.strip()]
    return [raw]


def resolve_path(value: str, root: Path) -> Path:
    path = Path(str(value).strip().strip('"')).expanduser()
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def safe_package_path(package_dir: Path, relative: str) -> Path:
    candidate = (package_dir / str(relative)).resolve()
    try:
        candidate.relative_to(package_dir.resolve())
    except ValueError as exc:
        raise CorpusError(f"Upstream package path escapes its package: {relative}") from exc
    return candidate


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"Cannot read upstream JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CorpusError(f"Expected a JSON object in {path}")
    return value


def load_sampling_provenance(path: Path, paired_csv: Path) -> dict[str, Any]:
    """Verify the sample manifest is bound to the exact CSV used for preparation."""
    sampling = read_json(path)
    if sampling.get("sampling_manifest_version") != "0.1":
        raise CorpusError(
            f"Unsupported sampling_manifest_version: {sampling.get('sampling_manifest_version')!r}"
        )
    expected_hash = str(sampling.get("sampled_csv_sha256") or "").lower()
    actual_hash = sha256_file(paired_csv)
    if not expected_hash or expected_hash != actual_hash:
        raise CorpusError(
            "Sampling manifest does not match the selected paired CSV SHA-256; "
            "the sample may have been edited or replaced."
        )
    selected_keys = sampling.get("selected_paper_keys")
    if not isinstance(selected_keys, list) or len(selected_keys) != sampling.get("selected_paper_count"):
        raise CorpusError("Sampling manifest has an invalid selected_paper_keys inventory.")
    if len(selected_keys) != len(set(str(item) for item in selected_keys)):
        raise CorpusError("Sampling manifest contains duplicate selected paper keys.")
    source_hash = str(sampling.get("source_csv_sha256") or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise CorpusError("Sampling manifest has an invalid source_csv_sha256.")
    for field in ("source_paper_count", "eligible_paper_count", "selected_paper_count", "selected_row_count"):
        if not isinstance(sampling.get(field), int) or sampling[field] < 0:
            raise CorpusError(f"Sampling manifest has an invalid {field}.")
    return {
        "sampling_manifest_version": sampling["sampling_manifest_version"],
        "algorithm": sampling.get("algorithm"),
        "seed": sampling.get("seed"),
        "requested_fraction": sampling.get("requested_fraction"),
        "stratify_by": sampling.get("stratify_by", []),
        "source_csv_path": sampling.get("source_csv_path"),
        "source_csv_sha256": source_hash,
        "source_paper_count": sampling["source_paper_count"],
        "eligible_paper_count": sampling["eligible_paper_count"],
        "ineligible_paper_count": sampling.get("ineligible_paper_count"),
        "selected_paper_count": sampling["selected_paper_count"],
        "selected_row_count": sampling["selected_row_count"],
        "sampled_csv_path": str(paired_csv.resolve()),
        "sampled_csv_sha256": actual_hash,
        "selected_paper_keys": sorted(str(item) for item in selected_keys),
        "sampling_manifest_path": str(path.resolve()),
        "sampling_manifest_sha256": sha256_file(path),
    }


def load_paired_csv(path: Path) -> list[dict[str, str]]:
    required = {
        "doi", "title", "year", "journal", "article_type",
        "local_pdf_path", "local_si_info", "preprocessing_path", "preprocessing_status",
    }
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise CorpusError("Paired corpus CSV has no header row.")
            names = {name.strip().casefold() for name in reader.fieldnames if name}
            missing = sorted(required - names)
            if missing:
                raise CorpusError(
                    "CSV is not a preprocessed chem-literature handoff; missing columns: "
                    + ", ".join(missing)
                    + ". Run the chem-literature preprocessing stage first."
                )
            return [
                {(key or "").strip().casefold(): (value or "").strip() for key, value in row.items()}
                for row in reader
            ]
    except OSError as exc:
        raise CorpusError(f"Cannot read paired corpus CSV {path}: {exc}") from exc


def verify_raw_inputs(row: dict[str, str], root: Path, package: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Hash-check the upstream raw inputs without parsing or attaching them to workers."""
    problems: list[str] = []
    raw_inventory: list[dict[str, Any]] = []
    main_paths = parse_path_list(row.get("local_pdf_path"))
    si_paths = parse_path_list(row.get("local_si_info"))

    if len(main_paths) != 1:
        problems.append(f"Expected exactly one local_pdf_path; found {len(main_paths)}.")
    if not si_paths:
        problems.append("No local_si_info path is listed in the paired CSV.")

    package_main = package.get("inventory", {}).get("main_pdf") or {}
    expected_main_hash = str(package_main.get("sha256") or "").lower()
    if main_paths:
        main_path = resolve_path(main_paths[0], root)
        if not main_path.is_file():
            problems.append(f"CSV-listed main PDF is missing: {main_paths[0]}")
        else:
            with main_path.open("rb") as handle:
                magic = handle.read(5)
            actual_hash = sha256_file(main_path)
            if magic != b"%PDF-":
                problems.append(f"CSV-listed main source is not a valid PDF: {main_paths[0]}")
            if not expected_main_hash or actual_hash != expected_main_hash:
                problems.append("Main PDF hash does not match the upstream evidence_index inventory.")
            raw_inventory.append({
                "role": "main_raw_pdf", "csv_path": main_paths[0],
                "absolute_path": str(main_path), "sha256": actual_hash,
                "size_bytes": main_path.stat().st_size,
                "worker_attachment": False, "reparsed_downstream": False,
            })

    expected_si = package.get("inventory", {}).get("source_supplementary_files") or []
    if not expected_si:
        expected_si = package.get("inventory", {}).get("supplementary_files") or []
    expected_hashes = {str(item.get("sha256") or "").lower() for item in expected_si}
    for raw in si_paths:
        path = resolve_path(raw, root)
        if not path.is_file():
            problems.append(f"CSV-listed SI source is missing: {raw}")
            continue
        actual_hash = sha256_file(path)
        if not expected_hashes or actual_hash not in expected_hashes:
            problems.append(f"SI hash does not match upstream evidence_index inventory: {raw}")
        raw_inventory.append({
            "role": "si_raw_source", "csv_path": raw,
            "absolute_path": str(path), "sha256": actual_hash,
            "size_bytes": path.stat().st_size,
            "worker_attachment": False, "reparsed_downstream": False,
        })
    return raw_inventory, problems


def write_indexed_text(output_text_dir: Path, source_id: str, text: str) -> str:
    output_text_dir.mkdir(parents=True, exist_ok=True)
    path = output_text_dir / f"{source_id}.txt"
    path.write_text(text, encoding="utf-8", newline="\n")
    return path.relative_to(output_text_dir.parent).as_posix()


def add_text_source(
    sources: list[dict[str, Any]], *, paper_id: str, doc_type: str,
    source_type: str, component: str, label: str, upstream_path: Path,
    output_text_dir: Path, package_dir: Path, page_numbers: list[int],
) -> str:
    if not upstream_path.is_file():
        raise CorpusError(f"Required upstream parsed text is missing: {upstream_path}")
    text = upstream_path.read_text(encoding="utf-8", errors="replace")
    digest = sha256_file(upstream_path)
    source_id = stable_id("SRC", f"{paper_id}|{doc_type}|{source_type}|{digest}")
    output_text_dir.mkdir(parents=True, exist_ok=True)
    staged_text = output_text_dir / f"{source_id}.txt"
    shutil.copyfile(upstream_path, staged_text)
    indexed_path = staged_text.relative_to(output_text_dir.parent).as_posix()
    try:
        package_relative = upstream_path.resolve().relative_to(package_dir.resolve()).as_posix()
    except ValueError:
        package_relative = str(upstream_path.resolve())
    sources.append({
        "source_id": source_id, "paper_id": paper_id, "source_role": "main" if doc_type == "MAIN" else "si",
        "source_type": source_type, "component": component, "label": label,
        "relative_path": f"preprocessing/{package_relative}",
        "absolute_path": str(upstream_path.resolve()), "attachment_path": None,
        "sha256": digest, "size_bytes": upstream_path.stat().st_size,
        "parse_status": "upstream_preprocessed_text", "page_count": len(page_numbers),
        "page_numbers": page_numbers, "extracted_text_path": indexed_path,
        "extracted_text_chars": len(text), "is_visual_asset": False,
        "preprocessing_package_root": str(package_dir.resolve()),
    })
    return source_id


def table_text(table: dict[str, Any]) -> str:
    lines = [
        f"TABLE_ID: {table.get('table_id', 'UNKNOWN')}",
        f"DOCUMENT: {table.get('doc_type', 'UNKNOWN')}",
        f"PAGE: {table.get('page_number', 'UNKNOWN')}",
        f"CAPTION: {table.get('caption') or ''}",
    ]
    for row_index, row in enumerate(table.get("rows") or [], start=1):
        for col_index, value in enumerate(row, start=1):
            lines.append(f"ROW {row_index} | COLUMN {col_index} | CELL: {'' if value is None else value}")
    return "\n".join(lines) + "\n"


def component_for_asset(asset_type: str, doc_type: str) -> tuple[str, str]:
    prefix = "main" if str(doc_type).upper() == "MAIN" else "si"
    if asset_type == "table_image":
        return f"{prefix}_table", f"{prefix}_tables"
    if asset_type in {"figure", "embedded_image", "composite_figure"}:
        return f"{prefix}_figure", f"{prefix}_figures"
    return "other", "other"


def build_manifest_from_paired_csv(
    paired_csv: Path, output: Path, corpus_root: Path,
    sampling_manifest_path: Path | None = None,
) -> dict[str, Any]:
    rows = load_paired_csv(paired_csv)
    sampling = load_sampling_provenance(sampling_manifest_path, paired_csv) if sampling_manifest_path else None
    if not rows:
        raise CorpusError("chem-literature paired corpus contains zero paper rows.")

    text_dir = output / "extracted_text"
    index_dir = output / "upstream_evidence_indexes"
    text_dir.mkdir(parents=True, exist_ok=True)
    index_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[dict[str, str]]] = {}
    for row_index, row in enumerate(rows, start=1):
        doi = normalize_doi(row.get("doi"))
        paper_key = doi or stable_id("PAPER", f"{row.get('title','')}|{row.get('year','')}|{row_index}", 12)
        grouped.setdefault(paper_key, []).append(row)

    sources: list[dict[str, Any]] = []
    papers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    for paper_id, paper_rows in sorted(grouped.items()):
        row = paper_rows[0]
        title, year = row.get("title", ""), row.get("year", "")
        package_rel = row.get("preprocessing_path", "")
        pair_warnings: list[str] = []
        raw_inventory: list[dict[str, Any]] = []
        paper_sources: list[str] = []
        unparsed_material: list[dict[str, Any]] = []
        package: dict[str, Any] = {}
        package_dir: Path | None = None
        evidence_index_path: Path | None = None
        qc_path: Path | None = None
        processing_status = str(row.get("preprocessing_status") or "").upper()
        input_status = "ready"
        pair_status = "paired_files_verified"

        try:
            if not package_rel:
                raise CorpusError("preprocessing_path is empty; upstream preprocessing was not completed.")
            package_dir = resolve_path(package_rel, corpus_root)
            if not package_dir.is_dir():
                raise CorpusError(f"Preprocessing package directory does not exist: {package_dir}")
            evidence_index_path = package_dir / "evidence_index.json"
            qc_path = package_dir / "PREPROCESSING_QC_REPORT.md"
            if not evidence_index_path.is_file():
                raise CorpusError(f"Upstream evidence_index.json is missing: {evidence_index_path}")
            if not qc_path.is_file():
                raise CorpusError(f"Upstream PREPROCESSING_QC_REPORT.md is missing: {qc_path}")
            package = read_json(evidence_index_path)
            if package.get("package_version") != "1.1.0":
                raise CorpusError(f"Unsupported upstream package_version: {package.get('package_version')!r}")
            metadata = package.get("metadata") or {}
            package_doi = normalize_doi(metadata.get("doi"))
            if normalize_doi(row.get("doi")) and package_doi and normalize_doi(row.get("doi")) != package_doi:
                raise CorpusError("CSV DOI does not match upstream evidence_index metadata DOI.")
            summary = package.get("processing_summary") or {}
            package_status = str(summary.get("status") or "").upper()
            if processing_status != package_status:
                raise CorpusError(
                    f"CSV preprocessing_status ({processing_status or 'empty'}) does not match "
                    f"evidence_index status ({package_status or 'empty'})."
                )
            if package_status not in ALLOWED_PREPROCESSING_STATUSES:
                raise CorpusError(f"Upstream preprocessing status is not usable: {package_status or 'empty'}.")
            if package_status == "FAILED":
                raise CorpusError("Upstream preprocessing reported FAILED.")
            raw_inventory, raw_problems = verify_raw_inputs(row, corpus_root, package)
            if raw_problems:
                pair_warnings.extend(raw_problems)
                pair_status = "blocked_pair_integrity"
                input_status = "blocked_pair_integrity"

            main_text_path = package_dir / "main_analysis.md"
            if not main_text_path.is_file():
                raise CorpusError("Upstream main_analysis.md is missing.")
            main_pages = [
                int(page.get("page_number")) for page in package.get("pages", [])
                if str(page.get("doc_type", "")).upper() == "MAIN" and page.get("page_number") is not None
            ]
            si_pages = [
                int(page.get("page_number")) for page in package.get("pages", [])
                if str(page.get("doc_type", "")).upper() == "SI" and page.get("page_number") is not None
            ]
            source_id = add_text_source(
                sources, paper_id=paper_id, doc_type="MAIN", source_type="main_text",
                component="main_text", label="MAIN manuscript parsed text",
                upstream_path=main_text_path, output_text_dir=text_dir,
                package_dir=package_dir, page_numbers=main_pages,
            )
            paper_sources.append(source_id)

            si_text_path = package_dir / "si_analysis.md"
            if not si_text_path.is_file():
                raise CorpusError("Upstream si_analysis.md is missing.")
            parsed_si_files = [
                item for item in (package.get("inventory", {}).get("supplementary_files") or [])
                if item.get("processing_status") == "PARSED"
            ]
            if parsed_si_files or si_pages:
                source_id = add_text_source(
                    sources, paper_id=paper_id, doc_type="SI", source_type="si_text",
                    component="si_text", label="Supporting information parsed text",
                    upstream_path=si_text_path, output_text_dir=text_dir,
                    package_dir=package_dir, page_numbers=si_pages,
                )
                paper_sources.append(source_id)

            for table in package.get("tables", []):
                table_id = str(table.get("table_id") or f"TABLE_{len(paper_sources)+1}")
                doc_type = str(table.get("doc_type") or "MAIN").upper()
                source_type = "main_table" if doc_type == "MAIN" else "si_table"
                component = "main_tables" if doc_type == "MAIN" else "si_tables"
                table_sid = stable_id("SRC", f"{paper_id}|{source_type}|{table_id}|{json.dumps(table.get('rows', []), ensure_ascii=False)}")
                indexed_path = write_indexed_text(text_dir, table_sid, table_text(table))
                md_path = safe_package_path(package_dir, table["md_file"]) if table.get("md_file") else None
                csv_path = safe_package_path(package_dir, table["csv_file"]) if table.get("csv_file") else None
                for referenced in (md_path, csv_path):
                    if referenced is not None and not referenced.is_file():
                        pair_warnings.append(f"Upstream table export missing for {table_id}: {referenced}")
                sources.append({
                    "source_id": table_sid, "paper_id": paper_id,
                    "source_role": "main" if doc_type == "MAIN" else "si",
                    "source_type": source_type, "component": component,
                    "label": f"{table_id} — {table.get('caption') or 'table'}",
                    "relative_path": f"preprocessing/tables/{table_id}",
                    "absolute_path": str(md_path) if md_path and md_path.is_file() else None,
                    "attachment_path": None,
                    "sha256": hashlib.sha256(table_text(table).encode("utf-8")).hexdigest(),
                    "parse_status": "upstream_structured_table",
                    "page_number": table.get("page_number"), "bbox": table.get("bbox"),
                    "table_id": table_id, "table_data": table,
                    "md_path": str(md_path) if md_path and md_path.is_file() else None,
                    "csv_path": str(csv_path) if csv_path and csv_path.is_file() else None,
                    "extracted_text_path": indexed_path,
                    "extracted_text_chars": len(table_text(table)), "is_visual_asset": False,
                    "preprocessing_package_root": str(package_dir.resolve()),
                })
                paper_sources.append(table_sid)

            for figure in package.get("figures", []):
                figure_id = str(figure.get("figure_id") or f"FIG_{len(paper_sources)+1}")
                doc_type = str(figure.get("doc_type") or "MAIN").upper()
                source_type = "main_figure_caption" if doc_type == "MAIN" else "si_figure_caption"
                component = "main_figures" if doc_type == "MAIN" else "si_figures"
                caption_text = (
                    f"FIGURE_ID: {figure_id}\nDOCUMENT: {doc_type}\n"
                    f"PAGE: {figure.get('page_number')}\nCAPTION: {figure.get('caption') or ''}\n"
                    f"BBOX: {figure.get('bbox')}\n"
                )
                fig_sid = stable_id("SRC", f"{paper_id}|{source_type}|{figure_id}|{caption_text}")
                indexed_path = write_indexed_text(text_dir, fig_sid, caption_text)
                sources.append({
                    "source_id": fig_sid, "paper_id": paper_id,
                    "source_role": "main" if doc_type == "MAIN" else "si",
                    "source_type": source_type, "component": component,
                    "label": f"{figure_id} caption", "relative_path": f"preprocessing/figures/{figure_id}",
                    "absolute_path": None, "attachment_path": None,
                    "sha256": hashlib.sha256(caption_text.encode("utf-8")).hexdigest(),
                    "parse_status": "upstream_figure_caption_indexed",
                    "page_number": figure.get("page_number"), "bbox": figure.get("bbox"),
                    "figure_id": figure_id, "caption": figure.get("caption"),
                    "extracted_text_path": indexed_path, "extracted_text_chars": len(caption_text),
                    "is_visual_asset": False, "preprocessing_package_root": str(package_dir.resolve()),
                })
                paper_sources.append(fig_sid)

            for asset in package.get("media_assets", []):
                asset_type = str(asset.get("asset_type") or "")
                if asset_type == "source_file":
                    unparsed_material.append({
                        "asset_type": asset_type, "doc_type": asset.get("doc_type"),
                        "relative_path": asset.get("relative_path"),
                        "source_file": asset.get("source_file"),
                        "processing_status": "PRESERVED_NOT_PARSED",
                    })
                    continue
                if asset_type not in IMAGE_ASSET_TYPES:
                    continue
                rel_asset = str(asset.get("relative_path") or "")
                asset_path = safe_package_path(package_dir, rel_asset)
                asset_id = str(asset.get("asset_id") or rel_asset)
                asset_doc_type = str(asset.get("doc_type") or "MAIN").upper()
                source_type, component = component_for_asset(asset_type, asset_doc_type)
                role = "main" if asset_doc_type == "MAIN" else "si"
                asset_hash = str(asset.get("sha256") or "").lower()
                if not asset_path.is_file():
                    pair_warnings.append(f"Upstream visual asset is missing: {rel_asset}")
                    asset_parse_status = "missing_upstream_asset"
                else:
                    actual_hash = sha256_file(asset_path)
                    if not asset_hash or actual_hash != asset_hash:
                        raise CorpusError(f"Upstream visual asset hash mismatch: {rel_asset}")
                    asset_parse_status = "upstream_extracted_visual_asset"
                caption = asset.get("caption") or ""
                descriptor = (
                    f"ASSET_ID: {asset_id}\nASSET_TYPE: {asset_type}\nDOCUMENT: {asset_doc_type}\n"
                    f"PAGE: {asset.get('page_number')}\nFIGURE_ID: {asset.get('figure_id')}\n"
                    f"TABLE_ID: {asset.get('table_id')}\nCAPTION: {caption}\n"
                )
                asset_sid = stable_id("SRC", f"{paper_id}|{asset_id}|{asset_hash or rel_asset}")
                indexed_path = write_indexed_text(text_dir, asset_sid, descriptor)
                sources.append({
                    "source_id": asset_sid, "paper_id": paper_id,
                    "source_role": role, "source_type": source_type, "component": component,
                    "label": f"{asset_type}: {asset.get('figure_id') or asset.get('table_id') or asset_id}",
                    "relative_path": f"preprocessing/{rel_asset}",
                    "absolute_path": str(asset_path) if asset_path.is_file() else None,
                    "attachment_path": str(asset_path) if asset_path.is_file() else None,
                    "sha256": asset_hash or None, "parse_status": asset_parse_status,
                    "page_number": asset.get("page_number"), "bbox": asset.get("bbox"),
                    "figure_id": asset.get("figure_id"), "table_id": asset.get("table_id"),
                    "caption": caption, "asset_type": asset_type,
                    "extracted_text_path": indexed_path, "extracted_text_chars": len(descriptor),
                    "is_visual_asset": True, "preprocessing_package_root": str(package_dir.resolve()),
                })
                paper_sources.append(asset_sid)

            package_copy = index_dir / f"{stable_id('PKG', paper_id, 12)}.json"
            shutil.copy2(evidence_index_path, package_copy)
            qc_text = qc_path.read_text(encoding="utf-8", errors="replace")
            unsupported = [
                {
                    "filename": item.get("filename"),
                    "format": item.get("format"),
                    "processing_status": item.get("processing_status"),
                    "preserved_path": item.get("preserved_path"),
                    "sha256": item.get("sha256"),
                }
                for item in (package.get("inventory", {}).get("supplementary_files") or [])
                if item.get("processing_status") == "PRESERVED_NOT_PARSED"
            ]
            unparsed_material.extend(unsupported)
            if package_status != "COMPLETE":
                pair_warnings.append(f"Upstream preprocessing status: {package_status}")
            if summary.get("warnings"):
                pair_warnings.extend(str(item) for item in summary.get("warnings", []))

            if pair_warnings and input_status == "ready":
                input_status = "ready_with_warnings"
        except Exception as exc:
            pair_warnings.append(f"{type(exc).__name__}: {exc}")
            input_status = "blocked_preprocessing"
            package_copy = None

        paper_record = {
            "paper_id": paper_id, "doi": normalize_doi(row.get("doi")),
            "title": title, "year": year, "journal": row.get("journal", ""),
            "article_type": row.get("article_type", ""),
            "landing_page_url": row.get("landing_page_url", ""),
            "abstract": row.get("abstract", ""),
            "upstream_csv_row_count": len(paper_rows), "upstream_csv_rows": paper_rows,
            "source_ids": list(dict.fromkeys(paper_sources)),
            "source_count": len(set(paper_sources)), "pair_status": pair_status,
            "input_status": input_status, "preprocessing_status": processing_status,
            "preprocessing_path": package_rel,
            "preprocessing_package_root": str(package_dir.resolve()) if package_dir else None,
            "evidence_index_path": str(evidence_index_path.resolve()) if evidence_index_path else None,
            "evidence_index_copy": str(package_copy.resolve()) if package_copy else None,
            "evidence_index_sha256": sha256_file(evidence_index_path) if evidence_index_path and evidence_index_path.is_file() else None,
            "qc_report_path": str(qc_path.resolve()) if qc_path and qc_path.is_file() else None,
            "qc_report_sha256": sha256_file(qc_path) if qc_path and qc_path.is_file() else None,
            "processing_summary": package.get("processing_summary", {}),
            "raw_source_inventory": raw_inventory,
            "unparsed_material": unparsed_material,
            "pair_warnings": sorted(set(pair_warnings)),
        }
        papers.append(paper_record)
        if pair_warnings:
            warnings.extend({"paper_id": paper_id, "warning": item} for item in sorted(set(pair_warnings)))

    fingerprint_payload = [
        f"{paper['paper_id']}|{paper.get('evidence_index_sha256') or paper['input_status']}|"
        f"{paper.get('preprocessing_status')}|{paper.get('raw_source_inventory')}"
        for paper in papers
    ]
    csv_hash = sha256_file(paired_csv)
    return {
        "manifest_version": "0.3",
        "source_kind": "chem-literature-preprocessed-research_corpus_paired.csv",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "upstream_skill": "chem-literature",
        "upstream_artifact": str(paired_csv.resolve()),
        "upstream_artifact_sha256": csv_hash,
        "corpus_root": str(corpus_root.resolve()), "output_root": str(output.resolve()),
        "upstream_row_count": len(rows), "paper_count": len(papers),
        "source_count": len(sources),
        "ready_paper_count": sum(p["input_status"] in {"ready", "ready_with_warnings"} for p in papers),
        "blocked_paper_count": sum(p["input_status"].startswith("blocked") for p in papers),
        "corpus_fingerprint": hashlib.sha256("\n".join(fingerprint_payload).encode("utf-8")).hexdigest(),
        "papers": papers, "sources": sources, "upstream_warnings": warnings,
        "sampling": sampling,
        "parsing_performed_by_feature_create": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Index chem-literature preprocessed evidence packages without reparsing PDFs or SI."
    )
    parser.add_argument("--paired-csv", type=Path, required=True)
    parser.add_argument("--corpus-root", type=Path, help="Base directory for upstream relative paths; defaults to CSV directory.")
    parser.add_argument("--sampling-manifest", type=Path, help="Optional sampling_manifest.json; its selected-CSV hash is verified and provenance is copied into source_manifest.json.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    paired_csv = args.paired_csv.expanduser().resolve()
    output = args.output.expanduser().resolve()
    corpus_root = args.corpus_root.expanduser().resolve() if args.corpus_root else paired_csv.parent
    try:
        if not paired_csv.is_file():
            raise CorpusError(f"Paired corpus CSV does not exist: {paired_csv}")
        output.mkdir(parents=True, exist_ok=True)
        sampling_manifest = args.sampling_manifest.expanduser().resolve() if args.sampling_manifest else None
        if sampling_manifest and not sampling_manifest.is_file():
            raise CorpusError(f"Sampling manifest does not exist: {sampling_manifest}")
        manifest = build_manifest_from_paired_csv(
            paired_csv, output, corpus_root, sampling_manifest_path=sampling_manifest
        )
        manifest_path = output / "source_manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )
        summary = {
            "status": "complete" if manifest["blocked_paper_count"] == 0 else "partial",
            "upstream_csv": str(paired_csv), "upstream_row_count": manifest["upstream_row_count"],
            "paper_count": manifest["paper_count"], "source_count": manifest["source_count"],
            "ready_paper_count": manifest["ready_paper_count"],
            "blocked_paper_count": manifest["blocked_paper_count"],
            "parsing_performed_by_feature_create": False,
            "manifest": str(manifest_path),
            "sampling": {
                "selected_paper_count": manifest["sampling"]["selected_paper_count"],
                "source_paper_count": manifest["sampling"]["source_paper_count"],
                "requested_fraction": manifest["sampling"]["requested_fraction"],
                "sampled_csv_sha256": manifest["sampling"]["sampled_csv_sha256"],
            } if manifest.get("sampling") else None,
        }
        (output / "prepare_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except CorpusError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
