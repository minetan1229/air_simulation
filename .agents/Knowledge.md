# Knowledge Base Index（常駐圧縮版 ≤80行 / 全文履歴: `.agents/archive/Knowledge_archive_2026.md`）
<!--
運用規約: 本ファイルは 80行以内を維持する。並列セッションは直接編集せず `.agents/knowledge.d/` へ出力し、
統合ゲートで `tools/distill_agents_state.py` により蒸留する。過去の全詳細履歴は `.agents/archive/Knowledge_archive_2026.md` を grep 抽出すること。
-->

## 1. 物理・6DoF・空力の不変知見（P0 優先）
- **座標系・風高度パリティ**: 風場(+z上)⇔NED(+z下)は $w_{NED}=-w_{wind}$。高度は `z_up=-z_ned` で 6DoF CPU 経路と GPU バッチ経路を統一。
- **エネルギー非増加性**: 無風・無推力滑空時の $dE/dt \le 0$ 維持。接地クーロン摩擦の `dt` 依存性・地中貫通（`min_alt<-0.05`）・CCD 補間・翼端渦散逸上限を厳守。
- **慣性・空力整合**: デルタ翼ロール慣性 $b^2/24$、$I_{xz}$ テンソルパリティ、Kirchhoff 付加質量/Munk 結合、キール二重減衰排除、失速後抗力ブレンド、ARゼロ除算防護。
- **多忠実度構成**: 探索時は中忠実度（5ステーションストリップ＋LSBヒステリシス＋非線形減衰、コスト ~2.0x）、最終候補のみ高忠実度再評価。

## 2. 折り紙幾何・milcy・最適化の不変知見
- **2.5D逐次折り（`geometry/v3_origami/kernel_25d.py`）**: Sutherland-Hodgman クリッピング＋剛体鏡映＋逆アフィン行列で UV 展開図と 3D メッシュを整合。中心線突破・自己貫通・A4原紙面積超過を構造的に禁止。
- **報酬ハッキング封鎖**: 墜落機スコア底上げ禁止、失敗評価の `timeout`/`crashed` リラベル禁止、パレート境界極値解（`math.inf`）保護、GA既定値は `physics/ga_config.py` に集約。
- **Red-Teaming 反転監査**: LLM目視全件スキャンではなく、`milcy` 自身をバグファインダーとして回し5観点サニティ異常機体のみ隔離→悪役 JSON（`tests/adversarial_cases/`）＋最小失敗 `pytest` で根本修正。

## 3. インフラ・CI・フロントエンド・並列運用の罠
- **CI / 文字コード**: GitHub Actions の run は `shell: bash` 必須。Windows PowerShell の `Get/Set-Content` での UTF-8 破壊禁止（Python/UTF-8 明示で編集）。
- **ブランチ保護**: `master` 上のローカル `pytest` は `tools/branch_guard.py` により停止する（作業は必ず `mission/*` ブランチ＋隔離 worktree）。CI では `PAPER_AIRCRAFT_BRANCH_GUARD=warn`。
- **フロントエンド WebGL**: `static/js/viewport.js` の `dispose()`（テクスチャ再帰解放＋`forceContextLoss`）と `app.js` の二重バインド抑止（`_booted` / `_eventsBound`）を維持。
- **429 / 並列安全**: 429 は失敗扱いにせず共有冷却（`rate_limit_guard.py`）＋バックオフ継続、不可時は Kilo 無料枠へフォールバック。`process_hygiene.py` はアクティブセッション台帳配下を保護。

## 4. アーカイブ検索キーワード索引（`.agents/archive/Knowledge_archive_2026.md`）
詳細経緯・コミットハッシュ・過去デバッグログが必要な時のみ以下キーワードで grep 抽出すること：
- `Paper-Conformance Audit` / `Added-Mass Passivity` / `Aerodynamic Coefficient Model Audit` / `Physical Consistency`
- `Origami Geometry` / `Procedural Origami Genome` / `MILCY Convergence Audit`
- `GA Research Engine` / `GA v2` / `Infeasible Status Forwarding` / `Wind Altitude Convention`
- `Discord Bridge` / `Process Hygiene` / `Mojibake Repair` / `Front-End Console Integrity` / `Branch Guard`
- `Modular Layered Architecture` / `Physics Stability & CI Infrastructure` / `Paper Registry Pipeline`
