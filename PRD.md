# DocSetQuery — PRD
2025-12-07

## Goal
Create a standalone, reusable documentation toolkit with a clear CLI, packaging, and release story that works across projects. Primary path is Python (pip/pipx + signed binary), with an informed option to port to Swift later if needed.

## Scope (MVP)
- CLI subcommands:
  - Index/search local markdown exports (front matter + headings)
  - Export/fetch Apple docset content to markdown (front matter + TOC)
  - Sanitize exports (front matter + trimmed TOC)
- Distributions:
  - Python package with console entry points (pip/pipx install)
  - Signed single-file binary (PyInstaller/PyOxidizer) for macOS (no Python dependency)
- Preserve behaviors: local search first; fetch/export only when local search misses; deterministic output.

## Non-Goals (MVP)
- UI/GUI.
- Cloud sync or remote hosting.
- Runtime/plugin system beyond the packager.

## Users / Journeys
- Agent/dev: `docindex search <term>` on local exports; `docset_query export --root /documentation/vision --output docs/apple/vision.md`; `docset_sanitize --input … --in-place`.
- Consumer without Python: download signed binary (or Homebrew formula) and run the same commands.

## Requirements
- Deterministic outputs (front matter + TOC stable).
- Fail fast on missing docset/cache, unreadable paths, or invalid arguments.
- Respect repo-local docs root defaults while allowing overrides via flags/env.
- Versioned releases (SemVer) + changelog per release.
- Tests: unit coverage for indexing/search front matter parsing; smoke for export/sanitize.
- Preserve current CLI surfaces and defaults:
  - `docindex` with `rebuild/list/search`, defaults to `docs/apple` and `Build/DocIndex/index.json`.
  - `docset-query export/fetch/init` with docset defaults, export `--max-depth 7`, fetch `--max-depth 1`, manifest cache under `~/.cache/apple-docs`, and language default `swift`.
  - `docset-sanitize` with default `toc-depth 2`, stopwords set, and deterministic front matter rebuild.

## Deliverables
- Source tree with CLI entry points and packaging metadata.
- Release pipeline that builds wheel/sdist + PyInstaller/PyOxidizer binary, signs/notarizes macOS artifact, publishes checksums.
- README/usage docs + CONTRIBUTING (build/release steps) + examples below.

## Open Questions
- Binary builder choice (PyInstaller vs PyOxidizer) — default PyInstaller unless size/startup require change.
- Homebrew tap inclusion for macOS distribution?
- Minimum Python version target (suggest 3.10+).

## Success Metrics
- Install friction: pipx install works; binary runs without Python.
- Deterministic outputs (hash-stable for same inputs).
- Coverage: core parsing/index tests green in CI.

## Language Choice — Python vs Swift
- Python (recommended to start)
  - Pros: fast iteration, batteries included (sqlite, json), existing codebase reuse, easy pip/pipx distribution, quick CI. PyInstaller/PyOxidizer can emit signed binaries for users without Python.
  - Cons: bundle sizes larger; startup slower than native; binary still contains .py payload unless fully frozen; need to manage interpreter version.
- Swift (alternative)
  - Pros: native Mach-O out of the box; straightforward codesign/notarize; fast startup; aligns with macOS tooling.
  - Cons: macOS-centric (Linux possible, Windows rough); more rebuild overhead; fewer off-the-shelf parsers (need SPM deps for YAML, SQLite); porting effort (~1k LOC rewrite, largest lift is docset traversal/brotli/SQLite).
- Guidance: keep Python for now with signed binaries + pip/pipx. Revisit Swift if we need macOS-only native, smaller binaries, or want to avoid bundled interpreters.

## Tool Behaviors & Settings (current)
- `docindex.py`
  - Purpose: index and search local markdown exports in `docs/apple` (default).
  - Defaults: docs root `docs/apple`; index path `Build/DocIndex/index.json`.
  - Commands: `rebuild` (reindex), `list` (show indexed entries), `search <term>` (heading/key-section substring search).
  - Notes: requires front matter at top of each md; builds stable JSON index for fast local search (no need to touch docsets once exports exist).
  - Examples:
    - `docindex.py rebuild`
    - `docindex.py search CVPixelBuffer`
    - `docindex.py --docs-root ~/exports/apple --index ~/tmp/index.json search vision`
- `docset_query.py`
  - Purpose: read Apple Dash docset and export DocC content to markdown with front matter + TOC.
  - Defaults: docset `~/Library/Application Support/Dash/DocSets/Apple_API_Reference/Apple_API_Reference.docset`; language `swift`; cache `~/.cache/apple-docs`.
  - Commands:
    - `export --root /documentation/vision --output docs/apple/vision.md [--max-depth 7]`
    - `fetch --path /documentation/vision/vnimagerequesthandler/init(cvpixelbuffer:orientation:options:) --output docs/apple/vision-handler.md [--max-depth 1]`
    - `init [--all]` to prebuild manifests for doc roots.
  - Settings: `--docset` to override path; `--language` to pick variant; `--max-depth` controls traversal (export default 7, fetch default 1); uses brotli + sqlite to read chunks; writes manifests per docset version.
  - Behavior: fails if docset not found or no entries for prefix; caches manifests by docset version; filters by language.
- `docset_sanitize.py`
  - Purpose: rebuild front matter and trim TOC for large DocC markdown exports.
  - Defaults: `--toc-depth 2`; default stopwords set; `--in-place` or `--output` to write.
  - Behavior: derives `key_sections` if missing; drops stopword headings; keeps deterministic front matter (adds sanitizer metadata).
  - Example: `docset_sanitize.py --input docs/apple/vision.md --in-place --toc-depth 2 --stopword "special considerations"`
- `test_docindex.py`
  - Unit tests covering front matter parsing/index/search basics.
- `scripts/atomic_commit.sh`
  - Helper for atomic commits (no direct git add/commit); use `AGENT=<name> scripts/atomic_commit.sh "Agent Name: message" <files...>`.

## Distribution Plan (Python path)
- Packaging: `pyproject.toml` with `[project.scripts]` for `docindex`, `docset-query`, `docset-sanitize`.
- Install: `pipx install .` (or `pip install .`); recommend pipx for global command.
- Binary: PyInstaller (default) to emit single-file macOS binary; codesign/notarize; publish with SHA256 and optional Homebrew formula.
- CI: build wheel/sdist + binary per release tag; attach artifacts to GitHub Releases; update changelog.

## Change Log
- 2025-12-06: Expanded PRD (language tradeoffs, tool behaviors, distribution) for doc toolkit extraction.
- 2025-12-06 (draft): Initial extraction PRD for doc tooling (Agent request).
