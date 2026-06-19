"""
MBT installer — one-shot setup.

Run from inside the MBT folder:
    python install.py

It will:
  1. install Python dependencies
  2. create config.yaml from the example if missing
  3. copy SignalLogger.mqh into your MT5 MQL5\Include folder (if it can find it)
  4. copy MBT_IndicatorHost.mq5 into MQL5\Experts (for headless run_indicator)
  5. print the exact `claude mcp add` command to register the server
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
    result = subprocess.run([sys.executable, "-m", "pip", "install", "-r",
                             os.path.join(ROOT, "requirements.txt")])
    if result.returncode != 0:
        print("WARNING: pip install failed. Check the error above before continuing.")


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


def copy_host_ea():
    """Copy the headless host EA into MQL5\\Experts so `run_indicator` works.
    Compile it once in MetaEditor (or ask Claude to) before first use."""
    step("MQL5 host EA (for headless run_indicator)")
    src = os.path.join(ROOT, "mql5", "MBT_IndicatorHost.mq5")
    base = os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal")
    if not os.path.isdir(base):
        print(f"Could not auto-find the terminal folder. Manually copy {src} "
              f"into your terminal's MQL5\\Experts folder and compile it.")
        return
    copied = 0
    for d in os.listdir(base):
        exp = os.path.join(base, d, "MQL5", "Experts")
        if os.path.isdir(exp):
            try:
                shutil.copy(src, os.path.join(exp, "MBT_IndicatorHost.mq5"))
                print(f"Copied MBT_IndicatorHost.mq5 -> {exp}")
                copied += 1
            except Exception as e:
                print(f"Skipped {exp}: {e}")
    if copied:
        print("Compile it once (MetaEditor, or ask Claude to 'compile the MBT "
              "indicator host') before the first headless run.")
    else:
        print(f"No Experts folders found. Manually copy {src} yourself.")


def print_mcp_cmd():
    step("Register the MCP server with Claude Code")
    server = os.path.join(ROOT, "mcp_server.py").replace("\\", "/")
    print("Run this once:\n")
    print(f'    claude mcp add mbt python "{server}"\n')
    print("Then restart Claude Code. Verify with:  claude mcp list")


if __name__ == "__main__":
    from banner import banner
    sys.stdout.write(banner(stream=sys.stdout))
    install_deps()
    make_config()
    copy_include()
    copy_host_ea()
    print_mcp_cmd()
    print("\nDone. Edit config.yaml, then ask Claude to run your indicator "
          "(with SignalLogger) and backtest it — no chart needed.")
