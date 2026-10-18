"""Combine DDL conversion and reconciliation results into a cutover decision.

A table is ready only when its DDL converted (warnings are reported but do not
block) AND every reconciliation check passed. DDL and data are reported
separately, so a table can match perfectly on data and still be blocked on a
DDL that needs review. The overall decision is GO only if every table is
ready; anything missing or unknown counts against readiness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .ddl_converter import ConversionResult, Status
from .reconcile import CheckResult, CheckStatus

READY_DDL = {Status.CONVERTED, Status.CONVERTED_WITH_WARNING}


class Decision(str, Enum):
    GO = "GO"
    NO_GO = "NO-GO"


@dataclass
class TableSummary:
    table: str
    ddl_status: Status | None
    ddl_reasons: list[str]
    checks: list[CheckResult]
    reconciliation_passed: bool
    ready: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class GateResult:
    decision: Decision
    reasons: list[str]
    tables: list[TableSummary]

    def table(self, name: str) -> TableSummary:
        return next(t for t in self.tables if t.table == name)


def decide(
    ddl_results: dict[str, ConversionResult],
    reconciliation: dict[str, list[CheckResult]],
) -> GateResult:
    names = list(dict.fromkeys([*ddl_results, *reconciliation]))
    tables = [_summarise(n, ddl_results.get(n), reconciliation.get(n)) for n in names]

    if not tables:
        return GateResult(Decision.NO_GO, ["No tables were validated"], [])

    reasons = [f"{t.table}: {b}" for t in tables for b in t.blockers]
    if reasons:
        return GateResult(Decision.NO_GO, reasons, tables)
    return GateResult(Decision.GO, [f"All {len(tables)} tables passed DDL and reconciliation checks"], tables)


def _summarise(name: str, ddl: ConversionResult | None, checks: list[CheckResult] | None) -> TableSummary:
    blockers: list[str] = []
    warnings: list[str] = []

    if ddl is None:
        blockers.append("no DDL conversion result")
    elif ddl.status not in READY_DDL:
        blockers.append(f"DDL {ddl.status.value}: {'; '.join(ddl.reasons) or 'no reason given'}")
    elif ddl.status == Status.CONVERTED_WITH_WARNING:
        warnings.extend(ddl.reasons)

    checks = checks or []
    if not checks:
        blockers.append("no reconciliation results")
    for c in checks:
        if c.status != CheckStatus.PASS:
            blockers.append(f"{c.check} {c.status.value}: {c.detail}")
    reconciliation_passed = bool(checks) and all(c.status == CheckStatus.PASS for c in checks)

    return TableSummary(
        table=name,
        ddl_status=ddl.status if ddl else None,
        ddl_reasons=list(ddl.reasons) if ddl else [],
        checks=checks,
        reconciliation_passed=reconciliation_passed,
        ready=not blockers,
        blockers=blockers,
        warnings=warnings,
    )
