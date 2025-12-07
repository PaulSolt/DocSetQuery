#!/usr/bin/env bash
# Atomic commit helper to prevent AI's from accidentally deleting
# changes from dangerous commands. I only allow AI agents to commit with this flow.
# I disallow AI agents from git reset, git checkout, and git revert because
# they are dangerous operations when multiple agents (or I) are editing
# files in the same work tree.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: scripts/atomic_commit.sh "commit message" <path> [<path> ...]

Runs the approved sequence:
  git reset
  git add -- <paths>
  git commit -m "commit message" -- <paths>
EOF
}

if [[ $# -lt 2 ]]; then
  usage
  exit 1
fi

commit_message=$1
shift

if [[ -z "${commit_message// }" ]]; then
  echo "Error: commit message must not be empty." >&2
  exit 1
fi

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "Error: scripts/atomic_commit.sh must be run inside a Git repository." >&2
  exit 1
}

cd "$repo_root"

files=("$@")

missing=()
pristine=()
for path in "${files[@]}"; do
  if [[ ! -e "$path" ]]; then
    if ! git ls-files --error-unmatch -- "$path" >/dev/null 2>&1; then
      missing+=("$path")
      continue
    fi
  fi

  status_output=$(git status --short -- "$path")
  if [[ -z "$status_output" ]]; then
    pristine+=("$path")
  fi
done

if [[ ${#missing[@]} -gt 0 ]]; then
  printf 'Error: the following paths do not exist or are not tracked:\n' >&2
  printf '  %s\n' "${missing[@]}" >&2
  exit 1
fi

if [[ ${#pristine[@]} -gt 0 ]]; then
  printf 'Error: no changes detected for:\n' >&2
  printf '  %s\n' "${pristine[@]}" >&2
  exit 1
fi

git reset
git add -- "${files[@]}"
git commit -m "$commit_message" -- "${files[@]}"
