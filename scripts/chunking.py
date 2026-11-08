"""Deterministic source-aware chunk planning for preprocessed paper evidence.

Text is split on upstream page/paragraph boundaries; oversized tables are split
at original row/cell boundaries; extracted images stay attached to their parent
evidence unit whenever possible. No source content is discarded.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

PAGE_HEADING_RE = re.compile(r"(?m)^## Page\s+(\d+)\s+\((MAIN|SI)\)\s*$")
DEFAULT_CONTEXT_CHARS = 600


class ChunkingError(RuntimeError):
    pass


def _stable_id(prefix: str, payload: str, length: int = 12) -> str:
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:length].upper()}"


def _read_source_text(output_root: Path, source: dict[str, Any]) -> str:
    value = source.get("extracted_text_path")
    if not value:
        return ""
    path = (output_root / str(value)).resolve()
    try:
        path.relative_to(output_root.resolve())
    except ValueError as exc:
        raise ChunkingError(f"Extracted text path escapes prepared directory: {path}") from exc
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _split_ranges(text: str, limit: int) -> list[tuple[int, int]]:
    """Return contiguous, non-overlapping ranges covering every input character."""
    if limit < 1:
        raise ValueError("Chunk character limit must be positive.")
    if not text:
        return [(0, 0)]
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + limit)
        if hard_end == len(text):
            end = hard_end
        else:
            search_start = min(hard_end, start + max(1, limit // 2))
            paragraph = text.rfind("\n\n", search_start, hard_end)
            newline = text.rfind("\n", search_start, hard_end)
            if paragraph >= search_start:
                end = paragraph + 2
            elif newline >= search_start:
                end = newline + 1
            else:
                end = hard_end
        if end <= start:
            end = hard_end
        ranges.append((start, end))
        start = end
    return ranges


def _text_units(source: dict[str, Any], text: str, max_chars: int) -> list[dict[str, Any]]:
    matches = list(PAGE_HEADING_RE.finditer(text))
    sections: list[tuple[str, int | None, int, int, str]] = []
    if matches:
        for index, match in enumerate(matches):
            # Attach any document preamble to page 1 so it is never dropped.
            global_start = 0 if index == 0 else match.start()
            global_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections.append((
                match.group(2).upper(),
                int(match.group(1)),
                global_start,
                global_end,
                text[global_start:global_end],
            ))
    else:
        role = str(source.get("source_role") or "other").upper()
        sections.append((role, None, 0, len(text), text))

    units: list[dict[str, Any]] = []
    for doc_type, page_number, global_start, _global_end, section in sections:
        ranges = _split_ranges(section, max_chars)
        for part_index, (start, end) in enumerate(ranges, start=1):
            content = section[start:end]
            # Whitespace-only ranges are retained: dropping them breaks exact
            # source reconstruction and can obscure section/page boundaries.
            if not content:
                continue
            before = section[max(0, start - DEFAULT_CONTEXT_CHARS):start] if start else ""
            after = section[end:min(len(section), end + DEFAULT_CONTEXT_CHARS)]
            locator: dict[str, Any] = {
                "doc_type": doc_type,
                "page_number": page_number,
                "text_segment_index_1based": part_index,
                "text_segment_count": len(ranges),
                "source_text_offset_start": global_start + start,
                "source_text_offset_end": global_start + end,
                "page_text_offset_start": start,
                "page_text_offset_end": end,
            }
            unit_id = _stable_id(
                "UNIT",
                f"{source['source_id']}|{json.dumps(locator, sort_keys=True)}",
            )
            units.append({
                "unit_id": unit_id,
                "source_id": source["source_id"],
                "source_ids": [source["source_id"]],
                "source_type": source.get("source_type", "other"),
                "component": source.get("component", "other"),
                "source_role": source.get("source_role", "other"),
                "doc_type": doc_type,
                "page_number": page_number,
                "unit_kind": "text",
                "locator": locator,
                "content": content,
                "context_before": before,
                "context_after": after,
                "attachment_paths": [],
                "asset_ids": [],
                "table_id": None,
                "figure_id": None,
                "sort_type": 0,
            })
    return units


def _render_row(row_index: int, row: list[Any]) -> str:
    return (
        f"ORIGINAL_ROW_INDEX_1BASED: {row_index}\n"
        f"CELLS_JSON: {json.dumps(row, ensure_ascii=False, separators=(',', ':'))}\n"
    )


def _table_base(table_id: str, doc_type: str, page_number: Any, caption: str) -> str:
    return (
        f"TABLE_ID: {table_id}\nDOCUMENT: {doc_type}\nPAGE: {page_number}\n"
        f"CAPTION: {caption}\n"
    )


def _make_table_unit(
    source: dict[str, Any],
    *,
    table_id: str,
    doc_type: str,
    page_number: Any,
    content: str,
    locator: dict[str, Any],
    sort_index: int,
) -> dict[str, Any]:
    unit_id = _stable_id("UNIT", f"{source['source_id']}|{json.dumps(locator, sort_keys=True)}")
    return {
        "unit_id": unit_id,
        "source_id": source["source_id"],
        "source_ids": [source["source_id"]],
        "source_type": source.get("source_type", "main_table" if doc_type == "MAIN" else "si_table"),
        "component": source.get("component", "main_tables" if doc_type == "MAIN" else "si_tables"),
        "source_role": source.get("source_role", "main" if doc_type == "MAIN" else "si"),
        "doc_type": doc_type,
        "page_number": page_number,
        "unit_kind": "table",
        "locator": locator,
        "content": content,
        "context_before": "",
        "context_after": "",
        "attachment_paths": [],
        "asset_ids": [],
        "table_id": table_id,
        "figure_id": None,
        "sort_type": 1,
        "table_sort_index": sort_index,
    }


def _table_units(source: dict[str, Any], max_chars: int) -> list[dict[str, Any]]:
    table = source.get("table_data") or {}
    rows = table.get("rows") or []
    table_id = str(source.get("table_id") or table.get("table_id") or "UNKNOWN_TABLE")
    doc_type = str(table.get("doc_type") or ("MAIN" if source.get("source_role") == "main" else "SI")).upper()
    page_number = table.get("page_number") or source.get("page_number")
    caption = table.get("caption") or source.get("caption") or ""
    base = _table_base(table_id, doc_type, page_number, caption)

    if not rows:
        locator = {
            "table_id": table_id,
            "page_number": page_number,
            "row_indices_1based": [],
        }
        return [_make_table_unit(
            source,
            table_id=table_id,
            doc_type=doc_type,
            page_number=page_number,
            content=base + "[No native table rows were supplied upstream.]\n",
            locator=locator,
            sort_index=1,
        )]

    context_count = min(3, len(rows))
    context_rows = rows[:context_count]
    context_text_full = "TABLE PREFIX ROWS FOR STRUCTURAL/HEADER CONTEXT ONLY:\n"
    context_text_full += "".join(
        _render_row(index, row) for index, row in enumerate(context_rows, start=1)
    )
    # A pathological header is kept in the attached chunk evidence index,
    # rather than duplicated into every task bundle.
    if len(context_text_full) <= max(256, int(max_chars * 0.30)):
        context_text = context_text_full
    else:
        context_text = (
            "HEADER CONTEXT: see header_context_rows in the attached "
            "chunk_evidence_index.json; original row numbers are retained.\n"
        )

    context_budget = len(context_text)
    current: list[tuple[int, list[Any]]] = []
    current_size = len(base) + context_budget
    units: list[dict[str, Any]] = []

    def emit_rows(group: list[tuple[int, list[Any]]]) -> None:
        if not group:
            return
        row_indices = [index for index, _ in group]
        assigned = "".join(_render_row(index, row) for index, row in group)
        locator = {
            "table_id": table_id,
            "page_number": page_number,
            "row_index_start_1based": row_indices[0],
            "row_index_end_1based": row_indices[-1],
            "row_indices_1based": row_indices,
            "column_count": max((len(row) for _, row in group), default=0),
            "header_context_row_indices_1based": list(range(1, context_count + 1)),
            "table_chunk_index_1based": len(units) + 1,
        }
        content = (
            base + context_text
            + f"\nASSIGNED ORIGINAL TABLE ROWS (review/extract these rows): {row_indices[0]}-{row_indices[-1]}\n"
            + assigned
        )
        units.append(_make_table_unit(
            source,
            table_id=table_id,
            doc_type=doc_type,
            page_number=page_number,
            content=content,
            locator=locator,
            sort_index=len(units) + 1,
        ))

    def emit_cell_fragments(row_index: int, row: list[Any]) -> None:
        nonlocal units
        prefix = (
            base + context_text
            + f"\nASSIGNED ORIGINAL TABLE ROW: {row_index}\n"
            + "Other cells in this row are linked by the same original row index; "
              "see assigned cell fragments and header rows in chunk_evidence_index.json.\n"
        )
        fragments: list[tuple[int, int, int, int, str, int]] = []
        cell_budget = max(128, max_chars - len(prefix) - 240)
        for column_index, value in enumerate(row, start=1):
            cell_text = "" if value is None else str(value)
            pieces = _split_ranges(cell_text, cell_budget)
            for segment_index, (start, end) in enumerate(pieces, start=1):
                fragments.append((
                    column_index,
                    start,
                    end,
                    segment_index,
                    cell_text[start:end],
                    len(pieces),
                ))

        current_fragments: list[tuple[int, int, int, int, str, int]] = []
        current_len = len(prefix)

        def emit_fragments(group: list[tuple[int, int, int, int, str, int]]) -> None:
            if not group:
                return
            rendered_cells = []
            cell_locators = []
            for column_index, start, end, segment_index, value, segment_count in group:
                rendered_cells.append(
                    "ASSIGNED_CELL_FRAGMENT_JSON: "
                    + json.dumps({
                        "original_row_index_1based": row_index,
                        "original_column_index_1based": column_index,
                        "header_context_row_indices_1based": list(range(1, context_count + 1)),
                        "cell_text_offset_start": start,
                        "cell_text_offset_end": end,
                        "cell_text_segment_index_1based": segment_index,
                        "cell_text_segment_count": segment_count,
                        "cell_text_fragment": value,
                    }, ensure_ascii=False, separators=(",", ":"))
                )
                cell_locators.append({
                    "row_index_1based": row_index,
                    "column_index_1based": column_index,
                    "cell_text_offset_start": start,
                    "cell_text_offset_end": end,
                    "cell_text_segment_index_1based": segment_index,
                    "cell_text_segment_count": segment_count,
                })
            locator = {
                "table_id": table_id,
                "page_number": page_number,
                "row_index_1based": row_index,
                "row_indices_1based": [row_index],
                "column_indices_1based": sorted({cell["column_index_1based"] for cell in cell_locators}),
                "cell_fragments": cell_locators,
                "header_context_row_indices_1based": list(range(1, context_count + 1)),
                "cell_level_chunk_index_1based": len(units) + 1,
            }
            content = prefix + "\n".join(rendered_cells) + "\n"
            units.append(_make_table_unit(
                source,
                table_id=table_id,
                doc_type=doc_type,
                page_number=page_number,
                content=content,
                locator=locator,
                sort_index=len(units) + 1,
            ))

        for fragment in fragments:
            rendered = "ASSIGNED_CELL_FRAGMENT_JSON: " + json.dumps({
                "original_row_index_1based": row_index,
                "original_column_index_1based": fragment[0],
                "cell_text_offset_start": fragment[1],
                "cell_text_offset_end": fragment[2],
                "cell_text_fragment": fragment[4],
            }, ensure_ascii=False, separators=(",", ":"))
            if current_fragments and current_len + len(rendered) > max_chars:
                emit_fragments(current_fragments)
                current_fragments = []
                current_len = len(prefix)
            current_fragments.append(fragment)
            current_len += len(rendered)
        emit_fragments(current_fragments)

    for row_index, row in enumerate(rows, start=1):
        rendered = _render_row(row_index, row)
        row_size = len(rendered)
        if row_size + len(base) + context_budget > max_chars:
            emit_rows(current)
            current = []
            current_size = len(base) + context_budget
            emit_cell_fragments(row_index, row)
            continue
        if current and current_size + row_size > max_chars:
            emit_rows(current)
            current = []
            current_size = len(base) + context_budget
        current.append((row_index, row))
        current_size += row_size

    emit_rows(current)
    return units


def build_evidence_units(
    sources: list[dict[str, Any]],
    source_output_root: Path,
    max_unit_chars: int,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    parent_lookup: dict[tuple[str, str], int] = {}

    for source in sources:
        # Visual assets are attached in the second pass to avoid creating a
        # duplicate standalone source unit when a matching caption/table exists.
        if source.get("is_visual_asset"):
            continue
        source_type = str(source.get("source_type") or "other")
        if source_type in {"main_text", "si_text"}:
            text = _read_source_text(source_output_root, source)
            created = _text_units(source, text, max_unit_chars) if text else []
        elif source_type in {"main_table", "si_table"} and source.get("table_data"):
            created = _table_units(source, max_unit_chars)
        else:
            text = _read_source_text(source_output_root, source)
            if not text.strip():
                text = (
                    f"SOURCE_ID: {source.get('source_id')}\n"
                    f"SOURCE_TYPE: {source_type}\n"
                    f"PARSE_STATUS: {source.get('parse_status')}\n"
                    "[No extracted text is available for this source item.]\n"
                )
            created = [{
                "unit_id": _stable_id("UNIT", f"{source['source_id']}|{source_type}|{source.get('relative_path')}"),
                "source_id": source["source_id"], "source_ids": [source["source_id"]],
                "source_type": source_type, "component": source.get("component", "other"),
                "source_role": source.get("source_role", "other"),
                "doc_type": "MAIN" if source.get("source_role") == "main" else "SI",
                "page_number": source.get("page_number"),
                "unit_kind": "source_item",
                "locator": {
                    "page_number": source.get("page_number"),
                    "figure_id": source.get("figure_id"),
                    "table_id": source.get("table_id"),
                    "bbox": source.get("bbox"),
                    "asset_id": source.get("asset_id"),
                },
                "content": text,
                "context_before": "", "context_after": "",
                "attachment_paths": [], "asset_ids": [],
                "table_id": source.get("table_id"), "figure_id": source.get("figure_id"),
                "sort_type": 2 if "figure_caption" in source_type else 3,
            }]

        for unit in created:
            unit["source_metadata"] = {
                "label": source.get("label"),
                "relative_path": source.get("relative_path"),
                "parse_status": source.get("parse_status"),
            }
            if unit.get("table_id"):
                parent_lookup.setdefault(("table", str(unit["table_id"])), len(units))
            if unit.get("figure_id"):
                parent_lookup.setdefault(("figure", str(unit["figure_id"])), len(units))
            units.append(unit)

    # Attach already-extracted images to their exact figure/table evidence unit.
    # A table crop is assigned once to the first evidence unit for that table.
    for source in sources:
        if not source.get("is_visual_asset"):
            continue
        path = source.get("attachment_path")
        kind = "table" if source.get("table_id") else "figure" if source.get("figure_id") else None
        parent_index = (
            parent_lookup.get((kind, str(source.get("table_id") or source.get("figure_id"))))
            if kind else None
        )
        if parent_index is not None:
            parent = units[parent_index]
            if path:
                parent["attachment_paths"].append(str(Path(path).resolve()))
            asset_id = source.get("asset_id")
            if asset_id:
                parent["asset_ids"].append(str(asset_id))
            parent["source_ids"].append(source["source_id"])
            parent.setdefault("related_assets", []).append({
                "source_id": source["source_id"],
                "asset_id": asset_id,
                "asset_type": source.get("asset_type"),
                "page_number": source.get("page_number"),
                "bbox": source.get("bbox"),
                "sha256": source.get("sha256"),
            })
        else:
            text = _read_source_text(source_output_root, source)
            unit = {
                "unit_id": _stable_id("UNIT", f"{source['source_id']}|orphan_visual_asset"),
                "source_id": source["source_id"], "source_ids": [source["source_id"]],
                "source_type": source.get("source_type", "other"),
                "component": source.get("component", "other"),
                "source_role": source.get("source_role", "other"),
                "doc_type": "MAIN" if source.get("source_role") == "main" else "SI",
                "page_number": source.get("page_number"),
                "unit_kind": "visual_asset",
                "locator": {
                    "page_number": source.get("page_number"), "figure_id": source.get("figure_id"),
                    "table_id": source.get("table_id"), "bbox": source.get("bbox"),
                    "asset_id": source.get("asset_id"),
                },
                "content": text or f"VISUAL_ASSET_ID: {source.get('asset_id')}\n",
                "context_before": "", "context_after": "",
                "attachment_paths": [str(Path(path).resolve())] if path else [],
                "asset_ids": [str(source.get("asset_id"))] if source.get("asset_id") else [],
                "table_id": source.get("table_id"), "figure_id": source.get("figure_id"),
                "sort_type": 3, "related_assets": [],
                "source_metadata": {"label": source.get("label"), "relative_path": source.get("relative_path")},
            }
            units.append(unit)

    units.sort(key=lambda unit: (
        0 if unit.get("source_role") == "main" else 1,
        int(unit.get("page_number") or 0),
        int(unit.get("sort_type") or 0),
        int(unit.get("table_sort_index") or 0),
        str(unit.get("unit_id")),
    ))
    for unit in units:
        unit["source_ids"] = sorted(set(unit.get("source_ids") or [unit["source_id"]]))
        unit["attachment_paths"] = sorted(set(unit.get("attachment_paths") or []))
    return units


def group_units_into_chunks(
    units: list[dict[str, Any]],
    max_chars: int,
    max_attachments: int,
) -> list[dict[str, Any]]:
    if max_chars < 1 or max_attachments < 1:
        raise ValueError("Chunk size and attachment limits must be positive.")
    if not units:
        return []
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    current_attachments: set[str] = set()

    def flush() -> None:
        nonlocal current, current_chars, current_attachments
        if not current:
            return
        unit_ids = [unit["unit_id"] for unit in current]
        chunk_id = _stable_id("CHUNK", "|".join(unit_ids), 10)
        source_ids = sorted({sid for unit in current for sid in unit.get("source_ids", [])})
        chunks.append({
            "chunk_id": chunk_id,
            "units": current,
            "unit_ids": unit_ids,
            "source_ids": source_ids,
            "unit_count": len(current),
            "source_count": len(source_ids),
            "content_chars": current_chars,
            "attachment_paths": sorted(current_attachments),
            "oversize_atomic_unit": any(
                len(unit.get("content", "")) > max_chars
                or len(unit.get("attachment_paths", [])) > max_attachments
                for unit in current
            ),
        })
        current = []
        current_chars = 0
        current_attachments = set()

    for unit in units:
        content_chars = len(str(unit.get("content") or ""))
        unit_attachments = set(unit.get("attachment_paths") or [])
        would_exceed = current and (
            current_chars + content_chars > max_chars
            or len(current_attachments | unit_attachments) > max_attachments
        )
        if would_exceed:
            flush()
        current.append(unit)
        current_chars += content_chars
        current_attachments.update(unit_attachments)
        if content_chars > max_chars or len(unit_attachments) > max_attachments:
            flush()
    flush()
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        chunk["chunk_index"] = index
        chunk["chunk_count"] = total
        chunk["previous_chunk_id"] = chunks[index - 2]["chunk_id"] if index > 1 else None
        chunk["next_chunk_id"] = chunks[index]["chunk_id"] if index < total else None
    return chunks
