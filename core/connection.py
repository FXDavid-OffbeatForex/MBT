"""
MT5 connection + config loading.

Everything is config-driven so the toolkit works for anyone: the user edits
config.yaml, never the code.
"""

import os
from pathlib import Path
import yaml
import MetaTrader5 as mt5

_CONFIG_CACHE = None


def _toolkit_root() -> str:
    """MBT folder (parent of core/)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_config() -> dict:
    """Load config.yaml from the MBT root. Cached after first read."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    path = os.path.join(_toolkit_root(), "config.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(
            "config.yaml not found. Copy config.example.yaml to config.yaml "
            "and set your MT5 path and signal file."
        )

    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    _CONFIG_CACHE = cfg
    return cfg


def connect() -> bool:
    """
    Initialize the MT5 connection using the path in config.yaml.
    Idempotent — safe to call before every operation.
    """
    cfg  = load_config()
    path = (cfg.get("mt5_path") or "").strip()

    # initialize() may launch MT5. Serialize that launch with tester operations;
    # a running data-session remains visible to their busy-terminal check.
    from .tester import _terminal_path, _data_dir
    from .terminal_lock import terminal_lock
    if path and Path(path).resolve() != Path(_terminal_path()).resolve():
        raise RuntimeError("mt5_path and tester.terminal_path must match for a shared MT5 session")
    with terminal_lock(_terminal_path(), _data_dir()):
        ok = mt5.initialize(path=str(Path(_terminal_path()).resolve()))

    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    return True


def signal_file_path() -> str:
    """
    Resolve the signal file location. If config gives an absolute path, use it.
    Otherwise treat it as a bare filename inside the MT5 .../MQL5/Files folder.
    """
    cfg  = load_config()
    name = cfg.get("signal_file", "signals.csv")

    if os.path.isabs(name):
        return name

    connect()
    data_path = mt5.terminal_info().data_path
    return os.path.join(data_path, "MQL5", "Files", name)


def reports_dir() -> str:
    cfg = load_config()
    d   = cfg.get("reports_dir", "reports")
    if not os.path.isabs(d):
        d = os.path.join(_toolkit_root(), d)
    os.makedirs(d, exist_ok=True)
    return d
