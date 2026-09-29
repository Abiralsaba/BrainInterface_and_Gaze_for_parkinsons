"""Non-blocking, latest-command-wins transport for the ESP32 rover."""

from __future__ import annotations

import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple


VALID_THROTTLE = {"STOP", "FORWARD", "BACKWARD"}
VALID_STEERING = {"LEFT", "CENTER", "RIGHT"}


class CarCommandSender:
    """Send commands without blocking camera inference.

    Only one request is allowed in flight. If vision produces several commands
    while Wi-Fi is slow, intermediate commands are discarded and only the
    newest state is sent next.
    """

    def __init__(self, base_url: str, timeout: float = 0.15):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.last_error: Optional[str] = None
        self._condition = threading.Condition()
        self._pending: Optional[Tuple[str, str, int]] = None
        self._generation = 0
        self._completed_generation = 0
        self._closing = False
        self._last_error_report_at = 0.0
        self._thread = threading.Thread(
            target=self._run,
            name="car-command-sender",
            daemon=True,
        )
        self._thread.start()

    def send(self, throttle: str, steering: str) -> int:
        throttle = throttle.upper()
        steering = steering.upper()
        if throttle not in VALID_THROTTLE:
            raise ValueError(f"Invalid throttle command: {throttle}")
        if steering not in VALID_STEERING:
            raise ValueError(f"Invalid steering command: {steering}")

        with self._condition:
            if self._closing:
                return self._generation
            self._generation += 1
            generation = self._generation
            self._pending = (throttle, steering, generation)
            self._condition.notify()
            return generation

    def close(self, stop_timeout: float = 0.75) -> None:
        """Queue STOP after earlier in-flight work and wait briefly for it."""
        stop_generation = self.send("STOP", "CENTER")
        deadline = time.monotonic() + stop_timeout

        with self._condition:
            while self._completed_generation < stop_generation:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)
            self._closing = True
            self._condition.notify()

        self._thread.join(timeout=self.timeout + 0.1)

    def _run(self) -> None:
        while True:
            with self._condition:
                while self._pending is None and not self._closing:
                    self._condition.wait()
                if self._pending is None and self._closing:
                    return
                throttle, steering, generation = self._pending
                self._pending = None

            query = urllib.parse.urlencode(
                {"throttle": throttle, "steering": steering}
            )
            url = f"{self.base_url}/control?{query}"
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as response:
                    response.read(16)
                self.last_error = None
            except (urllib.error.URLError, OSError) as exc:
                self.last_error = str(exc)
                now = time.monotonic()
                if now - self._last_error_report_at >= 2.0:
                    print(f"Car command failed: {self.last_error}", file=sys.stderr)
                    self._last_error_report_at = now
            finally:
                with self._condition:
                    self._completed_generation = max(
                        self._completed_generation, generation
                    )
                    self._condition.notify_all()

