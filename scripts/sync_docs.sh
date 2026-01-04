#!/usr/bin/env bash
set -euo pipefail

# Sync Apple docs exports between a source and target directory.
# Default direction: PULL (SOURCE -> TARGET). Use push mode to mirror TARGET -> SOURCE.
# Defaults:
#   SOURCE = $DOCS_SOURCE if set, else (~/docs + <category>)
#   TARGET = $DOCS_TARGET or repo/docs/<category> (category defaults to target basename or $DOCS_CATEGORY)
#
# Examples:
#   # Refresh current repo cache from canonical home docs/apple (mirror, allow deletes)
#   scripts/sync_docs.sh pull --allow-delete
#   # Push current repo cache back to canonical (no deletions unless you mean it)
#   scripts/sync_docs.sh push
#   # Custom endpoints
#   scripts/sync_docs.sh --source ~/docs/apple --target ./docs/apple --allow-delete
#   # Choose a different category under your docs root (when DOCS_SOURCE is unset)
#   scripts/sync_docs.sh pull --category apple --target ./docs/apple
#   # Initialize DOCS_SOURCE in your shell rc (defaults to ~/docs/<category> if unset)
#   scripts/sync_docs.sh --init [--source PATH | --category NAME]

print_help() {
  cat <<'EOF'
sync_docs.sh - sync Apple docs between a source and target directory
Default direction: pull (SOURCE -> TARGET). Use push for TARGET -> SOURCE.

Usage:
  scripts/sync_docs.sh [pull|push] [--allow-delete] [--dry-run]
                       [--source PATH] [--target PATH] [--category NAME]
  scripts/sync_docs.sh --init [--source PATH | --category NAME]

Defaults:
  SOURCE = $DOCS_SOURCE if set, else (~/docs + <category>)
  TARGET = $DOCS_TARGET or repo/docs/apple
  CATEGORY = basename of TARGET (e.g., apple)

Common flows:
  # pull canonical -> repo cache (mirror; deletes allowed)
  DOCS_SOURCE=~/docs/apple scripts/sync_docs.sh pull --allow-delete
  # push repo cache -> canonical (no deletes unless you mean it)
  DOCS_SOURCE=~/docs/apple scripts/sync_docs.sh push

Flags:
  pull|--pull     pull SOURCE -> TARGET (default)
  push|--push     push TARGET -> SOURCE
  --allow-delete  overwrite/mirror (delete files in destination that aren't in source)
  --dry-run       simulate only; show what would change without applying
  --source PATH   override source dir (absolute; takes precedence over env/category)
  --target PATH   override target dir
  --category NAME     leaf under docs root (default: $DOCS_CATEGORY, else basename of target; ignored if DOCS_SOURCE is set)
  --init          write DOCS_SOURCE export to ~/.zshrc (or $ZDOTDIR/.zshrc)
  --force         skip non-root confirmation
  -h, --help      show this help
EOF
}

MODE="pull"
ALLOW_DELETE=0
DRY_RUN=0
INIT_ONLY=0
SOURCE_OVERRIDE=""
TARGET_OVERRIDE=""
CATEGORY_OVERRIDE=""
FORCE=0

# Positional mode (pull|push) for clarity
if [[ $# -gt 0 && "$1" != -* ]]; then
  case "$1" in
    pull) MODE="pull" ;;
    push) MODE="push" ;;
    *)
      echo "[sync_docs] Unknown mode: $1 (use pull or push)" >&2
      print_help
      exit 1
      ;;
  esac
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pull) MODE="pull" ;;
    --push) MODE="push" ;;
    --allow-delete) ALLOW_DELETE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --init) INIT_ONLY=1 ;;
    --force) FORCE=1 ;;
    --source)
      shift
      SOURCE_OVERRIDE="${1-}"
      ;;
    --target)
      shift
      TARGET_OVERRIDE="${1-}"
      ;;
    --category)
      shift
      CATEGORY_OVERRIDE="${1-}"
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      echo "[sync_docs] Unknown flag: $1" >&2
      print_help
      exit 1
      ;;
  esac
  shift || true
done

WORKDIR="$(pwd)"
SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_ROOT}/.." && pwd)"
TARGET="${TARGET_OVERRIDE:-${DOCS_TARGET:-${REPO_ROOT}/docs/apple}}"
TARGET_BASENAME="$(basename "$TARGET")"
DEFAULT_CATEGORY="${DOCS_CATEGORY:-$TARGET_BASENAME}"
CATEGORY="${CATEGORY_OVERRIDE:-$DEFAULT_CATEGORY}"
if [[ -n "${DOCS_SOURCE:-}" ]]; then
  SOURCE_BASE="${DOCS_SOURCE}"
else
  SOURCE_BASE="$HOME/docs"
fi
SOURCE_DEFAULT="${SOURCE_BASE%/}/${CATEGORY}"
SOURCE="${SOURCE_OVERRIDE:-$SOURCE_DEFAULT}"

if [[ $INIT_ONLY -eq 1 ]]; then
  RC_PATH="${ZDOTDIR:-$HOME}/.zshrc"
  CANONICAL_SOURCE="$SOURCE"
  if [[ "$SOURCE" == "$HOME"/* ]]; then
    CANONICAL_SOURCE="\$HOME/${SOURCE#$HOME/}"
  fi
  CANONICAL_CATEGORY="${CATEGORY}"
  mkdir -p "$(dirname "$RC_PATH")"
  if [[ -f "$RC_PATH" ]] && grep -q '^export DOCS_SOURCE=' "$RC_PATH"; then
    CURRENT=$(grep -m1 '^export DOCS_SOURCE=' "$RC_PATH" | sed 's/^export DOCS_SOURCE=//')
    if [[ "$CURRENT" == "\"$CANONICAL_SOURCE\"" || "$CURRENT" == "$CANONICAL_SOURCE" ]]; then
      echo "[sync_docs] DOCS_SOURCE already set in $RC_PATH; leaving unchanged"
    else
      tmpfile="$(mktemp)"
      sed "s|^export DOCS_SOURCE=.*$|export DOCS_SOURCE=\"$CANONICAL_SOURCE\"|" "$RC_PATH" > "$tmpfile"
      mv "$tmpfile" "$RC_PATH"
      echo "[sync_docs] Updated DOCS_SOURCE in $RC_PATH"
    fi
  else
    echo "export DOCS_SOURCE=\"$CANONICAL_SOURCE\"" >> "$RC_PATH"
    echo "[sync_docs] Added DOCS_SOURCE to $RC_PATH"
  fi
  if [[ -f "$RC_PATH" ]] && grep -q '^export DOCS_CATEGORY=' "$RC_PATH"; then
    CURRENT_CAT=$(grep -m1 '^export DOCS_CATEGORY=' "$RC_PATH" | sed 's/^export DOCS_CATEGORY=//')
    if [[ "$CURRENT_CAT" == "\"$CANONICAL_CATEGORY\"" || "$CURRENT_CAT" == "$CANONICAL_CATEGORY" ]]; then
      echo "[sync_docs] DOCS_CATEGORY already set in $RC_PATH; leaving unchanged"
    else
      tmpfile="$(mktemp)"
      sed "s|^export DOCS_CATEGORY=.*$|export DOCS_CATEGORY=\"$CANONICAL_CATEGORY\"|" "$RC_PATH" > "$tmpfile"
      mv "$tmpfile" "$RC_PATH"
      echo "[sync_docs] Updated DOCS_CATEGORY in $RC_PATH"
    fi
  else
    echo "export DOCS_CATEGORY=\"$CANONICAL_CATEGORY\"" >> "$RC_PATH"
    echo "[sync_docs] Added DOCS_CATEGORY to $RC_PATH"
  fi
  echo "[sync_docs] Current DOCS_SOURCE target: $SOURCE"
  echo "[sync_docs] Current DOCS_CATEGORY: $CATEGORY"
  exit 0
fi

if [[ ! -d "$SOURCE" ]]; then
  echo "[sync_docs] Source directory missing: $SOURCE" >&2
  echo "[sync_docs] Set DOCS_SOURCE or pass --source to point at your exports (e.g. ~/docs/apple)" >&2
  print_help
  exit 1
fi

mkdir -p "$TARGET"

# Only warn/fail when repo root is unknown or target is outside the repo
if [[ -z "$REPO_ROOT" ]] || [[ "$TARGET" != "$REPO_ROOT"/docs/* ]]; then
  echo "[sync_docs] Warning: cannot infer a normal repo docs target (repo_root=$REPO_ROOT, target=$TARGET)" >&2
  if [[ $FORCE -ne 1 ]]; then
    if [[ ! -t 0 ]]; then
      echo "[sync_docs] Non-interactive session; rerun with --force or set --target explicitly" >&2
      exit 1
    fi
    read -r -p "[sync_docs] Continue? [y/N]: " RESP
    case "$RESP" in
      y|Y|yes|YES) ;;
      *) echo "[sync_docs] Aborting."; exit 1 ;;
    esac
  fi
fi
if [[ ! -d "$TARGET" ]]; then
  echo "[sync_docs] Note: target directory did not exist; created $TARGET" >&2
fi

RSYNC_FLAGS=(-aiv --exclude ".git/" --exclude ".DS_Store")
if [[ $ALLOW_DELETE -eq 1 ]]; then
  RSYNC_FLAGS+=("--delete")
fi
if [[ $DRY_RUN -eq 1 ]]; then
  RSYNC_FLAGS+=("-n")
fi

if [[ "$MODE" == "pull" ]]; then
  echo "[sync_docs] Pulling (source -> target):"
else
  echo "[sync_docs] Pushing (target -> source):"
  if [[ $ALLOW_DELETE -ne 1 ]]; then
    echo "(No deletions on push unless --allow-delete is set)"
  fi
fi
printf "    source: %s\n" "$SOURCE"
printf "    target: %s\n" "$TARGET"

if [[ "$MODE" == "pull" ]]; then
  rsync "${RSYNC_FLAGS[@]}" "$SOURCE"/ "$TARGET"/
else
  rsync "${RSYNC_FLAGS[@]}" "$TARGET"/ "$SOURCE"/
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[sync_docs] DRY RUN (no changes were made)"
fi
