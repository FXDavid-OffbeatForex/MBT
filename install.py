"""
MBT installer — one-shot setup.

Run from inside the MBT folder:
    python install.py

It will:
  1. install Python dependencies
  2. create config.yaml from the example if missing
  3. copy SignalLogger.mqh into your MT5 MQL5\Include folder (if it can find it)
  4. print the exact `claude mcp add` command to register the server
"""

import os
import sys
import shutil
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))


def step(msg):
    print(f"\n=== {msg} ===")


def install_deps():
    step("Installing Python dependencies")
    subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                    os.path.join(ROOT, "requirements.txt")], check=False)


def make_config():
    step("Config")
    cfg = os.path.join(ROOT, "config.yaml")
    example = os.path.join(ROOT, "config.example.yaml")
    if os.path.exists(cfg):
        print("config.yaml already exists — leaving it untouched.")
    else:
        shutil.copy(example, cfg)
        print("Created config.yaml from the example. EDIT IT: set mt5_path and signal_file.")


def copy_include():
    step("MQL5 include")
    src = os.path.join(ROOT, "mql5", "SignalLogger.mqh")
    # Best-effort discovery of an MQL5\Include folder
    base = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal")
    if not os.path.isdir(base):
        print("Could not auto-find MetaQuotes terminal folder.")
        print(f"Manually copy {src} into your terminal's MQL5\\Include folder.")
        return

    copied = 0
    for d in os.listdir(base):
        inc = os.path.join(base, d, "MQL5", "Include")
        if os.path.isdir(inc):
            try:
                shutil.copy(src, os.path.join(inc, "SignalLogger.mqh"))
                print(f"Copied SignalLogger.mqh -> {inc}")
                copied += 1
            except Exception as e:
                print(f"Skipped {inc}: {e}")
    if copied == 0:
        print(f"No Include folders found. Manually copy {src} yourself.")


def print_mcp_cmd():
    step("Register the MCP server with Claude Code")
    server = os.path.join(ROOT, "mcp_server.py").replace("\\", "/")
    print("Run this once:\n")
    print(f'    claude mcp add mbt python "{server}"\n')
    print("Then restart Claude Code. Verify with:  claude mcp list")


if __name__ == "__main__":
    install_deps()
    make_config()
    copy_include()
    print_mcp_cmd()
    print("\nDone. Edit config.yaml, attach your indicator (with SignalLogger), "
          "then ask Claude to backtest.")
