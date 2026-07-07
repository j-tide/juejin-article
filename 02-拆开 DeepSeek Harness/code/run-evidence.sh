#!/usr/bin/env bash
set -euo pipefail

PIN=ddefc45fbc7f8e46dd73185e68295696d1297887
LAB_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ "$#" -ne 1 ]; then
  echo 'Usage: bash run-evidence.sh /absolute/path/to/deepseek-harness' >&2
  exit 2
fi
REPO_DIR="$(cd "$1" && pwd)"
if [ "$(git -C "$REPO_DIR" rev-parse HEAD)" != "$PIN" ]; then
  echo "Expected source commit $PIN; use a separate checkout at that revision." >&2
  exit 2
fi
PROBE="$REPO_DIR/packages/core/agent-loop/tests/column-evidence.spec.ts"
CREATED_PROBE=0
cleanup() {
  if [ "$CREATED_PROBE" -eq 1 ]; then rm -f "$PROBE"; fi
}
trap cleanup EXIT
if [ -e "$PROBE" ]; then
  if ! cmp -s "$PROBE" "$LAB_DIR/column-evidence.spec.ts"; then
    echo 'A different column-evidence.spec.ts already exists; refusing to overwrite it.' >&2
    exit 2
  fi
else
  cp "$LAB_DIR/column-evidence.spec.ts" "$PROBE"
  CREATED_PROBE=1
fi
RESULT_DIR="$(mktemp -d "${TMPDIR:-/tmp}/dsh-column-evidence.XXXXXX")"
TEST_FILES=()
while IFS= read -r test_file || [ -n "$test_file" ]; do
  [ -n "$test_file" ] && TEST_FILES+=("$test_file")
done < "$LAB_DIR/focused-tests.txt"
cd "$REPO_DIR"
# Install the pinned workspace without dependency lifecycle scripts.
# Only focused source-mode tests run; no GUI or application process is launched.
pnpm install --frozen-lockfile --ignore-scripts
pnpm exec vitest run --maxWorkers=4 "${TEST_FILES[@]}" \
  --reporter=default --reporter=json --outputFile.json="$RESULT_DIR/upstream-tests.json"
DSH_COLUMN_ASSETS_DIR="$LAB_DIR" DSH_COLUMN_EVIDENCE_DIR="$RESULT_DIR" \
  pnpm exec vitest run --maxWorkers=2 packages/core/agent-loop/tests/column-evidence.spec.ts \
  --reporter=default --reporter=json --outputFile.json="$RESULT_DIR/column-tests.json"
echo "Evidence written to $RESULT_DIR"
