import json
import re
import shutil
from pathlib import Path

from src.cli import main

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "tables.json"
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}$")


def run(capsys, *args):
    code = main([*args])
    out, err = capsys.readouterr()
    return code, out, err


def write_config(tmp_path: Path, **overrides) -> Path:
    cfg = json.loads(CONFIG.read_text())
    for key in ("source_dir", "target_dir"):
        cfg[key] = str((CONFIG.parent / cfg[key]).resolve())
    for table in cfg["tables"]:
        table["ddl"] = str((CONFIG.parent / table["ddl"]).resolve())
    cfg.update(overrides)
    path = tmp_path / "tables.json"
    path.write_text(json.dumps(cfg))
    return path


def test_sample_data_is_no_go_with_report(tmp_path, capsys):
    code, out, err = run(capsys, "--config", str(CONFIG), "--reports-dir", str(tmp_path))

    assert code == 1
    reports = list(tmp_path.glob("*.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert RUN_ID_RE.match(report["run_id"])
    assert reports[0].stem == report["run_id"]
    assert report["decision"] == "NO-GO"

    contract = next(t for t in report["tables"] if t["table"] == "sales.contract")
    assert contract["ddl_status"] == "NEEDS_REVIEW"
    assert contract["reconciliation_passed"] is True
    assert contract["ready"] is False

    assert "NO-GO" in out
    assert report["run_id"] in out


def test_every_log_line_has_run_id(tmp_path, capsys):
    _, _, err = run(capsys, "--config", str(CONFIG), "--reports-dir", str(tmp_path))
    run_id = next(tmp_path.glob("*.json")).stem
    lines = [l for l in err.splitlines() if l.strip()]
    assert lines
    assert all(f"run_id={run_id}" in l for l in lines), err


def test_go_when_target_matches_and_ddl_converts(tmp_path, capsys):
    shutil.copytree(ROOT / "sample-data" / "source", tmp_path / "source")
    shutil.copytree(ROOT / "sample-data" / "source", tmp_path / "target")
    cfg = json.loads(write_config(tmp_path).read_text())
    cfg["tables"] = [t for t in cfg["tables"] if t["name"] == "sales.orders"]
    config = write_config(
        tmp_path,
        source_dir=str(tmp_path / "source"),
        target_dir=str(tmp_path / "target"),
        tables=cfg["tables"],
    )

    code, out, _ = run(capsys, "--config", str(config), "--reports-dir", str(tmp_path / "reports"))
    assert code == 0
    assert "GO" in out and "NO-GO" not in out


def test_missing_config_exits_2_without_traceback(tmp_path, capsys):
    code, out, err = run(capsys, "--config", str(tmp_path / "missing.json"), "--reports-dir", str(tmp_path))
    assert code == 2
    assert "not found" in err
    assert "Traceback" not in err + out
    assert not list(tmp_path.glob("*.json"))


def test_invalid_json_config_exits_2(tmp_path, capsys):
    bad = tmp_path / "tables.json"
    bad.write_text("{ not json")
    code, _, err = run(capsys, "--config", str(bad), "--reports-dir", str(tmp_path / "reports"))
    assert code == 2
    assert "not valid JSON" in err
    assert "Traceback" not in err


def test_missing_csv_exits_2(tmp_path, capsys):
    config = write_config(tmp_path, target_dir=str(tmp_path / "nowhere"))
    code, _, err = run(capsys, "--config", str(config), "--reports-dir", str(tmp_path / "reports"))
    assert code == 2
    assert "Missing CSV" in err
    assert "Traceback" not in err


def test_missing_ddl_file_exits_2(tmp_path, capsys):
    cfg = json.loads(write_config(tmp_path).read_text())
    cfg["tables"][0]["ddl"] = str(tmp_path / "gone.sql")
    config = write_config(tmp_path, tables=cfg["tables"])
    code, _, err = run(capsys, "--config", str(config), "--reports-dir", str(tmp_path / "reports"))
    assert code == 2
    assert "gone.sql" in err
    assert "Traceback" not in err


def test_pii_values_never_in_report_or_output(tmp_path, capsys):
    code, out, err = run(capsys, "--config", str(CONFIG), "--reports-dir", str(tmp_path))
    assert code == 1
    report = next(tmp_path.glob("*.json")).read_text()

    for text in (report, out, err):
        assert "sydney harbour traders" not in text.lower()
    # The difference is still reported, in masked form.
    assert re.search(r"customer_name S\*\*\*#[0-9a-f]{4} vs S\*\*\*#[0-9a-f]{4}", report)


def test_malformed_csv_exits_2_without_leaking_row_values(tmp_path, capsys):
    shutil.copytree(ROOT / "sample-data" / "source", tmp_path / "source")
    shutil.copytree(ROOT / "sample-data" / "target", tmp_path / "target")
    bad = tmp_path / "target" / "sales_customer.csv"
    bad.write_text(bad.read_text() + "99,Zelda Fakeperson,NS,2024-01-01,unexpected\n")
    config = write_config(
        tmp_path, source_dir=str(tmp_path / "source"), target_dir=str(tmp_path / "target")
    )

    code, out, err = run(capsys, "--config", str(config), "--reports-dir", str(tmp_path / "reports"))

    assert code == 2
    assert "Fakeperson" not in out + err
    assert "Traceback" not in err
    errors = [l for l in err.splitlines() if " ERROR " in l]
    assert len(errors) == 1
    assert "sales_customer.csv" in errors[0] and "line 23" in errors[0]
    assert not (tmp_path / "reports").exists()
