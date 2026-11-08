# コントリビューションガイド (Contributing)

本プロジェクトは「**プロフェッショナルなエンジニアリング製品**」水準を目標に、物理的厳密性と再現性を最優先としています。
コントリビューション前に以下の原則と手順をご確認ください。

---

## 1. コミットメント（提案で同意いただく事項）

1. **Zero-Placeholder Policy**: `TODO`・空関数・ダミープレースホルダーは禁止。すべての関数・数式・APIは 100% 実動するコードとして実装してください。
2. **単一実装原則**: 同じ物理式は `physics/aero_kernel.py` の **canonical 関数 1 箇所**にのみ存在します。スカラー経路（`aero_domain.py`）と GPU バッチ経路（`gpu_dynamics.py`）は同一関数を呼ぶこと。重複定義は CI のパリティ検査で検出されます。
3. **物理的誠実さ（テキサスの狙撃兵の誤謬排除）**: 事前に仮説と反証可能な合否基準を明文化してください。偶然動作での完了認定・報酬ハッキング用の据え置き定数（意図的調整値）の導入は禁止です。
4. **安全なフォールバック**: GPU（CUDA / CuPy / PyTorch）が無い環境でも例外停止せず、NumPy / `multiprocessing` へ透過的にフォールバックしてください。返却スキーマ（`landed`, `crashed`, `status`, `horizontal_range_m` 等）は GPU 経路と CPU 経路で等価に維持してください。
5. **ブランチ隔離**: `genetic-project` フェーズでは `master` / `main` への直接コミット・マージ・チェリーピック・PR 作成を禁止します（`tools/branch_guard.py`）。すべてのミッションは `mission/<slug>` ブランチで実施し、完了時のみマージしてください。

---

## 2. Issue の起票

### 2.1 バグ報告 (Bug Report)

以下の項目をすべて記載してください:

- **環境**: OS / Python バージョン / GPU 有無（`curl /api/v1/system/gpu` の結果）
- **再現手順**: 最小再現ペイロード（`aircraft` / `environment` / `launch` JSON）
- **期待する挙動** vs **実際の挙動**（数値の乖離は相対誤差で示す）
- **関連**: `GET /api/v1/meta/physics-references` の該当 `id`（例: `helmbold_1942`）

### 2.2 機能リクエスト (Feature Request)

- 動機とユースケース、代替案との差分、物理モデルへの影響範囲を明記してください。

### 2.3 物理モデル提案 (Physics Model Proposal)

- **式**（LaTeX）、**出典**（論文・NASA TN 等）、**正本モジュール**候補、**適用限界**（Re / AR / Mach 範囲）、**検証テスト**（閉形式 or 実測値との比較）を必須で記載してください。
- 出典のない係数を新たに作らないこと。実装できない値は据え置き理由と再オープン条件を `docs/PHYSICS_MODELS.md` に記録してください。

---

## 3. Pull Request 手順

1. **ブランチ**: `mission/<slug>`（Issue 番号がある場合は `mission/<slug>-<issue番号>`）を作成して作業してください。
2. **Plan & Todo 先行**: 着手前に Plan（方針・全体像）と Todo リスト（細分化した具体的タスク）を策定・明示してください。
3. **コミットメッセージ規約**:
   - `feat(scope): ...` 新機能
   - `fix(scope): ...` バグ修正
   - `refactor(scope): ...` リファクタリング（機能変更なし）
   - `test(scope): ...` テスト追加・修正
   - `docs(scope): ...` ドキュメント整備
4. **検証（必須）**:
   ```powershell
   python -m compileall physics api tools app.py scripts/smoke_test.py
   ruff check .
   mypy physics api app.py scripts/smoke_test.py
   python -m pytest tests -q
   python scripts/smoke_test.py
   ```
5. **PR テンプレート**: 変更内容・検証結果・検証と妥当性（V&V）への影響・スクリーンショット（UI 変更時）を記載してください。

---

## 4. コード規約

- **型アノテーション**: 公開 API（`physics/*`、`api/*`）には型アノテーションを付けてください（`mypy --config-file mypy.ini`）。
- **行列演算**: 血統的な物理量（質量・慣性テンソル等）は `physics/geometry_utils.py` を使用し、行列の主要値への置換を行わないでください。
- **座標系の規約（Critical Traps）**:
  - 風場（+z 上向き）⇔ 世界 NED（+z 下向き）の符号変換: $w_{\text{NED}} = -w_{\text{wind}}$（`flight_sim.py::wind_vector` が正本）。
  - 風は地表面からの高度（`z_up = -z_ned`）でサンプルすること（6DoF 経路と GPU バッチ経路は同一規約）。

---

## 5. テスト戦略

- 解析解が存在する領域（ISA 大気・誘導抗力・Sutherland 粘性・メッシュ幾何特性）は相対誤差 $10^{-6} \sim 10^{-3}$ の厳格な数値テストを実施してください。
- GPU / CPU 経路間の**パリティ**はビット同一か、物理的許容誤差内であることを検証してください（例: `tools/check_aero_kernel_parity.py`）。
- 新しい物理モデルは**独立参照解**（閉形式・風洞試験値）との比較テストを同梱してください。

---

## 6. Discord / 外部通知

```powershell
python tools/notify_discord.py <start|subagent|end|fail|warning|question> "<title>" "<summary>"
```

詳細は [docs/discord-bridge.md](./docs/discord-bridge.md) を参照してください。

---

## 7. 関連ドキュメント

- [docs/development.md](./docs/development.md) 開発・テスト詳細
- [docs/PHYSICS_MODELS.md](./docs/PHYSICS_MODELS.md) 物理モデルの正本
- [docs/VALIDATION.md](./docs/VALIDATION.md) 検証と妥当性確認（V&V）
- [SECURITY.md](./SECURITY.md) セキュリティポリシー
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) 行動規範
- [CITATION.cff](./CITATION.cff) 学術引用
