"""Linux worker lifetime follows its UI parent, including abrupt UI termination."""
import ctypes
import os
import signal
import sys


def follow_parent():
    if not sys.platform.startswith('linux'):
        return
    parent = os.getppid()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        raise OSError(ctypes.get_errno(), 'Cannot link worker lifetime to parent')
    if parent == 1 or os.getppid() != parent:
        raise SystemExit(0)
