# 投擲条件の最適化と感度解析・設計パラメータスイープ手法

## 1. 概要
紙飛行機の最大飛行距離・最長滞空時間は、投擲条件（初速 $V_0$・投擲角 $\theta_0$・
ロール角 $\phi_0$）と機体設計パラメータ（翼面積・AR・トリム角・重心位置）の
複合的な最適化問題である。
単純な 1D パラメータスイープから多目的最適化（NSGA-II）まで、段階的に手法を適用する。

主要参考文献:
- Deb, K. et al. (2002). *A Fast and Elitist Multiobjective Genetic Algorithm: NSGA-II*. IEEE Trans. Evol. Comput., 6(2), 182–197.
- Anderson, B. D. O. & Moore, J. B. (1990). *Optimal Control: Linear Quadratic Methods*. Prentice Hall.
- Saltelli, A. et al. (2008). *Global Sensitivity Analysis: The Primer*. Wiley.

---

## 2. 投擲条件の物理的最適領域

### 2.1 投擲角の感度

ギネス記録機（ダーツ型, $m \approx 5.6\,\text{g}$, $S \approx 0.024\,\text{m}^2$）の場合:

| 投擲角 $\theta_0$ | 最大高度 $h_{apex}$ | 最大距離 $R$ | 備考 |
|---|---|---|---|
| $0^\circ$ | 1.2 m | 18 m | 地面に対して水平投擲 |
| $10^\circ$ | 3.5 m | 28 m | 一般的な投擲 |
| $20^\circ$ | 8.0 m | 45 m | 適切な弾道 |
| $30^\circ$ | 14.0 m | 60 m | 良好な軌道 |
| $38^\circ \sim 42^\circ$ | 18.0 m | **88 m** | **ギネス記録最適角度** |
| $60^\circ$ | 25.0 m | 42 m | 過上昇、水平距離が短い |
| $90^\circ$ | 30.0 m | 0 m（垂直に落下） | 最長滞空用 |

> **重要**: ダーツ型ギネス機は「放物線投擲+滑空」複合戦略のため最適角 $\approx 40^\circ$。
> グライダー型（Suzanne 等）の最適角は $20^\circ \sim 30^\circ$ と異なる。

### 2.2 初速の感度

$$R(V_0) = f(V_0, \theta_{opt}(V_0), \text{機体特性})$$

- $V_0 < 15\,\text{m/s}$: 最大高度が低く、滑空距離不足
- $V_0 = 25 \sim 35\,\text{m/s}$: **最適領域**（$h_{apex} = 15 \sim 25\,\text{m}$）
- $V_0 > 40\,\text{m/s}$: 高動圧による空力弾性変形増大、翼のよれで姿勢不安定化

---

## 3. 一変数感度解析 (OAT: One-At-a-Time)

各パラメータを基準値固定で1つずつ変化させ、出力（飛行距離 $R$）への影響度を評価:

$$S_i = \frac{\Delta R / R_0}{\Delta x_i / x_{i,0}}$$

### 感度指標のランキング（定性的推定）
| 順位 | パラメータ | 感度 | 影響方向 |
|------|-----------|------|---------|
| 1 | 投擲初速 $V_0$ | 高 | +強 |
| 2 | 投擲角 $\theta_0$ | 高 | 非線形（最適角あり） |
| 3 | 重心位置 $x_{CG}/\bar{c}$ | 高 | 安定マージン直結 |
| 4 | トリム角 $\delta_{elevon}$ | 中 | + (適切域に限る) |
| 5 | 翼スパン $b$ | 中 | + (AR・誘導抗力経由) |
| 6 | 坪量 $gsm$ | 中 | + (空力弾性モーフィング経由) |
| 7 | 二面角 $\Gamma$ | 低 | + (横安定性) |

---

## 4. 多目的最適化 NSGA-II

### 4.1 目的関数定義（2〜7 目的）

```python
OBJECTIVES = {
    "max_distance":   {"mode": "maximize", "weight": 1.0},
    "max_time":       {"mode": "maximize", "weight": 0.8},
    "max_ld_ratio":   {"mode": "maximize", "weight": 0.5},
    "min_max_stress": {"mode": "minimize", "weight": 0.6},
    "max_stability":  {"mode": "maximize", "weight": 0.4},  # SM > 5%
    "min_launch_v":   {"mode": "minimize", "weight": 0.3},
    "max_stall_margin": {"mode": "maximize", "weight": 0.5},
}
```

### 4.2 設計変数と探索範囲

| 変数 | 記号 | 下限 | 上限 | 単位 |
|------|------|------|------|------|
| 翼幅 | $b$ | 0.10 | 0.30 | m |
| 翼面積 | $S$ | 0.01 | 0.04 | m² |
| アスペクト比 | $AR$ | 0.5 | 3.0 | — |
| 後退角 | $\Lambda$ | 50 | 78 | deg |
| 二面角 | $\Gamma$ | 0 | 15 | deg |
| トリム角 | $\delta$ | -5 | 10 | deg |
| 重心位置 | $x_{CG}/\bar{c}$ | 0.15 | 0.40 | — |
| 坪量 | $gsm$ | 60 | 120 | g/m² |
| 投擲初速 | $V_0$ | 10 | 35 | m/s |
| 投擲角 | $\theta_0$ | 10 | 50 | deg |

### 4.3 NSGA-II アルゴリズム概要

```
NSGA-II 実行フロー:
1. 初期集団 P(0) を設計空間内でランダム生成（個体数 N = 100〜300）
2. 各個体を飛行シミュレーション（6DoF RK4）で評価 → 目的関数値算出
3. for gen = 1 to G_max:
   a. 非支配ソート (Fast Non-Dominated Sort):
      - 個体間の Pareto 支配関係を全ペアで比較
      - ランク1（Pareto Front）、ランク2、...に分類
   b. クラウディング距離計算:
      - 同一ランク内での個体間の多様性指標
      - 密集した領域の個体は低スコアとなりトリミングされやすい
   c. トーナメント選択 → SBX クロスオーバー → 多項式変異
   d. 新集団 Q を生成、P ∪ Q から上位 N 個体を選択
4. 最終世代の Pareto Front を Pareto 最適解として出力
```

### 4.4 Pareto Front の解釈

```
最大飛行距離
    ^
    |  [グライダー型最適]    [ハイブリッド]     [ダーツ型最適]
100m|      ●---●---●---●---●---●---●---●---●---●
    |   (幅広翼, 低V0)                    (細長翼, 高V0)
 50m|
    +---------------------------------------------> 最長滞空時間
    0s           30s                      60s
```

- **左端（最大距離特化）**: ダーツ型、高速投擲、低抗力
- **右端（最長滞空特化）**: グライダー型、緩やか投擲、高揚力
- **中央（バランス型）**: 現実的なギネス挑戦設計領域

---

## 5. サロゲートモデル（RBF 補間による計算コスト削減）

グリッドスイープ数が $10^4$ 以上になる場合、全数シミュレーションは非実用的。
少数のシミュレーション結果（$50 \sim 200$ サンプル）からサロゲート関数を構築:

**Radial Basis Function (RBF) 補間:**
$$\hat{f}(\mathbf{x}) = \sum_{i=1}^{N} \lambda_i \phi(\|\mathbf{x} - \mathbf{x}_i\|)$$
$$\phi(r) = \sqrt{r^2 + \epsilon^2} \quad (\text{Multiquadric})$$

**期待改善量 (Expected Improvement: EI) による効率的サンプリング:**
$$EI(\mathbf{x}) = (f_{best} - \hat{f}(\mathbf{x})) \Phi\left(\frac{f_{best} - \hat{f}(\mathbf{x})}{\hat{\sigma}(\mathbf{x})}\right) + \hat{\sigma}(\mathbf{x}) \phi\left(\frac{f_{best} - \hat{f}(\mathbf{x})}{\hat{\sigma}(\mathbf{x})}\right)$$

次に評価すべき点 = $\arg\max_{\mathbf{x}} EI(\mathbf{x})$
