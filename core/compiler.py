"""
MQL5 compile wrapper.

Compiles an EA/indicator with MetaEditor and returns the result as structured
data (errors / warnings / messages) instead of a log dump — the first feedback
step of a build-test-fix loop. MetaEditor compiles independently of any running
terminal, so this works whether or not the terminal is open.

Cross-platform: off-Windows a launcher ("wine") from config is prepended, same
as the tester runner.
"""

import os
import re
import sys
import subprocess

from .tester import _terminal_path, _data_dir, _tester_cfg

# MQL5 source subdirs searched by name (Include holds .mqh headers).
_MQL5_SUBDIRS = ("Experts", "Indicators", "Scripts", "Include")


def _metaeditor_path() -> str:
    """MetaEditor64.exe — config tester.metaeditor_path wins; otherwise it sits
    next to terminal64.exe in the install dir."""
    p = (_tester_cfg().get("metaeditor_path") or "").strip()
    if p:
        return p
    return os.path.join(os.path.dirname(_terminal_path()), "MetaEditor64.exe")


def _with_launcher(cmd: list) -> list:
    launcher = (_tester_cfg().get("launcher") or "").strip()
    if not launcher and sys.platform != "win32":
        launcher = "wine"
    return launcher.split() + cmd if launcher else cmd


def _resolve_source(source: str, data: str) -> str:
    """Find the .mq5/.mqh under the data dir's MQL5 tree (or use an abs path)."""
    if os.path.isabs(source) and os.path.isfile(source):
        return source
    name = source if source.lower().endswith((".mq5", ".mqh")) else source + ".mq5"
    cand = os.path.join(data, name)
    if os.path.isfile(cand):
        return cand
    for sub in _MQL5_SUBDIRS:
        cand = os.path.join(data, "MQL5", sub, name)
        if os.path.isfile(cand):
            return cand
    return ""


def _read_text(path: str) -> str:
    """Read a log that may be utf-16 (MetaEditor default), utf-8 or cp1252.
    utf-16 is trusted ONLY when a BOM is present: Python's BOM-less utf-16 codec
    silently decodes any even-length byte stream, so a utf-8/cp1252 log would turn
    into garbage that still looks non-empty — which would let a failed compile read
    back ok=True against a stale .ex5. With a BOM we honour utf-16; otherwise we
    decode as utf-8 then cp1252 (cp1252 maps every byte, so it never fails)."""
    try:
        data = open(path, "rb").read()
    except OSError:
        return ""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):       # utf-16 LE/BE BOM
        try:
            return data.decode("utf-16")
        except (UnicodeDecodeError, UnicodeError):
            pass
    for enc in ("utf-8", "cp1252"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return data.decode("cp1252", errors="ignore")   # cp1252 maps all bytes; last resort


def _parse_log(raw: str):
    errors = warnings = 0
    # Prefer the single authoritative "Result: N errors, M warnings" line so a
    # stray "warning" word in a message can't skew the count.
    m = re.search(r"Result:\s*(\d+)\s*errors?,\s*(\d+)\s*warnings?", raw)
    if m:
        errors, warnings = int(m.group(1)), int(m.group(2))
    else:
        me = re.search(r"Result:\s*(\d+)\s*error", raw)
        if me:
            errors = int(me.group(1))
        mw = re.search(r"Result:.*?(\d+)\s*warning", raw)
        if mw:
            warnings = int(mw.group(1))
    msgs = []
    for line in raw.splitlines():
        mm = re.search(r"(.+?)\((\d+),(\d+)\)\s*:\s*(error|warning)\s+(\w+)?:?\s*(.*)", line)
        if mm:
            msgs.append({
                "severity": mm.group(4),
                "file": os.path.basename(mm.group(1).strip()),
                "line": int(mm.group(2)),
                "col": int(mm.group(3)),
                "code": (mm.group(5) or "").strip(),
                "text": mm.group(6).strip(),
            })
    return errors, warnings, msgs


def compile_mql5(source: str, timeout: int = 180) -> dict:
    """Compile one .mq5/.mqh and return structured results.

    source: name (e.g. 'RegimePlusPro_Gold_EA'), a path under the MQL5 tree, or
            an absolute path. .mq5 assumed if no extension.
    """
    data = _data_dir()
    src = _resolve_source(source, data)
    if not src:
        return {"error": f"Source not found for '{source}' under {data}/MQL5/(Experts|Indicators|Scripts)"}

    me = _metaeditor_path()
    # Only stat the path when running natively. With a launcher (wine) the path is
    # a Windows path the host FS can't see — let the launcher resolve it.
    using_launcher = bool((_tester_cfg().get("launcher") or "").strip()) or sys.platform != "win32"
    if not using_launcher and not os.path.isfile(me):
        return {"error": f"MetaEditor not found at {me} (set tester.metaeditor_path in config.yaml)"}

    # Run with cwd=data dir and tree-relative args so paths resolve under Wine too.
    try:
        rel = os.path.relpath(src, data)
    except ValueError:
        # src on a different drive than data (Windows) — relpath can't bridge it.
        return {"error": f"Source {src} is not under the MT5 data dir {data}; "
                         f"put the .mq5 in MQL5/Experts (or Indicators/Scripts)."}
    logrel = os.path.splitext(rel)[0] + "_compile.log"
    loghost = os.path.join(data, logrel)
    # Clear the previous log so a stale one (from an earlier successful build)
    # can't be read back as this compile's result if MetaEditor fails to launch.
    if os.path.exists(loghost):
        try: os.remove(loghost)
        except OSError: pass
        if os.path.exists(loghost):
            return {"error": f"Could not clear the previous compile log at {loghost} "
                             f"(file locked?). Close anything holding it and retry — "
                             f"leaving it risks reporting a stale build as this one."}

    cmd = _with_launcher([me, "/compile:" + rel, "/log:" + logrel])
    timed_out = False
    try:
        subprocess.run(cmd, cwd=data, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        timed_out = True

    raw = _read_text(loghost)
    errors, warnings, msgs = _parse_log(raw)
    ok  = (errors == 0 and not timed_out and bool(raw))
    ex5 = os.path.splitext(src)[0] + ".ex5"
    # Only report the .ex5 on a clean compile — a stale one from a prior build may
    # still be on disk even though THIS compile failed.
    ex5_out = ex5 if (ok and os.path.isfile(ex5)) else None

    return {
        "source": src,
        "ok": ok,
        "errors": errors,
        "warnings": warnings,
        "messages": msgs,                       # [{severity,file,line,col,code,text}]
        "ex5": ex5_out,
        "log": loghost if raw else None,
        "timed_out": timed_out,
        "error": None if raw else "MetaEditor produced no log — check the launcher/MetaEditor path.",
    }
