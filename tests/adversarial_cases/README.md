# Adversarial Cases Registry (`tests/adversarial_cases/`)

`milcy` / GA2 の Red-Teaming ループや物理監査で発見された **P0 抜け穴（Reward Hacking・保存則破綻・幾何破綻・CPU/GPU乖離・微擾乱崩壊）** の再現機体 JSON を永続資産として保管するディレクトリです。

## 必須 JSON スキーマ
すべての `*.json` は以下の6キーを必須とし、`tools/check_agents_hygiene.py` および `tests/test_adversarial_benchmark.py` で自動検証されます：

| キー | 型 | 内容 |
|---|---|---|
| `case_id` | string | 一意な識別子（例: `p0_energy_zero_wind_glide`） |
| `discovered_by` | string | 発見元（例: `milcy-redteam-iter4`, `physics-audit`） |
| `sanity_violation_type` | string | 5観点のいずれか: `energy_generation` / `area_exceedance` / `cpu_gpu_divergence` / `perturbation_collapse` / `geometry_violation` |
| `root_cause` | string | 対症療法ではない根本の物理式・制約原因の要約 |
| `linked_test` | string | 対応する最小回帰テスト（例: `tests/test_physical_laws_audit.py`） |
| `genome_or_payload` | object | 再現に必要な機体パラメータまたは評価ペイロード |
