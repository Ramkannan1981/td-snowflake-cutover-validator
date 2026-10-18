#!/usr/bin/env bash
# Verify the project from a clean checkout. Runs each step in order and stops
# at the first failure:
#   1. unit tests
#   2. every converted table's Snowflake SQL parses and has no Teradata leftovers
#   3. the CLI on the sample data, which must return NO-GO (exit 1)
set -uo pipefail

cd "$(dirname "$0")/.."

if [[ -x .venv/bin/python ]]; then
    PY=.venv/bin/python
else
    PY=python3
fi

STEPS=("pytest" "snowflake-sql" "cli-sample-data")
declare -a RESULTS=()

summary() {
    echo
    echo "==== verify summary ===="
    local i
    for i in "${!STEPS[@]}"; do
        printf '  %-16s %s\n' "${STEPS[$i]}" "${RESULTS[$i]:-SKIPPED}"
    done
    echo "  overall          $1"
}

fail() {
    RESULTS+=("FAIL ($1)")
    summary FAIL
    exit 1
}

echo "==== [1/3] pytest ===="
"$PY" -m pytest -q || fail "pytest exit $?"
RESULTS+=("PASS")

echo
echo "==== [2/3] converted SQL is valid Snowflake ===="
"$PY" -m src.snowflake_check --config config/tables.json || fail "exit $?"
RESULTS+=("PASS")

echo
echo "==== [3/3] CLI on sample data (expect NO-GO, exit 1) ===="
"$PY" -m src.cli --config config/tables.json
code=$?
case $code in
    1) RESULTS+=("PASS (NO-GO as expected)") ;;
    0) fail "got GO; the sample data has planted issues and must be NO-GO" ;;
    *) fail "CLI exit $code" ;;
esac

summary PASS
