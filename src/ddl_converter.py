"""Convert Teradata CREATE TABLE DDL to Snowflake SQL.

sqlglot's Teradata -> Snowflake transpile passes many Teradata-only constructs
straight through (PRIMARY INDEX, FORMAT, CASESPECIFIC, PERIOD, ...), producing
SQL that looks converted but is not valid Snowflake. So this module does not
trust the transpile on its own: it inspects the parsed tree against an
allowlist, and anything not explicitly understood is NEEDS_REVIEW with no SQL.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError


class Status(str, Enum):
    CONVERTED = "CONVERTED"
    CONVERTED_WITH_WARNING = "CONVERTED_WITH_WARNING"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class ConversionResult:
    table_name: str | None
    status: Status
    snowflake_sql: str | None
    # Why the status is what it is. For CONVERTED this holds informational
    # notes only (e.g. physical options that were dropped).
    reasons: list[str] = field(default_factory=list)


# Types whose Teradata and Snowflake semantics match closely enough to convert
# without review. BYTEINT parses as SMALLINT, NUMBER(p,s) as DECIMAL.
SAFE_TYPES = {
    exp.DataType.Type.SMALLINT,
    exp.DataType.Type.INT,
    exp.DataType.Type.BIGINT,
    exp.DataType.Type.DECIMAL,
    exp.DataType.Type.FLOAT,
    exp.DataType.Type.DOUBLE,
    exp.DataType.Type.CHAR,
    exp.DataType.Type.VARCHAR,
    exp.DataType.Type.DATE,
    exp.DataType.Type.TIMESTAMP,
}

# Table options that only affect Teradata physical storage / recovery and can
# be dropped without changing what the table holds.
PHYSICAL_PROPERTIES = (exp.FallbackProperty, exp.JournalProperty)

_TABLE_NAME_RE = re.compile(
    r"CREATE\s+(?:(?:MULTISET|SET|VOLATILE|GLOBAL\s+TEMPORARY)\s+)*TABLE\s+([\w$#.\"]+)",
    re.IGNORECASE,
)


def convert_file(path: str | Path) -> ConversionResult:
    return convert_ddl(Path(path).read_text())


def convert_ddl(ddl: str) -> ConversionResult:
    try:
        statements = [
            s
            for s in sqlglot.parse(ddl, read="teradata", error_level=sqlglot.ErrorLevel.RAISE)
            if s is not None
        ]
    except SqlglotError as e:
        first_line = str(e).splitlines()[0]
        return _needs_review(_guess_table_name(ddl), f"Could not parse DDL: {first_line}")

    if len(statements) != 1:
        return _needs_review(
            _guess_table_name(ddl),
            f"Expected exactly one statement, found {len(statements)}",
        )

    create = statements[0]
    if not isinstance(create, exp.Create) or create.kind != "TABLE":
        return _needs_review(_guess_table_name(ddl), "Not a CREATE TABLE statement")

    table = create.find(exp.Table)
    table_name = ".".join(part.name for part in table.parts)

    review: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    if create.args.get("expression") is not None:
        review.append("CREATE TABLE AS SELECT is not auto-converted")

    _check_properties(create, review, warnings, notes)
    _check_indexes(create, warnings, notes)
    _check_columns(create, review)

    if review:
        return ConversionResult(table_name, Status.NEEDS_REVIEW, None, review + warnings)

    create.set("properties", None)
    create.set("indexes", None)
    snowflake_sql = create.sql(dialect="snowflake", comments=False)

    status = Status.CONVERTED_WITH_WARNING if warnings else Status.CONVERTED
    return ConversionResult(table_name, status, snowflake_sql, warnings + notes)


def _check_properties(create, review, warnings, notes):
    properties = create.args.get("properties")
    for prop in properties.expressions if properties else []:
        if isinstance(prop, exp.SetProperty):
            if not prop.args.get("multi"):
                warnings.append(
                    "SET table: Teradata silently rejects duplicate rows, Snowflake "
                    "will not. Duplicates must be prevented upstream or checked in "
                    "reconciliation."
                )
        elif isinstance(prop, PHYSICAL_PROPERTIES):
            notes.append(f"Dropped Teradata physical option: {prop.sql(dialect='teradata')}")
        else:
            review.append(f"Unsupported table option: {prop.sql(dialect='teradata')}")


def _check_indexes(create, warnings, notes):
    for index in create.args.get("indexes") or []:
        columns = ", ".join(c.this.name for c in index.args["params"].args.get("columns") or [])
        kind = "PRIMARY INDEX" if index.args.get("primary") else "INDEX"
        if index.args.get("unique"):
            warnings.append(
                f"UNIQUE {kind} ({columns}) enforces uniqueness in Teradata; "
                "Snowflake has no enforced equivalent, so duplicates will not be rejected."
            )
        else:
            notes.append(f"Dropped {kind} ({columns}); Snowflake has no equivalent.")


def _check_columns(create, review):
    for item in create.this.expressions:
        if not isinstance(item, exp.ColumnDef):
            review.append(f"Unsupported table element: {item.sql(dialect='teradata')}")
            continue

        name = item.name
        dtype = item.args.get("kind")
        if dtype is None:
            review.append(f"column '{name}' has no data type")
        elif dtype.this not in SAFE_TYPES:
            review.append(
                f"column '{name}' uses type {dtype.sql(dialect='teradata')}, "
                "which has no safe Snowflake equivalent"
            )

        for constraint in item.args.get("constraints") or []:
            kind = constraint.args.get("kind")
            if isinstance(kind, exp.NotNullColumnConstraint):
                continue
            if isinstance(kind, exp.DefaultColumnConstraint) and isinstance(
                kind.this, (exp.Literal, exp.Null)
            ):
                continue
            review.append(
                f"column '{name}' has attribute {constraint.sql(dialect='teradata')}, "
                "which is not auto-converted"
            )


def _needs_review(table_name: str | None, reason: str) -> ConversionResult:
    return ConversionResult(table_name, Status.NEEDS_REVIEW, None, [reason])


def _guess_table_name(ddl: str) -> str | None:
    match = _TABLE_NAME_RE.search(ddl)
    return match.group(1) if match else None
