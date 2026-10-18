"""Check that converted DDL is plausible Snowflake SQL.

sqlglot's Snowflake parser is lenient (it accepts PRIMARY INDEX, for one), so
parsing alone does not prove the SQL is Snowflake. This also rejects any
Teradata-only keyword left in the output.

Run as: python -m src.snowflake_check --config config/tables.json
Exit codes: 0 = all converted SQL valid, 1 = a problem found, 2 = input error.
"""

from __future__ import annotations

import argparse
import re
import sys

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from .ddl_converter import Status, convert_file
from .table_config import ConfigError, load_config

# Teradata-only syntax that sqlglot will pass through to "Snowflake" output if
# not stripped. None of these are valid in a Snowflake CREATE TABLE. Matching
# is word-based, so a column literally named e.g. "format" would be flagged:
# a false alarm here is preferable to a missed conversion.
TERADATA_ONLY = [
    r"\bMULTISET\b",
    r"\bSET\s+TABLE\b",
    r"\bPRIMARY\s+INDEX\b",
    r"\bFALLBACK\b",
    r"\bJOURNAL\b",
    r"\bCASESPECIFIC\b",
    r"\bCHARACTER\s+SET\b",
    r"\bFORMAT\b",
    r"\bPERIOD\b",
]


def snowflake_sql_problems(sql: str) -> list[str]:
    """Return what is wrong with `sql` as a Snowflake CREATE TABLE; empty if fine."""
    problems = [
        f"Teradata-only syntax {pattern!r}"
        for pattern in TERADATA_ONLY
        if re.search(pattern, sql, re.IGNORECASE)
    ]
    try:
        parsed = sqlglot.parse_one(sql, read="snowflake", error_level=sqlglot.ErrorLevel.RAISE)
    except SqlglotError as e:
        return problems + [f"does not parse as Snowflake: {str(e).splitlines()[0]}"]
    if not isinstance(parsed, exp.Create) or parsed.kind != "TABLE":
        problems.append("is not a CREATE TABLE statement")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.snowflake_check")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"Input error: {e}", file=sys.stderr)
        return 2

    checked, failed = 0, 0
    for table in config.tables:
        if not table.ddl.is_file():
            print(f"Input error: DDL file not found for {table.name}: {table.ddl}", file=sys.stderr)
            return 2
        result = convert_file(table.ddl)
        if result.status not in (Status.CONVERTED, Status.CONVERTED_WITH_WARNING):
            print(f"  skip  {table.name}: {result.status.value} (no SQL produced)")
            continue
        checked += 1
        problems = snowflake_sql_problems(result.snowflake_sql)
        if problems:
            failed += 1
            print(f"  FAIL  {table.name}: {'; '.join(problems)}\n        {result.snowflake_sql}")
        else:
            print(f"  ok    {table.name}: {result.status.value}")

    print(f"{checked - failed}/{checked} converted table(s) produce valid Snowflake SQL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
