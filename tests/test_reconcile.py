from pathlib import Path

import pytest

from src.adapters import DuckDBAdapter
from src.ddl_converter import ConversionResult, Status
from src.gate import Decision, decide
from src.reconcile import CheckResult, CheckStatus, reconcile_all, reconcile_table
from src.table_config import load_config

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def config():
    return load_config(ROOT / "config" / "tables.json")


@pytest.fixture(scope="module")
def results(config):
    with DuckDBAdapter() as db:
        db.load_tables(config)
        yield reconcile_all(db, config)


def by_check(checks: list[CheckResult]) -> dict[str, CheckResult]:
    return {c.check: c for c in checks}


def statuses(checks: list[CheckResult]) -> dict[str, CheckStatus]:
    return {c.check: c.status for c in checks}


# --- Reconciliation on the sample data -------------------------------------

def test_contract_all_checks_pass(results):
    checks = results["sales.contract"]
    assert {"row_count", "duplicate_keys", "sum:contract_value", "null_counts", "row_diff"} <= set(
        statuses(checks)
    )
    failed = [c for c in checks if c.status != CheckStatus.PASS]
    assert failed == []
    assert all(c.table == "sales.contract" for c in checks)


def test_customer_duplicate_key_in_target_fails(results):
    check = by_check(results["sales.customer"])["duplicate_keys"]
    assert check.status == CheckStatus.FAIL
    assert "customer_id=9" in check.detail


def test_customer_row_count_fails_because_of_duplicate(results):
    check = by_check(results["sales.customer"])["row_count"]
    assert check.status == CheckStatus.FAIL
    assert "20" in check.detail and "21" in check.detail


def test_customer_case_difference_detected(results):
    check = by_check(results["sales.customer"])["row_diff"]
    assert check.status == CheckStatus.FAIL
    assert "customer_id=1" in check.detail
    assert "customer_name" in check.detail
    assert "'Sydney Harbour Traders'" in check.detail
    assert "'SYDNEY HARBOUR TRADERS'" in check.detail


def test_orders_sum_differs_beyond_tolerance(results):
    check = by_check(results["sales.orders"])["sum:order_amount"]
    assert check.status == CheckStatus.FAIL
    assert "0.01" in check.detail


def test_orders_sum_within_wider_tolerance_passes(config):
    orders = next(t for t in config.tables if t.name == "sales.orders")
    with DuckDBAdapter() as db:
        db.load_tables(config)
        checks = reconcile_table(db, orders, tolerance=0.02)
    assert by_check(checks)["sum:order_amount"].status == CheckStatus.PASS


def test_orders_null_vs_empty_string_detected(results):
    checks = by_check(results["sales.orders"])

    nulls = checks["null_counts"]
    assert nulls.status == CheckStatus.FAIL
    assert "status_code" in nulls.detail
    assert "empty" in nulls.detail.lower()

    diff = checks["row_diff"]
    assert diff.status == CheckStatus.FAIL
    assert "order_id=1007" in diff.detail
    assert "NULL" in diff.detail and "''" in diff.detail


def test_orders_row_count_and_keys_pass(results):
    checks = statuses(results["sales.orders"])
    assert checks["row_count"] == CheckStatus.PASS
    assert checks["duplicate_keys"] == CheckStatus.PASS


# --- Gate ------------------------------------------------------------------

def ddl(name: str, status: Status) -> ConversionResult:
    reasons = [] if status == Status.CONVERTED else [f"{status.value} reason"]
    sql = None if status == Status.NEEDS_REVIEW else f"CREATE TABLE {name} (id INT)"
    return ConversionResult(name, status, sql, reasons)


def passing(name: str) -> list[CheckResult]:
    return [
        CheckResult(name, "row_count", CheckStatus.PASS, "ok"),
        CheckResult(name, "row_diff", CheckStatus.PASS, "ok"),
    ]


def test_gate_no_go_on_sample_data(config, results):
    from src.ddl_converter import convert_file

    ddl_results = {t.name: convert_file(t.ddl) for t in config.tables}
    gate = decide(ddl_results, results)

    assert gate.decision == Decision.NO_GO
    contract = gate.table("sales.contract")
    assert contract.reconciliation_passed is True
    assert contract.ddl_status == Status.NEEDS_REVIEW
    assert contract.ready is False
    assert gate.table("sales.orders").reconciliation_passed is False
    assert gate.table("sales.customer").reconciliation_passed is False
    assert any("sales.contract" in r and "NEEDS_REVIEW" in r for r in gate.reasons)


def test_gate_go_when_all_pass_and_ddl_converted_or_warning():
    gate = decide(
        {"a.t1": ddl("a.t1", Status.CONVERTED), "a.t2": ddl("a.t2", Status.CONVERTED_WITH_WARNING)},
        {"a.t1": passing("a.t1"), "a.t2": passing("a.t2")},
    )
    assert gate.decision == Decision.GO
    assert all(t.ready for t in gate.tables)
    # Warnings don't block, but are still reported.
    assert gate.table("a.t2").warnings == ["CONVERTED_WITH_WARNING reason"]


def test_gate_no_go_when_only_ddl_needs_review():
    gate = decide(
        {"a.t1": ddl("a.t1", Status.CONVERTED), "a.t2": ddl("a.t2", Status.NEEDS_REVIEW)},
        {"a.t1": passing("a.t1"), "a.t2": passing("a.t2")},
    )
    assert gate.decision == Decision.NO_GO
    assert gate.table("a.t2").reconciliation_passed is True
    assert gate.table("a.t2").ready is False


def test_gate_no_go_when_any_check_fails():
    failing = passing("a.t2") + [CheckResult("a.t2", "row_diff", CheckStatus.FAIL, "1 diff")]
    gate = decide(
        {"a.t1": ddl("a.t1", Status.CONVERTED), "a.t2": ddl("a.t2", Status.CONVERTED)},
        {"a.t1": passing("a.t1"), "a.t2": failing},
    )
    assert gate.decision == Decision.NO_GO
    assert any("a.t2" in r and "row_diff" in r and "1 diff" in r for r in gate.reasons)


def test_gate_no_go_when_a_table_has_no_results():
    gate = decide({"a.t1": ddl("a.t1", Status.CONVERTED)}, {})
    assert gate.decision == Decision.NO_GO
    assert gate.table("a.t1").ready is False


def test_gate_no_go_with_no_tables():
    assert decide({}, {}).decision == Decision.NO_GO


def test_row_diff_reports_values_as_exported(results):
    # Compared numerically, but shown as loaded: 100.00, not 100.
    detail = by_check(results["sales.orders"])["row_diff"].detail
    assert "order_amount '100.01' vs '100.00'" in detail
