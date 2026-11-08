"""Inventory and locally extract a paper/SI corpus without model calls."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

TEXT_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".xml", ".tei", ".html", ".htm",
    ".csv", ".tsv", ".json", ".yaml", ".yml",
}
PDF_EXTENSIONS = {".pdf"}
SPREADSHEET_EXTENSIONS = {".xlsx"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
SUPPORTED_EXTENSIONS = TEXT_EXTENSIONS | PDF_EXTENSIONS | SPREADSHEET_EXTENSIONS | IMAGE_EXTENSIONS
DOI_RE = re.compile(r"(10\.\d{4,9}/[^\s<>\"']+)", re.IGNORECASE)
SI_MARKERS = {
    "si", "supplement", "supplementary", "supporting", "supporting_information",
    "supporting-information", "supplementary_information", "supplementary-information",
    "papers_si", "si_files", "additional_file", "additional_files",
}
FIGURE_MARKERS = {"figure", "figures", "figure_images", "images", "fig"}
TABLE_MARKERS = {"table", "tables", "csv", "datasets", "data"}


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
    candidate = re.sub(r"[\])}>,.;:]+$", "", candidate)
    if not candidate.startswith("10."):
        return None
    return candidate


def find_doi(*values: str) -> str | None:
    for value in values:
        match = DOI_RE.search(value or "")
        if match:
            doi = normalize_doi(match.group(1))
            if doi:
                return doi
    return None


def clean_stem(stem: str) -> str:
    value = stem.casefold()
    value = re.sub(r"^(si|supp|supporting|supplementary)[_-]+", "", value)
    value = re.sub(r"[_-](si|supp|supporting|supplementary|supporting_information|supplementary_information)$", "", value)
    value = re.sub(r"\.(pdf|md|txt|xml|csv|xlsx)$", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "unnamed"


def infer_is_si(path: Path, relative: Path) -> bool:
    components = [part.casefold() for part in relative.parts[:-1]]
    stem = path.stem.casefold()
    if any(part in SI_MARKERS or any(marker in part for marker in ("supplement", "supporting_information", "papers_si")) for part in components):
        return True
    return bool(re.search(r"(^|[_-])(si|supp|supporting|supplementary)([_-]|$)", stem))


def infer_source_type(path: Path, relative: Path, is_si: bool) -> str:
    ext = path.suffix.casefold()
    parts = {part.casefold() for part in relative.parts[:-1]}
    if ext in IMAGE_EXTENSIONS or parts.intersection(FIGURE_MARKERS):
        return "si_figure" if is_si else "main_figure"
    if ext in {".csv", ".tsv", ".xlsx"} or parts.intersection(TABLE_MARKERS):
        return "supporting_dataset" if is_si else "main_table"
    return "si_text" if is_si else "main_text"


def read_pdf(path: Path) -> tuple[str, str, int]:
    try:
        import fitz  # type: ignore
    except ImportError:
        return "", "dependency_missing_pymupdf", 0

    chunks: list[str] = []
    try:
        with fitz.open(path) as document:
            for index, page in enumerate(document, start=1):
                page_text = page.get_text("text", sort=True) or ""
                chunks.append(f"\n\n===== PDF PAGE {index} =====\n{page_text}")
            return "\n".join(chunks), "parsed", len(document)
    except Exception as exc:  # noqa: BLE001 - report source parse failures in manifest
        return "", f"parse_error:{type(exc).__name__}:{exc}", 0


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
                return "\n".join(piece.strip() for piece in root.itertext() if piece and piece.strip()), "parsed"
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
    if ext in {".csv", ".tsv"}:
        text, status = read_csv_tsv(path)
        return text, status, None
    if ext in TEXT_EXTENSIONS:
        text, status = read_text_source(path)
        return text, status, None
    if ext in IMAGE_EXTENSIONS:
        return "", "visual_asset_indexed", None
    return "", "unsupported", None


def is_excluded(path: Path, root: Path, output: Path) -> bool:
    try:
        resolved = path.resolve()
        if resolved == output.resolve() or output.resolve() in resolved.parents:
            return True
    except OSError:
        pass
    excluded = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}
    return any(part in excluded for part in path.parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare an evidence-preserving local corpus manifest.")
    parser.add_argument("--input", type=Path, required=True, help="Corpus root containing papers and SI.")
    parser.add_argument("--output", type=Path, required=True, help="Run preparation directory.")
    parser.add_argument("--include-hidden", action="store_true", help="Include dot-prefixed directories/files.")
    args = parser.parse_args(argv)

    root = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"--input is not a directory: {root}")
    output.mkdir(parents=True, exist_ok=True)
    extracted_dir = output / "extracted_text"
    extracted_dir.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or is_excluded(path, root, output):
            continue
        if not args.include_hidden and any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.suffix.casefold() in SUPPORTED_EXTENSIONS:
            files.append(path)
    files.sort(key=lambda p: p.as_posix().casefold())

    sources: list[dict[str, Any]] = []
    for path in files:
        relative = path.relative_to(root)
        is_si = infer_is_si(path, relative)
        source_type = infer_source_type(path, relative, is_si)
        digest = sha256_file(path)
        text, parse_status, page_count = extract_text(path)
        doi = find_doi(str(relative), text[:200000])
        paper_key = f"doi:{doi}" if doi else f"stem:{clean_stem(path.stem)}"
        source_id = stable_id("SRC", relative.as_posix() + "|" + digest)
        text_path: str | None = None
        if text:
            text_file = extracted_dir / f"{source_id}.txt"
            text_file.write_text(text, encoding="utf-8", newline="\n")
            text_path = text_file.relative_to(output).as_posix()

        stat = path.stat()
        sources.append({
            "source_id": source_id,
            "paper_key": paper_key,
            "paper_id": None,
            "doi": doi,
            "source_role": "si" if is_si else "main",
            "source_type": source_type,
            "file_name": path.name,
            "relative_path": relative.as_posix(),
            "absolute_path": str(path),
            "extension": path.suffix.casefold(),
            "size_bytes": stat.st_size,
            "sha256": digest,
            "parse_status": parse_status,
            "page_count": page_count,
            "extracted_text_path": text_path,
            "extracted_text_chars": len(text),
            "is_visual_asset": path.suffix.casefold() in IMAGE_EXTENSIONS,
        })

    # Stable paper IDs: DOI where found; otherwise use a cleaned source stem.
    groups: dict[str, list[dict[str, Any]]] = {}
    for source in sources:
        key = source["paper_key"]
        if key.startswith("doi:"):
            paper_id = key[4:]
        else:
            paper_id = stable_id("PAPER", key, 12)
        source["paper_id"] = paper_id
        groups.setdefault(paper_id, []).append(source)

    papers = []
    for paper_id, group in sorted(groups.items()):
        doi_values = sorted({s["doi"] for s in group if s["doi"]})
        papers.append({
            "paper_id": paper_id,
            "doi": doi_values[0] if doi_values else None,
            "source_ids": [s["source_id"] for s in group],
            "main_source_ids": [s["source_id"] for s in group if s["source_role"] == "main"],
            "si_source_ids": [s["source_id"] for s in group if s["source_role"] == "si"],
            "source_count": len(group),
            "unparsed_source_ids": [s["source_id"] for s in group if s["parse_status"].startswith(("dependency_missing", "parse_error"))],
        })

    corpus_fingerprint = hashlib.sha256(
        "\n".join(f"{s['relative_path']}|{s['sha256']}" for s in sources).encode("utf-8")
    ).hexdigest()
    manifest = {
        "manifest_version": "0.1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "corpus_root": str(root),
        "output_root": str(output),
        "corpus_fingerprint": corpus_fingerprint,
        "source_count": len(sources),
        "paper_count": len(papers),
        "sources": sources,
        "papers": papers,
    }
    manifest_path = output / "source_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    summary = {
        "source_count": len(sources),
        "paper_count": len(papers),
        "text_parsed": sum(1 for s in sources if s["extracted_text_chars"] > 0),
        "visual_assets": sum(1 for s in sources if s["is_visual_asset"]),
        "parse_failures": sum(1 for s in sources if s["parse_status"].startswith(("dependency_missing", "parse_error"))),
        "manifest": str(manifest_path),
        "corpus_fingerprint": corpus_fingerprint,
    }
    (output / "prepare_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
