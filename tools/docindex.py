#!/usr/bin/env python3
"""
Lightweight CLI for indexing/searching exported Apple framework docs.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
import sys

from docmeta import peek_markdown

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DOCS_ROOT = PROJECT_ROOT / "docs" / "apple"
DEFAULT_INDEX_DIR = PROJECT_ROOT / "Build" / "DocIndex"
DEFAULT_INDEX_PATH = DEFAULT_INDEX_DIR / "index.json"


@dataclass
class HeadingRecord:
    text: str
    anchor: str
    level: int


def _slugify(text: str) -> str:
    keep: List[str] = []
    for char in text:
        if char.isalnum():
            keep.append(char.lower())
        elif char in "/-_":
            keep.append("-")
    slug = "".join(keep)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "section"


def _parse_front_matter(lines: List[str]) -> tuple[Optional[Dict[str, object]], int]:
    if not lines or lines[0].strip() != "---":
        return None, 0
    data: Dict[str, object] = {}
    idx = 1
    current_key: Optional[str] = None
    while idx < len(lines):
        raw = lines[idx].rstrip("\n")
        idx += 1
        if raw.strip() == "---":
            break
        if raw.startswith("  - ") and current_key:
            value = raw[4:].strip()
            data.setdefault(current_key, []).append(value)
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        if value == "":
            data[key] = []
        else:
            converted: object = value
            if value in ("[]", "null"):
                converted = []
            else:
                try:
                    converted = int(value)
                except ValueError:
                    converted = value
            data[key] = converted
    return data, idx


def _collect_headings(lines: Iterable[str], start_index: int = 0) -> List[HeadingRecord]:
    headings: List[HeadingRecord] = []
    anchor_pattern = re.compile(r'<a id="([^"]+)"></a>')
    pending_anchor: Optional[str] = None
    if isinstance(lines, list):
        iterator = lines[start_index:]
    else:
        cache = list(lines)
        iterator = cache[start_index:]
    for raw in iterator:
        stripped = raw.strip()
        match = anchor_pattern.match(stripped)
        if match:
            pending_anchor = match.group(1)
            continue
        content = raw.lstrip()
        if not content.startswith("#"):
            continue
        level = len(content) - len(content.lstrip("#"))
        text = content[level:].strip()
        if not text:
            continue
        anchor = pending_anchor or _slugify(text)
        headings.append(HeadingRecord(text=text, anchor=anchor, level=min(level, 6)))
        pending_anchor = None
    return headings


def build_index(docs_root: Path = DEFAULT_DOCS_ROOT, index_path: Path = DEFAULT_INDEX_PATH) -> Dict[str, object]:
    entries: List[Dict[str, object]] = []
    for md_path in sorted(docs_root.glob("*.md")):
        lines = md_path.read_text(encoding="utf-8").splitlines()
        front_matter, offset = _parse_front_matter(lines)
        if not front_matter:
            continue
        headings = _collect_headings(lines, offset)
        try:
            rel_path = md_path.relative_to(PROJECT_ROOT)
        except ValueError:
            rel_path = md_path
        entries.append(
            {
                "path": str(rel_path),
                "title": front_matter.get("title", md_path.stem),
                "docset_version": front_matter.get("docset_version", "unknown"),
                "exported_at": front_matter.get("exported_at", ""),
                "doc_count": front_matter.get("doc_count", 0),
                "file_size": front_matter.get("file_size", 0),
                "key_sections": front_matter.get("key_sections", []),
                "headings": [heading.__dict__ for heading in headings],
            }
        )
    try:
        docs_root_str = str(docs_root.relative_to(PROJECT_ROOT))
    except ValueError:
        docs_root_str = str(docs_root)
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "docs_root": docs_root_str,
        "entries": entries,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


def _load_index(index_path: Path) -> Dict[str, object]:
    return json.loads(index_path.read_text(encoding="utf-8"))


def _ensure_index(docs_root: Path, index_path: Path) -> Dict[str, object]:
    if index_path.exists():
        return _load_index(index_path)
    return build_index(docs_root, index_path)


def list_entries(index: Dict[str, object]) -> List[str]:
    output: List[str] = []
    for entry in index.get("entries", []):
        output.append(
            f"{entry.get('title')} — {entry.get('path')} "
            f"(exported {entry.get('exported_at')}, doc_count={entry.get('doc_count')})"
        )
    return output


def search_entries(index: Dict[str, object], term: str) -> List[str]:
    term_lower = term.lower()
    results: List[str] = []
    seen: set[str] = set()
    for entry in index.get("entries", []):
        path = entry.get("path")
        title = entry.get("title")
        for heading in entry.get("headings", []):
            text = heading.get("text", "")
            if term_lower in text.lower():
                anchor = heading.get("anchor")
                key = f"{path}#{anchor}"
                if key in seen:
                    continue
                seen.add(key)
                results.append(f"{title}: {text} — {path}#{anchor}")
        for section in entry.get("key_sections", []):
            if term_lower in str(section).lower():
                slug = _slugify(section)
                key = f"{path}#{slug}"
                if key in seen:
                    continue
                seen.add(key)
                results.append(f"{title}: {section} — {path}#{slug}")
    return results


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search indexed Apple docs.")
    parser.add_argument(
        "--docs-root",
        default=str(DEFAULT_DOCS_ROOT),
        help="Directory containing exported framework docs (default: docs/apple).",
    )
    parser.add_argument(
        "--index",
        default=str(DEFAULT_INDEX_PATH),
        help="Index file path (default: Build/DocIndex/index.json).",
    )
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("list", help="List indexed frameworks.")

    search_parser = subparsers.add_parser("search", help="Search for headings/key sections.")
    search_parser.add_argument("term", help="Search term (case-insensitive substring).")

    subparsers.add_parser("rebuild", help="Rebuild the index from docs/apple.")

    skim_parser = subparsers.add_parser(
        "skim",
        help="Inspect front matter (and optional TOC) without reading full markdown files.",
    )
    skim_parser.add_argument(
        "--input",
        action="append",
        help="Markdown file to skim (repeatable). If omitted, skims all *.md under --docs-root.",
    )
    skim_parser.add_argument(
        "--toc",
        action="store_true",
        help="Include the Table of Contents block (scans until next heading).",
    )
    skim_parser.add_argument(
        "--max-lines",
        type=int,
        default=800,
        help="Maximum lines to scan when searching for TOC (default: 800).",
    )
    return parser


def skim_files(paths: Sequence[Path], include_toc: bool, max_lines: int) -> List[Dict[str, object]]:
    results: List[Dict[str, object]] = []
    for path in paths:
        try:
            info = peek_markdown(path, include_toc=include_toc, max_lines=max_lines)
        except FileNotFoundError:
            print(f"[docindex] Missing file: {path}", file=sys.stderr)
            continue
        results.append(info)
    return results


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    docs_root = Path(args.docs_root).expanduser()
    index_path = Path(args.index).expanduser()
    if args.command == "rebuild":
        build_index(docs_root, index_path)
        print(f"[docindex] Rebuilt index at {index_path}")
        return 0
    index = _ensure_index(docs_root, index_path)
    if args.command == "list":
        for line in list_entries(index):
            print(line)
        return 0
    if args.command == "search":
        results = search_entries(index, args.term)
        if not results:
            print("[docindex] No matches found.")
        else:
            for line in results:
                print(line)
        return 0
    if args.command == "skim":
        target_paths: List[Path]
        if args.input:
            target_paths = [Path(p).expanduser() for p in args.input]
        else:
            target_paths = sorted(docs_root.glob("*.md"))
        if not target_paths:
            print(f"[docindex] No markdown files found under {docs_root}", file=sys.stderr)
            return 1
        results = skim_files(target_paths, include_toc=args.toc, max_lines=args.max_lines)
        for entry in results:
            print(json.dumps(entry))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
