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
   gate and the CLI (42 tests).
2. **Snowflake SQL check**: every converted table's SQL parses in the
   Snowflake dialect and contains no Teradata-only syntax (PRIMARY INDEX,
   FALLBACK, CASESPECIFIC, PERIOD, ...).
3. **CLI on the sample data**: must return NO-GO, because the sample data has
   planted migration issues. A GO here would mean a check stopped working.

## Example output

The sample target (Snowflake) data has four deliberately planted migration
issues: a duplicate row, a case change, a rounding difference, and a NULL
that became an empty string. The tool catches all of them: