"""Cross-process commit/policy barrier, always acquired before state mutexes."""
import os
import threading
from contextlib import contextmanager
from pathlib import Path


class WriteBarrier:
    def __init__(self, path):
        self.path = Path(path)
        self.mutex = threading.RLock()
        self.depth = 0

    @contextmanager
    def hold(self):
        with self.mutex:
            if self.depth:
                self.depth += 1
                try:
                    yield
                finally:
                    self.depth -= 1
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open('a+b') as stream:
                if stream.tell() == 0:
                    stream.write(b'0')
                    stream.flush()
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX)
                self.depth = 1
                try:
                    yield
                finally:
                    self.depth = 0
                    if os.name == 'nt':
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_UN)
