# Skill: Subagent Delegation (サブエージェント役割分担と並列開発)
## Trigger (読み込み条件)
- サブエージェントを起動して並列実装・作業委譲を行う時、担当境界の確認時。

## Procedure (最小手順)
1. **単一コミッター原則**: 書き込み・マージ・コミットは親が一元管理。
2. **2-Tier Architecture**:
   - Tier 1 (Main/Pro): アーキテクチャ・物理モデル・最終マージ・高難度推論。
   - Tier 2 (Kilocode / Nemotron): 定型調査・整形・単純テスト量産・簡易ドキュメント。親が検証後に採用。
3. **役割定義 (Agent Roles)**:
| # | エージェント名 | 担当ファイル | 責務 |
|---|---|---|---|
| 0 | `Manager` | `physics/config_schema.py`, `physics/geometry_utils.py`, `app.py`, `smoke_test.py`, `AGENTS.md` | 契約定義・API統合・E2E・Git統括 |
| 1 | `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | 翼型・失速・Drag Polar・受動空力弾性 |
| 2 | `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | ISA・3D Simplex風場・NED/風座標変換 |
| 3 | `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | 動圧・G・曲げモーメント・応力ヒートマップ |
| 4 | `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | 3Dメッシュ解析・慣性テンソル・パネル法・SSRF |
| 5 | `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | 6DoF RK4・四元数・適応刻み・CCD |
| 6 | `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | GPUバッチ・CPU完全一致フォールバック・NSGA-II |
| 7 | `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | JobManager・進捗・JSON/CSVエクスポート |
| 8 | `ui-gpu-viz` | `templates/index.html`, `static/css/console.css`, `static/js/*.js` | Clean UI・Three.js描画・テレメトリ |
4. **通知**: 委譲・受領時は `python tools/notify_discord.py subagent ...` 実行。

## Verification Criteria (事前合否基準)
- 成功: 担当範囲が排他的でコンフリクトせず、親のマージ後にテスト全パス。
- 失敗: 複数エージェントが同一ファイルを編集し競合発生。
