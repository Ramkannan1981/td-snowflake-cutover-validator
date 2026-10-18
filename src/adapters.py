"""Database access for the parallel run.

Reconciliation talks only to DatabaseAdapter, so it does not care whether a
side lives in local DuckDB or a real warehouse.

Locally, DuckDBAdapter stands in for both systems: Teradata exports go into the
`source` schema and the Snowflake load into the `target` schema.

Where Snowflake plugs in: a SnowflakeAdapter would implement DatabaseAdapter
using snowflake-connector-python, with `query` running against the warehouse
and `table_exists` / `row_count` reading the real target tables. It would have
no CSV loading, because the target data is already in Snowflake. That also
means reconciliation should compare per-side aggregates rather than join
across schemas, since a real source and target are not in the same engine.
"""

from __future__ import annotations

import csv
import re
from abc import ABC, abstractmethod
from pathlib import Path

import duckdb

from .table_config import ValidationConfig

SOURCE_SCHEMA = "source"
TARGET_SCHEMA = "target"


class DataLoadError(Exception):
    pass


class DatabaseAdapter(ABC):
    @abstractmethod
    def query(self, sql: str, params: list | None = None) -> list[tuple]:
        ...

    @abstractmethod
    def table_exists(self, schema: str, table: str) -> bool:
        ...

    @abstractmethod
    def row_count(self, schema: str, table: str) -> int:
        ...

    @abstractmethod
    def columns(self, schema: str, table: str) -> list[str]:
        """Column names in table order."""
        ...

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class DuckDBAdapter(DatabaseAdapter):
    def __init__(self, database: str = ":memory:"):
        self._con = duckdb.connect(database)
        for schema in (SOURCE_SCHEMA, TARGET_SCHEMA):
            self._con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    def query(self, sql: str, params: list | None = None) -> list[tuple]:
        return self._con.execute(sql, params or []).fetchall()

    def table_exists(self, schema: str, table: str) -> bool:
        rows = self.query(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema, table],
        )
        return bool(rows)

    def row_count(self, schema: str, table: str) -> int:
        return self.query(f"SELECT COUNT(*) FROM {schema}.{table}")[0][0]

    def columns(self, schema: str, table: str) -> list[str]:
        rows = self.query(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [schema, table],
        )
        return [r[0] for r in rows]

    def load_tables(self, config: ValidationConfig) -> None:
        """Load every configured table's source and target CSV.

        All files are checked first, so a missing file is reported up front
        (listing every missing file) rather than after a partial load.
        """
        plan = [
            (schema, table, data_dir / table.csv)
            for table in config.tables
            for schema, data_dir in (
                (SOURCE_SCHEMA, config.source_dir),
                (TARGET_SCHEMA, config.target_dir),
            )
        ]
        missing = [
            f"  {table.name} ({schema}): {path}" for schema, table, path in plan if not path.is_file()
        ]
        if missing:
            raise DataLoadError("Missing CSV file(s):\n" + "\n".join(missing))

        for schema, table, path in plan:
            self.load_csv(schema, table.load_name, path)

    def load_csv(self, schema: str, table: str, path: Path) -> None:
        # Every column is loaded as text, exactly as exported:
        # - columns as VARCHAR: type inference would read amounts as DOUBLE and
        #   lose exact decimal values; reconciliation casts numeric columns.
        # - allow_quoted_nulls=false: keeps "" (empty string) distinct from an
        #   unquoted empty field (NULL), a common Teradata vs Snowflake drift.
        # - auto_detect off, dialect fixed, strict_mode: a malformed file fails
        #   with a line number instead of being "sniffed" into one wide column.
        columns = _read_header(path)
        try:
            self._con.execute(
                f"CREATE OR REPLACE TABLE {schema}.{table} AS "
                "SELECT * FROM read_csv(?, header = true, auto_detect = false, columns = ?, "
                "delim = ',', quote = '\"', escape = '\"', strict_mode = true, "
                "allow_quoted_nulls = false)",
                [str(path), {c: "VARCHAR" for c in columns}],
            )
        except duckdb.Error as e:
            # DuckDB's message quotes the offending row ("Original Line: ..."),
            # which may hold PII. Keep only the line number and our own reason,
            # and drop the original exception so it can't surface later.
            raise DataLoadError(
                f"Could not load {path} into {schema}.{table}: {_describe_csv_error(str(e))}"
            ) from None

    def close(self) -> None:
        self._con.close()


_CSV_ERROR_REASONS = [
    ("Expected Number of Columns", "wrong number of columns"),
    ("unterminated quote", "unterminated quote"),
    ("Invalid unicode", "not valid UTF-8"),
]


def _describe_csv_error(message: str) -> str:
    reason = next((r for phrase, r in _CSV_ERROR_REASONS if phrase in message), "malformed CSV")
    line = re.search(r"CSV Error on Line: (\d+)", message)
    return f"{reason} at line {line.group(1)}" if line else reason


def _read_header(path: Path) -> list[str]:
    # Decode only the first line: decoding more would blame a bad byte in a
    # later row on the header. DuckDB reports those with their line number.
    with path.open("rb") as f:
        first_line = f.readline()
    try:
        header = next(csv.reader([first_line.decode("utf-8-sig")]), None)
    except UnicodeDecodeError:
        raise DataLoadError(f"Could not load {path}: header is not valid UTF-8") from None
    if not header:
        raise DataLoadError(f"Could not load {path}: file is empty or has no header")
    if any(not c.strip() for c in header) or len(set(header)) != len(header):
        raise DataLoadError(f"Could not load {path}: header has empty or repeated column names")
    return header
