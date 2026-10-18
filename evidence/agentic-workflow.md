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

## Prompt 3 – Reconciliation, cutover gate, report and CLI
Asked the agent to write tests first for each planted issue, then implement
reconciliation checks (row count, duplicate keys, sums with tolerance, null
counts, row-level diff on keys), a gate combining DDL and reconciliation
results into a per-table summary and overall GO / NO-GO, and a CLI with a
run_id, structured logging, a JSON report and exit codes (0 GO, 1 NO-GO,
2 input/config error).

**Prompt as sent:**

```text
Phase 2 (cutover readiness). Read evidence/intent.md and config/tables.json first.

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
```

### My review / corrections
- Ran the tests (39 passing) and the CLI myself. Checked every planted issue
  is reported with the exact row: duplicate customer_id=9, the case change on
  customer_id=1, the 100.01 vs 100.00 rounding, and NULL vs '' on
  order_id=1007.
- Asked for per-table results so a table can pass reconciliation while still
  blocked on DDL. Confirmed sales.contract shows reconciliation PASS but
  DDL NEEDS_REVIEW, so the gate is NO-GO for the right reason.
- Confirmed a GO path is tested, not only NO-GO.
- Checked the failure path: a missing config gives exit code 2 and a one-line
  error, not a stack trace.
- Kept the sum tolerance strict (0.005), so the 0.01 rounding difference
  fails. For financial amounts I'd rather surface differences and let data
  owners agree any tolerance than hide them.
- Noted the SET table warning from the DDL step is confirmed by the duplicate
  found in reconciliation: the DDL check predicts the risk, the data check
  proves it.
- Reviewed the output with Claude chat as a second check before committing.

## Prompt 4 – Verify script
Asked the agent for scripts/verify.sh that runs the tests, checks every
CONVERTED Snowflake SQL parses in the Snowflake dialect and contains no
Teradata-only keywords, then runs the CLI on the sample data, expecting
NO-GO (exit 1) and treating any other failure as a real error.

### My review / corrections
- Ran scripts/verify.sh myself: pytest (42 passing), Snowflake SQL check
  (2/2 converted tables valid; the NEEDS_REVIEW table is skipped, not
  silently passed), and the CLI returning NO-GO as expected.
- The agent moved the Teradata-leftover check from the tests into
  src/snowflake_check.py, so tests and verify.sh share one check. I accepted
  this and confirmed it added negative tests (PRIMARY INDEX, NOT CASESPECIFIC,
  non-CREATE statements are rejected).
- Confirmed verify.sh fails if the sample data ever returns GO, because the
  planted issues must always be caught.
- Known limit: this checks Snowflake syntax and Teradata leftovers, not
  runtime behaviour. A live Snowflake run is not included (see decisions.md).

## Prompt 5 – Evidence pack (design, verify, operate)
Asked the agent to draft design.md, verify.md and operate.md from what is
actually in the repo. I wrote decisions.md myself from my migration
experience, with Claude chat helping structure it.

### My review / corrections
- Checked the agent marked unbuilt parts (PR pipeline, SnowflakeAdapter,
  production shape) as proposed, not as built.
- Checked design.md does not claim Spark is used: cold data is simulated
  with CSV extracts in this slice.
- Agent added .env.example unprompted; kept it, confirmed it has empty
  placeholders only.

## Prompt 6 – Final README and clean-checkout test
Asked the agent to rewrite README.md for an assessor and run a clean-checkout
test of the setup steps.

### My review / corrections
- The clean-checkout test picked up macOS's built-in Python 3.9, which can't
  install the pinned packages. I kept the code and dependencies as they are
  and made the README state Python 3.10+ explicitly, with the macOS note.
- Wrote the final README with Claude chat from a real run, so the example
  output is genuine, not invented, and checked every CLI option against --help.
- Replaced an uncropped screenshot uploaded via the GitHub website with a
  cropped version, so no personal account details are shown.
- Final check from a fresh clone: setup, then verify.sh, all PASS.

## Step 7 – Teradata to Snowflake mapping (written by me)
Added docs/teradata-snowflake-mapping.md from my Teradata migration
experience, with Claude chat helping structure it. The "In this repo" column
is kept honest: only items the code actually handles are marked.

## Prompt 8 – PII masking in reports and logs
Noticed failure details printed raw values (e.g. a customer name), which on
real data would leak PII into logs and report files. Asked the agent to add
pii_columns to config and mask those values everywhere they are shown, while
still comparing them.

### My review / corrections
- Confirmed the customer_name difference is still detected (FAIL) but shown
  masked, and the raw name no longer appears in the report or logs.
- Kept PII detection out of scope: columns are marked in config by data
  owners, not guessed.
