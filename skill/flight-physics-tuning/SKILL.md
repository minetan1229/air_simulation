---
name: flight-physics-tuning
description: 6DoF飛行力学、空力モデル（失速・地面効果・モーフィング・頂点プッシュオーバー）、プリセット調整および物理バグ解析・診断を行う手順。飛行挙動の不具合調査や物理パラメータチューニング要求時にトリガーする。
prerequisites:
  - physics/aero_domain.py
  - physics/flight_sim.py
  - physics/config_schema.py
  - tools/ 内の診断スクリプト群
---

# Flight Physics Tuning Skill

紙飛行機シミュレーターの空気力学（Aero）、6自由度（6DoF）運動方程式、受動的空力弾性モーフィング、および頂点プッシュオーバーの挙動診断とチューニング手順書です。

## 1. Prerequisites (前提条件)
- `physics/config_schema.py` の物理データクラス構成を理解していること
- `physics/aero_domain.py` および `physics/flight_sim.py` の計算フローを理解していること
- `tools/` 配下の診断スクリプトを活用できる状態であること

## 2. Trigger Conditions (発火条件)
- 「飛行が途中で不自然に反転する」「頂点で滑空へ移行しない」「失速挙動がおかしい」といった物理バグ報告
- 機体プリセット（Classic Dart, Suzanne, White Wings 等）のパラメータ調整
- モーフィング減衰モデルや $C_m$, $C_L$, $C_D$ 関連の数理ロジック改善指示

## 3. Step-by-Step Workflow (実行手順)

### Step 1: 現状挙動のタイムライン抽出・診断
`tools/` 配下の診断スクリプトを実行し、ピッチ角、迎角、速度、モーフィング変形の推移を時系列で確認します。
```powershell
python tools/inspect_timeline.py
python tools/diagnose_horizontal_transition.py
```
- 頂点（$V < 2.0\text{ m/s}$）における $x_{cp}$ 移動およびモーフィング復元 $\delta_{\text{eff}}(q)$ が正常に作用しているか確認します。

### Step 2: プリセット別シミュレーション評価
全プリセットの飛行距離、滞空時間、最高到達高度を一括でシミュレーション・スコアリングします。
```powershell
python tools/evaluate_all_presets.py
```

### Step 3: 物理ドメイン単体検証
変更を加えるドメイン（空力・飛行力学）のテストを実行し、理論的整合性を維持します。
```powershell
python -m pytest tests/test_aero.py tests/test_flight.py -v
```

### Step 4: 物理監査テストの実行
物理法則（エネルギー保存、地面貫通防止、抗力極小値、安定性微係数など）の整合性を検証します。
```powershell
python -m pytest tests/test_physics_audit.py tests/test_physical_laws_audit.py -q
```

## 4. Constraints & Prohibitions (制約・禁止事項)
- **マジックナンバーのハードコード禁止**: 物理パラメータや補正係数は必ず `config_schema.py` で定義し、APIやUIから可変・透過的に扱えるようにする。
- **変形角の固定クランプ禁止**: 受動的空力弾性モーフィングは材料非線形性に基づく滑らかなシグモイド減衰モデルを保つこと（AGENTS.md §2.1 準拠）。
- **特異点対策**: 速度 $V \to 0$ や極低速時の風圧中心計算でゼロ除算を発生させないこと。
