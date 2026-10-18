import csv
import json
import shutil
from pathlib import Path

import pytest

from adapters import DataLoadError, DuckDBAdapter
from table_config import ConfigError, load_config

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "tables.json"


def csv_row_count(path: Path) -> int:
    with path.open(newline="") as f:
        return sum(1 for _ in csv.reader(f)) - 1  # minus header


@pytest.fixture(scope="module")
def config():
    return load_config(CONFIG_PATH)


@pytest.fixture(scope="module")
def loaded(config):
    with DuckDBAdapter() as db:
        db.load_tables(config)
        yield db


# --- Config ----------------------------------------------------------------

def test_config_reads_table_list(config):
    assert [t.name for t in config.tables] == ["sales.orders", "sales.customer", "sales.contract"]

    orders = config.tables[0]
    assert orders.key_columns == ["order_id"]
    assert orders.numeric_columns == ["order_amount"]
    assert orders.load_name == "sales_orders"
    assert orders.ddl == ROOT / "sample-data" / "ddl" / "sales_orders.sql"
    assert config.source_dir == ROOT / "sample-data" / "source"
    assert config.target_dir == ROOT / "sample-data" / "target"


def test_config_missing_file_gives_clear_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.json")


def test_config_missing_field_names_the_field(tmp_path):
    bad = json.loads(CONFIG_PATH.read_text())
    del bad["tables"][1]["key_columns"]
    path = tmp_path / "tables.json"
    path.write_text(json.dumps(bad))

    with pytest.raises(ConfigError, match=r"sales\.customer.*key_columns"):
        load_config(path)


# --- Loading ---------------------------------------------------------------

def test_tables_load_into_source_and_target_schemas(loaded, config):
    for table in config.tables:
        for schema in ("source", "target"):
            assert loaded.table_exists(schema, table.load_name), f"{schema}.{table.load_name}"


@pytest.mark.parametrize("schema", ["source", "target"])
def test_row_counts_match_csvs(loaded, config, schema):
    data_dir = config.source_dir if schema == "source" else config.target_dir
    for table in config.tables:
        expected = csv_row_count(data_dir / table.csv)
        assert loaded.row_count(schema, table.load_name) == expected, table.name


def test_known_row_counts(loaded):
    # The target SET table has one extra duplicate row that Teradata rejected.
    assert loaded.row_count("source", "sales_customer") == 20
    assert loaded.row_count("target", "sales_customer") == 21
    assert loaded.row_count("source", "sales_orders") == 20
    assert loaded.row_count("target", "sales_orders") == 20


def test_load_keeps_null_distinct_from_empty_string(loaded):
    sql = "SELECT status_code FROM {}.sales_orders WHERE order_id = '1007'"
    assert loaded.query(sql.format("source")) == [(None,)]
    assert loaded.query(sql.format("target")) == [("",)]


def test_load_keeps_amounts_exactly_as_exported(loaded):
    sql = "SELECT order_amount FROM {}.sales_orders WHERE order_id = '1012'"
    assert loaded.query(sql.format("source")) == [("100.01",)]
    assert loaded.query(sql.format("target")) == [("100.00",)]


def test_missing_csv_gives_clear_error(tmp_path):
    for side in ("source", "target"):
        shutil.copytree(ROOT / "sample-data" / side, tmp_path / side)
    (tmp_path / "target" / "sales_orders.csv").unlink()

    cfg = json.loads(CONFIG_PATH.read_text())
    cfg["source_dir"] = str(tmp_path / "source")
    cfg["target_dir"] = str(tmp_path / "target")
    path = tmp_path / "tables.json"
    path.write_text(json.dumps(cfg))

    with DuckDBAdapter() as db:
        with pytest.raises(DataLoadError) as err:
            db.load_tables(load_config(path))

    message = str(err.value)
    assert "sales.orders" in message
    assert "target" in message
    assert "sales_orders.csv" in message
