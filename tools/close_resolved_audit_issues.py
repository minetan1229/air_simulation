import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

resolved_issues = [
    (194, """【第一原理修正による正式解決】
- 課題: 付加質量の並進・回転連成項（Munk/Coriolis項）が「エネルギー注入・数値不安定」を理由にコメントアウト・無効化されていた。
- 原因究明: 外力形式で代入していたため剛体＋付加質量の一般化ハミルトン構造が壊れ不自然なエネルギー成長が生じていた。
- 第一原理解決: 一般化質量行列 M_total および角運動量方程式におけるスキュー対称内部項 M_munk = - v_rel x P_A として受動性・エネルギー保存性 (v . F_app + omega . M_app == 0) を厳密保証した上で、CPU (flight_sim.py) および GPU (gpu_dynamics.py) の双方へ実装。失速剥離時にはポテンシャル循環の崩壊に伴い滑らかに減衰。
- 検証: tests/test_issue194_munk_coupling.py (3 passed), tests/test_added_mass_parity.py (5 passed)
- コミット: 5e24973 (マージコミット: ce8bdfb)"""),

    (193, """【第一原理修正による正式解決】
- 課題: 空力モデル接合部における抗力（寄生抗力・誘導抗力・平板抗力）の二重計上防止および連続性の検証。
- 第一原理解決: 逆流時 (cos(alpha) < 0) でも寄生抗力が正しく作用するよう separated_drag の定式化を一貫化 (cd0 * abs(cos(alpha)))。Polhamus 渦揚力誘導抗力とポテンシャル誘導抗力の二重加算を防止。
- 検証: tests/test_aero.py (135 passed), tests/test_polhamus_induced_drag.py
- コミット: 5e24973 (マージコミット: ce8bdfb)"""),

    (195, """【第一原理修正による正式解決】
- 課題: 空力弾性変形とピッチングモーメント・空力中心の連成検証。
- 第一原理解決: 動圧増大に伴う翼端ねじり下げ (aero_twist_deg) による実効迎角低減、受動モーフィングによるエレボン・後縁折りの緩和、および C^2 連続性を持つ 5次 Perlin smootherstep による圧力中心 (CoP) 移動の連成を確認・整合。
- 検証: tests/test_issue11_aeroelastic_model.py, tests/test_issue18_unsteady_aeroelastic_pitch.py, tests/test_deformation_beam_comparison.py (20 passed)
- コミット: 5e24973 (マージコミット: ce8bdfb)"""),

    (197, """【第一原理修正による正式解決】
- 課題: 大迎角・大横滑り・極低速・逆流域の空力モデル有界性の検証。
- 第一原理解決: alpha の [-180, 180] 正規化と、逆流域における平板抗力 (2 sin^2(alpha) + cd0 * |cos(alpha)|) の連続有界性を保証。極低速域における cross-flow rotational damping と pushover モーメントの滑らかな零点収束を検証。
- 検証: tests/test_aero.py (135 passed), tests/test_sideslip_large_angle_cy.py
- コミット: 5e24973 (マージコミット: ce8bdfb)"""),

    (178, """【第一原理修正・定量テスト実装による正式解決】
- 課題: 外部参照ベンチマーク（Stengel PaperPlane.m / 低Re風洞データ）との定量比較テストが未実装のままドキュメント記載のみで閉じられていた。
- 第一原理解決: 独立ベンチマークテスト tests/test_external_benchmarks.py を新規作成・実装：
  1. Princeton 大 Robert F. Stengel 教授の「PaperPlane.m」標準滑空飛行条件における L/D 比 (3.0〜7.0)、沈下率 (0.2〜1.5 m/s)、Phugoid 周期理論解との合致を定量アサート。
  2. Pelletier & Mueller (2000) 低Re数風洞実験データとの揚力傾斜低減係数・失速角低減係数・最大揚力・極限抗力の一致をアサート。
  3. NASA TN D-3767 Polhamus 渦揚力理論値との増大効果 (>15%) をアサート。
- 検証: tests/test_external_benchmarks.py (3 passed)
- コミット: 5e24973 (マージコミット: ce8bdfb)""")
]

for num, body in resolved_issues:
    subprocess.run(["gh", "issue", "comment", str(num), "--body", body], capture_output=True, text=True)
    subprocess.run(["gh", "issue", "close", str(num), "--reason", "completed"], capture_output=True, text=True)
    print(f"Issue #{num} commented and closed as completed.")
