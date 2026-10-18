from pathlib import Path

import pytest
import sqlglot
from sqlglot import exp

from src.ddl_converter import Status, convert_ddl, convert_file
from src.snowflake_check import snowflake_sql_problems

DDL_DIR = Path(__file__).resolve().parent.parent / "sample-data" / "ddl"


def assert_valid_snowflake(sql: str) -> exp.Create:
    assert snowflake_sql_problems(sql) == [], sql
    return sqlglot.parse_one(sql, read="snowflake")


def column_types(create: exp.Create) -> dict[str, str]:
    return {
        col.name: col.args["kind"].sql("snowflake")
        for col in create.find_all(exp.ColumnDef)
    }


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE s.t (id INT) PRIMARY INDEX (id)",
        "CREATE TABLE s.t (nm VARCHAR(10) NOT CASESPECIFIC)",
        "SELECT 1",
    ],
    ids=["primary-index", "casespecific", "not-create-table"],
)
def test_snowflake_check_rejects_teradata_leftovers(sql):
    assert snowflake_sql_problems(sql) != []


# --- MULTISET: plain conversion -------------------------------------------

def test_multiset_table_converts_to_valid_snowflake():
    result = convert_file(DDL_DIR / "sales_orders.sql")

    assert result.table_name == "sales.orders"
    assert result.status == Status.CONVERTED
    parsed = assert_valid_snowflake(result.snowflake_sql)
    assert column_types(parsed) == {
        "order_id": "INT",
        "customer_id": "INT",
        "order_date": "DATE",
        "order_amount": "DECIMAL(12, 2)",
        "status_code": "CHAR(1)",
        "created_ts": "TIMESTAMP(0)",
    }
    assert "NOT NULL" in result.snowflake_sql
    assert "DEFAULT 'N'" in result.snowflake_sql


# --- SET: converts, but duplicate semantics change ---------------------------

def test_set_table_converts_with_duplicate_warning():
    result = convert_file(DDL_DIR / "sales_customer.sql")

    assert result.table_name == "sales.customer"
    assert result.status == Status.CONVERTED_WITH_WARNING
    assert_valid_snowflake(result.snowflake_sql)
    assert any("duplicate" in r.lower() for r in result.reasons), result.reasons


def test_unique_primary_index_warns_uniqueness_not_enforced():
    result = convert_ddl(
        "CREATE MULTISET TABLE s.t (id INTEGER NOT NULL) UNIQUE PRIMARY INDEX (id);"
    )

    assert result.status == Status.CONVERTED_WITH_WARNING
    assert_valid_snowflake(result.snowflake_sql)
    assert any("unique" in r.lower() for r in result.reasons), result.reasons


# --- Unsupported constructs: NEEDS_REVIEW, never a guess -------------------

def test_period_type_needs_review_without_guessed_sql():
    result = convert_file(DDL_DIR / "sales_contract.sql")

    assert result.table_name == "sales.contract"
    assert result.status == Status.NEEDS_REVIEW
    assert result.snowflake_sql is None
    assert any("PERIOD" in r and "validity" in r for r in result.reasons), result.reasons


@pytest.mark.parametrize(
    "ddl, expected_in_reason",
    [
        ("CREATE MULTISET TABLE s.t (nm VARCHAR(10) NOT CASESPECIFIC);", "column 'nm'"),
        ("CREATE MULTISET TABLE s.t (d DATE FORMAT 'YYYY-MM-DD');", "column 'd'"),
        ("CREATE MULTISET TABLE s.t (id INTEGER", "parse"),
    ],
    ids=["casespecific", "format", "unparseable"],
)
def test_other_unsafe_input_needs_review(ddl, expected_in_reason):
    result = convert_ddl(ddl)

    assert result.status == Status.NEEDS_REVIEW
    assert result.snowflake_sql is None
    assert any(expected_in_reason in r.lower() for r in result.reasons), result.reasons
