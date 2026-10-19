# Skill: Subagent Delegation (サブエージェント役割分担と並列開発)
## Trigger (読み込み条件)
- サブエージェントを起動して並列実装・作業委譲を行う時、または担当ファイルの競合を防止する時。

## Procedure (最小手順)
1. **単一コミッター原則の維持**:
   - サブエージェントにはファイル読み込み・調査・テスト実行等を指示し、リポジトリ変更・コミット・ファイル書き込みは親エージェント（単一コミッター）に一元化する。
2. **サブエージェント階層分離 (2-Tier Subagent Architecture)**:
   タスクの重要度・難易度に応じてサブエージェントを明確に2系統へ分離する：
   - **Tier 1: 統合・重要推論エージェント (Main / Pro / Inherit)**
     - 用途: アーキテクチャ設計、数学・物理モデル検証、API統合、親エージェント（単一コミッター）による最終マージと整合性判断。
     - 特徴: 高推論力・高精度、破壊的変更や契約定義を担当。
   - **Tier 2: 量産型高速エージェント (Kilocode: NVIDIA Nemotron 3.5 Super VL / Fast Worker)**
     - 用途: 「後から修正が効く簡単なタスク」「定型的な調査・探索」「ファイル走査・フォーマット整形」「大量の単純テストケース作成」「簡易なドキュメント作成・整理」。
     - 特徴: 高速・低コストでの量産並列処理。親エージェントが結果を必ずチェック・検証した上で取り込む前提で運用する。

3. **役割定義 (Agent Roles v4)**:
   以下の担当ファイル境界を厳守し、排他的に作業すること：

| # | エージェント名 | 担当ファイル | 必須実装内容・責務 |
|---|---------------|------------|------------------|
| 0 | `Manager` | `physics/config_schema.py`, `physics/geometry_utils.py`, `app.py`, `smoke_test.py`, `AGENTS.md` | 契約定義・API Blueprint統合・UI・E2Eテスト・Git制御 |
| 1 | `aero-physics` | `physics/aero_domain.py`, `tests/test_aero.py` | 翼型・失速・Drag Polar・Trim・受動空力弾性・低速渦リフト |
| 2 | `atmosphere-fluid` | `physics/atmosphere_domain.py`, `physics/wind_domain.py`, `tests/test_atmo_wind.py` | ISA・Sutherland・全可変環境大気・3D Simplex風場・座標変換整合性 |
| 3 | `structural-stress` | `physics/stress_domain.py`, `tests/test_stress.py` | 動圧・Gフォース・翼根曲げモーメント・頂点応力ヒートマップ |
| 4 | `mesh-3d` | `physics/mesh_domain.py`, `tests/test_mesh.py` | glTF/GLB/OBJ/STL 解析・慣性テンソル・パネル法・SSRF防止 |
| 5 | `flight-dynamics` | `physics/flight_sim.py`, `tests/test_flight.py` | 6DoF RK4・四元数・適応/固定時間刻み・リングバッファ |
| 6 | `parallel-optimizer` | `physics/parallel_engine.py`, `physics/optimizer_domain.py`, `tests/test_optimizer.py` | GPU一括評価・ケース別duration_s・CPU完全一致フォールバック・NSGA-II |
| 7 | `job-eta-storage` | `physics/job_manager.py`, `physics/storage_domain.py`, `tests/test_jobs.py` | JobManager・EMA ETA・進捗管理・JSON/CSV エクスポート |
| 8 | `ui-gpu-viz` | `templates/index.html`, `static/css/console.css`, `static/js/*.js` | Google Clean UI・Three.js シェーダー・時間軸・テレメトリ |

4. **Discord 通知連携**:
   サブエージェント委譲時および結果受領時には `python tools/notify_discord.py subagent ...` で進捗を通知する。


## Verification Criteria (事前合否基準の型)
- 成功条件: 委譲先の担当範囲が明確に排他的であり、親エージェントが結果をマージした際にテストスイートがパスすること。
- 失敗条件: 複数エージェントが同一ファイルを直接編集してコンフリクトを起こすこと。
