# 遺伝的アルゴリズム統合アーキテクチャ (Genetic Algorithm Integration Architecture)

> **概要**: 本ドキュメントは、紙飛行機シミュレーターにおける遺伝的アルゴリズム（GA）の統合方法、探索戦略、多段階評価パイプライン、および自律的メタ制御の実装詳細を技術的に解説する。

---

## 目次

1. [全体アーキテクチャ](#1-全体アーキテクチャ)
2. [ハイブリッド折り紙ゲノム](#2-ハイブリッド折り紙ゲノム)
3. [多段階評価パイプライン](#3-多段階評価パイプライン)
4. [CMA-ES による共分散適応](#4-cma-es-による共分散適応)
5. [リンケージブロッククロスオーバー](#5-リンケージブロッククロスオーバー)
6. [修復型突然変異](#6-修復型突然変異)
7. [アイランドモデルとメタ制御](#7-アイランドモデルとメタ制御)
8. [目的関数とフィットネス評価](#8-目的関数とフィットネス評価)
9. [チェックポイント・レジューム機構](#9-チェックポイントレジューム機構)
10. [ハードウェア階層とスケーリング](#10-ハードウェア階層とスケーリング)

---

## 1. 全体アーキテクチャ

### 1.1 設計思想

本システムは「研究プログラムのように振る舞う」遺伝的探索エンジンとして設計されている。単純な最適化ではなく、**段階的フィデリティ（忠実度）向上**、**アイランドモデルによる多様性維持**、**CMA-ESによる連続空間の効率的探索**、**自律的メタ制御による停滞検出と戦略転換**を組み合わせる。

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        ResearchRun (自律探索ラン)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Island 0   │  │   Island 1   │  │   Island 2   │  │   Island 3   │   │
│  │  (CMA-ES)    │  │  (CMA-ES)    │  │  (CMA-ES)    │  │  (CMA-ES)    │   │
│  │  max_fold=8  │  │  max_fold=8  │  │  max_fold=10 │  │  max_fold=12 │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │                 │            │
│         └─────────────────┼─────────────────┼─────────────────┘            │
│                           ▼                                                 │
│              ┌────────────────────────┐                                    │
│              │   Elite Migration      │  (2世代ごと、エリート2個体)          │
│              └───────────┬────────────┘                                    │
│                          ▼                                                │
│              ┌────────────────────────┐                                    │
│              │  Meta Controller       │  (停滞検出→構造探索シフト)           │
│              │  - Stagnation Counter  │                                    │
│              │  - Fold Step Increase  │                                    │
│              │  - Micro Flaps Enable  │                                    │
│              │  - Objective Reweight  │                                    │
│              └───────────┬────────────┘                                    │
│                          ▼                                                │
│              ┌────────────────────────┐                                    │
│              │  Decision Journal      │  (全自律決定をJSONLで記録)           │
│              └────────────────────────┘                                    │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 コアコンポーネント

| コンポーネント | ファイル | 責務 |
|--------------|---------|------|
| `ResearchRun` | `physics/ga_domain.py:1136` | 探索ラン全体の統括、予算管理、チェックポイント |
| `Island` | `physics/ga_domain.py:1049` | 独立した個体群、専用CMA-ES、乱数ストリーム |
| `Individual` | `physics/ga_domain.py:474` | ゲノム＋投擲パラメータ＋評価結果 |
| `GAConfig` | `physics/ga_config.py:413` | 全探索パラメータの単一正本 |
| `JournalWriter` | `physics/ga_domain.py:989` | 自律決定の不変ログ（JSONL） |

---

## 2. ハイブリッド折り紙ゲノム

### 2.1 ゲノム表現

紙飛行機は「メッシュのスケーリング」ではなく、**1枚の平らな用紙に対する順序付き折り操作のプログラム**として表現される。

```python
@dataclass(frozen=True)
class FoldStep:
    step_index: int                    # 手順番号（1始まり）
    crease_line: Tuple[Vec2, Vec2]     # 折り目線（正規化シート座標）
    fold_angle_deg: float              # 折り角度 [-180, +180]、符号で山/谷
    symmetry: str                      # "symmetric" | "left" | "right"
    layer_order: str                   # "auto" | "above" | "below"
    target_face_ids: Tuple[int, ...]   # この折り目が切るパネル
    group: Tuple[str, ...]             # 参照元グループ
    set_group: str                     # 新しく作るグループ名
    mountain: bool                     # True=山折り, False=谷折り
    label: str                         # 人間向けラベル
```

### 2.2 遺伝子構成

ゲノムは **離散的折り紙プログラム（3〜8ステップ）** と **連続パラメータ（9遺伝子）** のハイブリッドである。

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Individual Genotype                          │
├─────────────────────────────┬───────────────────────────────────────┤
│  Discrete: OrigamiGenome    │  Continuous: 9 Genes (CMA-ES space)  │
│  ┌─────────────────────┐    │  ┌────────────────────────────────┐  │
│  │ Step 1: Keel fold   │    │  │ main_wing_fold_angle_deg       │  │
│  │ Step 2: Nose fold   │    │  │ elevon_angle_deg               │  │
│  │ Step 3: Main wing   │    │  │ cg_offset_x_m                  │  │
│  │ Step 4: Elevon      │    │  ├────────────────────────────────┤  │
│  │ Step 5: Trim        │    │  │ throw_speed_mps                │  │
│  │ ... (3-8 steps)     │    │  │ release_elevation_deg          │  │
│  └─────────────────────┘    │  │ pitch_rate_radps               │  │
│                             │  │ roll_deg                       │  │
│  Written INTO crease prog   │  │ yaw_deg                        │  │
│  by apply_genes_to_genome() │  └────────────────────────────────┘  │
└─────────────────────────────┴───────────────────────────────────────┘
```

**継承される継続的遺伝子**（`physics/ga_domain.py:326-391`）：

| 遺伝子名 | 範囲 | 単位 | 説明 |
|---------|------|------|------|
| `main_wing_fold_angle_deg` | -180 〜 +180 | deg | 主翼折り角度（ゲノムに書き込まれる） |
| `elevon_angle_deg` | -45 〜 +45 | deg | 後縁トリム折り角度（ゲノムに書き込まれる） |
| `cg_offset_x_m` | -0.05 〜 +0.05 | m | 重心前後オフセット（+ = 後方） |
| `throw_speed_mps` | 5.0 〜 20.0 | m/s | 投擲速度 |
| `release_elevation_deg` | 0.0 〜 45.0 | deg | 仰角 |
| `pitch_rate_radps` | 0.0 〜 8.0 | rad/s | リリース時ピッチレート |
| `roll_deg` | -45.0 〜 +45.0 | deg | リリース時ロール |
| `yaw_deg` | -45.0 〜 +45.0 | deg | リリース時ヨー |

### 2.3 リンケージブロック（`LINKAGE_BLOCKS`）

```python
LINKAGE_BLOCKS = (
    ("main_wing", ("main_wing_fold_angle_deg", "elevon_angle_deg", "cg_offset_x_m")),
    ("throw",     ("throw_speed_mps", "release_elevation_deg")),
    ("attitude",  ("pitch_rate_radps", "roll_deg", "yaw_deg")),
)
```

- **main_wingブロック**: 主翼折り角・エレボン角・重心位置を**単位として継承**。実機では翼折り角とトリムは物理的に連動するため。
- **throwブロック**: 速度と仰角をペアで継承。
- **attitudeブロック**: 3軸の姿勢レートをまとめて継承。

---

## 3. 多段階評価パイプライン

### 3.1 4段階フィデリティ階層

探索予算を効率化するため、**安価なフィルタから高精度な評価へ段階的に絞り込む**。

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Multi-Fidelity Evaluation Pipeline                        │
├──────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  Generation N Population (e.g., 16 individuals per island)                  │
│           │                                                                   │
│           ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ TIER 0: Geometric Screen (Grip Filter)                              │    │
│  │  ├─ Cost: ~1ms/individual (pure geometry)                           │    │
│  │  ├─ Checks: Grip existence, CG in grip, Self-intersection,         │    │
│  │  │         Wing area, Span, Keel depth, Layer count                │    │
│  │  ├─ Output: grip_quality (0.0-1.0), penalty, feasible=True/False   │    │
│  │  └─ Survivors: Top N by grip_quality → Tier 1                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│           │                                                                   │
│           ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ TIER 1: Analytical Surrogate (evaluate_flight_surrogate)           │    │
│  │  ├─ Cost: ~0.5ms/individual (scalar math, no trajectory)           │    │
│  │  ├─ Model: Helmbold CLα → Parabolic drag polar → L/D_max → Range   │    │
│  │  ├─ Physics: Closed-form glide (no 6-DoF, no attitude dynamics)    │    │
│  │  ├─ Output: distance_m, flight_time_s, stress_index, status        │    │
│  │  └─ Survivors: Top N → Tier 2                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│           │                                                                   │
│           ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ TIER 2: Short Reference 6-DoF (RK4, dt=0.02s, 4s duration)        │    │
│  │  ├─ Cost: ~50ms/individual                                         │    │
│  │  ├─ Model: Full 13-state rigid body, adaptive RK4                  │    │
│  │  ├─ Wind: Calm (no turbulence)                                     │    │
│  │  ├─ Output: Full trajectory + all physics metrics                  │    │
│  │  └─ Survivors: Top N → Tier 3                                      │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│           │                                                                   │
│           ▼                                                                   │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ TIER 3: Full Fidelity 6-DoF (Turbulent, Jittered, Monte-Carlo)     │    │
│  │  ├─ Cost: ~200ms/individual × MC samples                           │    │
│  │  ├─ Model: 13-state RK4 + 3D Simplex turbulence + Thermal          │    │
│  │  ├─ Wind: Indoor (2m/s) + Gust (0.25) + Thermal (0.5)              │    │
│  │  ├─ Jitter: Speed±0.5, Elev±2°, Pitch±1, Roll±3, Yaw±3             │    │
│  │  ├─ MC Samples: 1-3 per individual (configurable)                  │    │
│  │  └─ Output: Mean metrics + failure flags per sample                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 予算配分（`GAConfig` 既定値）

| 階層 | 試行数 | 用途 | 選抜基準 |
|------|--------|------|----------|
| Tier 0 | 全個体 (全世代) | 幾何学的妥当性フィルタ | `grip_quality` 上位 |
| Tier 1 | 20 | 解析的サロゲート高速スクリーニング | サロゲート距離/滞空時間 上位 |
| Tier 2 | 1,000 | 短時間6-DoFリファレンス | 実飛行距離/滞空時間 上位 |
| Tier 3 | 100,000 | 完全忠実度評価（乱流・MC） | 最終ランキング・アーカイブ登録 |

**重要**: 予算は `GAConfig` で**全て外部設定**され、探索ロジックには**数値リテラルが一切ない**（`physics/ga_config.py:72-90`）。

---

## 4. CMA-ES による共分散適応

### 4.1 正規化ユニットボックスでの探索

CMA-ES は連続遺伝子空間（9次元）を `[0, 1]^9` の**正規化ユニットボックス**で探索する。物理境界は `GeneSpec.clip()` でクリップ。

```
Raw Genes (Physical)          Normalised [0,1]          CMA-ES Search Space
┌─────────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│ main_wing: -180..180│  ──►  │   0.0 .. 1.0    │  ──►  │  μ ∈ ℝ^9        │
│ elevon:   -45..45   │       │   0.0 .. 1.0    │       │  σ ∈ ℝ^9        │
│ cg_offset: -0.05.05 │       │   0.0 .. 1.0    │       │  C ∈ ℝ^(9×9)    │
│ throw:      5..20   │       │   0.0 .. 1.0    │       │  pc, ps ∈ ℝ^9   │
│ elev:      0..45    │       │   0.0 .. 1.0    │       │                 │
│ pitch:     0..8     │       │   0.0 .. 1.0    │       │                 │
│ roll:    -45..45    │       │   0.0 .. 1.0    │       │                 │
│ yaw:     -45..45    │       │   0.0 .. 1.0    │       │                 │
└─────────────────────┘       └─────────────────┘       └─────────────────┘
```

### 4.2 設定パラメータ（`ga_config.py`）

```python
DEFAULT_CMA_SIGMA0 = 0.15      # 初期ステップサイズ（遺伝子範囲の15%）
cma_lambda: int = 0            # 0 → 標準 4 + 3*ln(n) ルール
cma_mu: int = 0                # 0 → lambda / 2
```

### 4.3 平均ベクトル初期化

各アイランドの世代0で、`_bootstrap_island()` 内で個体群の遺伝子平均を計算し `cma.set_mean(mean)` で初期化。

---

## 5. リンケージブロッククロスオーバー

### 5.1 アルゴリズム

```python
def block_crossover(parent_a, parent_b, rng):
    child = {}
    assigned = set()
    # 1. 各ブロックを単位としてどちらかの親から継承
    for _name, members in LINKAGE_BLOCKS:
        source = parent_a if rng.random() < 0.5 else parent_b
        for member in members:
            child[member] = source[member]
            assigned.add(member)
    # 2. ブロックに属さない遺伝子（存在しないが将来用）は個別に継承
    for spec in GENE_SPECS:
        if spec.name in assigned: continue
        source = parent_a if rng.random() < 0.5 else parent_b
        child[spec.name] = source[spec.name]
    return child
```

### 5.2 効果

- **共分散学習の保護**: CMA-ES がブロック内で発見した相関（例：主翼折り角とエレボン角の連動）がクロスオーバーで破壊されない。
- **建設的組み合わせ**: 異なるアイランドで進化した「良い翼形状」と「良い投擲パラメータ」を組み合わせ可能。

---

## 6. 修復型突然変異

### 6.1 設計思想

「完全ランダムゲノムは約75%が把持不合格」→ 毎世代スクラッチから生成すると探索が進まない。**「合格済み親ゲノムを微小摂動」** する修復型突然変異を採用。

### 6.2 突然変異オペレータ（`physics/ga_domain.py:637-686`）

```python
def mutate_genome(genome, rng, max_steps, angle_sigma_deg=6.0,
                  crease_shift=0.02, micro_flaps=False,
                  fold_flip_rate=0.08, symmetry_change_rate=0.08):
    for step in genome.steps:
        # 1. 折り角度のガウス摂動
        angle = step.fold_angle_deg + rng.gauss(0.0, angle_sigma_deg)
        angle = clamp(angle, -180, 180)
        
        # 2. 山/谷 反転（8%確率）
        if rng.random() < fold_flip_rate:
            mountain = not mountain
            angle = abs(angle) if mountain else -abs(angle)
        
        # 3. 対称モード変更（8%確率）
        if rng.random() < symmetry_change_rate:
            symmetry = rng.choice(("symmetric", "left", "right"))
        
        # 4. 折り目線の微小平行移動
        du, dv = rng.gauss(0, crease_shift), rng.gauss(0, crease_shift)
        line = clamp_line(step.crease_line + (du, dv))
```

### 6.3 構造探索シフト（メタ制御発動時）

停滞検出時に `micro_flaps=True` で発動 → **微小調整フラップ（ウイングレット相当）を追加**：

```python
def _add_micro_flaps(steps, rng):
    count = 1 + (1 if rng.random() < 0.4 else 0)  # 1-2枚
    for offset in range(count):
        angle = rng.uniform(4, 16) * (±1)
        side = rng.uniform(0.30, 0.46)
        position = rng.uniform(0.62, 0.92)
        steps.append(FoldStep(..., label="micro flap", ...))
```

---

## 7. アイランドモデルとメタ制御

### 7.1 アイランド構成

```python
islands: int = 4                    # 並列アイランド数
migration_interval: int = 2         # 2世代ごとに移住
migration_elites: int = 2           # 各アイランドからエリート2個体
```

各アイランドは**独立した乱数シード・CMA-ES・個体群・最大折りステップ数・マイクロフラップ有無**を持つ。

### 7.2 エリート移住

```
Generation N                          Generation N+2
┌─────────────┐                       ┌─────────────┐
│ Island 0    │  Elite(2)  ────────►  │ Island 1    │
│ Best: 45.2m │                       │ receives    │
└─────────────┘                       └─────────────┘
     │                                    ▲
     │ Elite(2)                           │ Elite(2)
     ▼                                    │
┌─────────────┐                       ┌─────────────┐
│ Island 1    │  Elite(2)  ◄────────  │ Island 0    │
│ Best: 42.8m │                       │ receives    │
└─────────────┘                       └─────────────┘
```

移住先では**最悪個体と置換**（エリート保存＋新規血統導入の両立）。

### 7.3 自律メタ制御（停滞検出と構造探索シフト）

```
┌────────────────────────────────────────────────────────────────────┐
│                    Meta Controller Decision Logic                  │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  stagnation_counter ≥ stagnation_generations (既定10)              │
│           │                                                         │
│           ▼                                                         │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ TRIGGER: "stagnation_detected"                              │   │
│  │ DECISION: "structure_search_shift"                          │   │
│  │ CHANGES:                                                    │   │
│  │   - max_fold_steps += 2 (上限 max_fold_steps_limit=14)      │   │
│  │   - micro_flaps = True (微小調整フラップ有効化)              │   │
│  │   - stat_weights reweighted by sensitivity analysis         │   │
│  └─────────────────────────────────────────────────────────────┘   │
│           │                                                         │
│           ▼                                                         │
│  JournalWriter.record(generation, island_id,                       │
│     decision="structure_search_shift",                             │
│     trigger="stagnation_detected",                                 │
│     changes={"max_fold_steps": +2, "micro_flaps": True})           │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

**決定ジャーナル**（`research_journal.jsonl`）に**全自律決定を1行1JSONで追記**。再現性・監査性を担保。

---

## 8. 目的関数とフィットネス評価

### 8.2 相互排他的な2目的

| 目的 | キー | 指標 | 単位 | 参照目標 | Tier 2時間 | Tier 3時間 |
|------|------|------|------|----------|------------|------------|
| 距離最大化 | `distance` | `distance_m` | m | 88.31m (Dillon Ruble) | 4.0s | 12.0s |
| 滞空最大化 | `airtime` | `flight_time_s` | s | 29.2s (Takuo Toda) | 8.0s | 32.0s |

**フィットネス正規化**（`physics/ga_domain.py:853-855`）：

```python
# hybrid_median_mean (既定) の場合
fitness = (0.5 * mean + 0.5 * median) / reference_target
# fitness = 1.0 なら「世界記録と同等」
```

### 8.3 統計モード（`STAT_MODES`）

| モード | 式 | 特徴 |
|--------|-----|------|
| `mean` | 算術平均 | 外れ値に敏感 |
| `median` | 中央値 | ロバスト、劇的な一発に引っ張られない |
| `best` | 最大値 | エリート重視 |
| `hybrid_median_mean` | 0.5×mean + 0.5×median | **既定・バランス型** |

### 8.4 構造破壊判定（共有許容応力）

```python
def is_structural_failure(result, allowable_pa):
    status = result.get("status", "").lower()
    if status in ("diverged", "crashed", "failed", "error", "no-launch"):
        return True
    if allowable_pa and result.get("max_stress_pa", 0) > allowable_pa:
        return True
    return False
```

- 許容応力は **`physics.flight_sim.allowable_stress_pa` および `physics.stress_domain.paper_allowable_stress` の単一実装** を全経路で共有。
- 破壊個体のメトリクスは **0.0** に固定（報酬ハッキング防止）。

---

## 9. チェックポイント・レジューム機構

### 9.1 チェックポイント内容（`physics/ga_domain.py:1180-1190`）

```json
{
  "run_id": "abc123...",
  "fingerprint": "config_hash...",
  "generation": 42,
  "islands": [...],           // 各アイランドの全状態（個体群、CMA-ES、RNG状態）
  "hall_of_fame": [...],      // エリート個体
  "history": [...],           // 世代ごと統計
  "evaluation_count": 12345,
  "overall_values": [...],    // 全評価値履歴
  "rng_state": "base64_pickle"
}
```

### 9.2 原子的書き込み

```python
# checkpoint.py (GA v2) と同様のパターン
tmp_path = checkpoint_path.with_suffix(".tmp")
with open(tmp_path, "w") as f:
    json.dump(state, f)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp_path, checkpoint_path)  # 原子的置換
```

### 9.3 ホットマイグレーション（ラップトップ → サーバー）

```python
# 1. ラップトップでチェックポイント作成
runner.run(100)  # generation_000100.json 生成

# 2. サーバーでロード＋プロファイル置換
runner = EvolutionRunner(profile=ScalingProfile.cluster_20m(...))
runner.load("generation_000100.json")
# → archive, elites 保持、個体群を新population_sizeにパディング
runner.run(900)  # 100万個体・1億セル格子で継続
```

---

## 10. ハードウェア階層とスケーリング

### 10.1 3ティア構成（`physics/ga2/scaling.py`）

| パラメータ | `small_long` (1×8GB) | `workstation` (1-2×24-48GB) | `cluster_20m` (4-8×48-94GB) |
|-----------|---------------------|----------------------------|----------------------------|
| 人口/世代 | 10,000 | 30,000 | 100,000 |
| 目標世代数 | 2,000-3,000 | 2,500-3,000 | 3,000-5,000 |
| Stage 1チャンク | 5,000×2 | 15,000×2 | 25,000×4 |
| Stage 2通過スロット | 500 | 1,500 | 5,000 |
| Stage 3通過スロット | 10 | 25 | 80-100 |
| Stage 3格子 | 5-8M cells | 25M cells | 100-150M cells |
| マルチGPU戦略 | 1 GPU 直列 | 1 GPU/個体 | 1 GPU/個体 |

### 10.2 自動検出と明示固定

```python
profile = detect_profile()  # CUDAデバイスから自動判定
# または明示固定
profile = ScalingProfile.cluster_20m(8, 80 * 2**30)
```

### 10.3 VRAMプール（`physics/ga2/vram.py`）

- 世代ループ開始前に**単一フラットバッファを確保**
- 名前付きワークスペースを**ビューとして切り出し**
- ループ内でのアロケーション**ゼロ**（フラグメンテーション防止）

---

## 付録: 主要ファイルマップ

```
physics/
├── ga_domain.py        # GAエンジン本体（ResearchRun, Island, Individual, オペレータ）
├── ga_config.py        # 設定契約（GAConfig, YAMLパーサー, 全デフォルト値）
├── ga_cma.py           # CMA-ES実装（共分散適応戦略）
├── origami_genome.py   # 折り紙ゲノム・把持フィルタ・レシピ生成
├── parallel_engine.py  # 並列評価（サロゲート/6-DoF/GPUアンサンブル）
├── flight_sim.py       # 6-DoFリファレンス積分（RK4, 13状態）
├── ga2/                # GA v2: 大規模探索・LBM・MAP-Elites
│   ├── runner.py       # EvolutionRunner
│   ├── genome.py       # ハイブリッド折り遺伝子
│   ├── stage0-3.py     # 4段階パイプライン
│   ├── lbm.py          # D3Q19 LBM + Immersed Boundary
│   ├── archive.py      # MAP-Elites + Pareto前線
│   ├── surrogate.py    # SQLite蓄積 + Ridge回帰補正
│   └── checkpoint.py   # 原子的チェックポイント + ホットマイグレーション
```

---

## 参考文献・関連仕様

- `docs/SPECIFICATION.md` §1.5 (物理モデル正本), §2 (GPU並列), §8 (エージェント役割)
- `docs/GA_V2_ARCHITECTURE.md` (GA v2 詳細アーキテクチャ)
- `docs/PHYSICS_MODELS.md` (物理モデル正本：式→出典→モジュール→適用限界)
- `physics/ga_config.py` (全探索パラメータの単一正本)
- `physics/ga_domain.py` (GAエンジン実装)
- `physics/ga2/scaling.py` (ハードウェア階層プロファイル)

---

*最終更新: 2026-11-12 | バージョン: 1.0 | 正本: `docs/GA_ALGORITHM.md`*