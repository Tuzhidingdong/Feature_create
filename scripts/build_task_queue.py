"""Build source-aware, bounded Worker A/B tasks from upstream-preprocessed evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .chunking import build_evidence_units, group_units_into_chunks
except ImportError:  # direct execution as python scripts/build_task_queue.py
    from chunking import build_evidence_units, group_units_into_chunks


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    "source_discovery": ROOT / "worker_prompts" / "worker_a_source_discovery.md",
    "feature_audit": ROOT / "worker_prompts" / "worker_b_feature_audit.md",
}
MODEL_CONFIG = ROOT / "config" / "execution_policy.json"
SI_LABEL_RE = re.compile(r"\b(?:figure|fig\.?|table|scheme)\s+(S?\d+[A-Za-z]?)\b", re.I)


class TaskQueueError(RuntimeError):
    pass


def stable_task_id(paper_id: str, worker_role: str, fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{paper_id}|{worker_role}|{fingerprint}|0.2".encode("utf-8")
    ).hexdigest()[:12].upper()
    prefix = "A" if worker_role == "source_discovery" else "B"
    return f"FC-{prefix}-{digest}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskQueueError(f"Unable to read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TaskQueueError(f"Expected JSON object: {path}")
    return value


def _safe_output_path(path: str | None) -> Path | None:
    if not path:
        return None
    value = Path(path).expanduser().resolve()
    return value if value.is_file() else None


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _unit_summary(unit: dict[str, Any], chunk_id: str) -> dict[str, Any]:
    return {
        "unit_id": unit["unit_id"],
        "chunk_id": chunk_id,
        "source_ids": unit.get("source_ids", [unit.get("source_id")]),
        "source_type": unit.get("source_type"),
        "component": unit.get("component"),
        "unit_kind": unit.get("unit_kind"),
        "doc_type": unit.get("doc_type"),
        "page_number": unit.get("page_number"),
        "table_id": unit.get("table_id"),
        "figure_id": unit.get("figure_id"),
        "locator": unit.get("locator", {}),
        "asset_ids": unit.get("asset_ids", []),
        "related_assets": unit.get("related_assets", []),
    }


def _paper_chunk_index(
    paper: dict[str, Any],
    chunks: list[dict[str, Any]],
    upstream_index: dict[str, Any],
    upstream_index_sha256: str | None,
) -> dict[str, Any]:
    units = [
        _unit_summary(unit, chunk["chunk_id"])
        for chunk in chunks
        for unit in chunk["units"]
    ]
    all_tables = upstream_index.get("tables", [])
    all_figures = upstream_index.get("figures", [])
    supplementary_targets = []
    for table in all_tables:
        if str(table.get("doc_type", "")).upper() == "SI":
            supplementary_targets.append({
                "evidence_kind": "table",
                "target_id": table.get("table_id"),
                "page_number": table.get("page_number"),
                "caption": table.get("caption"),
            })
    for figure in all_figures:
        if str(figure.get("doc_type", "")).upper() == "SI":
            supplementary_targets.append({
                "evidence_kind": "figure",
                "target_id": figure.get("figure_id"),
                "page_number": figure.get("page_number"),
                "caption": figure.get("caption"),
            })

    links = []
    for ref in upstream_index.get("si_references", []):
        label_match = SI_LABEL_RE.search(str(ref.get("ref_text") or ""))
        label = label_match.group(1).casefold() if label_match else None
        candidates = []
        for target in supplementary_targets:
            caption = str(target.get("caption") or "").casefold()
            target_id = str(target.get("target_id") or "").casefold()
            if label and (label in caption or label in target_id):
                candidates.append(target)
        links.append({
            "reference_text": ref.get("ref_text"),
            "source_doc_type": ref.get("doc_type"),
            "source_page_number": ref.get("page_number"),
            "source_bbox": ref.get("bbox"),
            "normalized_label": label,
            "target_candidates": candidates,
            "target_resolution": "candidate_match" if candidates else "not_resolved_by_label",
        })

    return {
        "index_type": "feature_create_paper_chunk_index",
        "index_version": "0.2",
        "paper_id": paper["paper_id"],
        "doi": paper.get("doi"),
        "upstream_package_version": upstream_index.get("package_version"),
        "upstream_evidence_index_sha256": upstream_index_sha256 or paper.get("evidence_index_sha256"),
        "chunk_count": len(chunks),
        "chunks": [{
            "chunk_id": chunk["chunk_id"],
            "chunk_index": chunk["chunk_index"],
            "chunk_count": chunk["chunk_count"],
            "previous_chunk_id": chunk.get("previous_chunk_id"),
            "next_chunk_id": chunk.get("next_chunk_id"),
            "unit_count": chunk["unit_count"],
            "source_ids": chunk["source_ids"],
            "locators": [
                {
                    "unit_id": unit["unit_id"],
                    "source_ids": unit.get("source_ids", []),
                    "unit_kind": unit.get("unit_kind"),
                    "source_type": unit.get("source_type"),
                    "page_number": unit.get("page_number"),
                    "table_id": unit.get("table_id"),
                    "figure_id": unit.get("figure_id"),
                    "locator": unit.get("locator", {}),
                }
                for unit in chunk["units"]
            ],
        } for chunk in chunks],
        "all_supplementary_evidence_locators": supplementary_targets,
        "cross_source_references": links,
        "rules": [
            "This is a navigation index, not the full source content.",
            "Only the assigned chunk's units count as reviewed by its worker.",
            "Context-only overlap and references to another chunk do not count as reviewed.",
            "A target locator is a candidate link; verify it against the exact upstream caption/index before using it.",
            "Preserve original page, table, row, column, figure, bbox, and source IDs when evidence is cited.",
        ],
        "all_unit_locators": units,
    }


def _chunk_evidence_index(
    paper: dict[str, Any],
    chunk: dict[str, Any],
    source_map: dict[str, dict[str, Any]],
    upstream_index: dict[str, Any],
    upstream_index_sha256: str | None,
    destination: Path,
) -> Path:
    units = chunk["units"]
    source_ids = {sid for unit in units for sid in unit.get("source_ids", [])}
    selected_pages = {
        (str(unit.get("doc_type") or "").upper(), int(unit["page_number"]))
        for unit in units
        if unit.get("page_number") is not None
    }
    page_rows = [
        page for page in upstream_index.get("pages", [])
        if (str(page.get("doc_type") or "").upper(), int(page.get("page_number") or 0)) in selected_pages
    ]

    table_rows: dict[str, set[int]] = {}
    for unit in units:
        locator = unit.get("locator") or {}
        table_id = unit.get("table_id") or locator.get("table_id")
        if table_id and locator.get("row_indices_1based"):
            table_rows.setdefault(str(table_id), set()).update(
                int(value) for value in locator["row_indices_1based"]
            )
    selected_tables = []
    for table in upstream_index.get("tables", []):
        table_id = str(table.get("table_id") or "")
        if table_id not in table_rows:
            continue
        selected = table_rows[table_id]
        rows = table.get("rows") or []
        selected_tables.append({
            "table_id": table_id,
            "doc_type": table.get("doc_type"),
            "page_number": table.get("page_number"),
            "bbox": table.get("bbox"),
            "caption": table.get("caption"),
            "row_count_original": len(rows),
            "col_count_original": table.get("col_count"),
            "assigned_rows": [
                {"row_index_1based": index, "cells": rows[index - 1]}
                for index in sorted(selected) if 1 <= index <= len(rows)
            ],
            "header_context_rows": [
                {"row_index_1based": index, "cells": rows[index - 1]}
                for index in range(1, min(3, len(rows)) + 1)
            ],
        })

    figure_ids = {
        str(unit.get("figure_id") or (unit.get("locator") or {}).get("figure_id"))
        for unit in units
        if unit.get("figure_id") or (unit.get("locator") or {}).get("figure_id")
    }
    selected_figures = [
        figure for figure in upstream_index.get("figures", [])
        if str(figure.get("figure_id") or "") in figure_ids
    ]
    asset_ids = {asset_id for unit in units for asset_id in unit.get("asset_ids", [])}
    selected_assets = [
        asset for asset in upstream_index.get("media_assets", [])
        if str(asset.get("asset_id") or "") in asset_ids
    ]
    selected_refs = [
        ref for ref in upstream_index.get("si_references", [])
        if (str(ref.get("doc_type") or "").upper(), int(ref.get("page_number") or 0)) in selected_pages
    ]
    source_items = []
    for source_id in sorted(source_ids):
        source = source_map.get(source_id, {})
        source_items.append({
            "source_id": source_id,
            "source_type": source.get("source_type"),
            "component": source.get("component"),
            "source_role": source.get("source_role"),
            "relative_path": source.get("relative_path"),
            "parse_status": source.get("parse_status"),
            "sha256": source.get("sha256"),
            "page_number": source.get("page_number"),
            "bbox": source.get("bbox"),
            "table_id": source.get("table_id"),
            "figure_id": source.get("figure_id"),
            "asset_id": source.get("asset_id"),
        })

    result = {
        "index_type": "feature_create_chunk_evidence_index",
        "index_version": "0.2",
        "paper_id": paper["paper_id"],
        "chunk_id": chunk["chunk_id"],
        "chunk_index": chunk["chunk_index"],
        "chunk_count": chunk["chunk_count"],
        "upstream_package_version": upstream_index.get("package_version"),
        "upstream_evidence_index_sha256": upstream_index_sha256 or paper.get("evidence_index_sha256"),
        "source_items": source_items,
        "pages": page_rows,
        "tables": selected_tables,
        "figures": selected_figures,
        "media_assets": selected_assets,
        "si_references_from_assigned_pages": selected_refs,
        "assigned_units": [_unit_summary(unit, chunk["chunk_id"]) for unit in units],
        "unparsed_material": paper.get("unparsed_material", []),
        "processing_summary": paper.get("processing_summary", {}),
        "qc_warnings": paper.get("pair_warnings", []),
        "note": "Derived locator slice. The upstream evidence_index.json remains unchanged and is identified by its SHA-256.",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    return destination.resolve()


def build_bundle(
    paper: dict[str, Any],
    chunk: dict[str, Any],
    paper_index: dict[str, Any],
    chunk_evidence_path: Path,
) -> tuple[str, list[str], int]:
    lines = [
        "# Feature_create bounded paper-chunk input",
        "",
        f"- Paper ID: {paper['paper_id']}",
        f"- DOI: {paper.get('doi') or 'not found in local source metadata'}",
        f"- Title: {paper.get('title') or 'not supplied'}",
        f"- Year: {paper.get('year') or 'not supplied'}",
        f"- Journal: {paper.get('journal') or 'not supplied'}",
        f"- Article type: {paper.get('article_type') or 'not supplied'}",
        f"- Chunk: {chunk['chunk_index']} of {chunk['chunk_count']} ({chunk['chunk_id']})",
        f"- Assigned evidence units: {chunk['unit_count']}",
        f"- Assigned source IDs: {', '.join(chunk['source_ids'])}",
        f"- Upstream preprocessing status: {paper.get('preprocessing_status', 'not recorded')}",
        f"- Upstream preprocessing package: {paper.get('preprocessing_path') or 'not supplied'}",
        "",
        "- Discovery boundary: all result variables actually reported in the upstream-preprocessed sources; existing schemas are not a discovery boundary.",
        "- Scope boundary: review only the evidence units explicitly assigned to this chunk. Other chunks are navigation/context only.",
        "- The chunk plan is deterministic and lossless: each original text range/table row is assigned to exactly one extraction unit.",
        "",
        "## Abstract and paper metadata",
        "",
        str(paper.get("abstract") or "[No abstract supplied in paired CSV.]"),
        "",
        json.dumps(paper.get("upstream_csv_rows", []), ensure_ascii=False, indent=2),
        "",
        "## Upstream preprocessing quality and scope",
        "",
        json.dumps(paper.get("processing_summary", {}), ensure_ascii=False, indent=2),
        "",
        "### Upstream QC warnings",
        "",
        *[f"- {item}" for item in paper.get("pair_warnings", [])],
        "",
        "### Supporting material preserved but not parsed upstream",
        "",
        json.dumps(paper.get("unparsed_material", []), ensure_ascii=False, indent=2),
        "",
        "## Paper-wide chunk navigation index",
        "",
        "The index lists all assigned and unassigned chunks, source IDs, locators, and detected MAIN-to-SI references. It does not mean other chunks have been reviewed.",
        "",
        json.dumps(paper_index, ensure_ascii=False, indent=2),
        "",
        "## Assigned evidence manifest",
        "",
        "| Unit ID | Source ID(s) | Component | Type | Locator |",
        "|---|---|---|---|---|",
    ]
    attachments: list[str] = []
    all_chars = 0
    for unit in chunk["units"]:
        locator = json.dumps(unit.get("locator", {}), ensure_ascii=False, separators=(",", ":")).replace("|", "\\|")
        lines.append(
            f"| {unit['unit_id']} | {', '.join(unit.get('source_ids', []))} | "
            f"{unit.get('component', 'other')} | {unit.get('source_type', 'other')} | {locator} |"
        )
        attachments.extend(unit.get("attachment_paths", []))
    lines.extend([
        "",
        "## Review requirements",
        "",
        "- Review every assigned evidence unit in this chunk; report coverage for the assigned source IDs only.",
        "- For source IDs spanning multiple chunks, mark only the assigned page/range/table rows as reviewed in the coverage notes.",
        "- Never claim the whole source or paper is fully reviewed merely because this chunk is complete.",
        "- Use the attached chunk_evidence_index.json for exact upstream page blocks/bboxes, table rows/indices, figure IDs, image asset IDs, and SI references.",
        "- Use paper_chunk_index.json only to navigate evidence in other chunks and preserve cross-chunk relationships. Do not extract from its locator summaries.",
        "- Context-only text and repeated table prefix rows are orientation context, not assigned evidence and must not be counted as reviewed or extracted again.",
        "- Original MAIN/SI PDFs and archives are provenance only. Do not request or perform a second PDF/SI parsing pass.",
        "- Preserve source IDs and original page/table/row/column/figure/bbox locators exactly.",
        "- Keep sample identity, conditions, time/cycle facts, protocols, and result values separate.",
        "- If a value depends on evidence assigned to another chunk, record the current evidence and mark the association as unresolved pending that chunk; never invent the missing link.",
        "- Explicitly report scanned pages, preserved-but-unparsed materials, missing assets, and upstream QC limitations.",
        "",
        "## Assigned evidence content",
        "",
    ])

    for unit in chunk["units"]:
        lines.extend([
            f"### UNIT {unit['unit_id']} — SOURCE(S) {', '.join(unit.get('source_ids', []))}",
            "",
            f"Unit kind: {unit.get('unit_kind')}; component: {unit.get('component')}; locator: "
            f"{json.dumps(unit.get('locator', {}), ensure_ascii=False)}",
            "",
            str(unit.get("content") or "[No extracted text is available.]"),
            "",
        ])
        if unit.get("context_before"):
            lines.extend([
                "CONTEXT ONLY — preceding text from the same source; not assigned for review:",
                str(unit["context_before"]),
                "",
            ])
        if unit.get("context_after"):
            lines.extend([
                "CONTEXT ONLY — following text from the same source; not assigned for review:",
                str(unit["context_after"]),
                "",
            ])
        all_chars += len(str(unit.get("content") or ""))

    return "\n".join(lines), sorted(set(attachments)), all_chars


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build bounded, source-aware OpenCode Worker A/B tasks from chem-literature preprocessed evidence."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-bundle-chars", type=int, default=120_000)
    parser.add_argument("--max-command-chars", type=int, default=7600)
    parser.add_argument("--max-attachments-per-chunk", type=int, default=8)
    args = parser.parse_args(argv)
    if (
        args.max_bundle_chars < 10_000
        or args.max_command_chars < 1000
        or args.max_attachments_per_chunk < 1
    ):
        parser.error("max-bundle-chars must be >= 10000, max-command-chars >= 1000, and max-attachments-per-chunk positive.")

    manifest_path = args.manifest.expanduser().resolve()
    manifest = load_json(manifest_path)
    output_root = args.output.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    config = load_json(MODEL_CONFIG)
    workers = config.get("workers", {})
    model_refs = {
        "source_discovery": workers.get("worker_a", {}).get("model_ref"),
        "feature_audit": workers.get("worker_b", {}).get("model_ref"),
    }
    if not all(model_refs.values()) or model_refs["source_discovery"] == model_refs["feature_audit"]:
        raise TaskQueueError("Worker A and B must have distinct, explicitly configured model references.")

    sources_by_id = {source["source_id"]: source for source in manifest.get("sources", [])}
    tasks: list[dict[str, Any]] = []
    paper_chunk_plans: list[dict[str, Any]] = []
    papers = manifest.get("papers", [])
    manifest_root = manifest_path.parent
    paper_count_with_sources = 0
    oversize_atomic_unit_count = 0

    for paper in papers:
        paper_sources = [
            sources_by_id[sid] for sid in paper.get("source_ids", []) if sid in sources_by_id
        ]
        if not paper_sources:
            continue
        paper_count_with_sources += 1
        upstream_index_path = _safe_output_path(paper.get("evidence_index_copy"))
        upstream_index = load_json(upstream_index_path) if upstream_index_path else {}
        units = build_evidence_units(
            paper_sources,
            manifest_root,
            max_unit_chars=max(1024, int(args.max_bundle_chars * 0.65)),
        )
        chunks = group_units_into_chunks(
            units,
            max_chars=max(1024, int(args.max_bundle_chars * 0.78)),
            max_attachments=args.max_attachments_per_chunk,
        )
        if not chunks:
            continue

        paper_dir = output_root / "tasks" / hashlib.sha1(paper["paper_id"].encode("utf-8")).hexdigest()[:10]
        paper_dir.mkdir(parents=True, exist_ok=True)
        paper_index = _paper_chunk_index(
            paper,
            chunks,
            upstream_index,
            paper.get("evidence_index_sha256"),
        )
        paper_index_path = paper_dir / "paper_chunk_index.json"
        paper_index_path.write_text(
            json.dumps(paper_index, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
        paper_hash_payload = {
            "paper_metadata": {
                key: paper.get(key) for key in (
                    "paper_id", "doi", "title", "year", "journal", "article_type",
                    "landing_page_url", "abstract",
                )
            },
            "upstream_csv_rows": paper.get("upstream_csv_rows", []),
            "evidence_index_sha256": paper.get("evidence_index_sha256"),
            "qc_report_sha256": paper.get("qc_report_sha256"),
            "preprocessing_status": paper.get("preprocessing_status"),
            "raw_source_inventory": paper.get("raw_source_inventory", []),
        }
        paper_hash = _hash_json(paper_hash_payload)
        paper_chunk_plans.append({
            "paper_id": paper["paper_id"],
            "chunk_count": len(chunks),
            "source_ids": sorted({sid for chunk in chunks for sid in chunk["source_ids"]}),
            "chunk_ids": [chunk["chunk_id"] for chunk in chunks],
            "paper_chunk_index_path": str(paper_index_path.resolve()),
        })

        for chunk in chunks:
            chunk_dir = paper_dir / "chunks" / chunk["chunk_id"]
            chunk_dir.mkdir(parents=True, exist_ok=True)
            chunk_evidence_path = _chunk_evidence_index(
                paper,
                chunk,
                sources_by_id,
                upstream_index,
                paper.get("evidence_index_sha256"),
                chunk_dir / "chunk_evidence_index.json",
            )
            bundle, visual_attachments, extracted_chars = build_bundle(
                paper, chunk, paper_index, chunk_evidence_path
            )
            bundle_path = chunk_dir / "task_input.md"
            bundle_path.write_text(bundle, encoding="utf-8", newline="\n")
            if chunk["oversize_atomic_unit"]:
                oversize_atomic_unit_count += 1

            attachments = sorted(set([
                *visual_attachments,
                str(chunk_evidence_path),
                str(paper_index_path.resolve()),
                *([str(path)] for path in []),
            ]))
            qc_path = _safe_output_path(paper.get("qc_report_path"))
            if qc_path:
                attachments.append(str(qc_path))
            attachments = sorted(set(attachments))
            chunk_hash = _hash_json({
                "paper_hash": paper_hash,
                "chunk_id": chunk["chunk_id"],
                "units": [
                    {
                        "unit_id": unit["unit_id"],
                        "source_ids": unit.get("source_ids", []),
                        "content_sha256": hashlib.sha256(
                            str(unit.get("content") or "").encode("utf-8")
                        ).hexdigest(),
                        "attachments": unit.get("asset_ids", []),
                    }
                    for unit in chunk["units"]
                ],
            })

            for role in ("source_discovery", "feature_audit"):
                prompt_path = PROMPTS[role]
                if not prompt_path.is_file():
                    raise TaskQueueError(f"Worker prompt file is missing: {prompt_path}")
                task_id = stable_task_id(paper["paper_id"], role, chunk_hash)
                task_dir = chunk_dir / ("worker_a" if role == "source_discovery" else "worker_b")
                task_dir.mkdir(parents=True, exist_ok=True)
                model_ref = model_refs[role]
                agent_name = "feature_create_worker_a" if role == "source_discovery" else "feature_create_worker_b"
                command_preview = [
                    "opencode", "--pure", "run",
                    "--model", model_ref,
                    "--agent", agent_name,
                    "--title", task_id,
                    "--format", "json",
                    "--dir", str(task_dir.resolve()),
                ]
                for attachment_path in [str(prompt_path.resolve()), str(bundle_path.resolve()), *attachments]:
                    command_preview.extend(["--file", attachment_path])
                command_preview.append(
                    f"Execute Feature_create task {task_id} (chunk {chunk['chunk_index']}/{chunk['chunk_count']}). Return the contract JSON."
                )
                command_chars = len(subprocess.list2cmdline(command_preview))
                if paper.get("pair_status") != "paired_files_verified" or paper.get("input_status") == "blocked_pair_integrity":
                    task_status = "blocked_pair_integrity"
                elif str(paper.get("input_status", "")).startswith("blocked"):
                    task_status = "blocked_preprocessing"
                elif command_chars > args.max_command_chars:
                    task_status = "blocked_command_line"
                else:
                    task_status = "pending"

                source_unit_ids: dict[str, list[str]] = {}
                for unit in chunk["units"]:
                    for source_id in unit.get("source_ids", []):
                        source_unit_ids.setdefault(source_id, []).append(unit["unit_id"])
                worker_task = {
                    "task_id": task_id,
                    "contract_version": "0.2",
                    "paper_id": paper["paper_id"],
                    "doi": paper.get("doi"),
                    "worker_role": role,
                    "model_ref": model_ref,
                    "prompt_path": str(prompt_path.resolve()),
                    "bundle_path": str(bundle_path.resolve()),
                    "attachment_paths": attachments,
                    "output_dir": str(task_dir.resolve()),
                    "task_status": task_status,
                    "extracted_text_chars": extracted_chars,
                    "source_ids": chunk["source_ids"],
                    "source_unit_ids": source_unit_ids,
                    "source_count": len(chunk["source_ids"]),
                    "unit_ids": chunk["unit_ids"],
                    "unit_count": chunk["unit_count"],
                    "attachment_count": len(attachments),
                    "preprocessing_status": paper.get("preprocessing_status"),
                    "input_status": paper.get("input_status", "ready"),
                    "chunk_id": chunk["chunk_id"],
                    "chunk_index": chunk["chunk_index"],
                    "chunk_count": chunk["chunk_count"],
                    "previous_chunk_id": chunk.get("previous_chunk_id"),
                    "next_chunk_id": chunk.get("next_chunk_id"),
                    "paper_chunk_index_path": str(paper_index_path.resolve()),
                    "chunk_evidence_index_path": str(chunk_evidence_path),
                    "evidence_index_path": paper.get("evidence_index_copy"),
                    "evidence_index_sha256": paper.get("evidence_index_sha256"),
                    "raw_source_inventory": paper.get("raw_source_inventory", []),
                    "command_length_estimate": command_chars,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "input_fingerprint": chunk_hash,
                    "max_bundle_chars": args.max_bundle_chars,
                    "oversize_atomic_unit": chunk["oversize_atomic_unit"],
                }
                task_path = task_dir / "task.json"
                task_path.write_text(
                    json.dumps(worker_task, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                    newline="\n",
                )
                worker_task["task_json_path"] = str(task_path.resolve())
                tasks.append(worker_task)

    queue = {
        "queue_version": "0.2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_path": str(manifest_path),
        "corpus_fingerprint": manifest.get("corpus_fingerprint"),
        "source_kind": manifest.get("source_kind"),
        "upstream_artifact": manifest.get("upstream_artifact"),
        "upstream_artifact_sha256": manifest.get("upstream_artifact_sha256"),
        "paper_count": paper_count_with_sources,
        "chunk_count": sum(plan["chunk_count"] for plan in paper_chunk_plans),
        "paper_chunk_plans": paper_chunk_plans,
        "task_count": len(tasks),
        "pending_count": sum(task["task_status"] == "pending" for task in tasks),
        "blocked_count": sum(task["task_status"] != "pending" for task in tasks),
        "oversize_atomic_unit_count": oversize_atomic_unit_count,
        "chunking_policy": {
            "text_boundary": "upstream page headings, then paragraph/newline boundaries",
            "table_boundary": "original source table rows with global 1-based row and column indices",
            "asset_assignment": "each extracted image assigned once to its matching figure/table evidence unit when possible",
            "context_policy": "adjacent text and repeated table prefix rows are context-only, not extraction assignments",
            "default_max_bundle_chars": args.max_bundle_chars,
            "default_max_attachments_per_chunk": args.max_attachments_per_chunk,
        },
        "tasks": tasks,
    }
    queue_path = output_root / "task_queue.json"
    queue_path.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    summary = {
        "paper_count": paper_count_with_sources,
        "chunk_count": queue["chunk_count"],
        "task_count": len(tasks),
        "pending_count": queue["pending_count"],
        "blocked_count": queue["blocked_count"],
        "oversize_atomic_unit_count": oversize_atomic_unit_count,
        "queue": str(queue_path),
    }
    (output_root / "queue_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
