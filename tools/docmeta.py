#!/usr/bin/env python3
"""Quickly inspect markdown metadata (front matter + optional TOC) without reading whole files."""

from __future__ import annotations

import argparse
import json
from itertools import chain
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


def _parse_front_matter_stream(fh: Iterable[str]) -> Tuple[Dict[str, object], List[str], List[str]]:
    """
    Read just the front matter block from the stream.
    Returns parsed data, raw lines, and any lines consumed before front matter (to re-use when scanning TOC).
    """
    data: Dict[str, object] = {}
    carry: List[str] = []
    raw_lines: List[str] = []
    parent_key: str | None = None
    nested_current_key: str | None = None

    try:
        first = next(fh)
    except StopIteration:
        return data, raw_lines, carry

    if first.strip() != "---":
        carry.append(first)
        return data, raw_lines, carry

    current_key: str | None = None
    for raw in fh:
        if raw.strip() == "---":
            break
        raw_lines.append(raw.rstrip("\n"))
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))

        if indent > 0 and parent_key:
            target = data.get(parent_key)
            if isinstance(target, list) and not stripped.startswith("- "):
                target = {}
                data[parent_key] = target
            if isinstance(target, list) and stripped.startswith("- "):
                target.append(stripped[2:].strip())
                continue
            if isinstance(target, dict):
                if stripped.startswith("- "):
                    if nested_current_key:
                        target.setdefault(nested_current_key, []).append(stripped[2:].strip())
                    continue
                if ":" in stripped:
                    key, value = stripped.split(":", 1)
                    nested_current_key = key.strip()
                    value = value.strip()
                    if value == "":
                        target[nested_current_key] = []
                    else:
                        target[nested_current_key] = value
                    continue
        nested_current_key = None
        if stripped.startswith("- ") and current_key:
            data.setdefault(current_key, []).append(stripped[2:].strip())
            continue
        if stripped.startswith("  - ") and current_key:
            data.setdefault(current_key, []).append(stripped[4:].strip())
            continue
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        key = key.strip()
        value = value.strip()
        current_key = key
        parent_key = key if indent == 0 else parent_key
        if value == "":
            data[key] = []
        else:
            try:
                data[key] = int(value)
            except ValueError:
                data[key] = value
    return data, raw_lines, carry


def _read_toc_block(fh: Iterable[str], carry: List[str], max_lines: int) -> Tuple[List[str], bool]:
    """
    Scan for the TOC block after front matter and return lines + truncation flag.
    Stops at the next level-2 heading to avoid reading the full file.
    """
    toc: List[str] = []
    started = False
    lines_scanned = 0
    truncated = False
    for raw in chain(carry, fh):
        lines_scanned += 1
        if max_lines and lines_scanned > max_lines:
            truncated = True
            break
        stripped = raw.strip()
        if not started:
            if stripped == "## Table of Contents":
                started = True
                toc.append(stripped)
            continue
        if stripped.startswith("## ") and stripped != "## Table of Contents":
            break
        if not stripped:
            continue
        toc.append(raw.rstrip("\n"))
    return toc, truncated


def peek_markdown(path: Path, include_toc: bool, max_lines: int) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as fh:
        front_matter, front_matter_raw, carry = _parse_front_matter_stream(fh)
        toc: List[str] = []
        toc_truncated = False
        if include_toc:
            toc, toc_truncated = _read_toc_block(fh, carry, max_lines=max_lines)
    return {
        "path": str(path),
        "has_front_matter": bool(front_matter),
        "front_matter": front_matter,
        "front_matter_raw": front_matter_raw,
        "toc_found": bool(toc),
        "toc_truncated": toc_truncated,
        "toc": toc if include_toc else None,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Peek front matter (and optional TOC) without loading full markdown."
    )
    parser.add_argument("--input", required=True, type=Path, help="Markdown file to inspect.")
    parser.add_argument(
        "--toc",
        action="store_true",
        help="Also return the Table of Contents block (scans until next heading).",
    )
    parser.add_argument(
        "--max-lines",
        type=int,
        default=800,
        help="Maximum lines to scan when searching for TOC (after front matter).",
    )
    args = parser.parse_args(argv)

    info = peek_markdown(args.input, include_toc=args.toc, max_lines=args.max_lines)
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
