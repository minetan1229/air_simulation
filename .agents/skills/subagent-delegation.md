# Skill: Subagent Delegation (サブエージェント役割分担と並列開発)
## Trigger (読み込み条件)
- サブエージェントを起動して並列実装・作業委譲を行う時、担当境界の確認時。

## Procedure (最小手順)
1. **単一コミッター原則**: 書き込み・マージ・コミットは親が一元管理。
2. **稼働上限4（超過委譲は拒否）**:
   | 枠 | エージェント名 | 責務 |
   |---|---|---|
   | 0 | `Manager`（親・Main） | 契約定義・API統合・E2E・Git統括・最終マージ |
   | 1 | **Monitor（必須1枠）** | 稼働状態・APIレスポンス・エラー・レートリミットを常時監視し、異常を即座に親へ報告・Discord通知。実務を兼務しない |
   | 2 | `implementer` | 指定ディレクトリの実装・修正（親が検証後に採用） |
   | 3 | `researcher` | 調査・検索・文献・ログ抽出（結果は報告のみ） |
   | 4 | `discord-responder` | Discord応答・Webhook通知・セッション/クォータ確認 |
   - 残り最大3枠（implementer / researcher / discord-responder）は**排他的**に担当領域を分ける。上限超過時は既存枠の完了を待つか親へ統合する（常駐数の拡大は禁止）。
3. **2-Tier Architecture**:
   - Tier 1 (Main/Pro): アーキテクチャ・物理モデル・最終マージ・高難度推論。
   - Tier 2 (Kilocode / MiniMax M3.1 Flash): 定型調査・整形・単純テスト量産・簡易ドキュメント。親が検証後に採用。
4. **担当ファイル境界（排他）**:
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
   | 9 | `discord-bridge` | `tools/discord_bridge.py`, `tools/bridge_*.py`, `docs/discord-bridge.md` | Discordブリッジ・Kilo Code連携・429継続・フォールバック・監視 |
5. **通知**: 委譲・受領時は `python tools/notify_discord.py subagent ...` 実行。

## Verification Criteria (事前合否基準)
- 成功: 担当範囲が排他的でコンフリクトせず、親のマージ後にテスト全パス。稼働サブエージェントは4以下。
- 失敗: 複数エージェントが同一ファイルを編集し競合発生、または上限4を超過して委譲した。
