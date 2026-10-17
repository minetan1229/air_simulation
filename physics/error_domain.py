"""Error logging and audit domain for 3D Paper Airplane Simulator.

Provides structured, persistent, thread-safe error logging:
- Log rotation to file (`logs/error.log` and `logs/error.jsonl`)
- In-memory ring buffer for low-latency queries and API inspection
- Client-side error reporting endpoint support
- Filtering by severity, category, time range
- Clean statistics & export
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ErrorRecord:
    id: str
    timestamp: str
    level: str  # ERROR, WARNING, CRITICAL
    category: str  # api, physics, optimizer, mesh, storage, frontend, system
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
    traceback_str: Optional[str] = None
    path: Optional[str] = None
    method: Optional[str] = None
    status_code: Optional[int] = None
    client_ip: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


class ErrorLoggerDomain:
    """Thread-safe persistent error logger and in-memory ring buffer."""

    def __init__(
        self,
        log_dir: str = "logs",
        max_buffer_size: int = 500,
        max_file_bytes: int = 5 * 1024 * 1024,
        backup_count: int = 5,
    ) -> None:
        self.log_dir = log_dir
        self.max_buffer_size = max_buffer_size
        self._lock = threading.RLock()
        self._buffer: Deque[ErrorRecord] = deque(maxlen=max_buffer_size)
        self._counter = 0

        os.makedirs(self.log_dir, exist_ok=True)
        self.text_log_path = os.path.join(self.log_dir, "error.log")
        self.jsonl_log_path = os.path.join(self.log_dir, "error.jsonl")

        # Rotating file handler for structured JSONL
        self._file_handler = RotatingFileHandler(
            self.jsonl_log_path,
            maxBytes=max_file_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self._text_handler = RotatingFileHandler(
            self.text_log_path,
            maxBytes=max_file_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        self._text_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
        )
        self._text_handler.setFormatter(self._text_formatter)

    def record_error(
        self,
        message: str,
        level: str = "ERROR",
        category: str = "system",
        exc: Optional[BaseException] = None,
        traceback_str: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        path: Optional[str] = None,
        method: Optional[str] = None,
        status_code: Optional[int] = None,
        client_ip: Optional[str] = None,
    ) -> ErrorRecord:
        """Record an error to memory buffer and persistent log files."""
        if exc is not None and traceback_str is None:
            traceback_str = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )

        with self._lock:
            self._counter += 1
            record_id = f"err_{int(time.time() * 1000)}_{self._counter}"
            record = ErrorRecord(
                id=record_id,
                timestamp=utc_now_iso(),
                level=level.upper(),
                category=category.lower(),
                message=str(message),
                details=details or {},
                traceback_str=traceback_str,
                path=path,
                method=method,
                status_code=status_code,
                client_ip=client_ip,
            )

            self._buffer.append(record)

            # Write to JSONL
            try:
                stream = self._file_handler.stream
                if stream is not None:
                    line = json.dumps(record.to_dict(), ensure_ascii=False) + "\n"
                    stream.write(line)
                    stream.flush()
            except Exception:
                pass


            # Write to plain text log
            try:
                text_msg = f"[{record.category}] {record.message}"
                if record.path:
                    text_msg += f" (Path: {record.method or 'GET'} {record.path})"
                if record.traceback_str:
                    text_msg += f"\n{record.traceback_str}"
                record_obj = logging.LogRecord(
                    name="error_domain",
                    level=getattr(logging, record.level, logging.ERROR),
                    pathname=__file__,
                    lineno=0,
                    msg=text_msg,
                    args=(),
                    exc_info=None,
                )
                self._text_handler.emit(record_obj)
            except Exception:
                pass

            return record

    def get_logs(
        self,
        limit: int = 100,
        level: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve recent errors from in-memory ring buffer with optional filtering."""
        with self._lock:
            records = list(self._buffer)

        # Most recent first
        records.reverse()

        results: List[Dict[str, Any]] = []
        for r in records:
            if level and r.level != level.upper():
                continue
            if category and r.category != category.lower():
                continue
            if search:
                s_lower = search.lower()
                in_msg = s_lower in r.message.lower()
                in_path = r.path and s_lower in r.path.lower()
                in_tb = r.traceback_str and s_lower in r.traceback_str.lower()
                if not (in_msg or in_path or in_tb):
                    continue
            results.append(r.to_dict())
            if len(results) >= limit:
                break

        return results

    def clear(self) -> None:
        """Clear memory buffer."""
        with self._lock:
            self._buffer.clear()

    def statistics(self) -> Dict[str, Any]:
        """Summary metrics of recorded errors."""
        with self._lock:
            records = list(self._buffer)

        by_level: Dict[str, int] = {}
        by_category: Dict[str, int] = {}
        for r in records:
            by_level[r.level] = by_level.get(r.level, 0) + 1
            by_category[r.category] = by_category.get(r.category, 0) + 1

        return {
            "total_in_buffer": len(records),
            "by_level": by_level,
            "by_category": by_category,
            "log_files": {
                "text": self.text_log_path,
                "jsonl": self.jsonl_log_path,
                "text_size_bytes": os.path.getsize(self.text_log_path)
                if os.path.exists(self.text_log_path)
                else 0,
                "jsonl_size_bytes": os.path.getsize(self.jsonl_log_path)
                if os.path.exists(self.jsonl_log_path)
                else 0,
            },
        }


# Singleton instance for application-wide logging
ERROR_LOGGER = ErrorLoggerDomain()
