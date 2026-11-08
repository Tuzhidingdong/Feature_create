"""Build bounded, provenance-preserving Worker A/B tasks from upstream-preprocessed sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {
    "source_discovery": ROOT / "worker_prompts" / "worker_a_source_discovery.md",
    "feature_audit": ROOT / "worker_prompts" / "worker_b_feature_audit.md",
}
MODEL_CONFIG = ROOT / "config" / "execution_policy.json"
PAGE_HEADER = re.compile(r"(?m)^## Page (?P<page>\d+) \((?P<doc>MAIN|SI)\)\s*$")


class TaskQueueError(RuntimeError):
    pass


def stable_task_id(paper_id: str, worker_role: str, fingerprint: str) -> str:
    digest = hashlib.sha256(
        f"{paper_id}|{worker_role}|{fingerprint}|0.2".encode("utf-8")
    ).hexdigest()[:12].upper()
    prefix = "A" if worker_role == "source_discovery" else "B"
    return f"FC-{prefix}-{digest}"


def stable_scope_id(source_id: str, descriptor: str, text: str) -> str:
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    digest = hashlib.sha256(
        f"{source_id}|{descriptor}|{text_hash}".encode("utf-8")
    ).hexdigest()[:16].upper()
    return f"SCP-{digest}"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TaskQueueError(f"Unable to read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TaskQueueError(f"Expected JSON object: {path}")
    return value


def source_text(output_root: Path, source: dict[str, Any]) -> str:
    text_path = source.get("extracted_text_path")
    if not text_path:
        return ""
    candidate = (output_root / text_path).resolve()
    try:
        candidate.relative_to(output_root.resolve())
    except ValueError as exc:
        raise TaskQueueError(f"Extracted text path escapes run directory: {candidate}") from exc
    if not candidate.is_file():
        return ""
    return candidate.read_text(encoding="utf-8", errors="replace")


def split_text_preserving_content(text: str, max_chars: int) -> list[str]:
    """Split at paragraph/line/whitespace boundaries without dropping or overlapping characters."""
    if not text:
        return [""]
    if max_chars < 1:
        raise TaskQueueError("Text chunk limit must be positive.")
    parts: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_chars)
        end = hard_end
        if hard_end < len(text):
            search_floor = start + max(1, max_chars // 3)
            candidates = [
                text.rfind("\n\n", search_floor, hard_end),
                text.rfind("\n", search_floor, hard_end),
                text.rfind(" ", search_floor, hard_end),
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + (2 if text[boundary:boundary + 2] == "\n\n" else 1)
        if end <= start:
            end = hard_end
        parts.append(text[start:end])
        start = end
    if "".join(parts) != text:
        raise TaskQueueError("Internal text chunking error: split segments do not reconstruct the source.")
    return parts


def text_page_segments(text: str, source: dict[str, Any], max_unit_chars: int) -> list[dict[str, Any]]:
    """Use chem-literature's explicit page headings when available."""
    matches = list(PAGE_HEADER.finditer(text))
    raw_pages: list[tuple[int | None, str]] = []
    if matches:
        preamble = text[:matches[0].start()]
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            page_text = text[match.start():end]
            if index == 0 and preamble:
                page_text = preamble + page_text
            raw_pages.append((int(match.group("page")), page_text))
    else:
        page_numbers = source.get("page_numbers") or []
        fallback_page = page_numbers[0] if len(page_numbers) == 1 else None
        raw_pages.append((int(fallback_page) if fallback_page is not None else None, text))

    result: list[dict[str, Any]] = []
    for page_number, page_text in raw_pages:
        parts = split_text_preserving_content(page_text, max_unit_chars)
        for part_index, part in enumerate(parts, start=1):
            page_label = f"page_{page_number}" if page_number is not None else "unpaged_text"
            part_label = f"_part_{part_index}_of_{len(parts)}" if len(parts) > 1 else ""
            descriptor = f"{page_label}{part_label}"
            result.append({
                "scope_id": stable_scope_id(source["source_id"], descriptor, part),
                "scope_type": "page_text" if len(parts) == 1 else "page_text_segment",
                "scope_label": f"{source.get('component')} {page_label}{part_label}",
                "page_number": page_number,
                "page_numbers": [page_number] if page_number is not None else list(source.get("page_numbers") or []),
                "part_index": part_index,
                "part_count": len(parts),
                "text": part,
            })
    return result


def render_table_rows(table: dict[str, Any], row_indices: list[int], context_rows: list[int]) -> str:
    lines = [
        f"TABLE_ID: {table.get('table_id', 'UNKNOWN')}",
        f"DOCUMENT: {table.get('doc_type', 'UNKNOWN')}",
        f"PAGE: {table.get('page_number', 'UNKNOWN')}",
        f"BBOX: {table.get('bbox')}",
        f"CAPTION: {table.get('caption') or ''}",
        "All row/column numbers below are original 1-based positions in evidence_index.json.",
    ]
    rows = table.get("rows") or []
    rendered: set[int] = set()
    for row_number in context_rows + row_indices:
        if row_number < 1 or row_number > len(rows) or row_number in rendered:
            continue
        rendered.add(row_number)
        kind = "CONTEXT ROW (repeated for column-header interpretation)" if row_number in context_rows and row_number not in row_indices else "PRIMARY ROW"
        for column_number, value in enumerate(rows[row_number - 1], start=1):
            cell = "" if value is None else str(value)
            lines.append(f"{kind} | ROW {row_number} | COLUMN {column_number} | CELL: {cell}")
    return "\n".join(lines) + "\n"


def table_row_segments(source: dict[str, Any], max_unit_chars: int) -> list[dict[str, Any]]:
    table = source.get("table_data")
    rows = table.get("rows") if isinstance(table, dict) else None
    if not isinstance(rows, list) or not rows:
        return []
    table_id = str(table.get("table_id") or source.get("table_id") or source["source_id"])
    row_lines = [
        sum(len(str(cell or "")) for cell in row) + 48 * max(1, len(row))
        for row in rows
    ]
    segments: list[tuple[int, int]] = []
    start = 1
    running = 0
    for row_number, row_chars in enumerate(row_lines, start=1):
        if row_number > start and running + row_chars > max_unit_chars:
            segments.append((start, row_number - 1))
            start = row_number
            running = 0
        running += row_chars
    segments.append((start, len(rows)))

    result: list[dict[str, Any]] = []
    for start_row, end_row in segments:
        primary_rows = list(range(start_row, end_row + 1))
        context_rows = [1] if start_row > 1 and len(rows) > 1 else []
        rendered = render_table_rows(table, primary_rows, context_rows)
        descriptor = f"table_{table_id}_rows_{start_row}_{end_row}"
        result.append({
            "scope_id": stable_scope_id(source["source_id"], descriptor, rendered),
            "scope_type": "table_rows",
            "scope_label": f"{table_id} original rows {start_row}-{end_row}",
            "page_number": table.get("page_number"),
            "table_id": table_id,
            "row_start": start_row,
            "row_end": end_row,
            "context_rows": context_rows,
            "text": rendered,
        })
    return result


def make_source_units(
    source_records: list[dict[str, Any]],
    source_output_root: Path,
    max_unit_chars: int,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for source in source_records:
        source_type = str(source.get("source_type") or "other")
        text = source_text(source_output_root, source)
        if source_type in {"main_text", "si_text"} and text:
            segments = text_page_segments(text, source, max_unit_chars)
        elif source_type in {"main_table", "si_table"} and source.get("table_data"):
            segments = table_row_segments(source, max_unit_chars)
            if not segments and text:
                segments = [{
                    "scope_id": stable_scope_id(source["source_id"], "table_text", text),
                    "scope_type": "table_text",
                    "scope_label": str(source.get("label") or source["source_id"]),
                    "page_number": source.get("page_number"),
                    "table_id": source.get("table_id"),
                    "row_start": None,
                    "row_end": None,
                    "text": text,
                }]
        else:
            if not text:
                text = (
                    f"SOURCE_ID: {source.get('source_id')}\n"
                    f"SOURCE_TYPE: {source_type}\n"
                    f"PARSE_STATUS: {source.get('parse_status')}\n"
                    f"LOCATOR: page={source.get('page_number')}; bbox={source.get('bbox')}; "
                    f"figure_id={source.get('figure_id')}; table_id={source.get('table_id')}\n"
                    f"CAPTION: {source.get('caption') or ''}\n"
                )
            segments = []
            pieces = split_text_preserving_content(text, max_unit_chars)
            for part_index, part in enumerate(pieces, start=1):
                descriptor = f"source_part_{part_index}_of_{len(pieces)}"
                segments.append({
                    "scope_id": stable_scope_id(source["source_id"], descriptor, part),
                    "scope_type": "source_item" if len(pieces) == 1 else "source_item_segment",
                    "scope_label": f"{source.get('label') or source['source_id']} {descriptor if len(pieces) > 1 else ''}".strip(),
                    "page_number": source.get("page_number"),
                    "part_index": part_index,
                    "part_count": len(pieces),
                    "text": part,
                })

        for segment in segments:
            scope = {key: value for key, value in segment.items() if key != "text"}
            scope.update({
                "source_id": source["source_id"],
                "component": source.get("component", "other"),
                "source_type": source_type,
                "source_role": source.get("source_role", "main"),
            })
            units.append({
                "scope": scope,
                "source": source,
                "text": segment["text"],
                "attachments": [source["attachment_path"]] if source.get("attachment_path") else [],
                "content_sha256": hashlib.sha256(segment["text"].encode("utf-8")).hexdigest(),
            })

    def sort_key(unit: dict[str, Any]) -> tuple[Any, ...]:
        source = unit["source"]
        role_order = 0 if source.get("source_role") == "main" else 1
        page = unit["scope"].get("page_number")
        page_order = int(page) if isinstance(page, int) else 10**9
        component = str(source.get("component") or "")
        component_order = {
            "main_text": 0, "main_tables": 1, "main_figures": 2,
            "si_text": 0, "si_tables": 1, "si_figures": 2,
        }.get(component, 3)
        return role_order, page_order, component_order, str(unit["scope"]["scope_id"])
    units.sort(key=sort_key)
    return units


def render_bundle(
    paper: dict[str, Any],
    units: list[dict[str, Any]],
    chunk_index: int,
    chunk_count: int,
) -> tuple[str, list[str], int, list[dict[str, Any]]]:
    source_records: dict[str, dict[str, Any]] = {}
    scopes = [unit["scope"] for unit in units]
    for unit in units:
        source = unit["source"]
        source_records[source["source_id"]] = source

    lines = [
        "# Feature_create chunked paper task input",
        "",
        f"- Paper ID: {paper['paper_id']}",
        f"- DOI: {paper.get('doi') or 'not found in local source metadata'}",
        f"- Title: {paper.get('title') or 'not supplied'}",
        f"- Year: {paper.get('year') or 'not supplied'}",
        f"- Journal: {paper.get('journal') or 'not supplied'}",
        f"- Article type from chem-literature: {paper.get('article_type') or 'not supplied'}",
        f"- Upstream landing page: {paper.get('landing_page_url') or 'not supplied'}",
        f"- Upstream preprocessing package: {paper.get('preprocessing_path') or 'not supplied'}",
        f"- Upstream preprocessing status: {paper.get('preprocessing_status', 'not recorded')}",
        f"- Feature_create input status: {paper.get('input_status', 'not recorded')}",
        f"- Chunk: {chunk_index} of {chunk_count}",
        f"- Assigned scope count: {len(scopes)}",
        "- Discovery boundary: all result variables actually reported in the source scopes assigned below; do not infer whole-paper coverage from one chunk.",
        "",
        "## Paper-wide context (metadata only; not a claim that other pages were reviewed)",
        "",
        str(paper.get("abstract") or "[No abstract supplied in paired CSV.]"),
        "",
        "## Original chem-literature CSV row metadata",
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
        "## Assigned source scopes",
        "",
        "| Scope ID | Source ID | Component | Scope | Original locator | Parse status |",
        "|---|---|---|---|---|---|",
    ]
    for scope in scopes:
        source = source_records[scope["source_id"]]
        locator = []
        for key in ("page_number", "table_id", "row_start", "row_end", "figure_id", "bbox"):
            if scope.get(key) is not None:
                locator.append(f"{key}={scope[key]}")
        lines.append(
            f"| {scope['scope_id']} | {scope['source_id']} | {scope.get('component')} | "
            f"{scope.get('scope_label')} | {'; '.join(locator) or source.get('relative_path')} | "
            f"{source.get('parse_status')} |"
        )

    lines.extend([
        "",
        "## Source inventory for this chunk",
        "",
        "| Source ID | Component | Type | Locator context | Parse status |",
        "|---|---|---|---|---|",
    ])
    attachments: list[str] = []
    for source_id, source in source_records.items():
        locator = []
        for key in ("page_number", "table_id", "figure_id", "bbox", "caption", "asset_type"):
            if source.get(key) is not None:
                locator.append(f"{key}={source.get(key)}")
        lines.append(
            f"| {source_id} | {source.get('component', 'other')} | "
            f"{source.get('source_type')} | {'; '.join(locator) or source.get('relative_path')} | "
            f"{source.get('parse_status')} |"
        )
    for unit in units:
        for attachment in unit["attachments"]:
            path = Path(attachment).resolve()
            if path.is_file():
                attachments.append(str(path))
    # Attach the complete authoritative upstream index and QC report to every
    # chunk. These are shared evidence context, not reparsed source material.
    for key in ("evidence_index_copy", "qc_report_path"):
        value = paper.get(key)
        if value:
            path = Path(value).resolve()
            if path.is_file():
                attachments.append(str(path))

    attachments = sorted(set(attachments))
    lines.extend([
        "",
        "## Review requirements",
        "",
        "- Review every assigned scope listed above. Do not claim to have reviewed unassigned pages, table rows, or assets.",
        "- Return one coverage entry for every assigned scope_id; repeated source_id values may have different scope_id values.",
        "- A source is fully reviewed only when every scope assigned to this task has been reviewed completely.",
        "- Keep the original source_id unchanged in all evidence references. Use the original page number and original 1-based table row/column positions.",
        "- For split table sources, inspect only the primary original row range for new result extraction; a repeated context row is only for reading column labels unless it is also in this scope's primary range.",
        "- Use the upstream evidence index and QC status as authoritative. Raw MAIN/SI source files are provenance only; never parse them again.",
        "- Preserve sample identity, conditions, time/cycle facts, protocol facts, and reported result values as separate facts.",
        "- Figure/table images are already-extracted upstream assets. Inspect all attached assets; an asset's existence alone does not prove it was interpreted.",
        "- Use explicit unresolved items when a result depends on context outside the assigned scope. Do not invent cross-page context.",
        "",
        "## Assigned preprocessed content",
        "",
    ])
    extracted_chars = 0
    for unit in units:
        scope = unit["scope"]
        extracted_chars += len(unit["text"])
        lines.extend([
            f"### SCOPE {scope['scope_id']} — SOURCE {scope['source_id']}",
            "",
            f"Scope type: {scope.get('scope_type')}; label: {scope.get('scope_label')}.",
            f"Original locator: page={scope.get('page_number')}; table={scope.get('table_id')}; "
            f"primary rows={scope.get('row_start')}-{scope.get('row_end')}.",
            "",
            unit["text"],
            "",
        ])
    return "\n".join(lines), sorted(set(attachments)), extracted_chars, scopes


def build_command_preview(
    model_ref: str,
    role: str,
    task_id: str,
    task_dir: Path,
    prompt_path: Path,
    bundle_path: Path,
    attachments: list[str],
) -> int:
    agent_name = "feature_create_worker_a" if role == "source_discovery" else "feature_create_worker_b"
    command = [
        "opencode", "--pure", "run", "--model", model_ref, "--agent", agent_name,
        "--title", task_id, "--format", "json", "--dir", str(task_dir.resolve()),
    ]
    for attachment_path in [str(prompt_path.resolve()), str(bundle_path.resolve()), *attachments]:
        command.extend(["--file", str(Path(attachment_path).resolve())])
    command.append(f"Execute Feature_create task {task_id}. Return the contract JSON.")
    return len(subprocess.list2cmdline(command))


def group_units(
    paper: dict[str, Any],
    units: list[dict[str, Any]],
    paper_dir: Path,
    model_refs: dict[str, str],
    max_bundle_chars: int,
    max_command_chars: int,
    max_attachments: int,
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def fits(candidate: list[dict[str, Any]], tentative_index: int) -> bool:
        bundle, attachments, _, _ = render_bundle(paper, candidate, tentative_index, tentative_index)
        if len(bundle) > max_bundle_chars or len(attachments) > max_attachments:
            return False
        bundle_path = paper_dir / f"chunk_{tentative_index:04d}" / "task_input.md"
        for role, model_ref in model_refs.items():
            task_dir = paper_dir / f"chunk_{tentative_index:04d}" / (
                "worker_a" if role == "source_discovery" else "worker_b"
            )
            if build_command_preview(
                model_ref, role, "FC-" + "X" * 14, task_dir,
                PROMPTS[role], bundle_path, attachments,
            ) > max_command_chars:
                return False
        return True

    for unit in units:
        candidate = current + [unit]
        if current and not fits(candidate, len(groups) + 1):
            groups.append(current)
            current = [unit]
        else:
            current = candidate
    if current:
        groups.append(current)
    return groups


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build bounded independent Worker A/B tasks from chem-literature preprocessed evidence."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-bundle-chars", type=int, default=120_000)
    parser.add_argument("--max-command-chars", type=int, default=7600)
    parser.add_argument("--max-attachments", type=int, default=12)
    args = parser.parse_args(argv)
    if args.max_bundle_chars < 1000 or args.max_command_chars < 1000 or args.max_attachments < 1:
        parser.error("max-bundle-chars and max-command-chars must be at least 1000; max-attachments must be positive.")

    manifest = load_json(args.manifest.resolve())
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
    paper_chunk_counts: dict[str, int] = {}
    papers = manifest.get("papers", [])
    manifest_root = args.manifest.expanduser().resolve().parent
    max_unit_chars = max(1000, min(50_000, args.max_bundle_chars // 2))

    for paper in papers:
        paper_sources = [
            sources_by_id[sid] for sid in paper.get("source_ids", []) if sid in sources_by_id
        ]
        if not paper_sources:
            continue
        paper_dir = output_root / "tasks" / hashlib.sha1(paper["paper_id"].encode("utf-8")).hexdigest()[:10]
        paper_dir.mkdir(parents=True, exist_ok=True)
        units = make_source_units(paper_sources, manifest_root, max_unit_chars)
        if not units:
            continue
        groups = group_units(
            paper, units, paper_dir, model_refs,
            args.max_bundle_chars, args.max_command_chars, args.max_attachments,
        )
        paper_chunk_counts[paper["paper_id"]] = len(groups)

        for chunk_index, chunk_units in enumerate(groups, start=1):
            chunk_id = stable_scope_id(
                paper["paper_id"],
                f"chunk_{chunk_index}_of_{len(groups)}",
                "|".join(unit["scope"]["scope_id"] for unit in chunk_units),
            )
            bundle, attachments, extracted_chars, scopes = render_bundle(
                paper, chunk_units, chunk_index, len(groups)
            )
            chunk_dir = paper_dir / f"chunk_{chunk_index:04d}"
            chunk_dir.mkdir(parents=True, exist_ok=True)
            scope_text_dir = chunk_dir / "scope_texts"
            scope_text_dir.mkdir(parents=True, exist_ok=True)
            for unit in chunk_units:
                scope = unit["scope"]
                scope_text_path = scope_text_dir / f"{scope['scope_id']}.txt"
                scope_text_path.write_text(unit["text"], encoding="utf-8", newline="\n")
                scope["scope_text_path"] = str(scope_text_path.resolve())
                scope["content_sha256"] = unit["content_sha256"]
            bundle_path = chunk_dir / "task_input.md"
            bundle_path.write_text(bundle, encoding="utf-8", newline="\n")

            source_ids = sorted({scope["source_id"] for scope in scopes})
            chunk_fingerprint_payload = {
                "paper_id": paper["paper_id"],
                "paper_metadata": {
                    key: paper.get(key)
                    for key in (
                        "doi", "title", "year", "journal", "article_type",
                        "landing_page_url", "abstract",
                    )
                },
                "upstream_csv_rows": paper.get("upstream_csv_rows", []),
                "evidence_index_sha256": paper.get("evidence_index_sha256"),
                "qc_report_sha256": paper.get("qc_report_sha256"),
                "preprocessing_status": paper.get("preprocessing_status"),
                "raw_source_inventory": paper.get("raw_source_inventory", []),
                "scopes": [
                    {
                        "scope_id": scope["scope_id"],
                        "source_id": scope["source_id"],
                        "content_sha256": next(
                            unit["content_sha256"] for unit in chunk_units
                            if unit["scope"]["scope_id"] == scope["scope_id"]
                        ),
                    }
                    for scope in scopes
                ],
            }
            chunk_fingerprint = hashlib.sha256(
                json.dumps(
                    chunk_fingerprint_payload, ensure_ascii=False, sort_keys=True
                ).encode("utf-8")
            ).hexdigest()

            for role in ("source_discovery", "feature_audit"):
                prompt_path = PROMPTS[role]
                if not prompt_path.is_file():
                    raise TaskQueueError(f"Worker prompt file is missing: {prompt_path}")
                task_id = stable_task_id(paper["paper_id"], role, chunk_fingerprint)
                task_dir = chunk_dir / ("worker_a" if role == "source_discovery" else "worker_b")
                task_dir.mkdir(parents=True, exist_ok=True)
                command_chars = build_command_preview(
                    model_refs[role], role, task_id, task_dir,
                    prompt_path, bundle_path, attachments,
                )

                if paper.get("pair_status") != "paired_files_verified" or paper.get("input_status") == "blocked_pair_integrity":
                    task_status = "blocked_pair_integrity"
                elif str(paper.get("input_status", "")).startswith("blocked"):
                    task_status = "blocked_preprocessing"
                elif len(bundle) > args.max_bundle_chars:
                    task_status = "blocked_oversize"
                elif len(attachments) > args.max_attachments:
                    task_status = "blocked_attachment_limit"
                elif command_chars > args.max_command_chars:
                    task_status = "blocked_command_line"
                else:
                    task_status = "pending"

                worker_task = {
                    "task_id": task_id,
                    "contract_version": "0.2",
                    "paper_id": paper["paper_id"],
                    "doi": paper.get("doi"),
                    "worker_role": role,
                    "model_ref": model_refs[role],
                    "prompt_path": str(prompt_path.resolve()),
                    "bundle_path": str(bundle_path.resolve()),
                    "attachment_paths": attachments,
                    "output_dir": str(task_dir.resolve()),
                    "task_status": task_status,
                    "chunk_id": chunk_id,
                    "chunk_index": chunk_index,
                    "chunk_count": len(groups),
                    "scope_count": len(scopes),
                    "source_scopes": scopes,
                    "source_ids": source_ids,
                    "source_count": len(source_ids),
                    "attachment_count": len(attachments),
                    "bundle_chars": len(bundle),
                    "extracted_text_chars": extracted_chars,
                    "preprocessing_status": paper.get("preprocessing_status"),
                    "input_status": paper.get("input_status", "ready"),
                    "evidence_index_path": paper.get("evidence_index_copy"),
                    "evidence_index_sha256": paper.get("evidence_index_sha256"),
                    "raw_source_inventory": paper.get("raw_source_inventory", []),
                    "command_length_estimate": command_chars,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "input_fingerprint": chunk_fingerprint,
                    "bundle_char_limit": args.max_bundle_chars,
                    "command_line_limit": args.max_command_chars,
                    "attachment_limit": args.max_attachments,
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
        "manifest_path": str(args.manifest.resolve()),
        "corpus_fingerprint": manifest.get("corpus_fingerprint"),
        "source_kind": manifest.get("source_kind"),
        "upstream_artifact": manifest.get("upstream_artifact"),
        "upstream_artifact_sha256": manifest.get("upstream_artifact_sha256"),
        "paper_count": len(papers),
        "chunked_paper_count": sum(1 for count in paper_chunk_counts.values() if count > 1),
        "chunk_count": sum(paper_chunk_counts.values()),
        "task_count": len(tasks),
        "pending_count": sum(task["task_status"] == "pending" for task in tasks),
        "blocked_count": sum(task["task_status"] != "pending" for task in tasks),
        "tasks": tasks,
    }
    queue_path = output_root / "task_queue.json"
    queue_path.write_text(
        json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    summary = {
        "paper_count": queue["paper_count"],
        "chunked_paper_count": queue["chunked_paper_count"],
        "chunk_count": queue["chunk_count"],
        "task_count": queue["task_count"],
        "pending_count": queue["pending_count"],
        "blocked_count": queue["blocked_count"],
        "max_bundle_chars": args.max_bundle_chars,
        "max_attachments": args.max_attachments,
        "queue": str(queue_path),
    }
    (output_root / "queue_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
