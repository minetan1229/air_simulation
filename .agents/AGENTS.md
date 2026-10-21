# 自律実行ループ OS（エントリポイント）

本ファイルは `.agents/` ワークスペースの**読み込み順序と正本マップ**を定義する。
個条目の規約そのものは重複して保持しない（`AGENTS.md` §4「正本原則」）。

---

## 1. 正本マップ（どのファイルが正本か）

| 関心事 | 正本 | 読み込み方 |
|---|---|---|
| エージェント基本プロトコル OS（常駐規約） | ルート `AGENTS.md` | 毎セッション必ず読む（§1〜§4） |
| 詳細技術仕様（物理/API/セキュリティ/モジュール構成） | `docs/SPECIFICATION.md` | 該当節のみ行指定で読む |
| 現在のゴール・マイルストーン・ループ状態 | `.agents/Project.md` | 差分確認時のみ読む |
| 常駐制約・技術ガードレール | `.agents/Knowledge-important.md` | 50行以内・常駐 |
| 確定知識ベース（過去知見） | `.agents/Knowledge.md` | キーワード検索で部分抽出（全文読まない） |
| 定型手順（技能） | `.agents/skills/<name>.md` | 該当1節のみ、完了後破棄 |

**重複保持の禁止**: 物理定数・仕様値・既知不具合は `docs/SPECIFICATION.md` と
`.agents/Knowledge.md` にのみ保持する。ルート `AGENTS.md` には移記しない。

---

## 2. 読み込み順序（コンテキスト最小化）

1. **常駐**: ルート `AGENTS.md` §1〜§4 ＋ `.agents/Knowledge-important.md`
2. **付録（該当時のみ）**: ルート `AGENTS.md` §5〜§8 / `.agents/Project.md` /
   `.agents/Knowledge.md` / `docs/SPECIFICATION.md`
3. **禁止**: ログ・ソースの全文出力。行指定・範囲指定・キーワード検索を徹底する。

---

## 3. 3 つの排除ルール（_protocol 正本: ルート `AGENTS.md`）

| ルール | 正本 | 要約 |
|---|---|---|
| コンテキスト最小化 | ルート `AGENTS.md` §1.1 | 常駐は §1〜§4 のみ。付録は該当節だけ読む |
| Anti-Fallacy（テキサスの狙撃兵） | ルート `AGENTS.md` §1.2 | 事前仮説と合否基準を宣言せず変更／実行しない。偶然動作を完了としない |
| 自律実行ループ | ルート `AGENTS.md` §1.3 | Observe→Hypothesize→Execute→Verify→Distill→Finalize の6フェーズ |

**ループ状態の正本は `.agents/Project.md` §「Loop State」** であり、_protocol の
定義（フェーズ内容・リトライ上限・ロールバック規則）はルート `AGENTS.md` §1.3 に
ある。両者は同一内容を持たない。

---

## 4. ファイル命名規約

- `.agents/Project.md`（大文字 `P`）、`.agents/Knowledge.md`、
  `.agents/Knowledge-important.md`、`.agents/AGENTS.md`（大文字 `AGENTS`）で固定する。
- ルート側のプロトコル正本は `AGENTS.md`（**大文字**）。大文字小文字を混ぜない。
  case-sensitive なファイルシステム（Linux / macOS / CI runner）でも解決できること。
