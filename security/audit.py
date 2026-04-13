"""
Helix Audit Logger
Structured JSON audit log. Append-only. Rotates at 100MB.
"""

import json
import time
from pathlib import Path
import threading

AUDIT_LOG_PATH = Path.home() / ".helix" / "logs" / "audit.log"
AUDIT_MAX_BYTES = 100 * 1024 * 1024  # 100MB


class AuditLogger:
    def __init__(self, path: Path = AUDIT_LOG_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _write(self, event: dict) -> None:
        event["ts"] = time.time()
        line = json.dumps(event) + "\n"
        with self._lock:
            # Rotate if over limit
            if self.path.exists() and self.path.stat().st_size > AUDIT_MAX_BYTES:
                rotated = self.path.with_suffix(f".log.{int(time.time())}")
                self.path.rename(rotated)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)

    def log(self, event: str, **fields) -> None:
        """Log a structured audit event."""
        entry = {"event": event}
        # Truncate any preview fields
        for k, v in fields.items():
            if isinstance(v, str) and "preview" in k:
                entry[k] = v[:200]
            else:
                entry[k] = v
        self._write(entry)
