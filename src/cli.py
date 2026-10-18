"""Cutover readiness check: python -m src.cli --config config/tables.json

Exit codes: 0 = GO, 1 = NO-GO, 2 = input or config error.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .adapters import DataLoadError, DuckDBAdapter
from .ddl_converter import ConversionResult, Status, convert_file
from .gate import Decision, GateResult, decide
from .reconcile import DEFAULT_TOLERANCE, reconcile_all
from .table_config import ConfigError, ValidationConfig, load_config

EXIT_GO, EXIT_NO_GO, EXIT_INPUT_ERROR = 0, 1, 2

log = logging.getLogger(__name__)


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: str):
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


def new_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return f"{now:%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_id = new_run_id()
    handler = _setup_logging(run_id, args.verbose)
    try:
        return _run(run_id, args)
    except (ConfigError, DataLoadError) as e:
        log.error("Input error: %s", e)
        return EXIT_INPUT_ERROR
    finally:
        logging.getLogger().removeHandler(handler)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Check Teradata to Snowflake cutover readiness (GO / NO-GO).",
    )
    parser.add_argument("--config", required=True, help="path to tables.json")
    parser.add_argument("--reports-dir", default="reports", help="where to write <run_id>.json")
    parser.add_argument(
        "--tolerance", type=float, default=DEFAULT_TOLERANCE,
        help=f"allowed absolute difference in numeric sums (default {DEFAULT_TOLERANCE})",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every check, not just failures")
    args = parser.parse_args(argv)
    if args.tolerance < 0:
        parser.error("--tolerance must not be negative")
    return args


def _setup_logging(run_id: str, verbose: bool) -> logging.Handler:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_RunIdFilter(run_id))
    # UTC timestamps, to line up with the UTC run_id.
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s run_id=%(run_id)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # Per-check PASS lines are INFO; keep them out of the default output.
    logging.getLogger("src.reconcile").setLevel(logging.INFO if verbose else logging.WARNING)
    return handler


def _run(run_id: str, args) -> int:
    log.info("Starting cutover check, config=%s", args.config)
    config = load_config(args.config)
    log.info("Loaded config: %d table(s)", len(config.tables))

    ddl_results = _convert_ddl(config)

    with DuckDBAdapter() as db:
        db.load_tables(config)
        log.info("Loaded source and target data")
        reconciliation = reconcile_all(db, config, args.tolerance)

    gate = decide(ddl_results, reconciliation)
    report_path = _write_report(run_id, args, config, ddl_results, gate)
    log.info("Decision %s, report written to %s", gate.decision.value, report_path)

    print(_summary(run_id, gate, report_path))
    return EXIT_GO if gate.decision == Decision.GO else EXIT_NO_GO


def _convert_ddl(config: ValidationConfig) -> dict[str, ConversionResult]:
    missing = [f"  {t.name}: {t.ddl}" for t in config.tables if not t.ddl.is_file()]
    if missing:
        raise ConfigError("Missing DDL file(s):\n" + "\n".join(missing))

    results = {}
    for table in config.tables:
        result = convert_file(table.ddl)
        results[table.name] = result
        level = logging.INFO if result.status == Status.CONVERTED else logging.WARNING
        log.log(level, "DDL %s: %s", table.name, result.status.value)
    return results


def _write_report(run_id, args, config, ddl_results, gate: GateResult) -> Path:
    report = {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": str(Path(args.config).resolve()),
        "tolerance": args.tolerance,
        "decision": gate.decision.value,
        "reasons": gate.reasons,
        "tables": [
            {**asdict(t), "snowflake_sql": ddl_results[t.table].snowflake_sql if t.table in ddl_results else None}
            for t in gate.tables
        ],
    }
    reports_dir = Path(args.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{run_id}.json"
    path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return path


def _summary(run_id: str, gate: GateResult, report_path: Path) -> str:
    lines = [
        "",
        f"Cutover readiness  run_id={run_id}",
        f"Decision: {gate.decision.value}",
        "",
    ]
    width = max([len(t.table) for t in gate.tables] + [5])
    lines.append(f"  {'TABLE':<{width}}  {'DDL':<22}  {'RECONCILIATION':<14}  READY")
    for t in gate.tables:
        ddl = t.ddl_status.value if t.ddl_status else "MISSING"
        recon = "PASS" if t.reconciliation_passed else "FAIL"
        lines.append(f"  {t.table:<{width}}  {ddl:<22}  {recon:<14}  {'yes' if t.ready else 'no'}")

    if gate.decision == Decision.NO_GO:
        lines += ["", "Blocking reasons:"]
    else:
        lines += [""]
    lines += [f"  - {r}" for r in gate.reasons]

    warnings = [f"{t.table}: {w}" for t in gate.tables for w in t.warnings]
    if warnings:
        lines += ["", "Warnings (not blocking):"] + [f"  - {w}" for w in warnings]

    lines += ["", f"Report: {report_path}"]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
