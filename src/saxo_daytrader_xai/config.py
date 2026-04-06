from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def load_dotenv(dotenv_path: str | os.PathLike[str] = ".env") -> None:
    path = Path(dotenv_path).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _resolve_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _resolve_env(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_env(item) for item in value]
    if isinstance(value, str) and value.startswith("ENV:"):
        return os.getenv(value.split(":", 1)[1], "")
    return value


def load_config(config_path: str | os.PathLike[str] = "config.yaml") -> dict[str, Any]:
    load_dotenv()
    path = Path(config_path).expanduser().resolve()
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle)

    config = _resolve_env(config)
    portfolio_cfg = config.setdefault("portfolio", {})
    portfolio_cfg["database_path"] = str((path.parent / portfolio_cfg.get("database_path", "ledger.db")).resolve())
    portfolio_cfg["source_csv"] = str((path.parent / portfolio_cfg["source_csv"]).resolve())
    config["_meta"] = {
        "config_path": str(path),
        "config_dir": str(path.parent),
    }
    return config
