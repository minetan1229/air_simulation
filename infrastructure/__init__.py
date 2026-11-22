"""Infrastructure package — job management, data persistence, and record databases.

This package provides operational support services including asynchronous job
scheduling, data import/export, and world-record aircraft preset management.
It depends on :mod:`core` for data contracts.

Modules
-------
job_manager
    Non-blocking async job queue with ETA prediction.
storage_domain
    JSON/CSV export and import of simulation logs and presets.
records_domain
    World-record airframe database and Guinness presets.
"""

from __future__ import annotations

__all__ = [
    "job_manager",
    "storage_domain",
    "records_domain",
]
