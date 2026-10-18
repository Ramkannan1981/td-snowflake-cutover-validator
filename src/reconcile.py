"""Reconcile each configured table between the source and target schemas.

Every check queries each side separately and compares the results in Python,
so no check depends on source and target being in the same engine (see the
note in adapters.py). The row-level diff pulls whole rows, which is fine for
this slice's sample data; at scale it would compare per-key row hashes from
each side and only fetch rows for the keys that differ.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum

from .adapters import SOURCE_SCHEMA, TARGET_SCHEMA, DatabaseAdapter
from .table_config import ConfigError, TableConfig, ValidationConfig

log = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 0.005
MAX_EXAMPLES = 5


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True)
class CheckResult:
    table: str
    check: str
    status: CheckStatus
    detail: str


def reconcile_all(
    adapter: DatabaseAdapter,
    config: ValidationConfig,
    tolerance: float = DEFAULT_TOLERANCE,
) -> dict[str, list[CheckResult]]:
    return {t.name: reconcile_table(adapter, t, tolerance) for t in config.tables}


def reconcile_table(
    adapter: DatabaseAdapter,
    table: TableConfig,
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[CheckResult]:
    src = f"{SOURCE_SCHEMA}.{table.load_name}"
    tgt = f"{TARGET_SCHEMA}.{table.load_name}"
    src_cols = adapter.columns(SOURCE_SCHEMA, table.load_name)
    tgt_cols = adapter.columns(TARGET_SCHEMA, table.load_name)

    configured = table.key_columns + table.numeric_columns
    missing = [c for c in configured if c not in src_cols]
    if missing:
        raise ConfigError(
            f"table '{table.name}': configured column(s) {', '.join(missing)} not found in source data"
        )

    results = [_check_column_names(table, src_cols, tgt_cols)]
    if results[0].status == CheckStatus.FAIL:
        # The remaining checks compare column by column; with mismatched
        # columns they would only produce noise.
        _log(results)
        return results

    results.append(_check_row_count(adapter, table, src, tgt))
    results.append(_check_duplicate_keys(adapter, table, tgt))
    results.extend(_check_sum(adapter, table, src, tgt, col, tolerance) for col in table.numeric_columns)
    results.append(_check_null_counts(adapter, table, src, tgt, src_cols))
    results.append(_check_row_diff(adapter, table, src, tgt, src_cols))
    _log(results)
    return results


def _log(results: list[CheckResult]) -> None:
    for r in results:
        level = logging.INFO if r.status == CheckStatus.PASS else logging.WARNING
        log.log(level, "%s %s %s: %s", r.table, r.check, r.status.value, r.detail)


# --- Checks ----------------------------------------------------------------

def _check_column_names(table, src_cols, tgt_cols) -> CheckResult:
    if src_cols == tgt_cols:
        return _result(table, "column_names", True, f"{len(src_cols)} columns match")
    only_src = [c for c in src_cols if c not in tgt_cols]
    only_tgt = [c for c in tgt_cols if c not in src_cols]
    parts = []
    if only_src:
        parts.append(f"missing in target: {', '.join(only_src)}")
    if only_tgt:
        parts.append(f"extra in target: {', '.join(only_tgt)}")
    if not parts:
        parts.append("same columns in a different order")
    return _result(table, "column_names", False, "; ".join(parts))


def _check_row_count(adapter, table, src, tgt) -> CheckResult:
    s = adapter.query(f"SELECT COUNT(*) FROM {src}")[0][0]
    t = adapter.query(f"SELECT COUNT(*) FROM {tgt}")[0][0]
    return _result(table, "row_count", s == t, f"source={s}, target={t}")


def _check_duplicate_keys(adapter, table, tgt) -> CheckResult:
    keys = ", ".join(_q(c) for c in table.key_columns)
    rows = adapter.query(
        f"SELECT {keys}, COUNT(*) AS n FROM {tgt} GROUP BY {keys} HAVING COUNT(*) > 1 ORDER BY {keys}"
    )
    if not rows:
        return _result(table, "duplicate_keys", True, "no duplicate keys in target")
    examples = [f"{_fmt_key(table, r[:-1])} ({r[-1]} rows)" for r in rows[:MAX_EXAMPLES]]
    return _result(
        table, "duplicate_keys", False,
        f"{len(rows)} duplicate key(s) in target: {'; '.join(examples)}",
    )


def _check_sum(adapter, table, src, tgt, column, tolerance) -> CheckResult:
    check = f"sum:{column}"
    col = _q(column)
    sql = (
        f"SELECT SUM(TRY_CAST({col} AS DECIMAL(38, 6))), "
        f"COUNT(*) FILTER (WHERE {col} IS NOT NULL AND TRY_CAST({col} AS DECIMAL(38, 6)) IS NULL) "
        f"FROM {{}}"
    )
    s_sum, s_bad = adapter.query(sql.format(src))[0]
    t_sum, t_bad = adapter.query(sql.format(tgt))[0]

    if s_bad or t_bad:
        return _result(
            table, check, False,
            f"non-numeric values: source={s_bad}, target={t_bad}",
        )
    s_sum = s_sum if s_sum is not None else Decimal(0)
    t_sum = t_sum if t_sum is not None else Decimal(0)
    diff = abs(s_sum - t_sum)
    ok = diff <= Decimal(str(tolerance))
    return _result(
        table, check, ok,
        f"source={_fmt_dec(s_sum)}, target={_fmt_dec(t_sum)}, "
        f"diff={_fmt_dec(diff)}, tolerance={tolerance}",
    )


def _check_null_counts(adapter, table, src, tgt, columns) -> CheckResult:
    # Empty strings are counted alongside NULLs: a NULL that became '' (or the
    # reverse) is a real drift, since consumers filter on IS NULL.
    def counts(rel):
        select = ", ".join(
            f"COUNT(*) FILTER (WHERE {_q(c)} IS NULL), COUNT(*) FILTER (WHERE {_q(c)} = '')"
            for c in columns
        )
        row = adapter.query(f"SELECT {select} FROM {rel}")[0]
        return {c: (row[2 * i], row[2 * i + 1]) for i, c in enumerate(columns)}

    s, t = counts(src), counts(tgt)
    mismatches = []
    for c in columns:
        if s[c] == t[c]:
            continue
        msg = f"{c}: source NULL={s[c][0]}, target NULL={t[c][0]}"
        if s[c][1] or t[c][1]:
            msg += f"; source empty string={s[c][1]}, target empty string={t[c][1]}"
        mismatches.append(msg)

    if not mismatches:
        return _result(table, "null_counts", True, f"NULL and empty-string counts match for {len(columns)} columns")
    return _result(table, "null_counts", False, "; ".join(mismatches))


def _check_row_diff(adapter, table, src, tgt, columns) -> CheckResult:
    keys = table.key_columns
    values = [c for c in columns if c not in keys]
    numeric = set(table.numeric_columns)
    select = ", ".join(_q(c) for c in keys + values)

    def rows_by_key(rel):
        # key -> {normalised values: values as loaded}; compare on the former,
        # report the latter so the detail shows what was actually exported.
        grouped = defaultdict(dict)
        for row in adapter.query(f"SELECT {select} FROM {rel}"):
            key, vals = row[: len(keys)], row[len(keys):]
            grouped[key][tuple(_normalise(v, c in numeric) for v, c in zip(vals, values))] = vals
        return grouped

    s, t = rows_by_key(src), rows_by_key(tgt)
    missing = sorted(k for k in s if k not in t)
    extra = sorted(k for k in t if k not in s)
    changed = []
    for key in sorted(k for k in s if k in t):
        if s[key].keys() == t[key].keys():
            continue
        if len(s[key]) == 1 and len(t[key]) == 1:
            ((s_norm, s_raw),), ((t_norm, t_raw),) = s[key].items(), t[key].items()
            cols = [
                f"{c} {_fmt_val(a)} vs {_fmt_val(b)}"
                for c, a, b, sn, tn in zip(values, s_raw, t_raw, s_norm, t_norm) if sn != tn
            ]
            changed.append(f"{_fmt_key(table, key)}: {', '.join(cols)}")
        else:
            changed.append(f"{_fmt_key(table, key)}: differing versions of a duplicated key")

    if not (missing or extra or changed):
        return _result(table, "row_diff", True, f"{len(s)} keys match on all {len(values)} value columns")

    parts = []
    if missing:
        parts.append(f"{len(missing)} key(s) missing in target: "
                     + "; ".join(_fmt_key(table, k) for k in missing[:MAX_EXAMPLES]))
    if extra:
        parts.append(f"{len(extra)} key(s) only in target: "
                     + "; ".join(_fmt_key(table, k) for k in extra[:MAX_EXAMPLES]))
    if changed:
        parts.append(f"{len(changed)} key(s) with different values: " + "; ".join(changed[:MAX_EXAMPLES]))
    return _result(table, "row_diff", False, " | ".join(parts))


# --- Helpers ---------------------------------------------------------------

def _result(table: TableConfig, check: str, ok: bool, detail: str) -> CheckResult:
    return CheckResult(table.name, check, CheckStatus.PASS if ok else CheckStatus.FAIL, detail)


def _normalise(value, is_numeric: bool):
    # Numeric columns compare by value so '100.0' and '100.00' are equal;
    # everything else compares exactly, including case and NULL vs ''.
    if is_numeric and value not in (None, ""):
        try:
            return Decimal(value).normalize()
        except InvalidOperation:
            return value
    return value


def _q(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _fmt_key(table: TableConfig, key) -> str:
    return ", ".join(f"{c}={v}" for c, v in zip(table.key_columns, key))


def _fmt_val(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, Decimal):
        return _fmt_dec(value)
    return repr(value)


def _fmt_dec(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text if text not in ("-0",) else "0"
