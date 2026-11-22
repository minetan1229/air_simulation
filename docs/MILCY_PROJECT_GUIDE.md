# milcy プロジェクト実践ガイド

> [!TIP]
> **中高生・一般読者・入門者の方へ**:  
> 紙飛行機が飛ぶ理由やAIの仕組み（ポケモン図鑑や将棋AIのたとえ、高校物理とのつながりなど）を直感的にわかりやすく解説した入門ガイドはこちらをご覧ください！  
> 👉 [**高校生でもわかる！AI紙飛行機研究所「milcy」完全入門**](MILCY_FOR_HIGH_SCHOOL.md)

## 1. はじめに (Introduction)

### 1.1 milcy アルゴリズム / milcyproject とは何か

**milcy** (旧称 **v3 パイプライン**) は、紙飛行機の**形態創発 (Morphogenetic Emergence)** を実現するための **Quality-Diversity (QD) 探索アーキテクチャ** を実装した自律研究パイプラインです。従来の「単一の最良解を探す」遺伝的アルゴリズム (GA) とは根本的に異なり、**多様な高性能解を同時に発見・保存する**ことを目的とします。

本システムの核心的な特徴は以下の 3 点です:

| 特徴 | 従来の GA | milcy (QD / MAP-Elites) |
|------|-----------|------------------------|
| **探索目的** | 単一のグローバル最適解 | **行動空間全体を照明** (Illumination) する多様なエリート集団 |
| **多様性の扱い** | 適応度関数へのペナルティ項やニッチングで事後的に確保 | **行動記述子 (Behavior Descriptor) グリッド** に明示的に配置・管理 |
| **解の価値** | 「最良の 1 個」のみが価値を持つ | **1,000 個のニッチそれぞれの最良解** がすべて価値を持つ |

milcy では**「翼面荷重 (Wing Loading)」「静安定余裕 (Static Stability Margin)」「アスペクト比 (Aspect Ratio)」**の 3 次元行動空間を **10×10×10 = 1,000 ニッチ** のグリッドで離散化し、各ニッチに**「そのニッチで最高性能の機体」**を保存します。これにより、設計者は「長距離滑空向け」「安定性重視」「高機動」など、**目的に応じた多様な機体設計図を一度の探索で入手**できます。

### 1.2 パイプライン全体像

milcy パイプラインは **4 つのステージ** で構成されます。各ステージは独立して実行可能ですが、前段の出力を後段の入力として連鎖させることで、形態生成から実環境検証までを一気通貫で自動化します。

```mermaid
flowchart TD
    %% Stage 1
    subgraph S1 [Stage 1: 20工程パラメトリック折り紙文法 + MCTS]
        S1A[パラメトリック設定<br/>ParametricOrigamiConfig] --> S1B[MCTS探索<br/>MCTSOrigamiGenerator]
        S1B --> S1C[折り紙ゲノム生成<br/>OrigamiGenome]
        S1C --> S1D[幾何コンパイル・Tier 0 検証<br/>OrigamiAdapter + fold_genome]
        S1D --> S1E[有効な機体候補群<br/>AircraftCandidate]
    end

    %% Stage 2
    subgraph S2 [Stage 2: 3D MAP-Elites 行動アーカイブ]
        S1E --> S2A[初期集団ブートストラップ<br/>bootstrap_population]
        S2A --> S2B[反復照明探索<br/>run_iterations]
        S2B --> S2C[決定論的 6DoF 水平ベンチマーク<br/>DeterministicFlightBenchmark]
        S2C --> S2D[3D 行動グリッドへの挿入<br/>MAPElitesGridArchive]
        S2D --> S2E[グリッド覆盖率・エリート集団<br/>Coverage & Elites]
    end

    %% Stage 3
    subgraph S3 [Stage 3: Nelder-Mead 局所トリム最適化]
        S2E --> S3A[上位エリート選択<br/>get_elites top-k]
        S3A --> S3B[シンプレックス探索<br/>NelderMeadTrimOptimizer]
        S3B --> S3C[エレボン角・上反角の最適化<br/>elevon [-4,8]°, dihedral [0,8]°]
        S3C --> S3D[トリム済みエリート群<br/>TrimResult]
    end

    %% Stage 4
    subgraph S4 [Stage 4: 多次元頑健性ストレステスト & エクスポート]
        S3D --> S4A[Dryden 乱流テスト<br/>wind=2m/s, gust=0.8]
        S4A --> S4B[射出摂動テスト<br/>roll±5°, yaw∓3°]
        S4B --> S4C[高ピッチ投射テスト<br/>launch 18° climb]
        S4C --> S4D[頑健性スコア算出<br/>RobustnessReport]
        S4D --> S4E[成果物出力<br/>.obj .json .jsonl]
    end

    %% Styling
    classDef stage1 fill:#e3f2fd,stroke:#1565c0,stroke-width:2px;
    classDef stage2 fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px;
    classDef stage3 fill:#fff3e0,stroke:#ef6c00,stroke-width:2px;
    classDef stage4 fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px;
    class S1A,S1B,S1C,S1D,S1E stage1;
    class S2A,S2B,S2C,S2D,S2E stage2;
    class S3A,S3B,S3C,S3D stage3;
    class S4A,S4B,S4C,S4D,S4E stage4;
```

---

## 2. アーキテクチャと 4 つのステージ (Pipeline Architecture)

### 2.1 Stage 1: MCTS 折り紙文法と幾何生成

**実装モジュール**: `geometry.v3_origami` (`adapter.py`, `compiler.py`, `mcts.py`)  
**互換エイリアス**: `geometry.milcy_origami`, `v3.origami`

#### 2.1.1 20工程パラメトリック折り紙文法

milcy では紙飛行機の折り工程を **3 フェーズ・計 20 ステップ** のパラメトリック文法で定義します。これは「任意の折り紙を生成する」のではなく、**「飛行可能な紙飛行機の構造を持つ折り紙のみを生成する」**ためのドメイン固有言語 (DSL) です。

| フェーズ | 役割 | パラメータ (ParametricOrigamiConfig) | 物理的意義 |
|----------|------|--------------------------------------|------------|
| **Phase 1: Nose (鼻部多層折り)** | 重心前方移動・先端剛性確保 | `fold_count` (3〜6), `taper_ratio` (0.4〜0.85), `nose_length_ratio` (0.20〜0.35) | 機首の層数で重心を前方へシフトし、スタールマージンを稼ぐ |
| **Phase 2: Keel (竜骨・ロック折り)** | 把持部 (グリップ) の構造的保証 | `keel_depth_ratio` (0.06〜0.14), `has_lock_fold` (bool), `crease_offset_u` (0.48〜0.52) | **Tier 0 フィルタ** (keel 高さ ≥ 8mm, 長さ ≥ 30mm, 層数 ≥ 2) を構造的に満たす |
| **Phase 3: Wing (主翼・エレボン・ウイングレット・上反角)** | 空力性能・安定性の連続パラメータ | `wingspan_ratio` (0.55〜0.88), `dihedral_deg` (-2〜10°), `elevon_left/right_deg` (-5〜12°), `winglet_angle_deg` (15〜80°), `has_winglet` | Stage 3 でさらに連続最適化される可変パラメータ |

#### 2.1.2 MCTS (Monte Carlo Tree Search) による骨格探索

`MCTSOrigamiGenerator` クラスが、上記パラメータ空間を **高速幾何ヒューリスティック** (非 6DoF) で探索し、Tier 0 を通過する「有効な骨格」を効率的に発見します。

- **UCB1 選択**: `exploit + c * sqrt(ln(parent_visits) / visits)` (c = 1.414)
- **展開アクション**: 6 種類のパラメータ摂動 (nose_fold±1, taper±0.1, keel_depth±0.02, lock_fold toggle, span±0.08, dihedral±2°)
- **ロールアウト評価**: `GeometricStaticScore` = 0.4×CGマージン + 0.3×左右対称性 + 0.3×翼面荷重妥当性
- **Tier 0 刈り取り**: 把持不可・貫通・ステップ数違反は即座に価値 0 として打ち切り

#### 2.1.3 幾何コンパイルと Tier 0 検証

`OrigamiAdapter.compile_candidate()` がパラメトリック設定を以下の流れで実体化します:

1. `ParametricOrigamiCompiler` → 20 ステップの `FoldStep` 列 (OrigamiGenome)
2. `fold_genome()` → 実際の折り紙物理シミュレーション (ヒンジツリー評価、層順序解決、メッシュ構築)
3. `extract_airframe_geometry()` → 質量・慣性テンソル・翼面積・スパン・弦長・重心・竜骨寸法を抽出
4. `validate_tier0()` → **ステップ数 16〜24**、**keel 高さ ≥ 8mm / 長さ ≥ 30mm / 層数 ≥ 2**、**自己貫通なし**を強制

Tier 0 を通過した個体のみが `AircraftCandidate` として Stage 2 へ渡されます。

---

### 2.2 Stage 2: 3D MAP-Elites グリッドと 6DoF ベンチマーク

**実装モジュール**: `optimization.map_elites` (`engine.py`, `grid.py`, `mutation.py`)

#### 2.2.1 行動記述子 (Behavior Descriptor) と 3D グリッド

各機体は以下の 3 次元ベクトルで特徴づけられ、グリッド上のニッチにマッピングされます:

```python
@dataclass(frozen=True)
class BehaviorDescriptor:
    wing_loading_kg_m2: float    # 翼面荷重 [kg/m²]  = mass / wing_area
    static_stability: float      # 静安定余裕 [0-1]    = ピッチ復元係数の正規化
    aspect_ratio: float          # アスペクト比 [-]     = span² / wing_area
```

**デフォルトグリッド設定** (`config/milcy_map_elites.yaml`):

| 次元 | ビン数 | 範囲 | 物理的意味 |
|------|--------|------|------------|
| Wing Loading | 10 | [0.118, 0.128] kg/m² | 軽量機〜重量機のスペクトル |
| Static Stability | 10 | [0.70, 0.90] | わずかに不安定〜強安定 |
| Aspect Ratio | 10 | [0.215, 0.255] | 低アスペクト比〜高アスペクト比 |

総ニッチ数 = **10 × 10 × 10 = 1,000**。各ニッチには「そのニッチで最高の composite_score を持つ機体」が 1 つだけ保存されます。

#### 2.2.2 決定論的 6DoF 水平ベンチマーク

`DeterministicFlightBenchmark` は **風なし・固定射出条件** での 2 点評価を行い、ズームクライミングや着地バウンス等のチートを防ぎます。

| 条件 | 値 | 固定理由 |
|------|-----|----------|
| 射出ピッチ | 0.0° | 上向き投射による稼ぎ防止 |
| 射出ロール/ヨー | 0.0° | 姿勢制御の公平性 |
| 射出高度 | 1.5 m | 着地までの十分な滑空時間 |
| 風速 | 0.0 m/s | 環境ノイズの排除 |
| 評価速度 | 6.0 m/s (低速), 10.0 m/s (中速) | 低速安定性と高速効率の両面評価 |
| シミュレーション時間 | 6.0 s | 十分な滑空・着地観測 |
| 積分時間刻み | ≤ 0.02 s | 数値安定性 |

**評価指標 (EvaluationMetric)**: 各速度で以下を計測

- `flight_time_s`: 滞空時間
- `max_ld_ratio`: 最大滑空比 (L/D)
- `steady_glide_fraction`: 定常滑空区間の割合
- `stability`: 姿勢安定性指標
- `distance_m`: 水平飛行距離
- `crashed / diverged / landed`: 終端状態

**合成スコア (composite_score)**:

```
Score = T_air × sqrt(max_ld_ratio) × steady_glide_fraction × stability
```

- `crashed/diverged/failed` → Score = 0
- `max_ld_ratio` は 40.0 でクランプ (非現実的値の排除)

#### 2.2.3 MAP-Elites 照明ループ

`MAPElitesEngine.run_iterations()` の 1 イテレーション:

1. **親選択**: 既存エリートからランダム選択
2. **変異**: `mutate_parametric_config()` でパラメトリック摂動 (mutation_rate = 0.35 デフォルト)
3. **コンパイル & Tier 0**: `OrigamiAdapter` → `validate_tier0()` (不合格ならスキップ)
4. **6DoF 評価**: `DeterministicFlightBenchmark.evaluate_candidate()`
5. **グリッド挿入**: `MAPElitesGridArchive.add_candidate()` → ニッチ座標計算 → 現エリートより高スコアなら置換
6. **進捗コールバック**: 覆蓋率・ベストスコア・ニッチ数を JSONL ストリーム出力

**タイブレーク規則**: 同一ニッチ・同スコアの場合、より新しい候補で置換 (探索の多様性維持)。

---

### 2.3 Stage 3: Nelder-Mead による連続トリム最適化

**実装モジュール**: `optimization.trim.nelder_mead` (`NelderMeadTrimOptimizer`)

Stage 2 で発見された離散的エリートは、折り紙骨格 (Phase 1-2) は固定されたまま、**連続パラメータのみ**を局所最適化します。

| 最適化変数 | 範囲 | 物理的役割 |
|------------|------|------------|
| **エレボン角** (左右同値) | -4.0° 〜 +8.0° | ピッチトリム・揚力係数調整 |
| **上反角** | 0.0° 〜 8.0° | ロール安定性・ダッチロール抑制 |

**アルゴリズム**: 2 変数 Nelder-Mead シンプレックス法

- 初期シンプレックス: `(e0, d0)`, `(e0+1.5, d0)`, `(e0, d0+1.5)`
- 反射係数 α = 1.0, 拡張係数 γ = 2.0, 収縮係数 ρ = 0.5, 縮小係数 σ = 0.5
- 収束判定: `abs(worst_cost - best_cost) < 1e-4`
- 最大反復: 25 回 (設定可能)

**出力**: `TrimResult` に `optimal_elevon_deg`, `optimal_dihedral_deg`, `initial_score`, `optimized_score`, `converged` を記録。改善があれば候補の設定を更新。

---

### 2.4 Stage 4: Dryden 乱流下でのロバストネス検証とエクスポート

**実装モジュール**: `optimization.eval.robustness` (`RobustnessEvaluator`)

実環境を模倣した 3 種類のストレステストを実施し、総合頑健性スコアを算出します。

#### 2.4.1 テストシナリオ

| テスト | 環境 / 射出条件 | 合格基準 | 重み |
|--------|----------------|----------|------|
| **Dryden 乱流** | 風速 2.0 m/s, ガスト強度 0.8, 粗さ 0.01 m<br>射出 8 m/s, 水平, 高度 1.5 m | `crashed/diverged` でないこと | 40% |
| **射出摂動 (Jitter)** | 無風<br>射出 8 m/s, ピッチ 2°, ロール +5°, ヨー -3°, ピッチレート 0.5 rad/s | `crashed/diverged` でないこと | 30% |
| **高ピッチ投射** | 無風<br>射出 9 m/s, ピッチ 18° (上昇), 高度 1.5 m | スタールせず墜落しないこと (`recovered`) | 30% |

#### 2.4.2 頑健性スコア計算

```
overall = 0.4 × (1.0 if dryden_survived else 0.2)
        + 0.3 × (1.0 if jitter_survived else 0.2)
        + 0.3 × (1.0 if high_pitch_recovered else (0.5 if not crashed else 0.0))
```

#### 2.4.3 成果物エクスポート

上位 `top_k` (デフォルト 5) 機体について以下を出力:

| ファイル | 形式 | 内容 |
|----------|------|------|
| `elite_rank{N}_{id}.json` | JSON | 全メタデータ: 設定・幾何・ベンチマーク・トリム・頑健性レポート |
| `elite_rank{N}_{id}.obj` | Wavefront OBJ | 実折り紙 3D メッシュ (頂点・法線・面・UV・層情報) |
| `v3_search.jsonl` | JSON Lines | 全イテレーションの進捗ログ (覆蓋率・ベストスコア・ニッチ数・タイムスタンプ) |

---

## 3. クイックスタート (Quick Start)

### 3.1 前提環境

| 要件 | バージョン / 備考 |
|------|------------------|
| Python | 3.10 以上 (3.11/3.12 推奨) |
| NumPy | ≥ 1.24 |
| SciPy | ≥ 1.10 (Nelder-Mead 等で使用) |
| PyYAML | ≥ 6.0 (設定ファイル読み込み) |
| tqdm | 任意 (プログレスバー表示用) |

**インストール**:

```bash
pip install numpy scipy pyyaml tqdm
```

### 3.2 最もシンプルな実行コマンド (ワンライナー)

```bash
python -m optimization.cli --iterations 10 --seed 42 --output-dir output/milcy_test
```

これだけで **10 イテレーションの完全パイプライン** が実行され、`output/milcy_test/` に成果物が生成されます。

### 3.3 スモークテスト・短時間動作確認

開発・デバッグ用に最小構成で動作を確認する場合:

```bash
# 設定ファイルを明示して 5 イテレーションのみ実行
python -m optimization.cli \
  --config config/milcy_map_elites.yaml \
  --iterations 5 \
  --seed 123 \
  --output-dir output/smoke \
  --top-k 1 \
  --trim-iter 5
```

**期待される出力例**:

```
======================================================================
  AIR SIMULATION: milcy ALGORITHM AUTONOMOUS RESEARCH PIPELINE
  (Quality Diversity MAP-Elites + MCTS Origami + Nelder-Mead Trim)
======================================================================
  Random Seed           : 123
  Search Iterations     : 5
  Mutation Rate         : 0.35
  Grid Bins             : (10, 10, 10) (1000 niches)
  Grid Ranges           : ((0.118, 0.128), (0.7, 0.9), (0.215, 0.255))
  Benchmark Duration    : 6.0s
  Trim Optimization     : 5 iterations
  Output Directory      : /path/to/output/smoke
======================================================================

[Stage 1] Bootstrapping 20-step origami skeletons with MCTS...
  Initialized 3 compliant niche elites in 2.34s.

[Stage 2] Running 3D MAP-Elites illumination search...
  Iteration 0005/0005 | Niches: 012 (1.2%) | Best Score: 4.231
  MAP-Elites illumination completed.

[Stage 3] Optimizing top elites with Nelder-Mead continuous trim...
  Trimming Elite #1 (map-elite-0003)...
    Score: 4.231 -> 4.512 | Elevon: 1.23 deg | Dihedral: 3.45 deg

[Stage 4] Performing Stage 4 robustness stress tests and exporting assets...
  Elite #1 [map-elite-0003]:
    Hang-Time Score  : 4.512
    Dryden Survival  : 100%
    Robustness Score : 0.850
    Exported Files   : elite_rank1_map-elite-0003.json, elite_rank1_map-elite-0003.obj

======================================================================
  ALL V3 STAGES COMPLETED SUCCESSFULLY
  Search Log        : /path/to/output/smoke/v3_search.jsonl
======================================================================
```

---

## 4. 設定ファイル詳解 (`config/milcy_map_elites.yaml`)

### 4.1 ファイル全体構造

```yaml
# Configuration for milcy Algorithm (formerly v3) 3D MAP-Elites Autonomous Research Framework
# Production deterministic configuration tuned for high-resolution illumination

search:
  seed: 42                    # 乱数シード (全ステージ共通の基準シード)
  bootstrap_count: 8          # MCTS ブートストラップで生成する初期エリート数
  mcts_iterations: 40         # MCTS 探索イテレーション数 (骨格品質向上のため増やせる)
  map_elites_iterations: 100  # MAP-Elites 照明イテレーション数 (本探索の規模)
  mutation_rate: 0.35         # パラメトリック変異率 (0.0-1.0)

grid:
  bins: [10, 10, 10]          # 3D グリッド分割数 (Wing Loading, Stability, Aspect Ratio)
  wing_loading_range: [0.118, 0.128]   # 翼面荷重 [kg/m²] の探索範囲
  stability_range: [0.70, 0.90]        # 静安定余裕 [0-1] の探索範囲
  aspect_ratio_range: [0.215, 0.255]   # アスペクト比 [-] の探索範囲

benchmark:
  low_speed_mps: 6.0          # 低速評価点 [m/s]
  mid_speed_mps: 10.0         # 中速評価点 [m/s]
  duration_s: 6.0             # シミュレーション時間 [秒]
  dt_max: 0.02                # 最大積分時間刻み [秒]
  launch_height_m: 1.5        # 射出高度 [メートル]

trim:
  enabled: true               # トリム最適化の有効/無効
  max_iterations: 25          # Nelder-Mead 最大反復回数
  elevon_bounds_deg: [-4.0, 8.0]      # エレボン角探索範囲 [度]
  dihedral_bounds_deg: [0.0, 8.0]     # 上反角探索範囲 [度]

evaluation:
  dryden_duration_s: 6.0      # Dryden テスト時間 [秒]
  top_k_export: 5             # エクスポートする上位エリート数
  output_dir: "output/milcy"  # 出力ディレクトリ (CLI --output-dir で上書き可能)
```

### 4.2 各セクション詳細とチューニング指針

#### `search` セクション

| パラメータ | デフォルト | 推奨範囲 | チューニング指針 |
|------------|------------|----------|------------------|
| `seed` | 42 | 任意の整数 | 再現性確保用。複数ランで統計を取る場合は変える |
| `bootstrap_count` | 8 | 5〜20 | 多いほど初期多様性↑、Stage 1 時間↑。8〜12 がバランス良い |
| `mcts_iterations` | 40 | 30〜100 | 骨格品質に直結。夜間バッチなら 80〜100 推奨 |
| `map_elites_iterations` | 100 | 50〜500+ | **探索の主規模**。覆蓋率が飽和するまで増やす。100=クイック, 500=標準, 2000=大規模 |
| `mutation_rate` | 0.35 | 0.2〜0.5 | 高すぎ=探索的・不安定、低すぎ=局所停滞。0.3〜0.4 が安定 |

#### `grid` セクション

**グリッド範囲は「探索したい設計空間」を定義します。** 目的によって調整してください。

| 目的 | wing_loading_range | stability_range | aspect_ratio_range |
|------|-------------------|-----------------|-------------------|
| **デフォルト (バランス)** | [0.118, 0.128] | [0.70, 0.90] | [0.215, 0.255] |
| **長距離滑空重視** | [0.08, 0.15] (広め) | [0.65, 0.85] (やや不安定許容) | [0.25, 0.35] (高AR重視) |
| **室内・低速飛行重視** | [0.10, 0.18] (軽量寄り) | [0.75, 0.95] (安定重視) | [0.15, 0.25] (低AR許容) |
| **高機動・曲技重視** | [0.12, 0.20] (重量寄り) | [0.60, 0.80] (不安定許容) | [0.20, 0.30] |

**ビン数 (`bins`)**:
- `[10,10,10]` = 1,000 ニッチ (標準・高速)
- `[15,15,15]` = 3,375 ニッチ (高解像度・メモリ/時間↑)
- `[8,8,8]` = 512 ニッチ (超高速・粗い照明)

#### `benchmark` セクション

| パラメータ | デフォルト | 注意点 |
|------------|------------|--------|
| `low_speed_mps` | 6.0 | これより低いとスタール判定が増え、スコアが 0 埋めになるリスク |
| `mid_speed_mps` | 10.0 | 高速すぎると構造破壊・発散が増える。8〜12 程度が安全 |
| `duration_s` | 6.0 | 短すくと着地前で打ち切り。長すくと計算コスト↑。5〜8 秒推奨 |
| `dt_max` | 0.02 | 固定ステップ RK4 等の上限。小さいほど精密・遅い |

#### `trim` セクション

| パラメータ | デフォルト | 物理的意味 |
|------------|------------|------------|
| `enabled` | true | false にすると Stage 3 をスキップ (高速化・デバッグ用) |
| `max_iterations` | 25 | 収束しきれない場合は 30〜40 に増やせる |
| `elevon_bounds_deg` | [-4.0, 8.0] | 負=アップエレボン(機首上げ), 正=ダウンエレボン(機首下げ) |
| `dihedral_bounds_deg` | [0.0, 8.0] | 負値(アネヘドラル)を許容するなら [-2.0, 8.0] 等に拡張可能 |

#### `evaluation` セクション

| パラメータ | デフォルト | 説明 |
|------------|------------|------|
| `dryden_duration_s` | 6.0 | Stage 4 の各テスト時間。benchmark.duration_s と合わせるのが無難 |
| `top_k_export` | 5 | エクスポートするエリート数。ディスク容量に応じて調整 |
| `output_dir` | "output/milcy" | CLI `--output-dir` で上書きされる基準パス |

---

## 5. CLI & Python API の使い方 (Execution & API)

### 5.1 CLI コマンドライン引数

```bash
python -m optimization.cli [OPTIONS]
```

| 引数 | 短縮形 | 型 | デフォルト | 説明 |
|------|--------|-----|------------|------|
| `--algorithm` / `--pipeline` | - | str | "milcy" | アルゴリズム識別子 (milcy/milkcy/v3/map_elites) |
| `--config` | - | str | 自動検出 | 設定 YAML パス (未指定時: milcy_map_elites.yaml → v3_map_elites.yaml の順で探索) |
| `--iterations` | - | int | 設定値 | MAP-Elites 照明イテレーション数 |
| `--seed` | - | int | 設定値 | 乱数シード (None なら設定値) |
| `--output-dir` | - | str | 設定値 | 成果物出力ディレクトリ |
| `--top-k` | - | int | 設定値 | トリム・エクスポート対象の上位エリート数 |
| `--trim-iter` | - | int | 設定値 | Nelder-Mead 最大反復回数 |

### 5.2 典型的な実行シナリオ

#### (A) デバッグ・開発用 (高速・少量)

```bash
python -m optimization.cli \
  --iterations 20 \
  --seed 42 \
  --output-dir output/debug \
  --top-k 1 \
  --trim-iter 10
```
- 所要時間: 約 30〜60 秒
- 用途: コード変更後の動作確認、パラメータ感度チェック

#### (B) 通常実験・論文用データ取得 (標準)

```bash
python -m optimization.cli \
  --config config/milcy_map_elites.yaml \
  --iterations 200 \
  --seed 2024 \
  --output-dir output/experiment_20241122 \
  --top-k 5 \
  --trim-iter 25
```
- 所要時間: 約 10〜20 分 (CPU コア数に依存)
- 期待覆蓋率: 15〜30% (1,000 ニッチ中 150〜300 埋まり)
- 用途: 設計空間の広範な照明、複数機体の比較検討

#### (C) 大規模夜間バッチ (最大品質)

```bash
# 設定ファイルで mcts_iterations=80, map_elites_iterations=2000 等にしておく
python -m optimization.cli \
  --config config/milcy_map_elites.yaml \
  --iterations 2000 \
  --seed 42 \
  --output-dir output/overnight_$(date +%Y%m%d) \
  --top-k 10 \
  --trim-iter 30
```
- 所要時間: 数時間〜半日
- 期待覆蓋率: 40〜60% 以上
- 用途: 最終成果物生成、設計カタログ作成

### 5.3 Python スクリプトからの直接呼び出し

CLI を介さず、自前の実験スクリプトや Jupyter Notebook からパイプラインを制御できます。

```python
"""カスタム実験スクリプト例: 複数シードで並列実行し、統計を取る"""
import json
from pathlib import Path
from optimization.cli import run_pipeline

# 共通設定
BASE_CONFIG = {
    "iterations": 500,
    "top_k": 5,
    "trim_iterations": 25,
    "grid_bins": (10, 10, 10),
    "grid_ranges": (
        (0.118, 0.128),
        (0.70, 0.90),
        (0.215, 0.255),
    ),
    "benchmark_duration_s": 6.0,
    "bootstrap_count": 8,
    "mcts_iterations": 40,
    "elevon_bounds": (-4.0, 8.0),
    "dihedral_bounds": (0.0, 8.0),
    "mutation_rate": 0.35,
}

# 複数シードで実行
seeds = [42, 123, 456, 789, 999]
results_summary = []

for seed in seeds:
    out_dir = Path(f"output/multi_seed/seed_{seed}")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n=== Running seed={seed} ===")
    run_pipeline(
        seed=seed,
        output_dir=str(out_dir),
        **BASE_CONFIG
    )
    
    # 生成された JSONL から最終統計を抽出
    jsonl_path = out_dir / "v3_search.jsonl"
    if jsonl_path.exists():
        with open(jsonl_path) as f:
            lines = [json.loads(l) for l in f]
        last = lines[-1]
        results_summary.append({
            "seed": seed,
            "final_coverage_pct": last["coverage_pct"],
            "final_niches": last["niches_filled"],
            "best_score": last["best_score"],
        })

# サマリー保存
with open("output/multi_seed/summary.json", "w") as f:
    json.dump(results_summary, f, indent=2)

print("\n=== Multi-seed Summary ===")
for r in results_summary:
    print(f"  Seed {r['seed']}: Coverage={r['final_coverage_pct']:.1f}%, Niches={r['final_niches']}, Best={r['best_score']:.3f}")
```

#### より低レベルな API 使用例 (ステージ個別実行)

```python
"""ステージごとに分解して実行・介入する上級者向け例"""
from geometry.v3_origami.adapter import OrigamiAdapter
from geometry.v3_origami.mcts import MCTSOrigamiGenerator
from optimization.map_elites.engine import MAPElitesEngine
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
from optimization.eval.robustness import RobustnessEvaluator
from physics.benchmark.benchmark import DeterministicFlightBenchmark
from core.interfaces import ParametricOrigamiConfig

# 1. アダプタ・ベンチマーク初期化
adapter = OrigamiAdapter()
benchmark = DeterministicFlightBenchmark(duration_s=6.0)
grid = MAPElitesGridArchive(
    bins=(10, 10, 10),
    ranges=((0.118, 0.128), (0.70, 0.90), (0.215, 0.255))
)

# 2. MCTS で初期骨格生成 (カスタムパラメータで)
mcts = MCTSOrigamiGenerator(adapter=adapter, seed=42)
initial_candidates = mcts.search_valid_candidates(iterations=50, top_k=8)

# 3. 手動で特定の候補をグリッドに投入 (例: 既知の良い設計をシード)
for cand in initial_candidates:
    benchmark.evaluate_candidate(cand)
    grid.add_candidate(cand)

# 4. MAP-Elites エンジンで照明探索
engine = MAPElitesEngine(
    archive=grid,
    adapter=adapter,
    benchmark=benchmark,
    seed=42,
    mutation_rate=0.35
)
best = engine.run_iterations(iterations=200)

# 5. 上位 3 機体をトリム最適化
trimmer = NelderMeadTrimOptimizer(
    adapter=adapter,
    benchmark=benchmark,
    max_iter=25,
    elevon_bounds=(-4.0, 8.0),
    dihedral_bounds=(0.0, 8.0)
)
elites = grid.get_elites()[:3]
for i, elite in enumerate(elites, 1):
    result = trimmer.optimize_trim(elite)
    print(f"Elite #{i}: {result.initial_score:.3f} -> {result.optimized_score:.3f}")

# 6. 頑健性評価 & 手動エクスポート
evaluator = RobustnessEvaluator(duration_s=6.0)
for i, elite in enumerate(elites, 1):
    report = evaluator.evaluate_candidate(elite)
    evaluator.export_candidate_json(elite, f"output/custom/elite_{i}.json")
    evaluator.export_candidate_obj(elite, f"output/custom/elite_{i}.obj")
    print(f"  Robustness: {report.overall_robustness_score:.3f}")
```

---

## 6. 出力結果の確認と活用 (Outputs & Visualizations)

### 6.1 出力ディレクトリ構造

```
output/milcy/
├── v3_search.jsonl           # 探索全イテレーションのストリームログ (JSON Lines)
├── elite_rank1_<id>.json     # エリート #1 の全メタデータ
├── elite_rank1_<id>.obj      # エリート #1 の 3D メッシュ (Wavefront OBJ)
├── elite_rank2_<id>.json
├── elite_rank2_<id>.obj
├── ...
└── elite_rankN_<id>.json/.obj
```

### 6.2 探索ログ (`v3_search.jsonl`) の読み方と分析

各行は 1 イテレーションのスナップショットです:

```json
{
  "iteration": 42,
  "niches_filled": 127,
  "coverage_pct": 12.7,
  "best_score": 5.234,
  "timestamp": 1732200123.456
}
```

**分析用 Python スニペット**:

```python
import json
import matplotlib.pyplot as plt

# ログ読み込み
data = []
with open("output/milcy/v3_search.jsonl") as f:
    for line in f:
        data.append(json.loads(line))

iters = [d["iteration"] for d in data]
coverage = [d["coverage_pct"] for d in data]
niches = [d["niches_filled"] for d in data]
best = [d["best_score"] for d in data]

# 覆蓋率推移プロット
fig, axes = plt.subplots(2, 2, figsize=(10, 8))
axes[0,0].plot(iters, coverage)
axes[0,0].set_title("Grid Coverage (%)")
axes[0,0].set_xlabel("Iteration")
axes[0,0].grid(True)

axes[0,1].plot(iters, niches)
axes[0,1].set_title("Niches Filled")
axes[0,1].set_xlabel("Iteration")
axes[0,1].grid(True)

axes[1,0].plot(iters, best)
axes[1,0].set_title("Best Composite Score")
axes[1,0].set_xlabel("Iteration")
axes[1,0].grid(True)

# スコア分布 (最終世代)
# ※最終世代の全エリートスコアを取るには grid.get_elites() を使う
axes[1,1].hist(best, bins=20, alpha=0.7)
axes[1,1].set_title("Best Score Distribution Over Time")
axes[1,1].set_xlabel("Score")

plt.tight_layout()
plt.savefig("output/milcy/analysis.png", dpi=150)
plt.show()
```

### 6.3 エリート機体 JSON の諸元データ解釈

`elite_rank1_<id>.json` の主要フィールド:

```json
{
  "candidate_id": "map-elite-0042",
  "step_count": 20,
  "config": {
    "nose": {"fold_count": 4, "taper_ratio": 0.62, "nose_length_ratio": 0.28},
    "keel": {"keel_depth_ratio": 0.095, "has_lock_fold": true, "keel_layers": 4},
    "wing": {"wingspan_ratio": 0.78, "dihedral_deg": 3.5, "elevon_left_deg": 1.2, "elevon_right_deg": 1.2, "has_winglet": true},
    "paper_gsm": 100.0
  },
  "geometry": {
    "mass_kg": 0.0048,
    "wing_area_m2": 0.0142,
    "wingspan_m": 0.176,
    "chord_m": 0.115,
    "aspect_ratio": 2.18,
    "cg_m": [0.038, 0.0, 0.004],
    "keel_height_m": 0.011,
    "keel_length_m": 0.058,
    "keel_layers": 4
  },
  "descriptor": {
    "wing_loading_kg_m2": 0.338,
    "static_stability": 0.78,
    "aspect_ratio": 2.18
  },
  "benchmark": {
    "low_speed": {"flight_time_s": 4.2, "max_ld_ratio": 8.5, "steady_glide_fraction": 0.85, "stability": 0.92, "deterministic_score": 5.12},
    "mid_speed": {"flight_time_s": 3.8, "max_ld_ratio": 9.1, "steady_glide_fraction": 0.78, "stability": 0.88, "deterministic_score": 5.34},
    "composite_score": 5.23,
    "valid": true
  },
  "metadata": {
    "tier0_valid": true,
    "trim_result": {"optimal_elevon_deg": 1.23, "optimal_dihedral_deg": 3.45, "initial_score": 4.89, "optimized_score": 5.23, "converged": true},
    "robustness_report": {"dryden_survival_rate": 1.0, "dryden_mean_hangtime_s": 4.1, "jitter_robustness_score": 1.0, "high_pitch_recovery_rate": 1.0, "overall_robustness_score": 0.92}
  }
}
```

**主要指標の見方**:

| 指標 | 良い値の目安 | 物理的意味 |
|------|-------------|------------|
| `composite_score` | > 4.0 (優秀 > 6.0) | 総合滑空性能 (滞空時間 × √L/D × 定常滑空率 × 安定性) |
| `max_ld_ratio` | 8〜12 (紙飛行機として現実的) | 最大滑空比。40 でクランプされるため異常値はカット済み |
| `steady_glide_fraction` | > 0.7 | 滑空の大半が定常状態 (チートでない証拠) |
| `stability` | > 0.8 | 姿勢が発散せず安定 |
| `wing_loading_kg_m2` | 0.1〜0.4 | 軽いほど低速で浮く、重いほど高速で伸びる |
| `aspect_ratio` | 1.5〜3.0 | 高いほど誘導抗力低減・滑空比向上 |
| `keel_height_m` | ≥ 0.008 (8mm) | Tier 0 必須。把持可能な厚み |
| `keel_layers` | ≥ 2 | Tier 0 必須。層数で剛性確保 |
| `overall_robustness_score` | > 0.8 | 実環境耐性。1.0 に近いほど頑健 |

### 6.4 3D メッシュ (`.obj`) の確認方法

#### Blender での確認 (推奨)

```bash
# Blender コマンドラインでインポート・レンダリング
blender --background --python-expr "
import bpy
bpy.ops.import_scene.obj(filepath='output/milcy/elite_rank1_xxx.obj')
# マテリアル設定・レンダリング等
bpy.ops.render.render(write_still=True)
"
```

#### Three.js / Web ビューアでの確認

```html
<!-- 簡易 HTML ビューア -->
<!DOCTYPE html>
<html>
<head>
  <script type="importmap">
  {"imports": {"three": "https://unpkg.com/three@latest/build/three.module.js"}}
  </script>
  <style>body{margin:0;overflow:hidden}canvas{display:block}</style>
</head>
<body>
<script type="module">
import * as THREE from 'three';
import {OBJLoader} from 'https://unpkg.com/three@latest/examples/jsm/loaders/OBJLoader.js';
import {OrbitControls} from 'https://unpkg.com/three@latest/examples/jsm/controls/OrbitControls.js';

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xf0f0f0);
const camera = new THREE.PerspectiveCamera(45, innerWidth/innerHeight, 0.01, 10);
camera.position.set(0.1, 0.15, 0.3);
const renderer = new THREE.WebGLRenderer({antialias: true});
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
new OrbitControls(camera, renderer.domElement);

// ライト
scene.add(new THREE.HemisphereLight(0xffffff, 0x444444, 2));
const dir = new THREE.DirectionalLight(0xffffff, 1);
dir.position.set(0.2, 0.5, 0.3);
scene.add(dir);

// OBJ 読み込み
const loader = new OBJLoader();
loader.load('elite_rank1_xxx.obj', (obj) => {
  obj.traverse((child) => {
    if (child.isMesh) {
      child.material = new THREE.MeshStandardMaterial({
        color: 0xcccccc, metalness: 0.1, roughness: 0.8, side: THREE.DoubleSide
      });
      child.castShadow = child.receiveShadow = true;
    }
  });
  scene.add(obj);
  
  // カメラをオブジェクトにフィット
  const box = new THREE.Box3().setFromObject(obj);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  camera.lookAt(center);
  const maxDim = Math.max(size.x, size.y, size.z);
  camera.position.set(center.x, center.y + maxDim*0.5, center.z + maxDim*1.5);
});

function animate() {
  requestAnimationFrame(animate);
  renderer.render(scene, camera);
}
animate();
</script>
</body>
</html>
```

#### OS 標準ビューア (Windows / macOS / Linux)

- **Windows**: 3D ビューア (標準搭載) で `.obj` をダブルクリック
- **macOS**: Preview.app で開ける (簡易表示)
- **Linux**: `globjview` (apt install globjview) や `meshlab` で確認

---

## 7. トラブルシューティング & FAQ (Troubleshooting & Best Practices)

### 7.1 グリッドの充足率 (Coverage) が上がらない

| 症状 | 考えられる原因 | 対策 |
|------|----------------|------|
| 覆蓋率 1% 未満で停滞 | `mutation_rate` が低すぎ / 高すぎ | 0.3〜0.4 に調整。`mcts_iterations` を増やし初期エリート品質を上げる |
| 特定次元のみ埋まらない | グリッド範囲が実現可能領域とズレている | `grid` 範囲を物理的に実現可能なレンジに拡張・シフト |
| ニッチ数は増えるがスコアが低い | Tier 0 が厳しすぎ / ベンチマーク条件が厳しい | `benchmark.low_speed_mps` を 5.0〜5.5 に下げる / `duration_s` を 8.0 に延ばす |
| 同一ニッチに同一機体が残り続ける | 変異が局所的すぎる | `mutation_rate` 上昇、または `mcts_iterations` 大幅増で多様な骨格を供給 |

**診断コマンド**: 現在のグリッド状態を可視化

```python
from optimization.map_elites.grid import MAPElitesGridArchive
from optimization.cli import _load_yaml_config

cfg = _load_yaml_config("config/milcy_map_elites.yaml")
grid_cfg = cfg["grid"]
grid = MAPElitesGridArchive(
    bins=tuple(grid_cfg["bins"]),
    ranges=(
        tuple(grid_cfg["wing_loading_range"]),
        tuple(grid_cfg["stability_range"]),
        tuple(grid_cfg["aspect_ratio_range"]),
    )
)

# 既存ログから再構築する場合は、jsonl の最終行に grid 状態がないため
# 別途 pickle 保存機能を追加するか、Stage 2 途中でデバッグ出力を入れる必要あり
print(f"Total cells: {len(grid.cells)}")
print(f"Coverage: {grid.coverage()*100:.1f}%")

# 次元別分布
import numpy as np
coords = np.array([c.descriptor.as_tuple() for c in grid.cells.values()])
for i, name in enumerate(["WingLoading", "Stability", "AspectRatio"]):
    print(f"  {name}: min={coords[:,i].min():.4f}, max={coords[:,i].max():.4f}, mean={coords[:,i].mean():.4f}")
```

### 7.2 トリム最適化が収束しない / 失速・発散する

| 症状 | チェックポイント | 対策 |
|------|------------------|------|
| `converged: false` で終了 | `max_iterations` 不足 | 30〜40 に増やす |
| スコアが改善しない (initial ≈ optimized) | 探索範囲外に最適解がある | `elevon_bounds_deg`, `dihedral_bounds_deg` を拡張 (例: [-8,12], [-2,12]) |
| 評価中に `crashed/diverged` 連発 | ベンチマーク条件が機体に合わない | `benchmark.duration_s` 短縮 (4.0〜5.0) / `dt_max` 縮小 (0.01) |
| エレボン角が境界に張り付く | 物理的にその境界が最適 | 境界を拡張するか、機体骨格 (Phase 1-2) 自体を見直す必要あり |

**デバッグ用: トリム探索軌跡の可視化**

```python
# NelderMeadTrimOptimizer 内部のシンプレックス履歴を取得するには改造が必要
# 簡易版: 手動でグリッドサーチして地形を確認
import numpy as np
from optimization.trim.nelder_mead import NelderMeadTrimOptimizer
from optimization.cli import run_pipeline  # ここで候補を取得

# 取得したエリートで 2D グリッド評価
elevons = np.linspace(-4, 8, 25)
dihedrals = np.linspace(0, 8, 17)
scores = np.zeros((len(elevons), len(dihedrals)))

for i, e in enumerate(elevons):
    for j, d in enumerate(dihedrals):
        # ここで _evaluate_point を呼ぶか、CLI 経由で単発評価
        pass

# matplotlib で等高線プロット
import matplotlib.pyplot as plt
E, D = np.meshgrid(elevons, dihedrals, indexing='ij')
plt.contourf(E, D, scores.T, levels=20)
plt.colorbar(label='Composite Score')
plt.xlabel('Elevon (deg)')
plt.ylabel('Dihedral (deg)')
plt.title('Trim Landscape')
plt.savefig('trim_landscape.png')
```

### 7.3 メモリや計算時間を節約する Tips

| 目的 | 設定・工夫 | 効果 |
|------|------------|------|
| **メモリ削減** | `grid.bins` を `[8,8,8]` (512 niches) に | グリッド保持メモリ ~40% 削減 |
| **高速化 (Stage 1)** | `bootstrap_count=3`, `mcts_iterations=20` | 初期化時間 50% 短縮 |
| **高速化 (Stage 2)** | `map_elites_iterations=50`, `mutation_rate=0.3` | イテレーション単位時間短縮 |
| **高速化 (Stage 3)** | `trim.enabled=false` または `trim_iter=10` | トリム時間 60% 削減 (精度犠牲) |
| **高速化 (Stage 4)** | `top_k_export=1`, `dryden_duration_s=3.0` | エクスポート・評価時間短縮 |
| **並列化** | 複数シードで独立実行し後で統合 | CPU コア数倍のスループット (コード変更不要) |
| **ログ軽量化** | `progress_callback` を無効化 / JSONL 出力間引き | I/O ボトルネック解消 |

**推奨「高速デバッグ」プロファイル** (`config/milcy_fast.yaml` として保存):

```yaml
search:
  seed: 42
  bootstrap_count: 3
  mcts_iterations: 20
  map_elites_iterations: 30
  mutation_rate: 0.3
grid:
  bins: [8, 8, 8]
  wing_loading_range: [0.118, 0.128]
  stability_range: [0.70, 0.90]
  aspect_ratio_range: [0.215, 0.255]
benchmark:
  low_speed_mps: 6.0
  mid_speed_mps: 10.0
  duration_s: 4.0
  dt_max: 0.02
  launch_height_m: 1.5
trim:
  enabled: true
  max_iterations: 10
  elevon_bounds_deg: [-4.0, 8.0]
  dihedral_bounds_deg: [0.0, 8.0]
evaluation:
  dryden_duration_s: 3.0
  top_k_export: 1
  output_dir: "output/milcy_fast"
```

実行:
```bash
python -m optimization.cli --config config/milcy_fast.yaml
```

---

## 8. 関連コード・ファイルマップ (Codebase Reference)

### 8.1 コアインターフェース・データ契約

| ファイル | 主要定義 | 役割 |
|----------|----------|------|
| `core/interfaces.py` | `ParametricOrigamiConfig`, `AircraftCandidate`, `BehaviorDescriptor`, `AirframeGeometry`, `FlightBenchmarkResult`, `TrimResult`, `RobustnessReport`, `GeometricStaticScore`, `MilcyPipelineProtocol` | **全ステージ共通の型定義・プロトコル**。変更時は全モジュール影響確認必須 |

### 8.2 Stage 1: 折り紙生成・幾何

| ファイル | クラス/関数 | 役割 |
|----------|-------------|------|
| `geometry/v3_origami/adapter.py` | `OrigamiAdapter`, `validate_tier0`, `extract_airframe_geometry` | パラメトリック設定 → OrigamiGenome → 折り紙物理 → Tier 0 検証 |
| `geometry/v3_origami/compiler.py` | `ParametricOrigamiCompiler`, `compile_steps` | 3フェーズ20ステップの `FoldStep` 列生成 |
| `geometry/v3_origami/mcts.py` | `MCTSOrigamiGenerator`, `MCTSNode` | UCB1ベースMCTSで有効骨格探索 |
| `geometry/origami_domain.py` | `CreasePattern`, `FoldedFace`, `OrigamiMesh`, `build_pattern_mesh`, `assign_layers`, `compile_creases`, `fold_genome` | **折り紙物理エンジン本体** (ヒンジツリー・層順序・メッシュ構築) |
| `geometry/origami_genome.py` | `OrigamiGenome`, `FoldStep`, `fold_genome`, `evaluate_grip`, `mutate_genome`, `step_meshes` | ゲノム表現・グリップフィルタ・メッシュ抽出 |

### 8.3 Stage 2: MAP-Elites 探索

| ファイル | クラス/関数 | 役割 |
|----------|-------------|------|
| `optimization/map_elites/engine.py` | `MAPElitesEngine`, `bootstrap_population`, `run_iterations` | 照明ループ統括・進捗コールバック |
| `optimization/map_elites/grid.py` | `MAPElitesGridArchive`, `add_candidate`, `get_elites`, `coverage` | 3Dグリッド管理・ニッチ挿入・エリート取得 |
| `optimization/map_elites/mutation.py` | `mutate_parametric_config` | パラメトリック変異 (Gaussian + clamp) |
| `physics/benchmark/benchmark.py` | `DeterministicFlightBenchmark`, `evaluate_candidate` | 2点固定水平6DoFベンチマーク |

### 8.4 Stage 3: トリム最適化

| ファイル | クラス/関数 | 役割 |
|----------|-------------|------|
| `optimization/trim/nelder_mead.py` | `NelderMeadTrimOptimizer`, `optimize_trim` | 2変数Nelder-Meadシンプレックス最適化 |

### 8.5 Stage 4: 頑健性評価・エクスポート

| ファイル | クラス/関数 | 役割 |
|----------|-------------|------|
| `optimization/eval/robustness.py` | `RobustnessEvaluator`, `evaluate_candidate`, `export_candidate_json/obj` | Dryden/Jitter/High-pitchテスト・OBJ/JSON出力 |

### 8.6 エントリポイント・CLI

| ファイル | 関数 | 役割 |
|----------|------|------|
| `optimization/cli.py` | `run_pipeline()`, `main()` | **メインパイプライン実行・CLI引数処理・設定読み込み** |

### 8.7 設定ファイル

| ファイル | 用途 |
|----------|------|
| `config/milcy_map_elites.yaml` | **milcy 本番用設定** (デフォルト) |
| `config/v3_map_elites.yaml` | 旧 v3 互換設定 (同一内容) |
| `config/genetic_research.yaml` | 旧 GA 用設定 (参考用) |

### 8.8 物理・飛行シミュレーション (下位層)

| ファイル | 役割 |
|----------|------|
| `physics/flight_sim.py` | 6DoF 飛行シミュレータ本体 (RK4積分・空力モデル) |
| `physics/config_schema.py` | `AircraftConfig`, `EnvironmentConfig`, `LaunchConfig` (シミュレーション入力スキーマ) |
| `physics/ga_config.py` | 旧 GA 用設定定数 (参照のみ) |

### 8.9 テスト・検証スクリプト

| ファイル | 用途 |
|----------|------|
| `tests/v3/test_v3_origami.py` | Stage 1 折り紙生成・Tier 0 の単体テスト |
| `scripts/smoke_test.py` | 全パイプラインのスモークテスト (CI 用) |
| `tools/run_v3_marathon.py` | 長時間自動実行スクリプト |

---

## 付録: よく使うコマンド集

```bash
# 1. 基本実行 (設定ファイル自動検出)
python -m optimization.cli --iterations 100 --seed 42 --output-dir output/run1

# 2. 設定ファイル明示
python -m optimization.cli --config config/milcy_map_elites.yaml --iterations 200

# 3. デバッグ用高速実行
python -m optimization.cli --iterations 20 --trim-iter 5 --top-k 1 --output-dir output/debug

# 4. 既存出力ディレクトリで再開不可 (現状は未対応: 全ステージ再実行)

# 5. 出力結果の簡易確認
ls -la output/milcy/
head -5 output/milcy/v3_search.jsonl
python -c "
import json
with open('output/milcy/v3_search.jsonl') as f:
    for line in f:
        d=json.loads(line)
        print(f'Iter {d[\"iteration\"]:4d}: Niches={d[\"niches_filled\"]:3d} Cov={d[\"coverage_pct\"]:5.1f}% Best={d[\"best_score\"]:.3f}')
"

# 6. エリート JSON の主要指標抽出
python -c "
import json, glob
for f in glob.glob('output/milcy/elite_rank*.json'):
    with open(f) as fp:
        d=json.load(fp)
    g=d['geometry']; b=d['benchmark']; m=d['metadata']
    print(f\"{f}: Score={b['composite_score']:.3f} L/D={b['low_speed']['max_ld_ratio']:.1f}/{b['mid_speed']['max_ld_ratio']:.1f} AR={g['aspect_ratio']:.2f} WL={g['mass_kg']/g['wing_area_m2']:.3f} Robust={m.get('robustness_report',{}).get('overall_robustness_score',0):.2f}\")
"

# 7. リンティング・型チェック (事前確認)
python -m compileall geometry optimization core physics app.py scripts/smoke_test.py
ruff check .
mypy geometry optimization core physics app.py scripts/smoke_test.py

# 8. 単体テスト実行
python -m pytest tests -q
```

---

**文書バージョン**: 1.0  
**対応コードベース**: milcy Algorithm (formerly v3 pipeline)  
**最終更新**: 2026-11-22