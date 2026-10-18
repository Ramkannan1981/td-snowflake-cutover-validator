"""Read the list of tables to validate from config/tables.json.

Relative paths in the config resolve against the config file's own directory,
so the tool behaves the same whatever directory it is run from.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class TableConfig:
    name: str                   # Teradata name, e.g. "sales.orders"
    ddl: Path
    csv: str                    # file name inside source_dir / target_dir
    key_columns: list[str]
    numeric_columns: list[str]
    pii_columns: list[str] = field(default_factory=list)  # values masked in all output

    @property
    def load_name(self) -> str:
        """Table name inside the source/target schemas, e.g. "sales_orders"."""
        return self.name.replace(".", "_")


@dataclass(frozen=True)
class ValidationConfig:
    source_dir: Path
    target_dir: Path
    tables: list[TableConfig]


def load_config(path: str | Path) -> ValidationConfig:
    path = Path(path).resolve()
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"Config file {path} is not valid JSON: {e}") from e

    base = path.parent
    tables_raw = _require(raw, "tables", list, "config")
    if not tables_raw:
        raise ConfigError(f"Config {path} lists no tables")

    tables = [_parse_table(t, i, base) for i, t in enumerate(tables_raw)]
    names = [t.name for t in tables]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"Tables listed more than once: {', '.join(duplicates)}")

    return ValidationConfig(
        source_dir=_resolve(base, _require(raw, "source_dir", str, "config")),
        target_dir=_resolve(base, _require(raw, "target_dir", str, "config")),
        tables=tables,
    )


def _parse_table(raw, index: int, base: Path) -> TableConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"tables[{index}] must be an object")
    where = f"table '{raw.get('name', f'tables[{index}]')}'"

    name = _require(raw, "name", str, where)
    for part in name.split("."):
        _check_identifier(part, where)

    key_columns = _require(raw, "key_columns", list, where)
    if not key_columns:
        raise ConfigError(f"{where}: key_columns must list at least one column")
    numeric_columns = _require(raw, "numeric_columns", list, where)
    pii_columns = _require(raw, "pii_columns", list, where) if "pii_columns" in raw else []
    for column in key_columns + numeric_columns + pii_columns:
        _check_identifier(column, where)

    return TableConfig(
        name=name,
        ddl=_resolve(base, _require(raw, "ddl", str, where)),
        csv=_require(raw, "csv", str, where),
        key_columns=key_columns,
        numeric_columns=numeric_columns,
        pii_columns=pii_columns,
    )


def _require(raw: dict, field: str, kind: type, where: str):
    if field not in raw:
        raise ConfigError(f"{where}: missing required field '{field}'")
    value = raw[field]
    if not isinstance(value, kind):
        raise ConfigError(f"{where}: field '{field}' must be a {kind.__name__}")
    return value


def _check_identifier(value, where: str) -> None:
    # Names are interpolated into SQL, so only plain identifiers are accepted.
    if not isinstance(value, str) or not _IDENTIFIER_RE.match(value):
        raise ConfigError(f"{where}: '{value}' is not a plain SQL identifier")


def _resolve(base: Path, value: str) -> Path:
    return (base / value).resolve()
