# DocSetQuery

Local-first Apple documentation extraction, cleanup, and search. Built for fast developer lookup and agent workflows that need deterministic, citeable Markdown instead of scraping the web.

This repo is a working toolkit and a work in progress. Not all planned features are fully implemented yet. It assumes you already have the Apple API Reference docset (Dash docset) on disk and a `brotli` CLI available.

## Why this exists
- Apple docs are large and dynamic; agents need stable, local references.
- DocC exports are noisy; we need predictable front matter and trimmed tables of contents.
- Local search should be instant, without re-reading docsets for every query.

## What you get
- `tools/docset_query.py` — exports DocC content from the Apple docset to Markdown.
- `tools/docset_sanitize.py` — rebuilds front matter + trims the TOC for cleaner context.
- `tools/docindex.py` — builds a local JSON index for fast search by heading/key sections.
- `tools/docmeta.py` — peeks front matter/TOC quickly for debugging.
- `scripts/sync_docs.sh` — syncs a canonical docs cache into `docs/apple` (repo cache is gitignored).

## Quickstart (local)
```bash
# Export a framework/topic tree to Markdown
python tools/docset_query.py export \
  --root /documentation/vision \
  --output docs/apple/vision.md

# Sanitize the export (trim TOC, rebuild front matter)
python tools/docset_sanitize.py --input docs/apple/vision.md --in-place --toc-depth 2

# Build or refresh the search index
python tools/docindex.py rebuild

# Search headings/key sections
python tools/docindex.py search "CVPixelBuffer"
```

## Agent workflow (how we use it)
This is the flow we use in other repos that need grounded Apple citations:
1. **Search locally first.** Agents call `docindex.py search` against `docs/apple`.
2. **Fetch only what’s missing.** If the topic isn’t there, use `docset_query.py fetch` or `export`.
3. **Sanitize for stable context.** Run `docset_sanitize.py` to keep front matter and TOC consistent.
4. **Rebuild the index.** `docindex.py rebuild` keeps agent search fast and deterministic.
5. **Keep a canonical cache.** Sync with `scripts/sync_docs.sh` so `docs/apple` stays a lightweight, shareable cache without committing the full docset.

This approach lets agents answer questions with local, vetted Markdown and avoids hitting remote docs during runs.

## How it works
### Docset export (`tools/docset_query.py`)
- Reads the Dash Apple API Reference docset directly (SQLite + brotli chunks).
- Commands:
  - `export` — walk a documentation tree and emit a single Markdown file.
  - `fetch` — render a single symbol/topic (optionally to stdout).
  - `init` — prebuild manifests for faster traversal.
- Defaults:
  - Docset path: `~/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset`
  - Language: `swift`
  - Cache: `~/.cache/apple-docs`
  - `export` depth: 7, `fetch` depth: 1
- Overrides:
  - `--docset` or `DOCSET_ROOT` for alternate docsets
  - `--language` for alternate language variants
  - `DOCSET_CACHE_DIR` for cache location

### Sanitize exports (`tools/docset_sanitize.py`)
- Rebuilds front matter with a stable summary and key sections.
- Trims TOC depth and drops noisy stopwords (e.g. “discussion”, “parameters”).
- Keeps output deterministic so agent prompts stay consistent.

### Index and search (`tools/docindex.py`)
- Builds `Build/DocIndex/index.json` from Markdown in `docs/apple`.
- Indexes front matter, headings, and key sections.
- Search matches headings/key sections and returns anchored paths.

### Sync docs cache (`scripts/sync_docs.sh`)
- `docs/apple` is a cache-only directory and is `.gitignore`’d.
- Use the sync script to mirror a canonical docs folder into the repo cache:
  - Pull: `DOCS_SOURCE=~/docs/apple scripts/sync_docs.sh pull --allow-delete`
  - Push: `DOCS_SOURCE=~/docs/apple scripts/sync_docs.sh push`

## Notes
- This toolchain assumes a local Apple docset; it does not download docsets.
- Docsets come from Dash and Kapeli’s feeds:
  - Dash app + docsets: https://kapeli.com/dash
  - Docset feeds (download without the app): https://github.com/Kapeli/feeds
- The scripts are intentionally CLI-first so they can be scripted by agents.
- See `AGENTS_RULES.md` for the workflow guardrails we use internally.

## Status
Implemented now:
- Docset export (`tools/docset_query.py`): `export`, `fetch`, and `init`.
- Sanitizer (`tools/docset_sanitize.py`): front matter rebuild + TOC trimming.
- Index + search (`tools/docindex.py`): JSON index + heading/key-section search.
- Metadata peek (`tools/docmeta.py`): front matter/TOC inspection.
- Cache sync (`scripts/sync_docs.sh`): pull/push to a canonical docs folder.

Planned (not implemented):
- Automated docset download/updates from Kapeli feeds or other vendor sources.

## Docsets: how it works today
- A “docset” here means the Dash-compatible docset format on disk.
- Dash is the most common way to install and update docsets, but the feed repo also lets you download without the app.
- This toolkit only consumes a docset you already have and reads it locally; it does not fetch or manage docsets.

## Future (not implemented)
- Add a small helper that reads Kapeli’s feed metadata and downloads vendor docsets automatically.
- Cache and unpack docsets into a consistent local location so agents can bootstrap a repo quickly.
