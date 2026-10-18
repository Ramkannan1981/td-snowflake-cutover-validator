# Operate

## How to run

```
python -m src.cli --config config/tables.json
```

Options:

| Option | Default | Meaning |
|---|---|---|
| `--config` | (required) | Path to the table list |
| `--reports-dir` | `reports` | Where the JSON report is written |
| `--tolerance` | `0.005` | Largest allowed absolute difference in a numeric column sum |
| `-v` | off | Also log checks that pass |

## Exit codes

| Code | Meaning | Report written? |
|---|---|---|
| 0 | GO: every table's DDL converted and every check passed | Yes |
| 1 | NO-GO: at least one table is blocked; reasons are in the summary and report | Yes |
| 2 | Input or config error (missing file, bad config); one-line error, no stack trace | No |

A scheduler can act on the exit code alone: 0 lets the cutover step proceed,
1 stops it and notifies, 2 means the run itself is broken and needs an
engineer.

## Logs, reports and run_id

- Every run gets a run_id: UTC timestamp plus 8 hex characters, for example
  `20261018T093615Z-e9271faa`.
- **Logs** go to stderr. Every line has `run_id=...` and a UTC timestamp.
  Failed checks and non-`CONVERTED` DDL are logged at `WARNING`.
- **Summary** goes to stdout: decision, a per-table table, blocking reasons
  and non-blocking warnings.
- **Report** goes to `reports/<run_id>.json`: decision, reasons, tolerance,
  config path, and per table the DDL status and reasons, every check result,
  and the converted Snowflake SQL.

Use the run_id to tie things together: quote it in the cutover decision
record, search logs for it, and open the matching report file. A GO decision
should always point to a specific run_id, so the group can see exactly what
was checked.

## Config and secrets

- The tables to validate come only from `config/tables.json`: name, DDL file,
  CSV file, key columns and numeric columns. Adding a table means adding an
  entry, not changing code. Relative paths are resolved from the config
  file's own folder.
- The local core path uses no credentials and reads no environment variables.
- `.env.example` lists placeholder names for a future Snowflake connection
  (account, user, role, warehouse, database, key path). Real values go in
  `.env`, which is git-ignored, or in the orchestrator's secret store. They
  are never committed.

## Failure modes

| What you see | Exit | What to do |
|---|---|---|
| `Config file not found` | 2 | Check the `--config` path. |
| `is not valid JSON` or `missing required field '...'` | 2 | Fix the config. The message names the table and field. |
| `... is not a plain SQL identifier` | 2 | Table and column names must be plain identifiers (letters, digits, `_`). |
| `Missing CSV file(s)` | 2 | Lists every missing file and which side (source or target). Re-run the export or load, or fix `source_dir` / `target_dir`. Nothing is loaded until all files are present. |
| `Missing DDL file(s)` | 2 | Fix the `ddl` path or add the exported DDL. |
| `configured column(s) ... not found in source data` | 2 | A key or numeric column in config is not in the CSV header. Fix the config or the export. |
| DDL `NEEDS_REVIEW` | 1 | The reason names the column and construct (for example `PERIOD(DATE)`). An engineer writes the Snowflake DDL by hand and agrees the mapping with the data owner. The tool will keep reporting `NEEDS_REVIEW` until the converter supports the construct. |
| DDL `CONVERTED_WITH_WARNING` | not blocking | Read the warning. For a SET table, make sure duplicates are stopped upstream; the `duplicate_keys` check confirms it on the data. |
| Check `FAIL` | 1 | The detail names the rows or columns (for example `customer_id=9 (2 rows)`). Find whether the cause is the load into Snowflake or a real difference, fix it, and re-run. Do not raise `--tolerance` to make a check pass without agreement from the data owner. |

## Degrade and rollback in a real cutover

- **Keep Teradata live until GO.** During Phase 1 both systems are fed, so
  a NO-GO costs time, not data. Consumers stay on Teradata.
- **Cut over in groups.** The gate works per table, so consumers that depend
  only on ready tables can move first. The rest wait for their tables.
- **Re-run right before switching.** A GO from last week does not cover
  loads since then. Run again just before each switch and record that run_id.
- **Rollback means pointing back.** Keep Teradata fed for an agreed period
  after the switch. If problems show up, consumers are pointed back to
  Teradata; no data restore is needed.
- **Validation unavailable.** If the validation run cannot complete (exit 2),
  there is no decision, and that counts as NO-GO. Do not switch on an old or
  missing report.
