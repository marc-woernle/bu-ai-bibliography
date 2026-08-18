#!/bin/sh
# Point git at the tracked hooks in .githooks/.
#
# core.hooksPath is a local setting, so it is NOT inherited by a fresh clone,
# a new machine, or a CI checkout. Run this once per clone. The CI guard in
# .github/workflows/no-ai-attribution.yml is the backstop for when nobody does.
set -e
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
echo "Hooks active: $(git config core.hooksPath)"
echo "  commit-msg  -> rejects Claude/Anthropic attribution trailers"
echo "  pre-commit  -> rejects commits authored by a Claude/Anthropic identity"
