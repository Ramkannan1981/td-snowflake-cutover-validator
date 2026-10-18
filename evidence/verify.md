# Verify

## How to run

From the repo root, with the venv set up (`requirements.txt`):

```
./scripts/verify.sh
```

It runs three steps in order and stops at the first failure. Steps after a
failure are shown as `SKIPPED`. Exit code is 0 if all steps pass, 1 otherwise.

| Step | What it checks |
|---|---|
| 1. `pytest` | All unit tests: DDL conversion, config reading, data loading, every reconciliation check, the gate, and CLI exit codes and report. |
| 2. `snowflake-sql` | Every table whose DDL is `CONVERTED` or `CONVERTED_WITH_WARNING`: the SQL parses with sqlglot's Snowflake dialect, is a `CREATE TABLE`, and contains no Teradata-only keywords (`PRIMARY INDEX`, `MULTISET`, `FORMAT`, `CASESPECIFIC`, `PERIOD`, and others). `NEEDS_REVIEW` tables are listed as skipped, not counted as passed. |
| 3. `cli-sample-data` | Runs the CLI on `config/tables.json`. The sample data has planted issues, so the expected result is NO-GO (exit 1). A GO (exit 0) fails this step, because it would mean a check missed a planted issue. Any other exit code also fails. |

The keyword check exists because sqlglot's Snowflake parser is lenient. It
accepts `PRIMARY INDEX`, for example, so "it parses" alone does not prove the
SQL is valid Snowflake.

## Latest result

Run on 2026-10-18 (Python 3.10.13, duckdb 1.5.6, sqlglot 30.23.0):

```
==== [1/3] pytest ====
54 passed in 1.04s

==== [2/3] converted SQL is valid Snowflake ====
  ok    sales.orders: CONVERTED
  ok    sales.customer: CONVERTED_WITH_WARNING
  skip  sales.contract: NEEDS_REVIEW (no SQL produced)
2/2 converted table(s) produce valid Snowflake SQL

==== [3/3] CLI on sample data (expect NO-GO, exit 1) ====
Decision: NO-GO
  TABLE           DDL                     RECONCILIATION  READY
  sales.orders    CONVERTED               FAIL            no
  sales.customer  CONVERTED_WITH_WARNING  FAIL            no
  sales.contract  NEEDS_REVIEW            PASS            no

==== verify summary ====
  pytest           PASS
  snowflake-sql    PASS
  cli-sample-data  PASS (NO-GO as expected)
  overall          PASS
```

(CLI log lines and the full list of blocking reasons are left out above.)

Each planted issue is reported in the NO-GO reasons with the exact row:

| Planted issue | Check that caught it |
|---|---|
| Duplicate row in the SET table target (`customer_id=9`) | `duplicate_keys`, `row_count` (20 vs 21) |
| Case change on `customer_name` (a PII column, so the values are shown masked, e.g. `S***#3978 vs S***#1ad3`) | `row_diff` |
| Rounding (`order_amount` 100.01 vs 100.00) | `sum:order_amount` (diff 0.01 > 0.005), `row_diff` |
| NULL in source, `''` in target (`order_id=1007`) | `null_counts`, `row_diff` |
| `PERIOD(DATE)` column | DDL `NEEDS_REVIEW`; `sales.contract` data passes but the table is still blocked |

## Where each check runs

| Stage | What runs | Status in this repo |
|---|---|---|
| Local (developer) | `./scripts/verify.sh`: unit tests, Snowflake syntax check, CLI on sample data | Built |
| PR pipeline | The same `verify.sh` on every pull request, so a change that stops catching a planted issue fails the build | Proposed; no CI config in this repo |
| Before cutover, on Snowflake | The CLI with a `SnowflakeAdapter` against real Teradata and Snowflake tables in test, then prod. Converted DDL also run in a Snowflake test account to confirm it executes, not just parses | Proposed; `SnowflakeAdapter` not built |

## Known gaps

- **No live Snowflake execution.** Converted SQL is checked for syntax and
  Teradata leftovers, not executed. A statement could pass here and still fail
  in Snowflake.
- **DuckDB is not Snowflake or Teradata.** It does not reproduce:
  - collation and case sensitivity. Teradata `NOT CASESPECIFIC` columns
    compare case-insensitively; the converter sends these to `NEEDS_REVIEW`,
    but no test shows the runtime difference.
  - some types: all columns are loaded as text, and numeric columns are cast
    for sums and diffs. Type behaviour such as `TIMESTAMP` precision, `FLOAT`
    rounding or Teradata `PERIOD` is not exercised.
  - how each system's export or load tools write NULLs, empty strings and
    number formats. The CSVs here are written by hand to look like exports.
- **Small sample.** Three tables, about 20 rows each. The row-level diff reads
  whole rows into memory, which does not scale; at volume it would compare
  per-key hashes from each side.
- **DDL coverage is narrow.** Only simple `CREATE TABLE` statements with a
  short list of safe types. Real exports will produce many `NEEDS_REVIEW`
  results (for example every `NOT CASESPECIFIC` column).

## Next steps

1. Implement the `SnowflakeAdapter` and run the same checks on a Snowflake
   dev account.
2. Replace the in-memory row diff with per-key hashes computed on each side,
   so it scales to large tables.
3. Add a CI workflow that runs `scripts/verify.sh` on every pull request.
4. Add a "likely cause" hint to each failure (for example a case-only
   difference points to collation), and later an agent with read-only tools
   to investigate failures, keeping the checks themselves deterministic.
