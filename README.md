# Teradata → Snowflake Cutover Validator

During a Teradata to Snowflake parallel run, both systems are live while data
marts and consumers move across. This tool answers one question: **is it safe
to switch consumers off Teradata yet?** It converts Teradata DDL to Snowflake,
reconciles source and target data, and returns **GO / NO-GO** with the reason
for every blocked table.

## Prerequisites

- **Python 3.10 or later.** The pinned packages need 3.10+. On macOS the
  built-in `/usr/bin/python3` is 3.9, so use a 3.10+ install (python.org or
  Homebrew), e.g. `python3.10`.
- No database, cloud account or credentials. The core path runs locally on
  DuckDB with synthetic data.

## Setup

```bash
git clone https://github.com/Ramkannan1981/td-snowflake-cutover-validator.git
cd td-snowflake-cutover-validator
python3.10 -m venv .venv          # any Python 3.10+
source .venv/bin/activate         # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python -m src.cli --config config/tables.json
```

| Option | Default | Meaning |
|---|---|---|
| `--config` | (required) | Table list: DDL file, CSV, key and numeric columns per table |
| `--reports-dir` | `reports` | Where the JSON report is written |
| `--tolerance` | `0.005` | Largest allowed difference in a numeric column sum |
| `-v`, `--verbose` | off | Log every check, not just failures |

| Exit code | Meaning |
|---|---|
| `0` | GO: every table passed and every DDL converted |
| `1` | NO-GO: at least one check failed or a DDL needs review |
| `2` | Input or config error (e.g. missing file), with a one-line message |

Every log line carries a `run_id`, and each run writes `reports/<run_id>.json`.

## Verify

```bash
./scripts/verify.sh
```

Runs the checks I would run in CI, stopping at the first failure:

1. **pytest**: unit tests for DDL conversion, loading, reconciliation, the
   gate, the CLI, PII masking and load errors (54 tests).
2. **Snowflake SQL check**: every converted table's SQL parses in the
   Snowflake dialect and contains no Teradata-only syntax (PRIMARY INDEX,
   FALLBACK, CASESPECIFIC, PERIOD, ...).
3. **CLI on the sample data**: must return NO-GO, because the sample data has
   planted migration issues. A GO here would mean a check stopped working.

## Example output

The sample target (Snowflake) data has four deliberately planted migration
issues: a duplicate row, a case change, a rounding difference, and a NULL
that became an empty string. The tool catches all of them:


```
Cutover readiness  run_id=20261018T104542Z-b18e4ddd
Decision: NO-GO

  TABLE           DDL                     RECONCILIATION  READY
  sales.orders    CONVERTED               FAIL            no
  sales.customer  CONVERTED_WITH_WARNING  FAIL            no
  sales.contract  NEEDS_REVIEW            PASS            no

Blocking reasons:
  - sales.orders: sum:order_amount FAIL: source=17579.33, target=17579.32, diff=0.01, tolerance=0.005
  - sales.orders: null_counts FAIL: status_code: source NULL=1, target NULL=0; source empty string=0, target empty string=1
  - sales.orders: row_diff FAIL: 2 key(s) with different values: order_id=1007: status_code NULL vs ''; order_id=1012: order_amount '100.01' vs '100.00'
  - sales.customer: row_count FAIL: source=20, target=21
  - sales.customer: duplicate_keys FAIL: 1 duplicate key(s) in target: customer_id=9 (2 rows)
  - sales.customer: row_diff FAIL: 1 key(s) with different values: customer_id=1: customer_name S***#347c vs S***#a266
  - sales.contract: DDL NEEDS_REVIEW: column 'validity' uses type PERIOD(DATE), which has no safe Snowflake equivalent

Warnings (not blocking):
  - sales.customer: SET table: Teradata silently rejects duplicate rows, Snowflake will not. Duplicates must be prevented upstream or checked in reconciliation.
  - sales.customer: Dropped Teradata physical option: NO FALLBACK
  - sales.customer: Dropped PRIMARY INDEX (customer_id); Snowflake has no equivalent.
```

Three things worth noting:

- The **SET table warning** from the DDL step predicts a risk (Snowflake won't
  reject duplicates), and reconciliation then **finds the duplicate** in the data.
- `sales.contract` **passes reconciliation** but is still blocked, because its
  DDL uses PERIOD(DATE) and needs review. Results are shown per table, so a
  data match never hides a DDL problem.
- `customer_name` is listed in `pii_columns`, so its values are masked
  (`S***#347c` vs `S***#a266`): the case difference is caught without
  showing the names. The hash changes every run.

## Known limitations

- **No live Snowflake run.** DuckDB stands in for both systems behind an
  adapter interface. Converted SQL is checked against the Snowflake dialect,
  which proves syntax, not runtime behaviour (collation, case sensitivity,
  some types). See `evidence/decisions.md` #4.
- **Small synthetic sample:** three tables, about 20 rows each.
- **DDL conversion covers common cases only.** Anything outside a known-safe
  list is marked NEEDS_REVIEW rather than guessed.
- Data movement (export/import and Spark ETL for cold data) is **simulated**
  with CSV extracts.

## Evidence pack

| File | Contents |
|---|---|
| `evidence/intent.md` | Problem, users, assumptions, scope, success criteria |
| `evidence/design.md` | Phase 1/Phase 2 approach, components, data flow, production shape |
| `evidence/agentic-workflow.md` | AI tools used, prompts, and my review and corrections at each step |
| `evidence/verify.md` | Checks, latest results, local vs pipeline checks, known gaps |
| `evidence/operate.md` | Running, logs, config and secrets, failure modes, rollback |
| `evidence/decisions.md` | Five decision records, including what I deliberately did not build |
| `evidence/snowflake-trial-card-required.png` | Snowflake trial sign-up requiring a credit card (decisions #4) |
| `docs/teradata-snowflake-mapping.md` | Teradata → Snowflake mapping: BTEQ, utilities, DDL, operations |

## Build log

Built with Claude Code as the implementation agent, one step at a time, tests
first. I ran the tests and made every commit after reviewing the output.

| Step | Commit |
|---|---|
| Setup and intent | `init: project structure and intent`, `docs: readme skeleton` |
| Phase 1: DDL conversion with safe abstain | `feat: ddl conversion with allowlist and safe abstain` |
| Phase 1: synthetic dual-feed data, DuckDB adapter | `feat: synthetic dual-feed data and duckdb adapter` |
| Phase 2: reconciliation, gate, report, CLI | `feat: reconciliation, cutover gate, report and cli` |
| Verify script | `chore: verify script and snowflake sql check` |
| Evidence pack | `docs: evidence pack (design, verify, operate, decisions)` |
| Docs fixes and screenshot | `docs: clarify simulated loads, ...`, `docs: add cropped trial screenshot` |
| Final README | `docs: final readme ...` |