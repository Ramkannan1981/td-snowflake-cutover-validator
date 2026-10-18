# Agentic workflow

**Tools:** Claude Code (Opus 5.5, auto permission mode) for implementation;
Claude chat for planning and reviewing agent output.

**Approach:** one step at a time, tests before code. The agent does not run
git; I run the tests myself, review the output, and make every commit.

## Prompt 1 – DDL conversion with safe abstain
Asked the agent to create 3 sample Teradata DDL files (MULTISET, SET, and an
unsupported PERIOD type), write pytest tests first, then implement the
converter with sqlglot, returning a typed result (CONVERTED /
CONVERTED_WITH_WARNING / NEEDS_REVIEW).

### My review / corrections
- The agent found that sqlglot's Teradata-to-Snowflake output passes
  Teradata-only syntax (PRIMARY INDEX, PERIOD, NOT CASESPECIFIC) through
  unchanged, so it looks converted but isn't valid Snowflake. I accepted its
  allowlist approach: only known-safe types and attributes are converted;
  everything else is NEEDS_REVIEW.
- Confirmed the tests check the output for Teradata-only keywords, since
  sqlglot's Snowflake parser alone also accepts PRIMARY INDEX.
- Kept NOT CASESPECIFIC as NEEDS_REVIEW, based on my migration experience:
  Teradata compares these columns case-insensitively and Snowflake doesn't by
  default, which causes silent mismatches in joins and filters.

## Prompt 2 – Synthetic dual-feed data and DuckDB adapter
Asked the agent to create synthetic source (Teradata export) and target
(Snowflake load) CSVs with planted migration issues in the target only, a
config file listing tables, and a DuckDB adapter behind an interface, with
tests first.

### My review / corrections
- Checked the planted issues are really in the target data by diffing source
  and target: duplicate row (SET table), case change, rounding difference
  (100.01 vs 100.00), NULL vs empty string. One table left fully matching.
- Confirmed the table list comes only from config/tables.json, so scaling to
  more tables means adding config, not changing code.
- Snowflake adapter deliberately not implemented; interface leaves a clear
  plug-in point.


## Phase 2 (cutover readiness). Read evidence/intent.md and config/tables.json first.

1. Write tests FIRST in tests/test_reconcile.py, using the existing sample data:
   - sales_contract: all reconciliation checks PASS
   - sales_customer: duplicate key detected in target (FAIL), and the
     'Sydney Harbour Traders' vs 'SYDNEY HARBOUR TRADERS' case difference is
     detected (FAIL)
   - sales_orders: sum of order_amount differs beyond tolerance (FAIL), and
     NULL in source vs empty string in target is detected (FAIL)
   - the gate returns NO-GO if ANY table has a FAIL or a NEEDS_REVIEW DDL status;
     GO only when every table passes and every DDL is CONVERTED or
     CONVERTED_WITH_WARNING

2. Implement src/reconcile.py using the DuckDB adapter. Checks per table:
   row count, duplicate keys in target, sum of numeric columns (configurable
   tolerance, default 0.005), null counts per column (treat empty string in
   target as a mismatch vs NULL in source), and a row-level diff on key
   columns to catch value differences such as case.
   Return typed results (dataclasses): table, check, status PASS/FAIL, detail.

3. Implement src/gate.py: combine DDL results and reconciliation results into
   a per-table summary and an overall GO / NO-GO with reasons.
   The report must show per-table results, so a table can PASS reconciliation
   while still being NEEDS_REVIEW on DDL.

4. Implement src/cli.py (run with: python -m src.cli --config config/tables.json):
   generate a run_id (timestamp + short uuid), use Python logging with run_id
   in every line, write reports/<run_id>.json and print a readable summary.
   Exit code 0 for GO, 1 for NO-GO, 2 for an input/config error (missing file,
   bad config) with a clear message, not a stack trace.

Do not run git commands. Run pytest and the CLI at the end and show both outputs.