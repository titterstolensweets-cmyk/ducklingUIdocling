"""
Utilities to prepare a translation-ready Markdown export where every
GFM/Markdown table is replaced by a fenced code block containing the
verbatim CSV extracted by ConverterService._extract_tables()
(tables/table_N.csv).

Rationale
---------
Docling's `export_to_markdown()` renders tables as native pipe (GFM) tables.
That's great for reading, but ambiguous for translation pipelines: a
paragraph-based chunker/translator can't always tell prose from tabular
data, and pipe tables get mangled easily by naive line-based translation.

Docling numbers extracted tables in reading order (table_1.csv, table_2.csv,
...), which matches the order tables appear in the exported Markdown. That
numbering is used here as the link key between "the Nth pipe table found in
the Markdown" and "tables/table_N.csv" on disk.

This module never mutates the original *.md export produced by the
converter; it always writes a separate "<stem>.translation.md" file so the
regular Markdown download/preview is untouched.
"""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Matches "table_<n>.csv", capturing n. Anchored so we don't accidentally
# match something like "table_1_old.csv".
_TABLE_CSV_RE = re.compile(r"^table_(\d+)\.csv$", re.IGNORECASE)

# A GFM delimiter cell: optional leading colon, one or more dashes, optional
# trailing colon (e.g. "---", ":--", "--:", ":-:").
_DELIMITER_CELL_RE = re.compile(r"^:?-{1,}:?$")

DEFAULT_FENCE_LANG = "csv"
TRANSLATION_SUFFIX = ".translation.md"


class TranslationExportError(Exception):
    """Raised when a translation-ready Markdown export cannot be produced."""


@dataclass
class TableEmbedStats:
    """Summary of what happened while embedding CSV tables into Markdown."""

    tables_found_in_markdown: int = 0
    csv_files_available: int = 0
    tables_embedded: int = 0
    tables_missing_csv: List[int] = field(default_factory=list)  # 1-based

    def to_dict(self) -> dict:
        return {
            "tables_found": self.tables_found_in_markdown,
            "csv_files_available": self.csv_files_available,
            "tables_embedded": self.tables_embedded,
            "tables_missing": self.tables_missing_csv,
        }


def _is_table_row(line: str) -> bool:
    """A line that could plausibly be part of a GFM pipe table."""
    stripped = line.strip()
    if not stripped:
        return False
    return "|" in stripped


def _is_delimiter_row(line: str) -> bool:
    """
    True if `line` is a valid GFM header-separator row, e.g.
    "| --- | :---: | ---: |" or "--- | ---".
    """
    stripped = line.strip()
    if not stripped or "-" not in stripped:
        return False

    inner = stripped
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    if not inner.strip():
        return False

    cells = [c.strip() for c in inner.split("|")]
    return bool(cells) and all(_DELIMITER_CELL_RE.match(c) for c in cells)


def find_markdown_table_blocks(lines: List[str]) -> List[Tuple[int, int]]:
    """
    Find contiguous line ranges that form GFM pipe tables.

    Returns a list of (start, end) 0-based, end-exclusive index pairs, in
    the order they appear in `lines`. A block requires a header row
    immediately followed by a valid delimiter row; it then extends through
    any following table-row lines.
    """
    blocks: List[Tuple[int, int]] = []
    i = 0
    n = len(lines)

    while i < n:
        if _is_table_row(lines[i]) and i + 1 < n and _is_delimiter_row(lines[i + 1]):
            start = i
            j = i + 2
            while j < n and _is_table_row(lines[j]):
                j += 1
            blocks.append((start, j))
            i = j
        else:
            i += 1

    return blocks


def _find_table_csv_files(tables_dir: Path) -> Dict[int, Path]:
    """Map table number -> Path, for every table_<n>.csv found in tables_dir."""
    result: Dict[int, Path] = {}
    if not tables_dir.is_dir():
        return result
    for entry in tables_dir.iterdir():
        if not entry.is_file():
            continue
        match = _TABLE_CSV_RE.match(entry.name)
        if match:
            result[int(match.group(1))] = entry
    return result


def _read_csv_as_text(csv_path: Path) -> str:
    """Read a CSV file verbatim (minus a trailing newline) for fencing."""
    return csv_path.read_text(encoding="utf-8").rstrip("\n")


def _pick_fence(text: str, min_ticks: int = 3) -> str:
    """
    Pick a backtick fence long enough that it can't collide with a run of
    backticks inside `text` (extremely unlikely in CSV data, but cheap to
    guard against).
    """
    max_run = 0
    run = 0
    for ch in text:
        if ch == "`":
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    return "`" * max(min_ticks, max_run + 1)


def embed_tables_in_markdown(
    markdown_text: str,
    tables_dir: Path,
    fence_lang: str = DEFAULT_FENCE_LANG,
    include_table_markers: bool = True,
) -> Tuple[str, TableEmbedStats]:
    """
    Replace every GFM table found in `markdown_text`, in reading order, with
    a fenced code block containing the verbatim contents of the matching
    tables/table_<n>.csv file (1-indexed, matching Docling's own numbering).

    Tables without a corresponding CSV file are left untouched as native
    Markdown tables, so nothing is silently dropped.
    """
    lines = markdown_text.splitlines()
    blocks = find_markdown_table_blocks(lines)
    csv_files = _find_table_csv_files(tables_dir)

    stats = TableEmbedStats(
        tables_found_in_markdown=len(blocks),
        csv_files_available=len(csv_files),
    )

    if not blocks:
        return markdown_text, stats

    out_lines: List[str] = []
    cursor = 0
    for table_index, (start, end) in enumerate(blocks, start=1):
        out_lines.extend(lines[cursor:start])

        csv_path = csv_files.get(table_index)
        if csv_path is None:
            logger.warning(
                "No CSV found for table #%d (expected table_%d.csv); "
                "keeping native Markdown table.",
                table_index,
                table_index,
            )
            stats.tables_missing_csv.append(table_index)
            out_lines.extend(lines[start:end])
        else:
            csv_text = _read_csv_as_text(csv_path)
            fence = _pick_fence(csv_text)
            if include_table_markers:
                out_lines.append(f"<!-- table_id: table_{table_index} -->")
            out_lines.append(f"{fence}{fence_lang}")
            out_lines.extend(csv_text.splitlines())
            out_lines.append(fence)
            stats.tables_embedded += 1

        cursor = end

    out_lines.extend(lines[cursor:])

    rebuilt = "\n".join(out_lines)
    if markdown_text.endswith("\n"):
        rebuilt += "\n"
    return rebuilt, stats


def generate_translation_markdown(
    markdown_path: Path,
    tables_dir: Path,
    output_path: Optional[Path] = None,
    fence_lang: str = DEFAULT_FENCE_LANG,
    include_table_markers: bool = True,
) -> Tuple[Path, TableEmbedStats]:
    """
    Read `markdown_path`, embed CSV tables from `tables_dir`, and write the
    result to `output_path` (defaults to "<stem>.translation.md" next to the
    source file). Returns (output_path, stats).
    """
    if not markdown_path.is_file():
        raise TranslationExportError(f"Markdown file not found: {markdown_path}")

    markdown_text = markdown_path.read_text(encoding="utf-8")
    embedded_text, stats = embed_tables_in_markdown(
        markdown_text,
        tables_dir,
        fence_lang=fence_lang,
        include_table_markers=include_table_markers,
    )

    if output_path is None:
        output_path = markdown_path.with_name(markdown_path.stem + TRANSLATION_SUFFIX)

    output_path.write_text(embedded_text, encoding="utf-8")
    logger.info(
        "Wrote translation-ready markdown to %s (%d/%d tables embedded)",
        output_path,
        stats.tables_embedded,
        stats.tables_found_in_markdown,
    )
    return output_path, stats


def _find_source_markdown(output_dir: Path) -> Path:
    """Pick the primary .md export, ignoring any previously generated
    "<stem>.translation.md" file living in the same directory."""
    candidates = [
        p for p in output_dir.glob("*.md") if not p.name.endswith(TRANSLATION_SUFFIX)
    ]
    if not candidates:
        raise TranslationExportError("No markdown export found for this job.")
    if len(candidates) > 1:
        candidates.sort(key=lambda p: len(p.name))
        logger.warning(
            "Multiple markdown files found in %s; using %s",
            output_dir,
            candidates[0].name,
        )
    return candidates[0]


def generate_translation_markdown_for_job(
    job_id: str,
    output_folder: Path,
    fence_lang: str = DEFAULT_FENCE_LANG,
    include_table_markers: bool = True,
) -> Tuple[Path, TableEmbedStats]:
    """
    Job-oriented convenience wrapper around generate_translation_markdown().
    Resolves the job's output directory using the app's existing security
    helpers, locates its Markdown export and tables/ folder, and writes
    "<stem>.translation.md" back into the same directory.
    """
    # Local import: keeps this module runnable as a plain CLI script without
    # needing the backend package on sys.path (see _cli(), which never
    # calls this function).
    from utils.security import validate_job_id, get_validated_output_dir

    validate_job_id(job_id)
    output_dir = get_validated_output_dir(job_id, output_folder)

    if not output_dir.exists():
        raise TranslationExportError(f"Output directory not found for job {job_id}")

    markdown_path = _find_source_markdown(output_dir)
    tables_dir = output_dir / "tables"

    return generate_translation_markdown(
        markdown_path,
        tables_dir,
        output_path=markdown_path.with_name(markdown_path.stem + TRANSLATION_SUFFIX),
        fence_lang=fence_lang,
        include_table_markers=include_table_markers,
    )


def _cli() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Embed extracted table CSVs (tables/table_N.csv) into a Docling "
            "Markdown export as fenced code blocks, for translation-friendly "
            "output. Writes <stem>.translation.md next to the input file."
        )
    )
    parser.add_argument("markdown", type=Path, help="Path to the source .md file")
    parser.add_argument(
        "--tables-dir",
        type=Path,
        default=None,
        help="Path to the tables/ directory (defaults to <markdown dir>/tables)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (defaults to <stem>.translation.md next to the input)",
    )
    parser.add_argument(
        "--fence-lang",
        default=DEFAULT_FENCE_LANG,
        help="Language tag for the fenced code block (default: csv)",
    )
    parser.add_argument(
        "--no-markers",
        action="store_true",
        help="Do not emit <!-- table_id: table_N --> comments before each block",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    tables_dir = args.tables_dir or (args.markdown.parent / "tables")
    output_path, stats = generate_translation_markdown(
        args.markdown,
        tables_dir,
        output_path=args.output,
        fence_lang=args.fence_lang,
        include_table_markers=not args.no_markers,
    )

    print(f"Wrote: {output_path}")
    print(
        f"Tables found in markdown: {stats.tables_found_in_markdown}; "
        f"embedded: {stats.tables_embedded}; "
        f"missing CSV: {stats.tables_missing_csv or 'none'}"
    )


if __name__ == "__main__":
    _cli()