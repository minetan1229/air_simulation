#!/usr/bin/env bash
# watchdog.sh - GA Simulation Watchdog script
#
# Soft Anomaly Thresholds (明記):
# - Best fitness stagnation: 8 generations without improvement (< 1e-4)
# - Diversity loss: steep drop (> 70% decrease)
# - Memory leak: > 500MB increase from baseline
# - Handle/FD leak: > 300 handles increase from baseline
#
# Exit Codes:
#  0: Complete
# 10: Hard anomaly (Crash, Exception, NaN, Inf, Stack)
# 20: Periodic audit (7200s elapsed)
# 30: Soft anomaly (Stagnation, memory/handle leak)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python >/dev/null 2>&1; then
    PY_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
else
    echo "No python found" >&2
    exit 1
fi

PID_FILE="sim_process.pid"
LOG_FILE="sim_output.log"
RUN_DIR="runs/ga_research"
STATUS_FILE="watchdog_status.txt"

# 1. Start simulation if not already running
START_SIM=false
if [ ! -f "$PID_FILE" ]; then
    START_SIM=true
else
    SIM_PID=$(cat "$PID_FILE")
    # Verify if PID is running
    if ! "$PY_CMD" -c "import ctypes, sys; h=ctypes.windll.kernel32.OpenProcess(0x0400, False, int(sys.argv[1])); sys.exit(0 if h else 1)" "$SIM_PID" 2>/dev/null; then
        START_SIM=true
    fi
fi

if [ "$START_SIM" = true ]; then
    echo "[watchdog.sh] Launching simulation in background..."
    # Launch independently (resume if latest.json exists)
    "$PY_CMD" -c "
import subprocess, sys, os
cmd = [sys.executable, 'tools/run_ga_research.py']
latest = 'runs/ga_research/checkpoints/latest.json'
if os.path.exists(latest):
    cmd.extend(['--resume', latest])
    print('Resuming from:', latest)
p = subprocess.Popen(cmd, stdout=open('sim_output.log', 'ab'), stderr=subprocess.STDOUT)
with open('sim_process.pid', 'w') as f:
    f.write(str(p.pid))
print('Started PID:', p.pid)
"
    sleep 2
fi

SIM_PID=$(cat "$PID_FILE")
echo "[watchdog.sh] Monitoring PID $SIM_PID..."

# 2. Run monitoring loop
set +e
"$PY_CMD" tools/watchdog_loop.py --pid "$SIM_PID" --log "$LOG_FILE" --run-dir "$RUN_DIR" --check-interval 30 --max-period 7200
WATCHDOG_EXIT=$?
set -e

# 3. Handle exit codes
if [ "$WATCHDOG_EXIT" -eq 20 ]; then
    echo "[watchdog.sh] Periodic audit triggered. Sending Discord report & updating progress.md..."
    "$PY_CMD" tools/notify_discord.py verify "GA 2-Hour Periodic Check" "Simulation running normally. Periodic audit initiated." || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Periodic 2h heartbeat: PID $SIM_PID active." >> progress.md
fi

exit "$WATCHDOG_EXIT"
