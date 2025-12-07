#!/usr/bin/env python3
"""
Apple docset helper for exporting Markdown references.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_DOCSET = Path(
    "~/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset"
).expanduser()


@dataclass
class DocumentEntry:
    identifier: str
    uuid: str
    data_id: int
    offset: int
    length: int
    doc: Optional[dict] = None
    paths_by_language: Dict[str, List[str]] = field(default_factory=dict)

    def primary_path(self, language: str = "swift") -> Optional[str]:
        paths = self.paths_by_language.get(language)
        if paths:
            return paths[0]
        # fallback to any available path
        for values in self.paths_by_language.values():
            if values:
                return values[0]
        return None


@dataclass
class HeadingMeta:
    level: int
    text: str
    anchor: str


class DocsetClient:
    def __init__(self, docset_root: Path, language: str = "swift") -> None:
        self.docset_root = docset_root
        self.language = language
        documents_root = self.docset_root / "Contents" / "Resources" / "Documents"
        if not documents_root.exists():
            raise FileNotFoundError(f"Docset documents directory not found: {documents_root}")
        self.documents_root = documents_root
        self.fs_root = documents_root / "fs"
        cache_db_path = documents_root / "cache.db"
        self.cache_conn = sqlite3.connect(cache_db_path)
        self.cache_conn.row_factory = sqlite3.Row
        self.chunk_cache: Dict[int, bytes] = {}
        self.path_index: Dict[str, str] = {}
        self.docset_version = self._read_docset_version()
        self.cache_dir = Path(os.environ.get("DOCSET_CACHE_DIR", "~/.cache/apple-docs")).expanduser()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._documentation_roots: Optional[List[str]] = None

    def close(self) -> None:
        self.cache_conn.close()

    # -------------------- public commands --------------------

    def _normalize_prefix(self, root_path: str) -> str:
        prefix = (root_path or "/").strip()
        if not prefix.startswith("/"):
            prefix = "/" + prefix
        prefix = prefix.rstrip("/")
        if not prefix:
            prefix = "/"
        return prefix

    def generate_markdown(
        self, root_path: str, max_depth: Optional[int] = None
    ) -> List[str]:
        prefix = self._normalize_prefix(root_path)
        print(f"[docset] Indexing prefix {prefix!r}", file=sys.stderr)
        entries = self._index_prefix(prefix)
        if not entries:
            raise RuntimeError(f"No documents found for prefix {prefix!r}")

        root_entry = self._find_root(entries, prefix)
        if not root_entry:
            raise RuntimeError(f"Root document for {prefix!r} not found in indexed entries.")

        print(f"[docset] Traversing graph starting at {root_entry.identifier}", file=sys.stderr)
        traversal = self._traverse(entries, root_entry.identifier, prefix, max_depth=max_depth)
        print(f"[docset] Rendering {len(traversal)} documents", file=sys.stderr)
        markdown = self._render_documents(traversal, prefix)
        markdown = self._apply_front_matter(markdown, traversal)
        return markdown

    def export_framework(
        self,
        root_path: str,
        output_path: Path,
        max_depth: Optional[int] = None,
    ) -> None:
        markdown = self.generate_markdown(root_path=root_path, max_depth=max_depth)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("\n".join(markdown), encoding="utf-8")
        print(f"[docset] Wrote {output_path}", file=sys.stderr)

    def prebuild_manifest(self, root_path: str) -> None:
        prefix = self._normalize_prefix(root_path)
        print(f"[docset] Prebuilding manifest for {prefix}", file=sys.stderr)
        entries = self._index_prefix(prefix)
        if not entries:
            print(f"[docset] Warning: no entries indexed for {prefix}", file=sys.stderr)
            return
        print(
            f"[docset] Manifest ready for {prefix} (entries: {len(entries)})",
            file=sys.stderr,
        )

    def list_documentation_roots(self) -> List[str]:
        if self._documentation_roots is not None:
            return self._documentation_roots

        idx_path = self.docset_root / "Contents" / "Resources" / "docSet.dsidx"
        modules: List[str] = []
        if idx_path.exists():
            conn = sqlite3.connect(idx_path)
            conn.row_factory = sqlite3.Row
            query = """
                SELECT DISTINCT
                       substr(rk,
                              instr(rk, 'documentation/') + length('documentation/'),
                              instr(substr(rk || '/', instr(rk, 'documentation/') + length('documentation/')), '/') - 1) AS module
                FROM (
                    SELECT CASE WHEN instr(req, '#') > 0 THEN substr(req, 1, instr(req, '#') - 1)
                                ELSE req
                           END AS rk
                    FROM (
                        SELECT substr(path, instr(path, 'request_key=') + length('request_key=')) AS req
                        FROM searchIndex
                        WHERE path LIKE 'dash-apple-api://load?request_key=ls/documentation/%'
                    ) s
                ) t
                WHERE module IS NOT NULL AND module <> ''
            """
            rows = conn.execute(query).fetchall()
            modules = sorted({row["module"] for row in rows if row["module"]})
            conn.close()
        self._documentation_roots = [f"/documentation/{module}" for module in modules]
        return self._documentation_roots

    # -------------------- indexing helpers --------------------

    def _index_prefix(self, prefix: str) -> Dict[str, DocumentEntry]:
        cached = self._load_manifest(prefix)
        if cached is not None:
            print(f"[docset] Loaded cached manifest for {prefix}", file=sys.stderr)
            return cached

        entries: Dict[str, DocumentEntry] = {}
        self.path_index = {}
        chunk: bytes = b""
        current_id: Optional[int] = None
        cursor = self.cache_conn.execute(
            "SELECT data_id, uuid, offset, length FROM refs ORDER BY data_id"
        )
        processed = 0
        for data_id, uuid, offset, length in cursor:
            if data_id != current_id:
                chunk = self._read_chunk(data_id)
                current_id = data_id
            raw = chunk[offset : offset + length]
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                continue
            identifier = doc.get("identifier", {}).get("url")
            if not identifier:
                continue
            paths_by_language: Dict[str, List[str]] = {}
            include = False
            for variant in doc.get("variants", []):
                langs = {
                    trait.get("interfaceLanguage")
                    for trait in variant.get("traits", [])
                    if trait.get("interfaceLanguage")
                }
                paths = [p for p in variant.get("paths", []) if isinstance(p, str)]
                for lang in langs:
                    paths_by_language.setdefault(lang, []).extend(paths)
                if paths and any(
                    (lang == self.language or lang is None) and path.startswith(prefix)
                    for lang in (langs or {None})
                    for path in paths
                ):
                    include = True
            if include:
                entries[identifier] = DocumentEntry(
                    identifier=identifier,
                    uuid=uuid,
                    data_id=data_id,
                    offset=offset,
                    length=length,
                    doc=doc,
                    paths_by_language=paths_by_language,
                )
                preferred_paths = paths_by_language.get(self.language) or []
                for path in preferred_paths:
                    self.path_index[path] = identifier
                if not preferred_paths:
                    for path_list in paths_by_language.values():
                        for path in path_list:
                            self.path_index.setdefault(path, identifier)
            processed += 1
            if processed % 50000 == 0:
                print(f"  indexed {processed} records...", file=sys.stderr)
        self._save_manifest(prefix, entries)
        return entries

    def _find_root(self, entries: Dict[str, DocumentEntry], prefix: str) -> Optional[DocumentEntry]:
        for entry in entries.values():
            path = entry.primary_path(self.language)
            if path == prefix:
                return entry
        return None

    def _manifest_path(self, prefix: str) -> Path:
        slug = self._slugify(prefix or "root")
        filename = f"{self.docset_version}_{slug}.json"
        return self.cache_dir / filename

    def _load_manifest(self, prefix: str) -> Optional[Dict[str, DocumentEntry]]:
        prefix = prefix or "/"
        prefixes_to_try: List[str] = [prefix]
        if prefix.startswith("/"):
            segments = [segment for segment in prefix.strip("/").split("/") if segment]
            for i in range(len(segments) - 1, 1, -1):
                candidate = "/" + "/".join(segments[:i])
                if candidate not in prefixes_to_try:
                    prefixes_to_try.append(candidate)
        for candidate in prefixes_to_try:
            path = self._manifest_path(candidate)
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if data.get("docset_version") != self.docset_version:
                continue
            entries_map: Dict[str, DocumentEntry] = {}
            for item in data.get("entries", []):
                entry = DocumentEntry(
                    identifier=item["identifier"],
                    uuid=item["uuid"],
                    data_id=item["data_id"],
                    offset=item["offset"],
                    length=item["length"],
                    doc=None,
                    paths_by_language=item.get("paths_by_language", {}),
                )
                entries_map[entry.identifier] = entry

            if candidate != prefix:
                filtered: Dict[str, DocumentEntry] = {}
                for entry in entries_map.values():
                    primary = entry.primary_path(self.language) or entry.primary_path()
                    if primary and primary.startswith(prefix):
                        filtered[entry.identifier] = entry
                if not filtered:
                    continue
                entries_map = filtered
                self._save_manifest(prefix, entries_map)
                print(
                    f"[docset] Loaded cached manifest for {candidate} (filtered for {prefix})",
                    file=sys.stderr,
                )
            else:
                print(f"[docset] Loaded cached manifest for {candidate}", file=sys.stderr)

            self.path_index = {}
            for entry in entries_map.values():
                for lang, paths in entry.paths_by_language.items():
                    if self.language == lang:
                        for path_value in paths:
                            self.path_index[path_value] = entry.identifier
                if self.language not in entry.paths_by_language:
                    for paths in entry.paths_by_language.values():
                        for path_value in paths:
                            self.path_index.setdefault(path_value, entry.identifier)
            return entries_map
        return None

    def _save_manifest(self, prefix: str, entries: Dict[str, DocumentEntry]) -> None:
        path = self._manifest_path(prefix)
        serializable = {
            "docset_version": self.docset_version,
            "entries": [],
        }
        for entry in entries.values():
            serializable["entries"].append(
                {
                    "identifier": entry.identifier,
                    "uuid": entry.uuid,
                    "data_id": entry.data_id,
                    "offset": entry.offset,
                    "length": entry.length,
                    "paths_by_language": entry.paths_by_language,
                }
            )
        try:
            path.write_text(json.dumps(serializable), encoding="utf-8")
        except Exception as exc:
            print(f"[docset] Warning: failed to write manifest {path}: {exc}", file=sys.stderr)

    def _traverse(
        self,
        entries: Dict[str, DocumentEntry],
        root_identifier: str,
        prefix: str,
        max_depth: Optional[int] = None,
    ) -> List[Tuple[DocumentEntry, int]]:
        visited: Dict[str, int] = {}
        order: List[Tuple[DocumentEntry, int]] = []
        queue: deque[Tuple[str, int]] = deque([(root_identifier, 1)])
        while queue:
            identifier, depth = queue.popleft()
            if identifier in visited and visited[identifier] <= depth:
                continue
            entry = entries.get(identifier)
            if not entry:
                continue
            doc = self._get_entry_doc(entry)
            visited[identifier] = depth
            order.append((entry, depth))
            next_depth = depth + 1
            if max_depth is not None and depth >= max_depth:
                continue
            references = doc.get("references", {})
            for section in doc.get("topicSections", []):
                for child_id in section.get("identifiers", []):
                    ref = references.get(child_id)
                    url = ref.get("url") if ref else ""
                    target_identifier = None
                    if url:
                        target_identifier = self.path_index.get(url)
                    if not target_identifier and child_id in entries:
                        target_identifier = child_id
                    if not target_identifier:
                        continue
                    target_entry = entries[target_identifier]
                    target_path = target_entry.primary_path(self.language) or url or ""
                    if target_path and not target_path.startswith(prefix):
                        continue
                    if target_identifier in visited and visited[target_identifier] <= next_depth:
                        continue
                    queue.append((target_identifier, next_depth))
        return order

    def _render_documents(
        self, traversal: Sequence[Tuple[DocumentEntry, int]], prefix: str
    ) -> List[str]:
        lines: List[str] = []
        lines.append(f"<!-- Generated from {self.docset_root} (version {self.docset_version}) -->")
        entry_lookup = {entry.identifier: entry for entry, _ in traversal}
        for entry, depth in traversal:
            doc = self._get_entry_doc(entry)
            metadata = doc.get("metadata", {})
            title = metadata.get("title") or entry.identifier
            path = entry.primary_path(self.language) or entry.primary_path() or ""
            slug = self._slugify(path or title)
            heading_level = min(depth, 6)
            heading = "#" * heading_level
            lines.append("")
            lines.append(f'<a id="{slug}"></a>')
            lines.append(f"{heading} {title}")
            role = metadata.get("roleHeading") or doc.get("kind", "")
            if role:
                lines.append(f"*Role:* {role}")
            if path:
                lines.append(f"*Path:* `{path}`")
            lines.append(f"*Identifier:* `{entry.identifier}`")
            platforms = metadata.get("platforms") or []
            if platforms:
                platform_bits = []
                for platform in platforms:
                    name = platform.get("name")
                    intro = platform.get("introducedAt")
                    if name:
                        bit = name
                        if intro:
                            bit += f" {intro}"
                        platform_bits.append(bit)
                if platform_bits:
                    lines.append(f"*Platforms:* {', '.join(platform_bits)}")
            abstract = doc.get("abstract")
            if abstract:
                lines.append("")
                lines.append("**Overview**")
                lines.extend(self._render_text_blocks(abstract, doc))
            primary_sections = doc.get("primaryContentSections", [])
            for section in primary_sections:
                kind = section.get("kind", "")
                if kind == "declarations":
                    lines.append("")
                    lines.append("**Declaration**")
                    for decl in section.get("declarations", []):
                        tokens = decl.get("tokens", [])
                        lines.extend(self._render_declaration(tokens, doc))
                elif kind == "parameters":
                    lines.append("")
                    lines.append("**Parameters**")
                    for param in section.get("parameters", []):
                        name = param.get("name", "")
                        content_lines = self._render_content_list(param.get("content", []), doc)
                        if content_lines:
                            lines.append(f"- `{name}` — {content_lines[0]}")
                            for extra in content_lines[1:]:
                                lines.append(f"  {extra}")
                        else:
                            lines.append(f"- `{name}`")
                elif kind == "returns":
                    lines.append("")
                    lines.append("**Return Value**")
                    lines.extend(self._render_content_list(section.get("content", []), doc))
                elif kind == "content":
                    lines.append("")
                    lines.extend(self._render_content_list(section.get("content", []), doc))
                elif kind == "discussion":
                    lines.append("")
                    lines.append("**Discussion**")
                    lines.extend(self._render_content_list(section.get("content", []), doc))
                else:
                    # fallback
                    content = section.get("content")
                    if content:
                        lines.append("")
                        lines.extend(self._render_content_list(content, doc))
            topic_sections = doc.get("topicSections", [])
            if topic_sections:
                lines.append("")
                lines.append("**Topics**")
                references = doc.get("references", {})
                for topic in topic_sections:
                    title = topic.get("title")
                    if title:
                        lines.append(f"- {title}")
                    for ident in topic.get("identifiers", []):
                        ref = references.get(ident)
                        if not ref:
                            continue
                        url = ref.get("url", "")
                        name = ref.get("title") or ref.get("name") or ident
                        target_identifier = self.path_index.get(url) if url else None
                        target_entry = entry_lookup.get(target_identifier) if target_identifier else None
                        target_path = (
                            target_entry.primary_path(self.language) if target_entry else url or name
                        )
                        slug_child = self._slugify(target_path)
                        display = name
                        if url:
                            display = f"{name} (path: {url})"
                        lines.append(f"  - {display}")
        return lines

    def _collect_headings(self, lines: Sequence[str]) -> List[HeadingMeta]:
        headings: List[HeadingMeta] = []
        anchor_pattern = re.compile(r'<a id="([^"]+)"></a>')
        pending_anchor: Optional[str] = None
        for line in lines:
            match = anchor_pattern.match(line.strip())
            if match:
                pending_anchor = match.group(1)
                continue
            stripped = line.lstrip()
            if not stripped.startswith("#"):
                continue
            hash_count = len(stripped) - len(stripped.lstrip("#"))
            text = stripped[hash_count:].strip()
            if not text:
                continue
            anchor = pending_anchor or self._slugify(text)
            headings.append(HeadingMeta(level=min(hash_count, 6), text=text, anchor=anchor))
            pending_anchor = None
        return headings

    def _build_toc_lines(self, headings: Sequence[HeadingMeta]) -> List[str]:
        filtered = [h for h in headings if h.text and h.text.lower() != "table of contents"]
        if not filtered:
            return []
        lines = ["## Table of Contents"]
        for heading in filtered:
            if heading.level > 3:
                continue
            indent = "  " * max(heading.level - 1, 0)
            anchor = heading.anchor or self._slugify(heading.text)
            lines.append(f"{indent}- [{heading.text}](#{anchor})")
        return lines

    def _apply_front_matter(
        self,
        lines: List[str],
        traversal: Sequence[Tuple[DocumentEntry, int]],
    ) -> List[str]:
        if not lines:
            return lines
        body = "\n".join(lines)
        body_size = len(body.encode("utf-8"))
        headings = self._collect_headings(lines)
        title = next((h.text for h in headings if h.level == 1), traversal[0][0].identifier if traversal else "Documentation")
        key_sections = [h.text for h in headings if h.level == 2][:12]
        if not key_sections:
            key_sections = [h.text for h in headings[:12]]
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        fm_lines: List[str] = [
            "---",
            f"title: {title}",
            f"docset_version: {self.docset_version}",
            f"exported_at: {timestamp}",
            f"doc_count: {len(traversal)}",
            f"file_size: {body_size}",
        ]
        if key_sections:
            fm_lines.append("key_sections:")
            for section in key_sections:
                fm_lines.append(f"  - {section}")
        else:
            fm_lines.append("key_sections: []")
        fm_lines.append("---")
        toc_lines = self._build_toc_lines(headings)
        result: List[str] = []
        result.extend(fm_lines)
        result.append("")
        if toc_lines:
            result.extend(toc_lines)
            result.append("")
        result.extend(lines)
        return result

    # -------------------- rendering helpers --------------------

    def _render_declaration(self, tokens: List[dict], doc: dict) -> List[str]:
        parts: List[str] = []
        for token in tokens:
            text = token.get("text") or ""
            if token.get("kind") == "typeIdentifier":
                parts.append(f"{text}")
            else:
                parts.append(text)
        code = "".join(parts)
        return ["```swift", code, "```"]

    def _render_text_blocks(self, blocks: Iterable[dict], doc: dict) -> List[str]:
        lines: List[str] = []
        for block in blocks:
            block_type = block.get("type")
            if block_type == "text":
                lines.append(block.get("text", ""))
            elif block_type == "paragraph":
                lines.append(self._render_inline(block.get("inlineContent", []), doc))
        return lines

    def _render_content_list(self, items: Iterable[dict], doc: dict, indent: int = 0) -> List[str]:
        lines: List[str] = []
        for item in items:
            block_type = item.get("type")
            if block_type == "paragraph":
                text = self._render_inline(item.get("inlineContent", []), doc)
                if text:
                    lines.append(" " * indent + text)
            elif block_type == "heading":
                level = item.get("level", 3)
                heading = "#" * min(level + indent, 6)
                lines.append(f"{heading} {item.get('text', '')}")
            elif block_type == "codeListing":
                language = "swift"
                code = ""
                code_listing = item.get("codeListing")
                if isinstance(code_listing, dict):
                    language = code_listing.get("language", language)
                    code = code_listing.get("code", "")
                else:
                    code = item.get("code", "")
                if isinstance(code, list):
                    code_text = "\n".join(code)
                else:
                    code_text = str(code)
                lines.append(f"```{language}")
                lines.append(code_text)
                lines.append("```")
            elif block_type in ("orderedList", "unorderedList"):
                bullet = "1." if block_type == "orderedList" else "-"
                for idx, list_item in enumerate(item.get("items", []), start=1):
                    bullet_text = f"{idx}." if bullet == "1." else "-"
                    sub_content = self._render_content_list(list_item.get("content", []), doc, indent=indent)
                    if sub_content:
                        first = sub_content[0]
                        prefix = bullet_text if bullet == "1." else "-"
                        lines.append(f"{prefix} {first}")
                        for extra in sub_content[1:]:
                            lines.append(f"  {extra}")
            elif block_type == "aside":
                name = item.get("name", "Note")
                lines.append(f"> **{name}**")
                aside_lines = self._render_content_list(item.get("content", []), doc)
                for aside_line in aside_lines:
                    lines.append(f"> {aside_line}")
            elif block_type == "image":
                identifier = None
                inline = item.get("inlineContent")
                if inline:
                    identifier = inline[0].get("identifier")
                identifier = identifier or item.get("identifier")
                alt = ""
                url = ""
                ref = doc.get("references", {}).get(identifier) if identifier else None
                if ref:
                    alt = ref.get("title") or identifier or ""
                    variants = ref.get("variants") or []
                    for variant in variants:
                        url = variant.get("url") or url
                descriptor = alt or identifier or "unavailable image"
                if url:
                    lines.append(f"*Image: {descriptor} (resource: {url})*")
                else:
                    lines.append(f"*Image: {descriptor}*")
            else:
                # fallback for unhandled types
                text = item.get("text") or ""
                if text:
                    lines.append(text)
        return lines

    def _render_inline(self, items: Iterable[dict], doc: dict) -> str:
        parts: List[str] = []
        for item in items:
            item_type = item.get("type")
            if item_type == "text":
                parts.append(item.get("text", ""))
            elif item_type == "code":
                parts.append(f"`{item.get('code', '')}`")
            elif item_type == "reference":
                identifier = item.get("identifier")
                title = item.get("title") or ""
                ref = doc.get("references", {}).get(identifier) if identifier else None
                if ref:
                    title = title or ref.get("title") or ref.get("name") or identifier or ""
                    url = ref.get("url") or ""
                    if url:
                        parts.append(f"{title or identifier} (path: {url})")
                    else:
                        parts.append(title or identifier or "")
                else:
                    parts.append(title or (identifier or ""))
            elif item_type in {"emphasis", "strong"}:
                inner = self._render_inline(item.get("inlineContent", []), doc)
                if item_type == "emphasis":
                    parts.append(f"*{inner}*")
                else:
                    parts.append(f"**{inner}**")
            elif item_type == "image":
                identifier = item.get("identifier")
                ref = doc.get("references", {}).get(identifier) if identifier else None
                url = ref.get("url") if ref else ""
                alt = ref.get("title") if ref else identifier or ""
                descriptor = alt or identifier or "unavailable image"
                if url:
                    parts.append(f"*Image: {descriptor} (resource: {url})*")
                else:
                    parts.append(f"*Image: {descriptor}*")
            elif item_type == "symbolCode":
                parts.append(f"`{item.get('code', '')}`")
            else:
                # nested inline content
                inner = item.get("inlineContent")
                if inner:
                    parts.append(self._render_inline(inner, doc))
                else:
                    text = item.get("text")
                    if text:
                        parts.append(text)
        return "".join(parts)

    # -------------------- utilities --------------------

    def _read_chunk(self, data_id: int) -> bytes:
        if data_id in self.chunk_cache:
            return self.chunk_cache[data_id]
        fs_path = self.fs_root / str(data_id)
        if not fs_path.exists():
            self.chunk_cache[data_id] = b""
            return b""
        result = subprocess.run(
            ["brotli", "-d", "-c", str(fs_path)],
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            self.chunk_cache[data_id] = b""
        else:
            self.chunk_cache[data_id] = result.stdout
        return self.chunk_cache[data_id]

    def _read_docset_version(self) -> str:
        version_plist = self.docset_root / "Contents" / "Resources" / "Documents" / "version.plist"
        if not version_plist.exists():
            return "unknown"
        try:
            import plistlib

            with version_plist.open("rb") as fh:
                data = plistlib.load(fh)
            return str(data.get("CFBundleVersion", "unknown"))
        except Exception:
            return "unknown"

    @staticmethod
    def _slugify(text: str) -> str:
        keep = []
        for char in text:
            if char.isalnum():
                keep.append(char.lower())
            elif char in "/-_":
                keep.append("-")
        slug = "".join(keep)
        while "--" in slug:
            slug = slug.replace("--", "-")
        return slug.strip("-") or "section"

    def _get_entry_doc(self, entry: DocumentEntry) -> dict:
        if entry.doc is None:
            chunk = self._read_chunk(entry.data_id)
            raw = chunk[entry.offset : entry.offset + entry.length]
            entry.doc = json.loads(raw)
        return entry.doc


def resolve_docset_path(path: Optional[str]) -> Path:
    if path:
        return Path(path).expanduser()
    env = os.environ.get("DOCSET_ROOT")
    if env:
        return Path(env).expanduser()
    return DEFAULT_DOCSET


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apple docset querying tool")
    parser.add_argument(
        "--docset",
        help="Path to the Apple API Reference docset (defaults to Dash location or $DOCSET_ROOT).",
    )
    parser.add_argument(
        "--language",
        default="swift",
        help="Preferred language variant (default: swift).",
    )
    subparsers = parser.add_subparsers(dest="command")

    export_parser = subparsers.add_parser("export", help="Export a framework/topic tree to Markdown.")
    export_parser.add_argument("--root", required=True, help="Root doc path (e.g. /documentation/vision).")
    export_parser.add_argument("--output", required=True, help="Destination Markdown file.")
    export_parser.add_argument(
        "--max-depth",
        type=int,
        default=7,
        help="Optional maximum depth for traversal (root depth = 1).",
    )

    fetch_parser = subparsers.add_parser(
        "fetch", help="Render a single symbol/topic to Markdown."
    )
    fetch_parser.add_argument(
        "--path",
        required=True,
        help="DocC path (e.g. /documentation/vision/vnimagerequesthandler/init(cvpixelbuffer:orientation:options:)).",
    )
    fetch_parser.add_argument(
        "--output",
        help="Destination file. If omitted, writes to stdout.",
    )
    fetch_parser.add_argument(
        "--max-depth",
        type=int,
        default=1,
        help="Traversal depth (default: 1 for the target document only).",
    )

    init_parser = subparsers.add_parser(
        "init",
        help="Prebuild manifests for one or more root documentation paths (defaults to all).",
    )
    init_parser.add_argument(
        "paths",
        nargs="*",
        help="One or more root paths (e.g. /documentation/vision /documentation/corevideo).",
    )
    init_parser.add_argument(
        "--all",
        action="store_true",
        help="Prebuild manifests for every top-level /documentation/<module>. (default)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    docset_path = resolve_docset_path(args.docset)
    client = DocsetClient(docset_path, language=args.language)
    try:
        if args.command == "export":
            output_path = Path(args.output)
            client.export_framework(
                root_path=args.root,
                output_path=output_path,
                max_depth=args.max_depth,
            )
        elif args.command == "fetch":
            markdown = client.generate_markdown(
                root_path=args.path,
                max_depth=args.max_depth,
            )
            if args.output:
                output_path = Path(args.output)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text("\n".join(markdown), encoding="utf-8")
                print(f"[docset] Wrote {output_path}", file=sys.stderr)
            else:
                sys.stdout.write("\n".join(markdown))
        elif args.command == "init":
            paths: List[str]
            use_all = args.all or not args.paths
            if use_all:
                roots = client.list_documentation_roots()
                if not roots:
                    parser.error("Unable to enumerate documentation roots from docset.")
                paths = roots
                if not args.all:
                    print(
                        f"[docset] --all not specified; defaulting to {len(paths)} documentation roots",
                        file=sys.stderr,
                    )
            else:
                paths = args.paths
            total = len(paths)
            for index, path in enumerate(paths, start=1):
                if total > 1:
                    print(f"[docset] ({index}/{total})", file=sys.stderr)
                client.prebuild_manifest(path)
        else:
            parser.error(f"Unsupported command: {args.command}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
