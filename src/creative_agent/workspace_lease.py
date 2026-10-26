"""OS-owned workspace lock. Stale PID files never grant or deny ownership."""
import os
from pathlib import Path


class WorkspaceLease:
    def __init__(self, root):
        self.root = Path(root)
        self.handle = None

    def acquire(self):
        if self.handle is not None: return
        self.root.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os,'O_NOFOLLOW',0)
        descriptor = os.open(self.root/'.workspace.lock',flags,0o600)
        handle = os.fdopen(descriptor,'r+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0: handle.write(b' ');handle.flush()
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ValueError('这个工作空间已经打开，请切回原窗口；不要同时启动多个版本。') from exc
        self.handle = handle

    def release(self):
        if self.handle is None: return
        handle,self.handle = self.handle,None
        try:
            if os.name == 'nt':
                import msvcrt
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally: handle.close()
