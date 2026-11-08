"""Headless 6DoF batch simulation without the web UI (Python / CLI example).

Web UI を介さず、Python コード単体で 6DoF バッチ計算を呼び出すサンプルです。
`physics.flight_sim.evaluate_flight` の後方互換ラッパーを直接利用します。
GPU（CuPy）が利用可能な環境では自動で高速化され、無い場合は NumPy に
透過的にフォールバックします（返却スキーマは同一）。

実行:
    pip install -e ".[dev]"
    python examples/quick_simulation.py

    # ペイロードを JSON で読み替える場合:
    python examples/quick_simulation.py --payload payload.json
    python examples/quick_simulation.py --payload payload.json --out flight.json

REST API 経路の等価サンプルは下記（サーバー起動後）:
    curl -X POST http://127.0.0.1:5000/api/v1/simulation/launch \\
      -H 'Content-Type: application/json' -d @payload.json
    # インタラクティブ仕様: http://127.0.0.1:5000/docs / /api/openapi.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# リポジトリルートを import パスに追加（`python examples/...` で直接実行可能にする）
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from physics.config_schema import (  # noqa: E402
    AircraftConfig,
    EnvironmentConfig,
    LaunchConfig,
    as_dict,
)
from physics.flight_sim import evaluate_flight  # noqa: E402


def _default_payload() -> dict[str, object]:
    return {
        "aircraft": as_dict(AircraftConfig()),
        "environment": as_dict(EnvironmentConfig()),
        "launch": as_dict(LaunchConfig()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Headless 6DoF paper-airplane simulation (no server required)."
    )
    parser.add_argument(
        "--payload",
        type=str,
        default=None,
        help="JSON file with {aircraft, environment, launch} payload (defaults: AircraftConfig defaults).",
    )
    parser.add_argument("--duration", type=float, default=6.0, help="Simulation duration in seconds.")
    parser.add_argument("--out", type=str, default=None, help="Write JSON result to this path (default: stdout).")
    args = parser.parse_args()

    if args.payload:
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8"))
    else:
        payload = _default_payload()

    result = evaluate_flight(
        payload.get("aircraft", {}),
        payload.get("environment", {}),
        payload.get("launch", {}),
        duration_s=args.duration,
    )

    # サマリ表示（全ペイロードは --out で書き出す）
    print(json.dumps(
        {
            "distance_m": result.get("distance_m"),
            "landing_distance_m": result.get("landing_distance_m"),
            "max_altitude_m": result.get("max_altitude_m"),
            "max_ld_ratio": result.get("max_ld_ratio"),
            "max_stress_index": result.get("max_stress_index"),
            "status": result.get("status"),
            "backend": result.get("backend", "numpy"),
        },
        indent=2,
        ensure_ascii=False,
    ))

    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"full result written to {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
