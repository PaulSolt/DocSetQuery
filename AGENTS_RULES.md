# AGENTS Quick Rules (for Doc Toolkit Extraction)

- Atomic commits via `scripts/atomic_commit.sh`; no direct `git add/commit/reset`; no destructive git (no reset --hard, no revert unless instructed).
- Keep build/test green between commits; do not leave tree broken. Use Makefile targets where applicable.
- Do not delete or revert files you didn't author; coordinate before removing shared work.
- Doc workflow: search local exports first (`docindex search`), only fetch new pages via `docset_query.py` when local search misses; regenerate doc index after adding exports.
- Fail fast when required inputs are missing (docset path, markdown roots, attachments); do not add silent fallbacks.
- Inline comments referencing governed docs must use `// Agent: Required reading ...` format when code depends on blueprint guidance.
- Respect 500-line guardrail by extracting helpers instead of growing monolith files.
- Use `AGENT=<name>` for builds/logs (avoid `Paul`); long runs go in tmux when needed.
