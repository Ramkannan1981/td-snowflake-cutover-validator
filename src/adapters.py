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
        # - all_varchar: type inference would read amounts as DOUBLE and lose
        #   exact decimal values; reconciliation casts numeric columns itself.
        # - allow_quoted_nulls=false: keeps "" (empty string) distinct from an
        #   unquoted empty field (NULL), a common Teradata vs Snowflake drift.
        try:
            self._con.execute(
                f"CREATE OR REPLACE TABLE {schema}.{table} AS "
                "SELECT * FROM read_csv(?, header = true, all_varchar = true, "
                "allow_quoted_nulls = false)",
                [str(path)],
            )
        except duckdb.Error as e:
            raise DataLoadError(f"Could not load {path} into {schema}.{table}: {e}") from e

    def close(self) -> None:
        self._con.close()
