# ドキュメント索引 (docs Index)

> [← README に戻る](../README.md)

本ディレクトリの文書一覧。根干は 3 文書に集約する:

- **仕様の正本**: [`SPECIFICATION.md`](./SPECIFICATION.md)（理論・API・定数・文献根拠の総括）
- **物理量ごとの正本**: [`PHYSICS_MODELS.md`](./PHYSICS_MODELS.md)（式 → 出典 → 正本モジュール → 適用限界 → 検証テスト）
- **導入・操作の正本**: [`getting-started.md`](./getting-started.md) + [`usage.md`](./usage.md)

其余は役割別に分類する。ファイル名は歴史的継承（小文字 = 旧 UI ガイド系、大文字 = 仕様・監査系）で、リンク断絶防止のため改名しない。

---

## 読み方（目的別ルート）

| あなたは | この順に読む |
| :--- | :--- |
| 初めて動かす | [getting-started](./getting-started.md) → [usage](./usage.md) → [troubleshooting](./troubleshooting.md) |
| 開発者・修正者 | [development](./development.md) → [architecture](./architecture.md) → [api](./api.md) → [SPECIFICATION §3](./SPECIFICATION.md) |
| 物理をいじる | [architecture §2](./architecture.md) → [PHYSICS_MODELS](./PHYSICS_MODELS.md) → [SPECIFICATION §1](./SPECIFICATION.md) |
| 運用・サーバ | [discord-bridge](./discord-bridge.md) → [REMOTE_COMPUTE_ENVIRONMENT](./REMOTE_COMPUTE_ENVIRONMENT.md) |

---

## 1. 入門・操作（利用者向け）

| 文書 | 読むタイミング | 状態 |
| :--- | :--- | :--- |
| [getting-started.md](./getting-started.md) | セットアップ / 起動 / 動作要件 | 現行 |
| [usage.md](./usage.md) | 画面操作 / パラメータ / 最適化 / 入出力 | 現行 |
| [troubleshooting.md](./troubleshooting.md) | 起動失敗 / GPU 未認識 / 描画・最適化トラブル | 現行 |

## 2. 開発・運用（開発者・運用者向け）

| 文書 | 読むタイミング | 状態 |
| :--- | :--- | :--- |
| [development.md](./development.md) | テスト・Git 規約・コントリビューション | 現行 |
| [api.md](./api.md) | REST エンドポイントを叩く / 仕様確認 | 現行（要約） |
| [architecture.md](./architecture.md) | 全体構成 / データフロー / 物理方程式の俯瞰 | 現行（俯瞰。数式の正本は PHYSICS_MODELS） |
| [discord-bridge.md](./discord-bridge.md) | Discord 遠隔操作 / ルーティング / クォータ | 現行 |
| [REMOTE_COMPUTE_ENVIRONMENT.md](./REMOTE_COMPUTE_ENVIRONMENT.md) | リモート GPU 計算機の構築・保守 | 現行（認証情報は `.env` のみ。追跡ファイルに実ホスト・ID・パスワードを持たない） |

## 3. 物理・仕様（正本・設計文書）

| 文書 | 読むタイミング | 状態 |
| :--- | :--- | :--- |
| [SPECIFICATION.md](./SPECIFICATION.md) | 仕様・定数・法則を横断確認（最優先の正本） | 正本 |
| [PHYSICS_MODELS.md](./PHYSICS_MODELS.md) | 係数・式の典拠と適用限界を調べる | 正本（補足） |
| [CFD_ROADMAP.md](./CFD_ROADMAP.md) | 3D Navier–Stokes 追加の将来計画（Issue #47） | 設計文書（コード変更なし） |

## 4. 最適化・データ

| 文書 | 読むタイミング | 状態 |
| :--- | :--- | :--- |
| [GA_V2_ARCHITECTURE.md](./GA_V2_ARCHITECTURE.md) | 大規模 GA 探索（`physics/ga2/`）の構造 | 現行 |
| [PAPER_REGISTRY_PIPELINE.md](./PAPER_REGISTRY_PIPELINE.md) | 論文レジストリ（2,000 件）の収集・運用 | 現行 |
| [`../search/INDEX.md`](../search/INDEX.md) | 論文レコード索引（40 チャンク） | データ索引 |

## 5. 監査・履歴（参照文書）

| 文書 | 読むタイミング | 状態 |
| :--- | :--- | :--- |
| [CODE_REVIEW_FINDINGS.md](./CODE_REVIEW_FINDINGS.md) | 過去レビュー（374 件 / 69 件再集約）の記録 | 履歴（911 行。目次付き） |
| [`../PHYSICS_PAPER_CONFORMANCE_AUDIT.md`](../PHYSICS_PAPER_CONFORMANCE_AUDIT.md) | 論文一致度監査の記録（P0–P3 完了台帳） | 履歴（リポジトリ直下に配置） |
| [`../archive/`](../archive/) | 旧監査・修復記録（3 件） | アーカイブ |

## 6. エージェント協定・旧 Wiki（開発基盤）

| 文書 | 読むタイミング | 状態 |
| :--- | :--- | :--- |
| [`../AGENTS.md`](../AGENTS.md) | 自律ループの常駐規約（優先） | 現行 |
| [`../.agents/Project.md`](../.agents/Project.md) | 現在地・Loop State | 現行（エージェント作業用） |
| [`../.agents/Knowledge.md`](../.agents/Knowledge.md) | 過去知見・回避策 | 現行（エージェント作業用） |
| [`../skill/README.md`](../skill/README.md) | スキル索引（定型手順） | 現行 |
| [`../wiki-docs/`](../wiki-docs/) | 旧 Wiki 鏡像（Home / Getting-Started / Architecture / Development-Guide） | **旧版**。重複保存のため参照は docs/ を優先 |

---

## 画面イメージ

`images/` 配下に 9 件ある。README の 2×2 表は代表画面のみで、以下も参照可能。

| ファイル | 内容 |
| :--- | :--- |
| `01_main_console.png` / `ui_main_console.png` | メインコンソール |
| `02_flight_trajectory.png` / `ui_flight_simulation.png` | 飛行軌道 |
| `03_wind_tunnel_mode.png` | 風洞モード |
| `04_workbench_investigate.png` / `05_workbench_optimizer.png` / `06_workbench_3d_mesh.png` | Workbench（調査 / 最適化 / 3D メッシュ） |
| `07_ensemble_100_planes.png` | アンサンブル 100 機 |

---

[README に戻る](../README.md)
