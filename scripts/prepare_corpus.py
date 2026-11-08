"""Prepare only the paired corpus emitted by chem-literature.

The authoritative entry point is research_corpus_paired.csv. This script does
not crawl a directory or discover papers. It follows local_pdf_path and
local_si_info for each upstream-curated row, validates the referenced files,
extracts text, safely expands SI ZIP archives, renders figure/table PDF pages,
and writes an internal source_manifest.json.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".xml", ".tei", ".html", ".htm",
    ".csv", ".tsv", ".json", ".yaml", ".yml",
}
PDF_EXTENSIONS = {".pdf"}
SPREADSHEET_EXTENSIONS = {".xlsx"}
WORD_EXTENSIONS = {".docx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
ARCHIVE_EXTENSIONS = {".zip"}
SUPPORTED_EXTENSIONS = (
    TEXT_EXTENSIONS | PDF_EXTENSIONS | SPREADSHEET_EXTENSIONS
    | WORD_EXTENSIONS | IMAGE_EXTENSIONS
)
DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s<>\"']+)", re.IGNORECASE)


class CorpusError(RuntimeError):
    pass


class TextOnlyHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden_depth += 1
        if tag in {"p", "div", "section", "article", "tr", "li", "h1", "h2", "h3", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.hidden_depth:
            self.hidden_depth -= 1
        if tag in {"p", "div", "section", "article", "tr", "li", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth and data.strip():
            self.parts.append(data.strip())

    def get_text(self) -> str:
        return "\n".join(self.parts)


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def stable_id(prefix: str, value: str, length: int = 16) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:length]}"


def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().lower()
    candidate = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", candidate)
    candidate = re.sub(r"[\])}>,.;:]+$", "", candidate)
    return candidate if re.match(r"^10\.\d{4,9}/", candidate) else None


def clean_stem(stem: str) -> str:
    value = stem.casefold()
    value = re.sub(r"\.(pdf|md|txt|xml|csv|xlsx|html?|docx)$", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "unnamed"


def read_pdf(path: Path) -> tuple[str, str, int]:
    try:
        import fitz  # type: ignore
    except ImportError:
        return "", "dependency_missing_pymupdf", 0

    try:
        with path.open("rb") as handle:
            if not handle.read(5).startswith(b"%PDF-"):
                return "", "invalid_pdf_magic", 0
        chunks: list[str] = []
        with fitz.open(path) as document:
            for index, page in enumerate(document, start=1):
                page_text = page.get_text("text", sort=True) or ""
                chunks.append(f"\n\n===== PDF PAGE {index} =====\n{page_text}")
            content = "\n".join(chunks)
            status = "parsed" if content.strip() else "parsed_no_text_scanned_or_image_pdf"
            return content, status, len(document)
    except Exception as exc:  # noqa: BLE001
        return "", f"parse_error:{type(exc).__name__}:{exc}", 0


def render_pdf_visual_pages(
    path: Path,
    assets_dir: Path,
    source_id: str,
    dpi_scale: float = 1.25,
) -> list[dict[str, Any]]:
    """Render pages containing Figure/Table/Scheme captions as visual evidence."""
    try:
        import fitz  # type: ignore
    except ImportError:
        return []

    hints = re.compile(
        r"\b(?:fig(?:ure)?\.?\s*[s]?\s*\d+|table\s*[s]?\s*\d+|scheme\s*\d+)\b",
        re.IGNORECASE,
    )
    rendered: list[dict[str, Any]] = []
    try:
        assets_dir.mkdir(parents=True, exist_ok=True)
        with fitz.open(path) as document:
            for page_index, page in enumerate(document):
                page_text = page.get_text("text", sort=True) or ""
                matches = hints.findall(page_text)
                if not matches:
                    continue
                pixmap = page.get_pixmap(
                    matrix=fitz.Matrix(dpi_scale, dpi_scale),
                    alpha=False,
                )
                output_path = assets_dir / f"{source_id}_page_{page_index + 1:04d}.jpg"
                pixmap.save(output_path, jpg_quality=82)
                rendered.append({
                    "relative_path": output_path.name,
                    "page_number": page_index + 1,
                    "trigger_labels": sorted(set(matches), key=str.casefold),
                    "size_bytes": output_path.stat().st_size,
                    "render_scale": dpi_scale,
                })
    except Exception as exc:  # noqa: BLE001
        return [{
            "render_status": f"render_error:{type(exc).__name__}:{exc}",
            "page_number": None,
        }]
    return rendered


def read_xlsx(path: Path) -> tuple[str, str]:
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return "", "dependency_missing_openpyxl"
    try:
        workbook = openpyxl.load_workbook(path, read_only=True, data_only=False)
        chunks: list[str] = []
        for sheet in workbook.worksheets:
            chunks.append(f"\n===== WORKSHEET: {sheet.title} =====")
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                values = ["" if value is None else str(value) for value in row]
                if any(value != "" for value in values):
                    chunks.append(f"ROW {row_index}\t" + "\t".join(values))
        workbook.close()
        return "\n".join(chunks), "parsed"
    except Exception as exc:  # noqa: BLE001
        return "", f"parse_error:{type(exc).__name__}:{exc}"


def read_docx(path: Path) -> tuple[str, str]:
    try:
        from docx import Document  # type: ignore
    except ImportError:
        return "", "dependency_missing_python_docx"
    try:
        document = Document(str(path))
        chunks: list[str] = []
        for index, paragraph in enumerate(document.paragraphs, start=1):
            if paragraph.text.strip():
                chunks.append(f"PARAGRAPH {index}: {paragraph.text}")
        for table_index, table in enumerate(document.tables, start=1):
            chunks.append(f"\n===== DOCX TABLE {table_index} =====")
            for row_index, row in enumerate(table.rows, start=1):
                chunks.append(
                    f"ROW {row_index}\t" + "\t".join(cell.text for cell in row.cells)
                )
        return "\n".join(chunks), "parsed"
    except Exception as exc:  # noqa: BLE001
        return "", f"parse_error:{type(exc).__name__}:{exc}"


def read_csv_tsv(path: Path) -> tuple[str, str]:
    delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            rows = list(csv.reader(handle, delimiter=delimiter))
        lines = []
        for index, row in enumerate(rows, start=1):
            lines.append(f"ROW {index}\t" + "\t".join(row))
        return "\n".join(lines), "parsed"
    except Exception as exc:  # noqa: BLE001
        return "", f"parse_error:{type(exc).__name__}:{exc}"


def read_text_source(path: Path) -> tuple[str, str]:
    ext = path.suffix.casefold()
    try:
        raw = path.read_text(encoding="utf-8-sig", errors="replace")
        if ext in {".html", ".htm"}:
            parser = TextOnlyHTMLParser()
            parser.feed(raw)
            return parser.get_text(), "parsed"
        if ext in {".xml", ".tei"}:
            try:
                root = ET.fromstring(raw)
                return "\n".join(
                    piece.strip() for piece in root.itertext()
                    if piece and piece.strip()
                ), "parsed"
            except ET.ParseError:
                return raw, "parsed_with_xml_fallback"
        return raw, "parsed"
    except Exception as exc:  # noqa: BLE001
        return "", f"parse_error:{type(exc).__name__}:{exc}"


def extract_text(path: Path) -> tuple[str, str, int | None]:
    ext = path.suffix.casefold()
    if ext in PDF_EXTENSIONS:
        text, status, pages = read_pdf(path)
        return text, status, pages
    if ext in SPREADSHEET_EXTENSIONS:
        text, status = read_xlsx(path)
        return text, status, None
    if ext in WORD_EXTENSIONS:
        text, status = read_docx(path)
        return text, status, None
    if ext in {".csv", ".tsv"}:
        text, status = read_csv_tsv(path)
        return text, status, None
    if ext in TEXT_EXTENSIONS:
        text, status = read_text_source(path)
        return text, status, None
    if ext in IMAGE_EXTENSIONS:
        return "", "visual_asset_indexed", None
    return "", "unsupported", None


def parse_path_list(value: Any) -> list[str]:
    """Read the current single-path field and tolerate future JSON/list SI paths."""
    if value is None:
        return []
    if isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
        return values
    raw = str(value).strip()
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    # Current chem-literature emits one relative path. Semicolon-separated
    # lists are accepted for forward compatibility; commas are not split.
    if ";" in raw:
        return [item.strip() for item in raw.split(";") if item.strip()]
    return [raw]


def resolve_csv_path(raw_path: str, corpus_root: Path) -> Path:
    candidate = Path(raw_path.strip().strip('"'))
    if not candidate.is_absolute():
        candidate = corpus_root / candidate
    return candidate.expanduser().resolve()


def detect_archive(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"PK\x03\x04"
    except OSError:
        return False


def safe_extract_zip(
    archive_path: Path,
    destination: Path,
    max_files: int,
    max_uncompressed_bytes: int,
) -> tuple[list[Path], str]:
    """Extract supported SI ZIP members while rejecting traversal and ZIP bombs."""
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            infos = [
                item for item in archive.infolist()
                if not item.is_dir() and not item.filename.startswith("__MACOSX/")
            ]
            if len(infos) > max_files:
                return [], f"archive_blocked_file_count:{len(infos)}>{max_files}"
            total_bytes = sum(item.file_size for item in infos)
            if total_bytes > max_uncompressed_bytes:
                return [], f"archive_blocked_uncompressed_size:{total_bytes}>{max_uncompressed_bytes}"

            extracted: list[Path] = []
            for info in infos:
                # ZIP paths are POSIX-like even when produced on Windows.
                member = PurePosixPath(info.filename.replace("\\", "/"))
                if member.is_absolute() or any(part in {"..", ""} for part in member.parts):
                    continue
                if member.parts and member.parts[0] == "__MACOSX":
                    continue
                suffix = Path(member.name).suffix.casefold()
                if suffix not in SUPPORTED_EXTENSIONS and suffix not in ARCHIVE_EXTENSIONS:
                    continue
                target = destination.joinpath(*member.parts).resolve()
                try:
                    target.relative_to(destination.resolve())
                except ValueError:
                    continue
                # Do not materialize symlinks from untrusted archives.
                unix_mode = (info.external_attr >> 16) & 0xFFFF
                if (unix_mode & 0o170000) == 0o120000:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink)
                extracted.append(target)
            return extracted, "archive_expanded"
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return [], f"archive_error:{type(exc).__name__}:{exc}"


def load_paired_csv(path: Path) -> list[dict[str, str]]:
    required = {
        "doi", "title", "year", "journal", "article_type",
        "local_pdf_path", "local_si_info",
    }
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise CorpusError("Paired corpus CSV has no header row.")
            normalized = {name.strip().casefold() for name in reader.fieldnames if name}
            missing = sorted(required - normalized)
            if missing:
                raise CorpusError(
                    "This CSV is not a chem-literature paired corpus: missing columns "
                    + ", ".join(missing)
                )
            rows = []
            for row in reader:
                normalized_row = {
                    (key or "").strip().casefold(): (value or "").strip()
                    for key, value in row.items()
                }
                rows.append(normalized_row)
            return rows
    except OSError as exc:
        raise CorpusError(f"Cannot read paired corpus CSV {path}: {exc}") from exc


def add_source(
    path: Path,
    original_path: Path,
    paper_id: str,
    doi: str | None,
    role: str,
    source_kind: str,
    manifest_root: Path,
    extracted_dir: Path,
    visual_assets_dir: Path,
    archive_member: str | None = None,
    forced_status: str | None = None,
) -> dict[str, Any]:
    exists = path.is_file()
    if not exists:
        parse_status = forced_status or "missing_file"
        text = ""
        page_count = None
        digest = None
        size = 0
    else:
        digest = sha256_file(path)
        size = path.stat().st_size
        text, parse_status, page_count = extract_text(path)
        if forced_status:
            parse_status = forced_status
        if path.suffix.casefold() in PDF_EXTENSIONS and not digest:
            parse_status = "missing_file"
    source_material = "|".join([
        paper_id, role, str(path), digest or parse_status,
    ])
    source_id = stable_id("SRC", source_material)
    visual_pages: list[dict[str, Any]] = []
    if exists and path.suffix.casefold() == ".pdf" and parse_status.startswith("parsed"):
        visual_pages = render_pdf_visual_pages(path, visual_assets_dir, source_id)

    text_path: str | None = None
    if text.strip():
        text_file = extracted_dir / f"{source_id}.txt"
        text_file.parent.mkdir(parents=True, exist_ok=True)
        text_file.write_text(text, encoding="utf-8", newline="\n")
        text_path = text_file.relative_to(manifest_root).as_posix()

    try:
        relative_path = original_path.resolve().relative_to(manifest_root.parent.resolve()).as_posix()
    except ValueError:
        relative_path = str(original_path.resolve())

    return {
        "source_id": source_id,
        "paper_id": paper_id,
        "doi": doi,
        "source_role": role,
        "source_type": source_kind,
        "file_name": path.name,
        "relative_path": relative_path if not archive_member else f"{relative_path}!/{archive_member}",
        "absolute_path": str(path.resolve()),
        "original_absolute_path": str(original_path.resolve()),
        "archive_member": archive_member,
        "extension": path.suffix.casefold(),
        "size_bytes": size,
        "sha256": digest,
        "parse_status": parse_status,
        "page_count": page_count,
        "extracted_text_path": text_path,
        "extracted_text_chars": len(text),
        "is_visual_asset": path.suffix.casefold() in IMAGE_EXTENSIONS,
        "visual_pages": visual_pages,
    }


def source_kind_for(path: Path, role: str) -> str:
    ext = path.suffix.casefold()
    if ext in PDF_EXTENSIONS:
        return "main_text" if role == "main" else "si_text"
    if ext in IMAGE_EXTENSIONS:
        return "main_figure" if role == "main" else "si_figure"
    if ext in SPREADSHEET_EXTENSIONS or ext in {".csv", ".tsv"}:
        return "main_table" if role == "main" else "supporting_dataset"
    if ext in WORD_EXTENSIONS or ext in TEXT_EXTENSIONS:
        return "main_text" if role == "main" else "si_text"
    if ext in ARCHIVE_EXTENSIONS:
        return "si_archive"
    return "si_other" if role == "si" else "main_other"


def build_manifest_from_paired_csv(
    paired_csv: Path,
    output: Path,
    corpus_root: Path,
    max_archive_files: int,
    max_archive_bytes: int,
) -> dict[str, Any]:
    rows = load_paired_csv(paired_csv)
    if not rows:
        raise CorpusError("chem-literature paired corpus contains zero paper rows.")

    extracted_dir = output / "extracted_text"
    visual_assets_dir = output / "visual_assets"
    si_extract_dir = output / "si_archives"
    for directory in (extracted_dir, visual_assets_dir, si_extract_dir):
        directory.mkdir(parents=True, exist_ok=True)

    # Preserve the upstream curated list. Duplicate DOI rows are grouped only
    # to avoid dispatching the same article twice; all distinct listed assets
    # and all raw CSV metadata rows are retained in the paper manifest.
    grouped: dict[str, list[dict[str, str]]] = {}
    for row_index, row in enumerate(rows, start=1):
        doi = normalize_doi(row.get("doi"))
        if not doi:
            title = row.get("title") or f"row-{row_index}"
            paper_id = stable_id("PAPER", f"{title}|{row.get('year','')}|{row_index}", 12)
        else:
            paper_id = doi
        grouped.setdefault(paper_id, []).append(row)

    sources: list[dict[str, Any]] = []
    papers: list[dict[str, Any]] = []
    row_errors: list[dict[str, Any]] = []

    for paper_id, paper_rows in sorted(grouped.items()):
        primary = paper_rows[0]
        doi = normalize_doi(primary.get("doi"))
        title = primary.get("title", "")
        year = primary.get("year", "")
        journal = primary.get("journal", "")
        article_type = primary.get("article_type", "")
        paper_source_ids: list[str] = []
        main_source_ids: list[str] = []
        si_source_ids: list[str] = []
        main_paths: set[str] = set()
        si_paths: set[str] = set()
        missing_main = False
        missing_si = False
        paper_warnings: list[str] = []

        for row_index, row in enumerate(paper_rows, start=1):
            for raw in parse_path_list(row.get("local_pdf_path")):
                path = resolve_csv_path(raw, corpus_root)
                path_key = str(path).casefold()
                if path_key in main_paths:
                    continue
                main_paths.add(path_key)
                source = add_source(
                    path=path,
                    original_path=path,
                    paper_id=paper_id,
                    doi=doi,
                    role="main",
                    source_kind=source_kind_for(path, "main"),
                    manifest_root=output,
                    extracted_dir=extracted_dir,
                    visual_assets_dir=visual_assets_dir,
                    forced_status=None if path.is_file() else "missing_file",
                )
                sources.append(source)
                paper_source_ids.append(source["source_id"])
                main_source_ids.append(source["source_id"])
                if not path.is_file():
                    missing_main = True
                    paper_warnings.append(f"Main PDF missing: {raw}")
                elif path.suffix.casefold() != ".pdf":
                    missing_main = True
                    paper_warnings.append(f"Main article path is not a PDF: {raw}")
                else:
                    try:
                        with path.open("rb") as handle:
                            if not handle.read(5).startswith(b"%PDF-"):
                                missing_main = True
                                paper_warnings.append(f"Main article has invalid PDF magic: {raw}")
                    except OSError:
                        missing_main = True

            raw_si_paths = parse_path_list(row.get("local_si_info"))
            if not raw_si_paths:
                missing_si = True
                paper_warnings.append("No local_si_info path in upstream paired CSV row.")
            for si_index, raw in enumerate(raw_si_paths, start=1):
                path = resolve_csv_path(raw, corpus_root)
                path_key = str(path).casefold()
                if path_key in si_paths:
                    continue
                si_paths.add(path_key)
                if not path.is_file():
                    missing_si = True
                    source = add_source(
                        path=path,
                        original_path=path,
                        paper_id=paper_id,
                        doi=doi,
                        role="si",
                        source_kind=source_kind_for(path, "si"),
                        manifest_root=output,
                        extracted_dir=extracted_dir,
                        visual_assets_dir=visual_assets_dir,
                        forced_status="missing_file",
                    )
                    sources.append(source)
                    paper_source_ids.append(source["source_id"])
                    si_source_ids.append(source["source_id"])
                    paper_warnings.append(f"SI file missing: {raw}")
                    continue

                if path.suffix.casefold() in ARCHIVE_EXTENSIONS or detect_archive(path):
                    archive_source = add_source(
                        path=path,
                        original_path=path,
                        paper_id=paper_id,
                        doi=doi,
                        role="si",
                        source_kind="si_archive",
                        manifest_root=output,
                        extracted_dir=extracted_dir,
                        visual_assets_dir=visual_assets_dir,
                        forced_status="archive_present",
                    )
                    sources.append(archive_source)
                    paper_source_ids.append(archive_source["source_id"])
                    si_source_ids.append(archive_source["source_id"])

                    archive_destination = si_extract_dir / stable_id(
                        "SIARCH", f"{paper_id}|{path}|{sha256_file(path)}", 12
                    )
                    members, archive_status = safe_extract_zip(
                        path,
                        archive_destination,
                        max_files=max_archive_files,
                        max_uncompressed_bytes=max_archive_bytes,
                    )
                    archive_source["archive_expansion_status"] = archive_status
                    if not members:
                        paper_warnings.append(f"SI archive not expanded ({raw}): {archive_status}")
                    for member_path in members:
                        try:
                            member_name = member_path.relative_to(archive_destination).as_posix()
                        except ValueError:
                            member_name = member_path.name
                        member = add_source(
                            path=member_path,
                            original_path=path,
                            paper_id=paper_id,
                            doi=doi,
                            role="si",
                            source_kind=source_kind_for(member_path, "si"),
                            manifest_root=output,
                            extracted_dir=extracted_dir,
                            visual_assets_dir=visual_assets_dir,
                            archive_member=member_name,
                        )
                        sources.append(member)
                        paper_source_ids.append(member["source_id"])
                        si_source_ids.append(member["source_id"])
                else:
                    source = add_source(
                        path=path,
                        original_path=path,
                        paper_id=paper_id,
                        doi=doi,
                        role="si",
                        source_kind=source_kind_for(path, "si"),
                        manifest_root=output,
                        extracted_dir=extracted_dir,
                        visual_assets_dir=visual_assets_dir,
                    )
                    sources.append(source)
                    paper_source_ids.append(source["source_id"])
                    si_source_ids.append(source["source_id"])

        has_main = bool(main_paths) and not missing_main
        has_si = bool(si_paths) and not missing_si
        pair_status = "paired_files_verified" if has_main and has_si else "blocked_pair_integrity"
        if not has_main:
            paper_warnings.append("No verified main PDF path found.")
        if not has_si:
            paper_warnings.append("No verified SI path found.")

        papers.append({
            "paper_id": paper_id,
            "doi": doi,
            "title": title,
            "year": year,
            "journal": journal,
            "article_type": article_type,
            "landing_page_url": primary.get("landing_page_url", ""),
            "abstract": primary.get("abstract", ""),
            "upstream_article_type": article_type,
            "upstream_csv_row_count": len(paper_rows),
            "upstream_csv_rows": paper_rows,
            "source_ids": list(dict.fromkeys(paper_source_ids)),
            "main_source_ids": list(dict.fromkeys(main_source_ids)),
            "si_source_ids": list(dict.fromkeys(si_source_ids)),
            "source_count": len(set(paper_source_ids)),
            "pair_status": pair_status,
            "pair_warnings": sorted(set(paper_warnings)),
        })
        if pair_warnings := sorted(set(paper_warnings)):
            row_errors.extend({
                "paper_id": paper_id,
                "doi": doi,
                "warning": warning,
            } for warning in pair_warnings)

    fingerprint = hashlib.sha256(
        "\n".join(
            f"{source['source_id']}|{source.get('sha256') or source['parse_status']}"
            for source in sources
        ).encode("utf-8")
    ).hexdigest()
    csv_sha = sha256_file(paired_csv)
    manifest = {
        "manifest_version": "0.2",
        "source_kind": "chem-literature-research_corpus_paired.csv",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "upstream_skill": "chem-literature",
        "upstream_artifact": str(paired_csv.resolve()),
        "upstream_artifact_sha256": csv_sha,
        "corpus_root": str(corpus_root.resolve()),
        "output_root": str(output.resolve()),
        "upstream_row_count": len(rows),
        "paper_count": len(papers),
        "source_count": len(sources),
        "paired_files_verified_count": sum(1 for paper in papers if paper["pair_status"] == "paired_files_verified"),
        "blocked_pair_count": sum(1 for paper in papers if paper["pair_status"] != "paired_files_verified"),
        "corpus_fingerprint": fingerprint,
        "papers": papers,
        "sources": sources,
        "upstream_warnings": row_errors,
    }
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare only the chem-literature research_corpus_paired.csv output."
    )
    parser.add_argument(
        "--paired-csv",
        type=Path,
        required=True,
        help="research_corpus_paired.csv generated by chem-literature.",
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        help="Directory against which relative local_pdf_path/local_si_info values are resolved (defaults to CSV directory).",
    )
    parser.add_argument("--output", type=Path, required=True, help="Feature_create run preparation directory.")
    parser.add_argument("--max-archive-files", type=int, default=1000)
    parser.add_argument("--max-archive-bytes", type=int, default=2_000_000_000)
    args = parser.parse_args(argv)
    if args.max_archive_files < 1 or args.max_archive_bytes < 1:
        parser.error("Archive extraction limits must be positive.")

    paired_csv = args.paired_csv.expanduser().resolve()
    if not paired_csv.is_file():
        parser.error(f"--paired-csv does not exist: {paired_csv}")
    corpus_root = (
        args.corpus_root.expanduser().resolve()
        if args.corpus_root
        else paired_csv.parent.resolve()
    )
    if not corpus_root.is_dir():
        parser.error(f"--corpus-root is not a directory: {corpus_root}")
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    try:
        manifest = build_manifest_from_paired_csv(
            paired_csv=paired_csv,
            output=output,
            corpus_root=corpus_root,
            max_archive_files=args.max_archive_files,
            max_archive_bytes=args.max_archive_bytes,
        )
    except CorpusError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    manifest_path = output / "source_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    summary = {
        "status": "prepared",
        "source_kind": manifest["source_kind"],
        "upstream_artifact": manifest["upstream_artifact"],
        "upstream_artifact_sha256": manifest["upstream_artifact_sha256"],
        "upstream_row_count": manifest["upstream_row_count"],
        "paper_count": manifest["paper_count"],
        "source_count": manifest["source_count"],
        "paired_files_verified_count": manifest["paired_files_verified_count"],
        "blocked_pair_count": manifest["blocked_pair_count"],
        "manifest": str(manifest_path),
        "corpus_fingerprint": manifest["corpus_fingerprint"],
    }
    (output / "prepare_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
