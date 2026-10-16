# 6自由度飛行力学・四元数積分・適応的時間刻み制御

## 1. 概要
紙飛行機は低慣性・高感度の動的システムであり、手投げによるパルス推力、急激な失速遷移、
頂点でのプッシュオーバー等を正確に再現するには、ジンバルロック回避・数値安定性に
優れた四元数ベースの6DoF積分器が不可欠である。

主要参考文献:
- Stevens, B. L., Lewis, F. L. (2015). *Aircraft Control and Simulation*, 3rd ed. Wiley.
- Zipfel, P. H. (2014). *Modeling and Simulation of Aerospace Vehicle Dynamics*, 3rd ed. AIAA.
- Diebel, J. (2006). *Representing Attitude: Euler Angles, Unit Quaternions, and Rotation Vectors*. Stanford Univ.

---

## 2. 13状態ベクトル定義

$$\mathbf{x} = \begin{bmatrix} \mathbf{p}^I \\ \mathbf{v}^B \\ \mathbf{q} \\ \boldsymbol{\omega}^B \end{bmatrix} \in \mathbb{R}^{13}$$

| 状態 | シンボル | 意味 | フレーム |
|------|---------|------|---------|
| 位置 | $\mathbf{p}^I = [x, y, z]^T$ | 慣性座標系 (NED) 位置 | 慣性系 |
| 並進速度 | $\mathbf{v}^B = [u, v, w]^T$ | 機体座標系前進・横滑り・下降速度 | 機体系 |
| 姿勢四元数 | $\mathbf{q} = [q_0, q_1, q_2, q_3]^T$ | 機体→慣性の回転 ($\|\mathbf{q}\| = 1$) | — |
| 角速度 | $\boldsymbol{\omega}^B = [p, q, r]^T$ | ロール率・ピッチ率・ヨー率 | 機体系 |

---

## 3. 運動方程式 (Newton-Euler)

### 3.1 並進力学（機体座標系）
$$\dot{\mathbf{v}}^B = \frac{1}{m} \mathbf{F}^B - \boldsymbol{\omega}^B \times \mathbf{v}^B$$

全力 $\mathbf{F}^B = \mathbf{F}_{aero}^B + m \mathbf{R}(\mathbf{q})^T \mathbf{g}^I$

- 空気力: 迎え角 $\alpha = \arctan(w/u)$、横滑り角 $\beta = \arcsin(v/V_t)$ から:
  $$\mathbf{F}_{aero}^B = \bar{q} S \begin{bmatrix} -C_D \cos\alpha + C_L \sin\alpha \\ C_Y \\ -C_D \sin\alpha - C_L \cos\alpha \end{bmatrix}$$
  ここで $\bar{q} = \frac{1}{2}\rho V_t^2$, $V_t = \|\mathbf{v}^B\|$

### 3.2 四元数運動学
$$\dot{\mathbf{q}} = \frac{1}{2} \boldsymbol{\Omega}(\boldsymbol{\omega}^B) \mathbf{q}$$

$$\boldsymbol{\Omega}(\boldsymbol{\omega}^B) = \begin{bmatrix}
0 & -p & -q & -r \\
p &  0 &  r & -q \\
q & -r &  0 &  p \\
r &  q & -p &  0
\end{bmatrix}$$

**毎ステップ後に再正規化**:
$$\mathbf{q} \leftarrow \frac{\mathbf{q}}{\|\mathbf{q}\|}$$

### 3.3 回転力学（オイラーの剛体方程式）
$$\dot{\boldsymbol{\omega}}^B = \mathbf{I}^{-1} \left( \mathbf{M}^B - \boldsymbol{\omega}^B \times (\mathbf{I} \boldsymbol{\omega}^B) \right)$$

- $\mathbf{I} = \text{diag}(I_{xx}, I_{yy}, I_{zz})$（紙飛行機は左右対称のため慣性乗積 $I_{xy} = I_{yz} = 0$ を仮定可能。$I_{xz}$ のみ非零の場合がある）
- $\mathbf{M}^B$: 空力モーメント（ピッチング $C_m$、ローリング $C_l$、ヨーイング $C_n$）+ ダンピング項（$C_{mq}$, $C_{lp}$, $C_{nr}$）

---

## 4. 適応的 RK4/5 (Runge-Kutta-Fehlberg) 積分法

### 4.1 基本アルゴリズム
1. RK4 ステージ $k_1 \sim k_6$（Butcher 配列に従う）を評価
2. 4次精度解 $\mathbf{x}_{n+1}$ と 5次精度解 $\mathbf{x}^*_{n+1}$ を同時に算出
3. 局所打ち切り誤差:
   $$e = \|\mathbf{x}_{n+1} - \mathbf{x}^*_{n+1}\|$$
4. ステップサイズ更新:
   $$h_{new} = 0.9 \cdot h \cdot \left(\frac{\text{tol}}{e}\right)^{0.2}$$
5. $e > \text{tol}$ ならステップ棄却・縮小、$e \le \text{tol}$ なら受理・拡大

### 4.2 紙飛行機特有の数値的課題

#### (a) 頂点付近 $V_t \to 0$ の特異点
- $\alpha = \arctan(w/u)$: $u, w \to 0$ で不定。`atan2(w, u)` を使用し、速度が閾値 $V_{min} = 0.1\,\text{m/s}$ 以下では空力係数をゼロにクランプする。
- 動圧 $\bar{q} \to 0$: モーメント計算で除算が発生しないよう、重力ピッチダウンモーメントに切り替える。

#### (b) 高速投擲時 $V_t > 25\,\text{m/s}$ のサブステッピング
- 動圧変化率が大きく、固定 $\Delta t = 0.01\,\text{s}$ では空力弾性変形の応答遅延やエネルギー散逸が生じる。
- $h_{min} = 0.0001\,\text{s}$ まで縮小可能とし、$q_{max} \cdot \Delta t \le$ CFL的安定条件に相当する条件を設ける。

#### (c) 着地判定
- $z \ge 0$（地面到達）時に二分法（Bisection）で着地瞬間の補間を行い、精確な飛行距離・時間を算出する。

---

## 5. 慣性テンソルの計算

紙飛行機の慣性テンソルは、折り紙構造を三角形パネルの集合体として分解し、各パネルの面積・坪量・位置から算出する:

$$I_{xx} = \sum_{i} m_i (y_i^2 + z_i^2), \quad I_{yy} = \sum_{i} m_i (x_i^2 + z_i^2), \quad I_{zz} = \sum_{i} m_i (x_i^2 + y_i^2)$$

- $m_i = \rho_{paper} \cdot \text{gsm} \cdot S_i$（各パネル質量）
- 典型値（ダーツ型, 80 gsm A4紙）:
  $I_{xx} \approx 5 \times 10^{-6}\,\text{kg·m}^2$,
  $I_{yy} \approx 2 \times 10^{-5}\,\text{kg·m}^2$,
  $I_{zz} \approx 2.5 \times 10^{-5}\,\text{kg·m}^2$

---

## 6. 飛行フェーズ遷移モデル

紙飛行機の飛行は以下の3フェーズに分類される:

### Phase I: 弾道上昇（Ballistic Ascent）
- 投擲直後〜頂点前。高速・高動圧。
- 空力弾性モーフィングにより実効トリム角が減少し、弾道に近い軌道。
- 抗力支配: $D \gg L$（$V > 20\,\text{m/s}$ 時）

### Phase II: 頂点プッシュオーバー（Apex Transition）
- $V < 3\,\text{m/s}$。空力的に「失速」に近い状態。
- 重心前方偏心による重力ノーズダウンモーメントが支配的。
- シミュレーション上の数値的に最も困難な区間。

### Phase III: 滑空降下（Glide Descent）
- 定常滑空に近い状態。$L/D$ が飛行効率を決定。
- 地面効果（$h < b$）による誘導抗力減少が水平距離を伸長。
- 着地時の速度・角度で最終飛行距離が確定。
