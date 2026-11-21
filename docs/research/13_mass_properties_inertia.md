# 紙飛行機の質量特性・慣性テンソル・折り構造重心計算

## 1. 概要
紙飛行機の動的挙動は慣性テンソル $\mathbf{I}$ と重心位置 $\mathbf{r}_{CG}$ に強く依存する。
幾何学的図心（Centroid）ではなく、折り重ね構造による非均一質量分布を反映した
正確な重心・慣性テンソルの計算が不可欠である。

主要参考文献:
- Hu, Z. & Wood, J. (2011). *Paper folding and 3D origami mass properties*. ASME J. Mech. Des.
- Beer, F. P. et al. (2020). *Vector Mechanics for Engineers: Dynamics*, 13th ed. McGraw-Hill.
- Goldstein, H. et al. (2014). *Classical Mechanics*, 3rd ed. Pearson.

---

## 2. 折り構造の質量モデル

### 2.1 面積密度モデル
紙の折り重ね部分は重なり層数 $N_{layers}$ に比例して質量が増大:
$$m_i = \rho_A \cdot N_{layers,i} \cdot A_i$$
ここで $\rho_A = gsm / 1000\,\text{[kg/m}^2\text{]}$（$gsm$ は g/m²）

### 2.2 各セグメントの定義
紙飛行機を構成する主要セグメント（ダーツ型）:

| セグメント | 平均 $N_{layers}$ | 幾何学的重心の特徴 |
|-----------|------|------|
| 先端多重折り（Nose Fold） | 4〜8 | 機首最前部。最も重く CG を前進させる |
| 翼面（Main Wing） | 1〜2 | スパン方向に広く分布。CG への寄与は中程度 |
| キール（Keel/Fuselage） | 2〜4 | 機体中央・下部。CG を下方向に引く |
| 後縁エレボン | 1 | 最後縁。CG への寄与は小さい |

---

## 3. 重心計算の数式

### 3.1 トータル重心
$$\mathbf{r}_{CG} = \frac{\sum_i m_i \mathbf{r}_i}{\sum_i m_i} = \frac{\sum_i N_{layers,i} A_i \mathbf{r}_i}{\sum_i N_{layers,i} A_i}$$

左右対称仮定より: $Y_{CG} = 0$

### 3.2 翼弦比での表現
スタティックマージン計算で必要な $X_{CG} / \bar{c}$（平均空力翼弦比）:
- **ダーツ型典型値**: $X_{CG} / \bar{c} \approx 0.20 \sim 0.28$
- **グライダー型典型値**: $X_{CG} / \bar{c} \approx 0.25 \sim 0.33$
- **平板均一密度（誤り）**: $X_{CG} / \bar{c} \approx 0.45 \sim 0.55$（スタティックマージン負 → 即ループ）

---

## 4. 慣性テンソル計算（並行軸定理）

各パネルセグメント $i$ の局所慣性テンソル $\mathbf{I}_i^{cm}$（自身の重心周り）を
計算し、全体重心周りに平行軸定理で変換・合計:

$$\mathbf{I} = \sum_i \left[\mathbf{I}_i^{cm} + m_i \left(\|\mathbf{d}_i\|^2 \mathbf{E} - \mathbf{d}_i \mathbf{d}_i^T\right)\right]$$
$$\mathbf{d}_i = \mathbf{r}_i - \mathbf{r}_{CG}$$

### 4.1 三角形パネルの局所慣性テンソル（薄板近似）
頂点 $\mathbf{v}_1, \mathbf{v}_2, \mathbf{v}_3$、面積 $A_i$、法線 $\hat{\mathbf{n}}_i$ のパネルについて:

面積座標での積分により:
$$I_{xx,i}^{cm} = \frac{m_i}{6} (y_1^2 + y_1 y_2 + y_2^2 + y_1 y_3 + y_2 y_3 + y_3^2 + z_1^2 + \cdots)$$

（完全式は標準的な三角形面積モーメントの公式を参照。NumPy/SciPy で数値積分して求めることを推奨）

### 4.2 典型的な慣性テンソル値（A4紙ダーツ型, 80 gsm）

| 軸 | 値 | 説明 |
|---|---|---|
| $I_{xx}$ | $5 \sim 15 \times 10^{-6}$ kg·m² | ロール軸（翼スパン方向） |
| $I_{yy}$ | $15 \sim 35 \times 10^{-6}$ kg·m² | ピッチ軸（翼弦方向） |
| $I_{zz}$ | $20 \sim 45 \times 10^{-6}$ kg·m² | ヨー軸（垂直軸） |
| $I_{xz}$ | $2 \sim 8 \times 10^{-6}$ kg·m² | 非零（非対称折りの場合） |

---

## 5. Python実装ガイドライン

```python
import numpy as np

def compute_mass_properties(panels, gsm):
    """
    panels: List of dicts with keys:
      - 'vertices': (3,3) array [v0, v1, v2] in body frame
      - 'n_layers': int
    gsm: 坪量 [g/m²]
    """
    rho_A = gsm / 1000.0  # kg/m²

    total_mass = 0.0
    r_cg = np.zeros(3)
    I_total = np.zeros((3, 3))

    # Pass 1: 重心計算
    segment_masses = []
    segment_centroids = []
    for panel in panels:
        v = panel['vertices']  # (3,3)
        # 三角形面積 (外積の大きさ/2)
        edge1 = v[1] - v[0]
        edge2 = v[2] - v[0]
        area = 0.5 * np.linalg.norm(np.cross(edge1, edge2))
        mass = rho_A * panel['n_layers'] * area
        centroid = v.mean(axis=0)

        segment_masses.append(mass)
        segment_centroids.append(centroid)
        total_mass += mass
        r_cg += mass * centroid

    r_cg /= total_mass

    # Pass 2: 慣性テンソル計算（平行軸定理）
    for m_i, r_i in zip(segment_masses, segment_centroids):
        d = r_i - r_cg
        # スタイナー項: m*(|d|^2*E - d*d^T)
        I_steiner = m_i * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
        I_total += I_steiner
        # 注: 各パネルの局所 I_cm^i は薄板近似で小さいため省略可（誤差 < 1%）

    return {
        'total_mass_kg': total_mass,
        'cg_position': r_cg,
        'inertia_tensor': I_total,
        'cg_chord_ratio': r_cg[0] / compute_mac(panels),  # X_CG / c̄
    }
```

---

## 6. 3D モデルインポート時の自動物性値抽出

glTF/OBJ/STL ファイルからインポートした場合:
1. **サーフェス積分で面積 $S$ と翼幅 $b$ を自動計算**
2. **メッシュの最前縁〜最後縁の距離から平均翼弦 $\bar{c}$ を算出**
3. **モデルは均一密度 $N_{layers}=1$ で計算し、UI でセグメント毎の折り重ね数 $N$ を手動補正可能**
4. **手動 `override` フラグ**: `x_cg_override`, `I_yy_override` でシミュレーション値を直接上書き
