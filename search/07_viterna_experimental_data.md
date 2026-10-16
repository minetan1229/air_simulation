# Viterna 法による失速後空力モデルと実験的 CL/CD データ

## 1. 概要
紙飛行機は投擲直後の高速弾道上昇から頂点での極低速プッシュオーバーまで、
迎え角 $\alpha$ が $-10^\circ$ から $90^\circ$ を超える範囲まで変動する。
通常の翼型データは $\alpha \le 20^\circ$ 程度までしかカバーしないため、
高迎え角（失速後）域のモデルが不可欠となる。

主要参考文献:
- Viterna, L. A. & Janetzke, D. C. (1982). *Theoretical and Experimental Power from Large Horizontal-Axis Wind Turbines*. NASA TM-82944.
- Pelletier, A. & Mueller, T. J. (2000). *Low Reynolds Number Aerodynamics of Low-Aspect-Ratio, Thin/Flat/Cambered-Plate Wings*. Journal of Aircraft, 37(5).
- Selig, M. S. et al. *Summary of Low-Speed Airfoil Data*, Vols. 1–5. UIUC Applied Aerodynamics Group.
- Okamoto, M. & Azuma, A. (2011). *Aerodynamic characteristics at low Reynolds numbers for wings of various planforms*. AIAA Journal.

---

## 2. Viterna-Corrigan 法の数式定義

### 2.1 入力パラメータ
- 失速開始角 $\alpha_s$ と対応する $C_{L,s}$, $C_{D,s}$
- 最大抗力係数（平板 $90^\circ$ 時）:
  $$C_{D,\max} = 1.11 + 0.018 \cdot AR$$
  （$AR$: アスペクト比。ダーツ型 $AR \approx 1.2$ → $C_{D,\max} \approx 1.13$）

### 2.2 失速後の抗力モデル（$\alpha_s \le \alpha \le 90^\circ$）
$$C_D(\alpha) = B_1 \sin^2\alpha + B_2 \cos\alpha$$

$$B_1 = C_{D,\max}$$
$$B_2 = \frac{C_{D,s} - C_{D,\max} \sin^2\alpha_s}{\cos\alpha_s}$$

### 2.3 失速後の揚力モデル（$\alpha_s \le \alpha \le 90^\circ$）
$$C_L(\alpha) = A_1 \sin 2\alpha + A_2 \frac{\cos^2\alpha}{\sin\alpha}$$

$$A_1 = \frac{C_{D,\max}}{2}$$
$$A_2 = \left(C_{L,s} - C_{D,\max} \sin\alpha_s \cos\alpha_s\right) \cdot \frac{\sin\alpha_s}{\cos^2\alpha_s}$$

### 2.4 $\alpha = 90^\circ$ での境界条件
$$C_L(90^\circ) = 0, \quad C_D(90^\circ) = C_{D,\max}$$

---

## 3. 低レイノルズ数平板翼の実験的 CL/CD 参考データ

### 3.1 Re = 50,000 (平板, AR = 6, UIUC 測定値に基づく近似)

| $\alpha$ (deg) | $C_L$ | $C_D$ | $L/D$ |
|---|---|---|---|
| 0 | 0.00 | 0.030 | 0.0 |
| 2 | 0.18 | 0.033 | 5.5 |
| 4 | 0.36 | 0.042 | 8.6 |
| 6 | 0.52 | 0.058 | 9.0 |
| 8 | 0.66 | 0.082 | 8.0 |
| 10 | 0.78 | 0.115 | 6.8 |
| 12 | 0.85 | 0.155 | 5.5 |
| 15 | 0.82 | 0.230 | 3.6 |
| 20 | 0.65 | 0.380 | 1.7 |
| 30 | 0.70 | 0.650 | 1.1 |
| 45 | 0.72 | 0.95 | 0.76 |
| 60 | 0.55 | 1.20 | 0.46 |
| 90 | 0.00 | 1.13 | 0.0 |

> ※ $\alpha = 30^\circ \sim 60^\circ$ における $C_L$ の維持（渦揚力効果）は、低ARデルタ翼で特に顕著。

### 3.2 Re = 100,000 (平板, AR = 3, Notre Dame 風洞データ近似)

| $\alpha$ (deg) | $C_L$ | $C_D$ | $L/D$ |
|---|---|---|---|
| 0 | 0.00 | 0.025 | 0.0 |
| 2 | 0.15 | 0.028 | 5.4 |
| 4 | 0.30 | 0.035 | 8.6 |
| 6 | 0.44 | 0.048 | 9.2 |
| 8 | 0.57 | 0.068 | 8.4 |
| 10 | 0.68 | 0.095 | 7.2 |
| 12 | 0.76 | 0.130 | 5.8 |
| 15 | 0.80 | 0.195 | 4.1 |
| 20 | 0.70 | 0.340 | 2.1 |
| 25 | 0.72 | 0.480 | 1.5 |
| 30 | 0.74 | 0.620 | 1.2 |

### 3.3 ダーツ型紙飛行機 Re = 60,000〜80,000 での推定特性 (AR ≈ 1.2, $\Lambda \approx 70^\circ$)

| $\alpha$ (deg) | $C_L$ | $C_D$ | $L/D$ | 備考 |
|---|---|---|---|---|
| 0 | 0.00 | 0.020 | 0.0 | 極低抗力（スリムダーツ） |
| 5 | 0.18 | 0.032 | 5.6 | 前縁渦発生開始 |
| 10 | 0.38 | 0.065 | 5.8 | $(L/D)_{\max}$ 近傍 |
| 15 | 0.55 | 0.115 | 4.8 | 渦揚力支配 |
| 20 | 0.68 | 0.185 | 3.7 | |
| 25 | 0.78 | 0.280 | 2.8 | 渦崩壊(Vortex Breakdown)開始 |
| 30 | 0.80 | 0.400 | 2.0 | $C_{L,\max}$ 近傍 |
| 35 | 0.73 | 0.530 | 1.4 | 渦崩壊進行、漸進的失速 |
| 45 | 0.55 | 0.800 | 0.69 | |

> ダーツ型の特徴: 前縁後退角が大きく LEV が安定するため、$\alpha_{stall} \approx 30^\circ$ まで延長される。
> これは高AR翼（$\alpha_{stall} \approx 12^\circ$）と根本的に異なる。

---

## 4. 前縁渦崩壊（Vortex Breakdown）モデル

### 4.1 渦崩壊角の推定
Polhamus の理論では渦揚力は安定した LEV を前提とするが、実際には臨界角度を超えると渦核が急拡大・崩壊する。
崩壊が主翼上方で起こると渦揚力が急落し、失速に至る。

後退角 $\Lambda$ との関係:
$$\alpha_{VB} \approx (0.4 \sim 0.5) \Lambda \quad (\text{deg})$$
- $\Lambda = 70^\circ$ → $\alpha_{VB} \approx 28^\circ \sim 35^\circ$
- $\Lambda = 60^\circ$ → $\alpha_{VB} \approx 24^\circ \sim 30^\circ$

### 4.2 渦崩壊の空力係数への影響
渦崩壊開始後の揚力減衰はViterna法に接続可能:
$$C_L(\alpha > \alpha_{VB}) = C_{L,VB} \cdot f_{decay}(\alpha - \alpha_{VB}) + C_{L,Viterna}(\alpha) \cdot (1 - f_{decay})$$
$$f_{decay}(\Delta\alpha) = \exp(-k_{VB} \cdot \Delta\alpha)$$
$k_{VB} \approx 0.05 \sim 0.15\,\text{deg}^{-1}$（実験適合パラメータ）

---

## 5. シミュレータ実装への反映ポイント

1. **線形域→渦揚力→Viterna の3区間モデル**: $\alpha < \alpha_{stall,linear}$（線形）、$\alpha_{stall,linear} \le \alpha \le \alpha_{VB}$（Polhamus 渦揚力）、$\alpha > \alpha_{VB}$（Viterna + 渦崩壊減衰）を連続的に接続する。
2. **$C_{D,\max}$ の AR 依存**: ダーツ型 ($AR \approx 1.2$) とグライダー型 ($AR \approx 4$) で異なる $C_{D,\max}$ を適用。
3. **$360^\circ$ 極曲線の構築**: $0^\circ \sim 90^\circ$ を Viterna で構築後、$90^\circ \sim 180^\circ$ は反対称（$C_L$ 符号反転）、$180^\circ \sim 360^\circ$ はミラーで生成。
4. **Re 依存性**: $C_{D0}(Re) = 2 \times 1.328 / \sqrt{Re}$ + form factor として皮膚摩擦抗力を Re に応じて補正。
