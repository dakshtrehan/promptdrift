#!/usr/bin/env bash
# End to end offline example: compare two logged versions of a support
# assistant prompt without calling any model. Needs no API key.
#
#   bash examples/offline/run.sh            # run and write reports to examples/offline/out/
#   UPDATE_EXPECTED=1 bash examples/offline/run.sh   # also refresh expected_output.txt
#
# Exit code: 1 when a regression is found (and the data contains two on
# purpose), so this script is a drop-in CI step. The threshold flags
# --max-drift and --max-regressions arrive in promptdrift v0.3.0; the script
# adds them only when the installed CLI knows them, so it also works on 0.2.0.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${PROMPTDRIFT_OUT:-$HERE/out}"
mkdir -p "$OUT"

# Keys are blanked so nothing in this script can reach a paid API.
export OPENAI_API_KEY="" ANTHROPIC_API_KEY=""

THRESHOLDS=()
if promptdrift diff --help 2>/dev/null | grep -q -- "--max-drift"; then
  # Fail when average drift is above 30 percent or any check regressed (v0.3.0+).
  THRESHOLDS=(--max-drift 0.30 --max-regressions 0)
else
  THRESHOLDS=(--fail-on-regression)
fi

# 1. Terminal report (stripped of colour codes) and JSON report.
set +e
promptdrift diff "$HERE/outputs.jsonl" \
  --name-a v1 --name-b v2 \
  --checks "$HERE/checks.yaml" \
  --output "$OUT/report.json" \
  --html "$OUT/report.html" \
  --csv "$OUT/report.csv" \
  --junit "$OUT/junit.xml" \
  "${THRESHOLDS[@]}" \
  | sed -E 's/\x1b\[[0-9;]*m//g' | tee "$OUT/terminal.txt"
status=${PIPESTATUS[0]}
set -e

# 2. The same report as machine readable JSON (first lines shown). The file
# written by --output above holds exactly what --json prints, so it is read
# back here instead of piping a second run through head, which would close
# the pipe early and turn a clean exit into a broken pipe error.
head -n 12 "$OUT/report.json"

echo
echo "reports written to $OUT:"
ls -1 "$OUT"

if [ "${UPDATE_EXPECTED:-0}" = "1" ]; then
  # The "saved" lines carry absolute paths, so they are left out of the file.
  grep -v "saved" "$OUT/terminal.txt" > "$HERE/expected_output.txt"
  echo "refreshed $HERE/expected_output.txt"
fi

echo
echo "promptdrift exit code: $status (1 means a regression or threshold failure, as expected here)"
exit "$status"
