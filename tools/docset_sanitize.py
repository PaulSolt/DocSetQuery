#!/usr/bin/env python3
"""
Sanitize large DocC Markdown exports (front matter + Table of Contents).
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Dict, List, Tuple

STOPWORDS_DEFAULT = {
    "return value",
    "discussion",
    "special considerations",
    "parameters",
    "see also",
}


def parse_front_matter(lines: List[str]) -> Tuple[Dict[str, object], List[str]]:
    if not lines or lines[0].strip() != "---":
        return {}, lines
    fm_lines: List[str] = []
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            fm_lines = lines[1:i]
            rest = lines[i + 1 :]
            break
    else:
        fm_lines = lines[1:]
        rest = []

    data: Dict[str, object] = {}
    key: str | None = None
    for raw in fm_lines:
        stripped = raw.strip()
        if stripped.startswith("- ") and key:
            data.setdefault(key, []).append(stripped[2:])
        elif stripped.startswith("  - ") and key:
            data.setdefault(key, []).append(stripped[2:])
        elif ":" in raw:
            key_part, value = raw.split(":", 1)
            key = key_part.strip()
            value = value.strip()
            if value:
                data[key] = value
            else:
                data[key] = []
        else:
            continue
    return data, rest


def build_front_matter(
    meta: Dict[str, object],
    key_sections: List[str],
    toc_depth: int,
    stopwords: set[str],
) -> List[str]:
    summary = ", ".join(key_sections[:6])
    lines = ["---"]

    def emit(key: str, value: object | None) -> None:
        if value is None or value == "":
            return
        lines.append(f"{key}: {value}")

    emit("title", meta.get("title"))
    emit("docset_version", meta.get("docset_version"))
    emit("exported_at", meta.get("exported_at"))
    emit("doc_count", meta.get("doc_count"))
    emit("file_size", meta.get("file_size"))
    if summary:
        emit("summary", summary)
    lines.append("key_sections:")
    for section in key_sections[:20]:
        lines.append(f"  - {section}")
    lines.append("sanitizer:")
    lines.append(f"  generated_at: {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"  toc_depth: {toc_depth}")
    lines.append("  stopwords:")
    for word in sorted(stopwords):
        lines.append(f"    - {word}")
    lines.append("---")
    return lines


def rebuild_toc(text_lines: List[str], toc_depth: int, stopwords: set[str]) -> List[str]:
    result: List[str] = []
    stop_set = {s.lower() for s in stopwords}
    for line in text_lines:
        if not line.lstrip().startswith("-"):
            continue
        match = re.match(r"(\s*)- \[(.+?)\]", line)
        if not match:
            continue
        indent = len(match.group(1))
        depth = indent // 2 + 1
        title = match.group(2).strip()
        lower = title.lower()
        if depth > toc_depth:
            continue
        if lower in stop_set:
            continue
        if lower == "overview" and depth > 1:
            continue
        result.append(line.rstrip())
    return result


def sanitize_file(
    path: Path,
    toc_depth: int,
    stopwords: set[str],
    output: Path,
) -> None:
    text = path.read_text(encoding="utf-8").splitlines()
    meta, rest = parse_front_matter(text)
    key_sections = meta.get("key_sections", [])
    if not isinstance(key_sections, list):
        key_sections = []

    toc_start = None
    toc_end = None
    for i, line in enumerate(rest):
        if line.strip() == "## Table of Contents":
            toc_start = i
            continue
        if toc_start is not None and line.startswith("## ") and i > toc_start:
            toc_end = i
            break
    if toc_start is not None and toc_end is not None:
        toc_block = rest[toc_start + 1 : toc_end]
        trimmed_toc = rebuild_toc(toc_block, toc_depth, stopwords)
        rest = rest[: toc_start + 1] + trimmed_toc + rest[toc_end:]

    cleaned_sections: List[str] = []
    for title in key_sections:
        lower = title.lower()
        if lower in stopwords or lower == "overview":
            continue
        if title in cleaned_sections:
            continue
        cleaned_sections.append(title)
    key_sections = cleaned_sections

    if not key_sections:
        derived: List[str] = []
        for line in rest:
            m = re.match(r"- \[(.+?)\]", line.strip())
            if not m:
                continue
            title = m.group(1)
            lower = title.lower()
            if lower in stopwords or lower == "overview":
                continue
            if title in derived:
                continue
            derived.append(title)
            if len(derived) >= 10:
                break
        key_sections = derived

    fm_lines = build_front_matter(meta, key_sections, toc_depth, stopwords)
    sanitized = "\n".join(fm_lines + rest) + "\n"
    output.write_text(sanitized, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sanitize DocC Markdown exports (front matter + TOC)."
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--toc-depth", type=int, default=2)
    parser.add_argument("--stopword", action="append", dest="stopwords")
    parser.add_argument("--in-place", action="store_true")
    args = parser.parse_args()

    stopwords = {s.lower() for s in STOPWORDS_DEFAULT}
    if args.stopwords:
        stopwords |= {s.lower() for s in args.stopwords}

    output_path = args.output
    if args.in_place or output_path is None:
        output_path = args.input

    sanitize_file(args.input, args.toc_depth, stopwords, output_path)


if __name__ == "__main__":
    main()
