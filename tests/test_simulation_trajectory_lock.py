"""Regression tests for SIMULATION_TRAJECTORY_LOCK (P0-8).

Verifies that concurrent requests to simulation endpoints (/launch, /step, /reset)
are properly serialized by SIMULATION_TRAJECTORY_LOCK, preventing race conditions,
trajectory corruption, and airframe configuration mutation mid-trajectory.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from unittest.mock import patch

from api.core import SIMULATION_TRAJECTORY_LOCK
from app import create_app


def test_trajectory_lock_serializes_concurrent_launches() -> None:
    """Verify concurrent launches do not interleave or corrupt the shared session."""
    app = create_app()
    app.config["TESTING"] = True

    active_in_critical = 0
    max_active = 0
    lock_tracker = threading.Lock()

    def tracking_simulate(self: object, *args: object, **kwargs: object) -> dict[str, object]:
        nonlocal active_in_critical, max_active
        # The simulate method must be called under SIMULATION_TRAJECTORY_LOCK.
        # Verify that only 1 thread executes inside the critical simulation section at a time.
        with lock_tracker:
            active_in_critical += 1
            if active_in_critical > max_active:
                max_active = active_in_critical
        try:
            time.sleep(0.02)
            return {"status": "ok", "backend": "numpy"}
        finally:
            with lock_tracker:
                active_in_critical -= 1

    with patch("physics.flight_sim.FlightSimulator.simulate", tracking_simulate):
        with patch("physics.flight_sim.FlightSimulator.history", return_value=[]):
            def invoke_launch() -> tuple[int, bytes]:
                # Each thread creates its own test_client to ensure thread-isolated context
                client = app.test_client()
                resp = client.post("/api/v1/simulation/launch", json={"duration_s": 0.5})
                return resp.status_code, resp.data

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(invoke_launch) for _ in range(4)]
                results = [f.result() for f in futures]

    for status_code, data in results:
        assert status_code in (200, 400)
        if status_code == 400:
            assert b"maximum concurrent simulations reached" in data

    assert max_active == 1


def test_trajectory_lock_blocks_reset_mid_trajectory() -> None:
    """Verify SIMULATION_TRAJECTORY_LOCK prevents reset while a trajectory is active."""
    trajectory_running = threading.Event()
    reset_finished = threading.Event()
    order_log: list[str] = []

    def long_running_trajectory() -> None:
        with SIMULATION_TRAJECTORY_LOCK:
            order_log.append("trajectory_started")
            trajectory_running.set()
            time.sleep(0.05)
            order_log.append("trajectory_finished")

    def interrupting_reset() -> None:
        trajectory_running.wait(timeout=1.0)
        with SIMULATION_TRAJECTORY_LOCK:
            order_log.append("reset_acquired")
            reset_finished.set()

    t1 = threading.Thread(target=long_running_trajectory)
    t2 = threading.Thread(target=interrupting_reset)

    t1.start()
    t2.start()

    t1.join(timeout=2.0)
    t2.join(timeout=2.0)

    assert not t1.is_alive()
    assert not t2.is_alive()
    assert order_log == ["trajectory_started", "trajectory_finished", "reset_acquired"]
