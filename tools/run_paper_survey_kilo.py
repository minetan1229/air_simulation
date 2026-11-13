"""
Runner to query papers via Kilo Code CLI using nemotron-3-ultra-550b-a55b:free,
collect streaming text responses, and write complete research reports.
"""
import os
import sys
import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.bridge_backends import resolve_kilo_executable

KILO_EXE = resolve_kilo_executable()
MODEL = "kilo/nvidia/nemotron-3-ultra-550b-a55b:free"

DOMAINS = [
    {
        "id": "domain_1_low_reynolds_aerodynamics",
        "title": "低レイノルズ数空力特性・薄翼・後流・剥離・ヒステリシス",
        "output_file": "docs/research/domain1_low_reynolds_aerodynamics.md",
        "prompt": (
            "あなたは低レイノルズ数空気力学（Low-Reynolds-Number Aerodynamics, Re=10^3〜10^5）および紙飛行機・超小型航空機（MAV）の学術研究の世界的権威です。\n"
            "以下のテーマに関して、主要な学術論文・研究報告（NASA TP/TM, AIAA Journal, Journal of Aircraft, Experiments in Fluids, Physical Review E/Fluids, 日本航空宇宙学会論文集等）を網羅的に徹底調査し、"
            "極めて詳細かつ体系的な文献調査レポート（Markdown形式）を作成してください。\n\n"
            "【調査トピック】\n"
            "1. 平板翼・薄キャンバー翼・折紙翼型（Origami Airfoils）の低レイノルズ数における揚力・抗力特性（Cl, Cd, Cm）\n"
            "2. 層流剥離泡（Laminar Separation Bubble: LSB）の形成・崩壊・再付着機構および迎角増減時の空力ヒステリシス現象\n"
            "3. 大迎角・失速後領域における挙動（Viterna法、Kirchhoff剥離モデル等の適用限界と低レイノルズ数での修正法）\n"
            "4. 低アスペクト比翼（Low AR）における翼端渦・誘導抗力・翼端失速・渦崩壊と散逸特性\n\n"
            "【必須要件】\n"
            "- 最低15本以上の重要論文・技術報告を網羅すること。\n"
            "- 各文献について以下の項目を漏れなく記述すること：\n"
            "  * 論文タイトル（英語/原題および日本語訳）\n"
            "  * 著者・所属機関・発表年\n"
            "  * 掲載誌 / 会議録名 / DOI または 出典URL\n"
            "  * 研究の背景と目的\n"
            "  * 採用された実験手法（風洞・PIV等）または数値計算手法（DNS, LES, XFOIL等）および対象レイノルズ数領域\n"
            "  * 判明した物理現象・提示された数式・実験データ／空力係数の具体的数値（Cl_max, stall angle, Cd0, L/D等）\n"
            "  * 紙飛行機物理シミュレータ（準定常・6DoF）への工学的インプリケーション・実装推奨アルゴリズム\n"
            "- レポート冒頭には「分野概要と主要な物理的知見の要約」、末尾には「シミュレータ実装への推奨マトリクス」を必ず含めること。\n"
            "- 全文を完全なMarkdown形式で出力すること。"
        )
    },
    {
        "id": "domain_2_6dof_flight_dynamics_stability",
        "title": "6DoF飛翔力学・動的安定性・回転減衰・スパイラル・フラッター",
        "output_file": "docs/research/domain2_6dof_flight_dynamics_stability.md",
        "prompt": (
            "あなたは飛行力学・運動制御工学（Flight Dynamics & Stability, 6DoF）の世界的権威です。\n"
            "紙飛行機・無尾翼機（Tailless Glider）・超小型滑空機（MAV）の6自由度運動方程式および動的安定性（Dynamic Stability）に関する主要論文・研究報告を徹底調査し、"
            "網羅的かつ体系的な学術文献調査レポート（Markdown形式）を作成してください。\n\n"
            "【調査トピック】\n"
            "1. 紙飛行機・無尾翼滑空機の縦の動的安定性（フゴイド運動 Phugoid、短周期運動 Short-Period）と縦減衰微係数（Cmq, Cma_dot）\n"
            "2. 横・方向の動的安定性（ダッチロール Dutch Roll、スパイラル不安定 Spiral Mode、ロールダンピング Clp, Cnr, Cnp）と上反角効果・後退角効果\n"
            "3. クォータニオン（四元数）を用いた特異点フリーな6DoF運動方程式の数値積分（RK4, Symplectic等）と付加質量（Added Mass）・非定常空気力効果（Theodorsen/Wagner）\n"
            "4. 投擲・射出直後の過渡挙動（初期ピッチ・ロール擾乱の減衰過程）および失速スピン（Spin）からの自律回復特性\n\n"
            "【出力要件】\n"
            "- 最低15本以上の主要論文・報告書を網羅すること。\n"
            "- 各文献について：[タイトル, 著者, 出版年, 掲載誌/DOI, 概要, 運動方程式・微係数の定式化と測定・推算値, シミュレータへの工学的インプリケーション] を詳細に記述すること。\n"
            "- 冒頭に「飛行力学要約」、末尾に「6DoFシミュレータ実装への推奨微係数レンジ一覧表」を含めること。\n"
            "- 全文を完全なMarkdown形式で出力すること。"
        )
    },
    {
        "id": "domain_3_atmospheric_boundary_layer_wind",
        "title": "大気境界層・サーマル・風外乱・地面効果・突風応答",
        "output_file": "docs/research/domain3_atmospheric_boundary_layer_wind.md",
        "prompt": (
            "あなたは大気物理学・マイクロ気象学・地面効果（Ground Effect）・環境風外乱の専門家です。\n"
            "屋外および屋内体育館等における小型滑空飛翔体が遭遇する大気境界層（ABL）、熱上昇気流（Thermal）、突風（Gust）、および対地地面効果に関する主要論文・研究報告を徹底調査し、"
            "網羅的かつ体系的な学術文献調査レポート（Markdown形式）を作成してください。\n\n"
            "【調査トピック】\n"
            "1. 地表面付近（高度0〜50m）の大気境界層風速鉛直プロファイル（対数則・べき法則・モニン・オブコフ相似則）およびサーマル上昇気流モデル\n"
            "2. 小型軽量飛翔体に対する大気乱流・突風モデル（von Kármánモデル、Drydenスペクトル、1-cosine離散突風）のスケール適合性\n"
            "3. 地面効果（Ground Effect, WIG: Wing-in-Ground Effect）による揚力増加・誘導抗力低減・ピッチモーメント急変の数理モデル（Wieselsberger, Phillips, Chawla等）\n"
            "4. 局所風シア（Wind Shear）遭遇時の滑空飛翔体の滑空比向上・エネルギー抽出（ダイナミックソアリング）の理論と実測\n\n"
            "【出力要件】\n"
            "- 最低15本以上の主要論文・報告書を網羅すること。\n"
            "- 各文献について：[タイトル, 著者, 出版年, 掲載誌/DOI, 概要, 提示された数式・実験結果, シミュレータへの工学的インプリケーション] を詳細に記述すること。\n"
            "- 冒頭に「環境風・地面効果物理要約」、末尾に「大気環境・地面効果モデル数式一覧」を含めること。\n"
            "- 全文を完全なMarkdown形式で出力すること。"
        )
    }
]

def run_single_domain(domain_info):
    output_path = PROJECT_ROOT / domain_info["output_file"]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[*] Querying Kilo Code ({MODEL}) for: {domain_info['title']} ...", flush=True)

    cmd = [
        KILO_EXE,
        "run",
        "--format", "json",
        "-m", MODEL,
        "--",
        domain_info["prompt"]
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    collected_text = []
    for line in proc.stdout:
        line_str = line.strip()
        if not line_str:
            continue
        try:
            data = json.loads(line_str)
            if data.get("type") == "text" and "part" in data and "text" in data["part"]:
                text_piece = data["part"]["text"]
                collected_text.append(text_piece)
        except Exception:
            pass

    proc.wait()
    full_text = "".join(collected_text).strip()

    if proc.returncode == 0 and len(full_text) > 200:
        output_path.write_text(full_text, encoding="utf-8")
        print(f"[✓] Success: {domain_info['title']} -> {domain_info['output_file']} ({len(full_text)} chars)", flush=True)
        return True
    else:
        err = proc.stderr.read()
        print(f"[!] Failed for {domain_info['title']} (exit code {proc.returncode}): {err[:300]}", flush=True)
        return False

if __name__ == "__main__":
    if len(sys.argv) > 1:
        idx = int(sys.argv[1])
        success = run_single_domain(DOMAINS[idx])
        sys.exit(0 if success else 1)
    else:
        for i, d in enumerate(DOMAINS):
            run_single_domain(d)
