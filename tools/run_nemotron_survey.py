"""
Robust runner for Kilo Code Nemotron-3-Ultra to generate supplementary survey analyses.
"""
import sys
import json
import subprocess
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.bridge_backends import resolve_kilo_executable

KILO_EXE = resolve_kilo_executable()
MODEL = "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"

TOPICS = [
    {
        "output": "docs/research/kilo_nemotron_low_reynolds_synthesis.md",
        "prompt": (
            "低レイノルズ数（Re=1000〜100000）における平板翼・折紙飛行機翼型の空力特性、層流剥離泡（LSB）、"
            "迎角ヒステリシス、大迎角Viterna/Kirchhoffモデルの修正に関する主要論文（10本以上）をまとめ、"
            "数式と定量的係数（Cl, Cd, Cm, L/D）を含むMarkdownレポートを作成してください。"
        )
    },
    {
        "output": "docs/research/kilo_nemotron_6dof_stability_synthesis.md",
        "prompt": (
            "無尾翼滑空機・紙飛行機の6自由度運動方程式（クォータニオン表現）、動的安定性（フゴイド、短周期、ダッチロール、スパイラル）、"
            "および安定微係数（Cmq, Clp, Cnr等）に関する学術論文（10本以上）をまとめ、"
            "推奨微係数レンジを含むMarkdownレポートを作成してください。"
        )
    },
    {
        "output": "docs/research/kilo_nemotron_ground_effect_synthesis.md",
        "prompt": (
            "地面効果（Ground Effect, WIG）、低空大気境界層風速プロファイル（対数則・粗度長）、"
            "および突風応答モデル（Dryden / von Karman）に関する主要学術論文（10本以上）をまとめ、"
            "定量的数理モデルを含むMarkdownレポートを作成してください。"
        )
    }
]

def run_topic(idx):
    item = TOPICS[idx]
    out_file = PROJECT_ROOT / item["output"]
    print(f"[*] Querying Nemotron for topic {idx}: {item['output']}...", flush=True)

    # Retry loop up to 3 times
    for attempt in range(1, 4):
        try:
            cmd = [
                KILO_EXE,
                "run",
                "--format", "json",
                "-m", MODEL,
                "--",
                item["prompt"]
            ]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace"
            )
            chunks = []
            for line in proc.stdout:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    data = json.loads(line_str)
                    if data.get("type") == "text" and "part" in data and "text" in data["part"]:
                        chunks.append(data["part"]["text"])
                except Exception:
                    pass
            proc.wait()
            text = "".join(chunks).strip()
            if proc.returncode == 0 and len(text) > 100:
                out_file.write_text(text, encoding="utf-8")
                print(f"[✓] Successfully wrote {item['output']} ({len(text)} chars)", flush=True)
                return True
            else:
                err = proc.stderr.read()
                print(f"[!] Attempt {attempt} failed (code {proc.returncode}): {err[:200]}", flush=True)
                time.sleep(3)
        except Exception as e:
            print(f"[!] Exception on attempt {attempt}: {e}", flush=True)
            time.sleep(3)
    return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_topic(int(sys.argv[1]))
    else:
        for i in range(len(TOPICS)):
            run_topic(i)
