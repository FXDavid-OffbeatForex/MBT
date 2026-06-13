"""MBT ASCII banner — sibling to Offbeat Engine, in a restrained grey.

Same block-letter + waveform family as the engine, but understated: MBT is
the open, no-nonsense backtester, so it wears grey where the engine wears
cyan. No dependencies (usable before pip install). Colour auto-detects, so
captured/non-TTY output stays clean plain text.

Preview:  python banner.py
"""

import os
import sys

_LOGO = [
    r"  ███╗   ███╗██████╗ ████████╗",
    r"  ████╗ ████║██╔══██╗╚══██╔══╝",
    r"  ██╔████╔██║██████╔╝   ██║   ",
    r"  ██║╚██╔╝██║██╔══██╗   ██║   ",
    r"  ██║ ╚═╝ ██║██████╔╝   ██║   ",
    r"  ╚═╝     ╚═╝╚═════╝    ╚═╝   ",
]
_TAG = "   MT5 Backtest Toolkit · replay real signals on real bars"
_WAVE = "   ╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴╴"

# subtle light→mid grey vertical gradient
_SHADES = [253, 251, 249, 247, 245, 243]


def _plain() -> str:
    return "\n" + "\n".join(_LOGO + ["", _TAG, _WAVE]) + "\n"


def _ansi() -> str:
    out = [f"\033[38;5;{s}m{line}\033[0m" for s, line in zip(_SHADES, _LOGO)]
    out.append("")
    out.append(f"\033[38;5;245m{_TAG}\033[0m")
    out.append(f"\033[38;5;240m{_WAVE}\033[0m")
    return "\n" + "\n".join(out) + "\n"


def _supports_color(stream) -> bool:
    return (hasattr(stream, "isatty") and stream.isatty()
            and os.environ.get("TERM") not in (None, "dumb")
            and not os.environ.get("NO_COLOR"))


def banner(color=None, stream=None) -> str:
    if color is None:
        color = _supports_color(stream or sys.stderr)
    return _ansi() if color else _plain()


if __name__ == "__main__":
    sys.stdout.write(banner(color=True))
