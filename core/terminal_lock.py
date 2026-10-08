"""Process-local/file-system exclusion for MBT launches of one MT5 terminal."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

from .connection import reports_dir


@contextmanager
def terminal_lock(terminal: str, data_dir: str, *, commission_recovery: bool = False):
    """Exclude launches, and block unrestored commission state under the OS lock.

    commission_recovery=True is solely for explicit profile recovery; its caller
    must not launch a terminal while holding that recovery bypass.
    """
    if not isinstance(commission_recovery, bool):
        raise ValueError('commission_recovery must be a boolean')
    identity = (os.path.normcase(str(Path(terminal).resolve())) + "|" +
                os.path.normcase(str(Path(data_dir).resolve())))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    root = Path(reports_dir()) / "optimization" / "locks"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (key + ".lock")
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise RuntimeError("Another MBT operation owns this MT5 terminal") from exc
            try:
                if not commission_recovery:
                    from .commission_profile import assert_no_pending_commission
                    assert_no_pending_commission(Path(data_dir))
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise RuntimeError("Another MBT operation owns this MT5 terminal") from exc
            try:
                if not commission_recovery:
                    from .commission_profile import assert_no_pending_commission
                    assert_no_pending_commission(Path(data_dir))
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
