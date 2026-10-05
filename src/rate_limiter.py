"""
Cross-process, cross-thread shared rolling window rate limiter.
Guarantees a strict maximum of N requests per rolling W-second window
across concurrent processes, threads, and tasks.
"""

import json
import logging
import os
import sys
import tempfile
import threading
import time
from typing import List, Optional

logger = logging.getLogger(__name__)

# Cross-platform file locking support
try:
    import msvcrt

    def _lock_file(f):
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock_file(f):
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

except ImportError:
    try:
        import fcntl

        def _lock_file(f):
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)

        def _unlock_file(f):
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    except ImportError:
        def _lock_file(f):
            pass

        def _unlock_file(f):
            pass


class SharedRollingRateLimiter:
    """
    Shared rolling window rate limiter using file-backed synchronization.
    Enforces a strict upper bound of `max_requests` in any rolling `window_seconds`.
    Safe across concurrent threads and processes.
    """

    def __init__(
        self,
        max_requests: int = 4,
        window_seconds: float = 60.0,
        state_file: Optional[str] = None,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._thread_lock = threading.Lock()

        if state_file:
            self.state_file = state_file
        else:
            base_dir = os.path.join(tempfile.gettempdir(), "job_bucket_locks")
            os.makedirs(base_dir, exist_ok=True)
            self.state_file = os.path.join(base_dir, "gemini_4rpm_rate_limiter.json")

        self.lock_file = self.state_file + ".lock"
        os.makedirs(os.path.dirname(os.path.abspath(self.state_file)), exist_ok=True)

    def _read_timestamps(self) -> List[float]:
        """Reads timestamp history from the shared state file."""
        if not os.path.exists(self.state_file):
            return []
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [float(x) for x in data if isinstance(x, (int, float))]
        except Exception:
            pass
        return []

    def _write_timestamps(self, timestamps: List[float]) -> None:
        """Writes timestamp history to the shared state file atomically."""
        temp_file = self.state_file + f".tmp.{os.getpid()}"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(timestamps, f)
            if os.path.exists(self.state_file):
                try:
                    os.replace(temp_file, self.state_file)
                except OSError:
                    os.remove(self.state_file)
                    os.rename(temp_file, self.state_file)
            else:
                os.rename(temp_file, self.state_file)
        except Exception as e:
            logger.warning("Could not write rate limiter timestamps: %s", e)
            if os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass

    def acquire(self, timeout: float = 120.0) -> float:
        """
        Blocks until a request slot is available in the rolling window.
        Returns the duration waited in seconds.
        Raises TimeoutError if slot cannot be acquired within timeout.
        """
        start_wait = time.time()

        while True:
            if time.time() - start_wait > timeout:
                raise TimeoutError(
                    f"Shared rate limiter timed out waiting for 60-second window slot (timeout={timeout}s)."
                )

            with self._thread_lock:
                lock_fd = None
                try:
                    lock_fd = open(self.lock_file, "a+", encoding="utf-8")
                    _lock_file(lock_fd)

                    now = time.time()
                    timestamps = self._read_timestamps()
                    # Filter timestamps to keep only those within the rolling window
                    active = [t for t in timestamps if (now - t) < self.window_seconds]

                    if len(active) < self.max_requests:
                        # Slot available
                        active.append(now)
                        self._write_timestamps(active)
                        _unlock_file(lock_fd)
                        lock_fd.close()
                        return time.time() - start_wait
                    else:
                        # Window full: calculate required wait time
                        oldest = min(active)
                        sleep_seconds = (oldest + self.window_seconds) - now + 0.05
                        _unlock_file(lock_fd)
                        lock_fd.close()
                except Exception:
                    if lock_fd:
                        try:
                            _unlock_file(lock_fd)
                            lock_fd.close()
                        except Exception:
                            pass
                    sleep_seconds = 0.5

            if sleep_seconds > 0:
                time.sleep(min(sleep_seconds, 2.0))

    def get_active_count(self) -> int:
        """Returns the number of requests currently in the rolling window."""
        with self._thread_lock:
            try:
                with open(self.lock_file, "a+", encoding="utf-8") as lock_fd:
                    _lock_file(lock_fd)
                    now = time.time()
                    timestamps = self._read_timestamps()
                    active = [t for t in timestamps if (now - t) < self.window_seconds]
                    _unlock_file(lock_fd)
                return len(active)
            except Exception:
                return 0

    def reset(self) -> None:
        """Resets the state file (used for isolated tests)."""
        with self._thread_lock:
            try:
                if os.path.exists(self.state_file):
                    os.remove(self.state_file)
            except Exception:
                pass
