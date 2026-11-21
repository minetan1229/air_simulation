# 低レイノルズ数・薄翼・デルタ翼の流体力学および紙飛行機の空力特性調査

## 1. 概要
紙飛行機（特にダーツ型、滑空型）の飛行領域は、通常レイノルズ数 $Re = 10^4 \sim 10^5$ の低レイノルズ数領域に属する。
商用航空機（$Re = 10^6 \sim 10^8$）の滑らかな流線型翼型とは根本的に異なる物理現象が支配的となる。

主要文献:
- Lissaman, P. B. S. (1983). *Low-Reynolds-Number Airfoils*. Annu. Rev. Fluid Mech., 15, 223–239.
- Polhamus, E. C. (1966). *A Concept of the Vortex Lift of Sharp-Edge Delta Wings Based on a Leading-Edge-Suction Analogy*. NASA TN D-3767.
- Schmitz, F. W. (1941/1960). *Aerodynamik des Flugmodells*. Tragflügelmessungen bei kleinen Reynolds-Zahlen.
- Okamoto, M., et al. (1996). *Aerodynamic characteristics of folded paper airplanes at low Reynolds numbers*. Journal of Aircraft / AIAA.

---

## 2. 低レイノルズ数領域における物理現象

### 2.1 層流剥離泡 (Laminar Separation Bubble: LSB) と平板翼の特性
- 滑らかな厚翼では、逆圧力勾配により層流境界層が剥離し、乱流遷移して再付着するまでに「層流剥離泡」を形成。これが急激なヒステリシスや早期失速（Bubble Burst）を引き起こす。
- 一方、紙飛行機のような**鋭頭平板翼（Sharp-edged Flat Plate）**では、前縁部（Leading Edge）で流れが強制的に剥離するため、不規則な LSB の挙動を回避し、迎え角に対して予測しやすい特性を示す。
- 平板翼の理論揚力傾斜:
  $$C_{L\alpha, 2D} \approx 2\pi \quad (\text{rad}^{-1})$$
  低レイノルズ数粘性剥離下では実効傾斜が約 $1.5\pi \sim 1.8\pi$ 程度まで低下する。

### 2.2 ポルハムスの渦揚力理論 (Polhamus Leading-Edge Suction Analogy)
デルタ翼や細長翼（ダーツ型紙飛行機など $AR \le 2.5$、後退角 $\Lambda \ge 50^\circ$）では、鋭い前縁剥離流が上面で渦（Leading Edge Vortex: LEV）を巻く。
この渦が上面に強い負圧を誘起し、非線形な「渦揚力（Vortex Lift）」を生み出す。

全揚力係数:
$$C_L(\alpha) = C_{L,pot}(\alpha) + C_{L,vortex}(\alpha)$$
$$C_{L,pot} = K_p \sin\alpha \cos^2\alpha$$
$$C_{L,vortex} = K_v \sin^2\alpha \cos\alpha$$

- $K_p$: ポテンシャル揚力係数（線形成分）
- $K_v$: 渦揚力係数（前縁吸い出し力理論に基づく非線形成分）

これにより、通常の翼が $\alpha \approx 10^\circ \sim 15^\circ$ で失速するのに対し、デルタ型紙飛行機は $\alpha \approx 25^\circ \sim 35^\circ$ の高迎え角まで失速せずに揚力を維持できる。

---

## 3. 抗力特性と極曲線 (Drag Polar)

### 3.1 抗力の構成成分
1. **ゼロ揚力抗力 (寄生抗力・摩擦抗力) $C_{D0}$**:
   - 平板層流境界層摩擦抗力:
     $$C_{f,lam} = \frac{1.328}{\sqrt{Re}}$$
   - 形状抗力（前縁厚み、段差、折り目による形状抵抗）:
     $$C_{D0} = 2 \cdot C_{f,lam} \cdot (1 + \text{form factor}) + C_{D,crease}$$
     典型的な紙飛行機では $C_{D0} \approx 0.025 \sim 0.055$ 程度。
2. **誘導抗力 (Induced Drag) $C_{Di}$**:
   - アスペクト比 $AR = b^2 / S$ が小さいため、翼端渦による誘導抗力が極めて大きい。
   - オズワルド効率係数 $e$: 平板翼・三角翼では $e \approx 0.4 \sim 0.7$。
     $$C_{Di}(\alpha) = \frac{C_L^2}{\pi \cdot AR \cdot e}$$
3. **渦抗力成分**:
   渦揚力発生時は前縁吸い出し力が失われ、法線力がそのまま後方へ傾くため抗力が増大する:
   $$C_D(\alpha) = C_{D0} + C_L(\alpha) \cdot \tan\alpha$$

### 3.2 揚抗比 (L/D) の限界
- 高性能紙飛行機グライダー（John Collins "Suzanne"）:
  $$(L/D)_{\max} \approx 6.5 \sim 8.5$$
- 高速ダーツ型（Dillon Ruble 等）:
  $$(L/D)_{\max} \approx 3.5 \sim 5.0$$
  （誘導抗力と形状抗力のバランスにより、低抗力で高速直進投擲に特化）

---

## 4. 地面効果 (Ground Effect)

機体が地面近傍（高度 $h < b$、特に $h < 0.5b$）を飛行する際、地面の鏡像効果によって吹き下ろし角（Downwash）が抑制される。
Wieselsberger の理論式:
$$\sigma(h/b) = \frac{16(h/b)^2}{1 + 16(h/b)^2}$$
$$C_{Di,eff} = C_{Di} \cdot \sigma(h/b)$$
地面効果により揚抗比 $L/D$ は $20\% \sim 50\%$ 向上し、着地直前の滑空距離が大幅に伸びる。

---

## 5. 空力弾性効果 (Aeroelastic Effects) と紙の坪量

紙（坪量 $80 \sim 100\,\text{g/m}^2$）は弾性体であり、高速投擲時の高動圧 $q = \frac{1}{2}\rho V^2$ 下では:
1. 後縁エレボン（Elevon）が風圧で押し下げられ、有効迎え角 $\alpha_{eff}$ やトリム角 $\delta_{eff}$ が減少。
2. これにより高動圧投擲時の急激なループ（Puff-up/Pitch-up）が抑制され、弾道を描いて高度を稼ぐ。
3. 頂点付近で減速すると、紙の剛性でエレボンが復元し、滑空姿勢へ遷移する。
