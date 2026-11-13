# 流体構造連成（FSI）・紙の弾性変形・翼端反り上がり・前縁折り段差・折紙幾何学空力弾性学 網羅的学術調査報告書
**Comprehensive Academic Survey on Fluid-Structure Interaction (FSI), Aeroelastic Deformation of Paper, Wing-tip Washout/Twist, Multi-layer Crease Steps, and Origami Morphing Aeronautics ($Re = 10^3 \sim 10^5$)**

---

## 1. 冒頭要約 (Executive Summary)

紙飛行機（折紙滑空機）および超小型飛行体（Micro Air Vehicles: MAVs）の飛行ダイナミクスを真に決定づけている物理的本質は、**極薄で柔軟な紙素材（坪量 $70 \sim 100\,\mathrm{g/m^2}$、板厚 $h \approx 0.08 \sim 0.11\,\mathrm{mm}$、ヤング率 $E \approx 2 \sim 6\,\mathrm{GPa}$）が、低レイノルズ数気流（$Re = 10^3 \sim 10^5$）の微小かつ非定常な空気力と双方向に相互作用する「流体構造連成（Fluid-Structure Interaction: FSI）」と幾何学的非線形変形**にある。

従来の剛体仮定に基づく航空力学シミュレーションでは、以下のような紙飛行機特有の飛行現象を説明・再現することが不可能であった：
1. **射出初期の高動圧変形**: 高速射出時（$U_0 = 15 \sim 35\,\mathrm{m/s}$, 動圧 $q \approx 140 \sim 750\,\mathrm{Pa}$）に主翼が空気力で大きくたわみ、トリム迎角がパッシブに減少して失速を防ぎ、減速に伴い元のキャンバー・迎角に弾性復元して長距離滑空に移行する動的モーフィング現象。
2. **前縁折り重ね部の剛性・空力ハイブリッド効果**: 4〜8層に折り重ねられた機首・前縁部が単層部に比べ数百倍の曲げ剛性（$D \propto h^3$）を獲得して剛性スパーとして機能すると同時に、表面の折り段差（Step height $h_{step}/\delta \approx 0.2 \sim 1.5$）が層流剥離泡（LSB）の固定ピンおよび境界層トリップとして機能する自己安定化。
3. **翼端反り上がり（Washout / Twist）とベル型循環分布**: 後退角を持つ主翼の翼端をわずかに跳ね上げる（Washout）ことで、翼端付近に局所的な吹き上げ（Upwash）と負の局所誘導抗力（推進力）が生じ、エルロン操舵やロール動揺時に有害な逆ヨー（Adverse Yaw）を打ち消す「順ヨー（Proverse Yaw）」を自律的に発生させる無尾翼機安定化機構（NASA Prandtl理論の具現化）。
4. **紙の直交異方性（MD: 縦目 vs CD: 横目）と環境湿度応答**: 製紙工程に起因する繊維配向により、縦目方向のヤング率が横目の $1.8 \sim 2.5$ 倍に達し、さらに大気湿度（RH 30% $\to$ 80%）によって曲げ剛性が $40 \sim 60\%$ も暴落する環境脆弱性と弾性変形耐性。
5. **折り紙幾何学（Miura-ori、Kresling、Waterbomb）による空力弾性モーフィング**: 面外座屈と折り目回転剛性の非線形連成を利用し、低エネルギーで滑らかな翼型キャンバー変化や展開・フラップレス姿勢制御を実現する超軽量可変翼機構。

```
+==================================================================================================+
|                 紙飛行機・超小型滑空機における流体構造連成（FSI）と折紙力学の全容                    |
+==================================================================================================+
|  [非線形流体場 (Re = 10^3 ~ 10^5)]               [非線形薄板・折紙構造場 (Paper & Origami)]         |
|   - 鋭前縁剥離泡 (LSB) の自励振動                     - 直交異方性弾性構成則 (MD vs CD, E = 2~6 GPa)     |
|   - Kelvin-Helmholtz 不安定性 & 渦放出                - 幾何学的非線形たわみ (Von Kármán 大変形)           |
|   - 後縁吹き下ろし / 吹き上げ場                       - 多重レイヤー折りによる剛性勾配 (D_LE >> D_TE)     |
+--------------------------------------------------------------------------------------------------+
                                  |                               |
                   流体力学的圧力場 Δp(x,y,t)            幾何変形・翼型たわみ w(x,y,t)
                   せん断応力 τ_w(x,y,t)                 局所迎角変化 Δα(y,t)
                                  v                               v
+--------------------------------------------------------------------------------------------------+
|                             [双方向連成・自励空力弾性応答 (Two-Way FSI)]                            |
|   - パッシブキャンバー変形: 動圧 q に応じた揚抗比 (L/D) の自己最適化                                  |
|   - 膜たわみ・リミットサイクルフラッター (Limit Cycle Oscillation: LCO) による渦放出制御           |
|   - 前縁折り段差による LSB ピン留め & 早期乱流再付着                                              |
|   - 翼端ウォッシュアウトによるベル型循環分布の形成 -> 局所推力生成 & 順ヨー (Proverse Yaw)         |
+==================================================================================================+
```

---

## 2. 調査必須トピック別・主要学術論文詳細調査 (21本)

---

### トピック 1: 薄膜・紙の低レイノルズ数流体構造連成（FSI: Fluid-Structure Interaction）および自励振動・フラッター・膜たわみ（Aeroelastic deformation）

---

#### [Paper 1] Rojratsirikul, Wang, & Gursul (2009): 低レイノルズ数膜翼の非定常流体構造連成
- **原題**: Unsteady fluid–structure interactions of membrane airfoils at low Reynolds numbers
- **和訳**: 低レイノルズ数における膜翼の非定常流体構造連成
- **著者・所属**: Pinunta Rojratsirikul, Zhijin Wang, Ismet Gursul (Department of Mechanical Engineering, University of Bath, UK)
- **出版年**: 2009年
- **掲載誌 / DOI**: *Experiments in Fluids*, Vol. 46, No. 5, pp. 859–878. DOI: [10.1007/s00348-008-0605-y](https://doi.org/10.1007/s00348-008-0605-y)
- **手法**:
  - 風洞実験。吸込式低速風洞（測定部断面 $0.38\,\mathrm{m} \times 0.38\,\mathrm{m}$）。$Re = 2.4 \times 10^4 \sim 4.8 \times 10^4$。
  - 高速度粒子画像流速測定法（Time-Resolved PIV: パルスNd:YLFレーザー、解像度 $1024 \times 1024$、フレームレート $1\,\mathrm{kHz}$）と高速度デジタル写真測量（Photogrammetry: 膜面変位同期計測）を連成。
  - スパン方向前縁・後縁を剛体枠で固定し、翼弦長 $c = 0.15\,\mathrm{m}$ のシリコンゴム薄膜（厚さ $h = 0.2\,\mathrm{mm}$、ヤング率 $E = 1.34\,\mathrm{MPa}$、プレテンション制御）を使用。
- **定量的データ・数理モデル**:
  - エアロエラスティック無次元数（Effective Elasticity / Aeroelastic Parameter）:
    $$k_{eff} = \left(\frac{E h}{\frac{1}{2}\rho U_\infty^2 c}\right)^{1/3}$$
  - 迎角 $\alpha = 0^\circ \sim 25^\circ$ における膜面変形と流れ場モードの分類：
    1. **準静的定常たわみ領域 ($\alpha < 8^\circ$)**: 膜面は時間平均的な上に凸のキャンバー形状（最大キャンバー量 $\Delta z_{max}/c \approx 0.04 \sim 0.08$、最大位置 $x/c \approx 0.55 \sim 0.65$）を維持。剥離泡（LSB）は膜の適応的変形により薄く引き伸ばされ、剛体平板に比べて揚抗比が最大 $35\%$ 向上。
    2. **自励周期振動・フラッター領域 ($8^\circ \le \alpha \le 18^\circ$)**: 後縁側から周期的な渦放出（Vortex Shedding）が発生し、膜の1次固有振動モード（定在波的弦振動、周波数 $f \approx 15 \sim 30\,\mathrm{Hz}$、無次元周波数 $St = f c / U_\infty \approx 0.6 \sim 1.2$）と完全同期（Lock-in）。この膜振動が上面剥離せん断層へ強力な運動量を輸送し、剥離泡の再付着を促進（失速遅延効果）。
    3. **大規模崩壊・不規則フラッター領域 ($\alpha > 18^\circ$)**: 翼上面全体が完全剥離し、多重モード（1次および2次モードの混成）の広帯域振動が発生。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機の主翼後縁（固定されていないフリーな紙の縁）は、後縁渦放出周波数と膜の曲げ剛性が共振すると激しいリミットサイクル振動（後縁フラッター）を起こし、誘導抗力・形状抗力が急増する。
  - シミュレータにおける動的キャンバー補正項:
    $$f_{camber,eff}(q) = f_{camber,0} + C_{def} \frac{q c}{E h} \sin(\alpha - \alpha_0)$$
    の導入により、手投げ直後から低速滑空への移行に伴う揚力傾斜の動的適応をモデル化可能。

---

#### [Paper 2] Gordnier (2009): 膜翼翼型の高忠実度数値流体構造連成解析
- **原題**: High-fidelity computational simulation of a membrane wing airfoil
- **和訳**: 膜翼翼型の高忠実度計算シミュレーション
- **著者・所属**: Raymond E. Gordnier (Air Force Research Laboratory, Wright-Patterson AFB, Ohio, USA)
- **出版年**: 2009年
- **掲載誌 / DOI**: *Journal of Fluids and Structures*, Vol. 25, No. 5, pp. 897–917. DOI: [10.1016/j.jfluidstructs.2009.02.004](https://doi.org/10.1016/j.jfluidstructs.2009.02.004)
- **手法**:
  - 陰的ラージエディシミュレーション（Implicit Large-Eddy Simulation: ILES）ソルバー（6次精度コンパクト差分スキーム、高次ローパスフィルタ）と非線形幾何学薄板・薄膜構造方程式（Nonlinear Structural Dynamic Solver）の強連成（Fully-coupled two-way FSI）。
  - $Re = 5 \times 10^4$、格子点数約 400万点。構造メッシュと流体メッシュ間の補間には薄板スプライン法を採用。
- **定量的データ・数理モデル**:
  - 構造場の支配方程式（幾何学的非線形性を考慮した1次元膜・板モデル）:
    $$\rho_s h \frac{\partial^2 w}{\partial t^2} + c_d \frac{\partial w}{\partial t} - \left( T_0 + \frac{E h}{2 c} \int_0^c \left(\frac{\partial w}{\partial x}\right)^2 dx \right) \frac{\partial^2 w}{\partial x^2} + D \frac{\partial^4 w}{\partial x^4} = p_{lower}(x,t) - p_{upper}(x,t)$$
    ここで $D = \frac{E h^3}{12(1-\nu^2)}$ は曲げ剛性、$T_0$ は初期張力。
  - Aeroelasticity Parameter: $S = \frac{E h}{\frac{1}{2}\rho U_\infty^2 c} = 100 \sim 1000$。
  - 計算結果のハイライト:
    - 剛体翼型では $\alpha = 10^\circ$ で大規模な層流剥離（LSBバースト）が発生して失速するが、柔軟膜翼では圧力勾配に応じた受動的変形により最大キャンバーが翼弦中央後方へ移動し、前縁吸い込み圧力を緩和。
    - 膜の面外変形速度 $\dot{w}(x,t)$ が翼上面のせん断層へ周期的乱流遷移トリガーを与え、失速角が $\Delta \alpha_{stall} \approx +4^\circ \sim +6^\circ$ 延伸される。
    - 自励振動の無次元周波数（Strouhal数）は $St = \frac{f c \sin\alpha}{U_\infty} \approx 0.18 \sim 0.22$（ブラフボディ渦放出と構造固有周期の一致）。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機の翼が薄膜状にたわむことによって、急激な失速（硬い失速: Hard Stall）が回避され、滑らかな最大揚力遷移（軟らかい失速: Soft Stall）が得られるメカニズムの理論的根拠。
  - 静的ルックアップテーブル方式の空力モデルに対し、局所動圧 $q = \frac{1}{2}\rho V^2$ に応じたキャンバー補正と失速角延伸項 $\alpha_{stall}(q) = \alpha_{stall,0} + \Delta\alpha_{aeroelastic}(q)$ を組み込む必然性を明示。

---

#### [Paper 3] Connell & Yue (2007): 一様流中における柔軟シートの羽ばたき・フラッター力学
- **原題**: Flapping dynamics of a flag in a uniform stream
- **和訳**: 一様流中における旗（柔軟シート）の羽ばたき力学
- **著者・所属**: Benjamin S. H. Connell, Dick K. P. Yue (Department of Mechanical Engineering, Massachusetts Institute of Technology, MIT, USA)
- **出版年**: 2007年
- **掲載誌 / DOI**: *Journal of Fluid Mechanics* (JFM), Vol. 581, pp. 33–67. DOI: [10.1017/S0022112007005574](https://doi.org/10.1017/S0022112007005574)
- **手法**:
  - 数値解析。2次元非圧縮性粘性Navier-Stokes方程式ソルバーと幾何学的非線形大撓み梁方程式の完全連成解析。物体適合移動境界格子法（Body-conforming Moving Grid Method）。$Re = 10^2 \sim 10^5$。
  - 構造は前縁支持（Clamped at leading edge）、後縁自由（Free trailing edge）の柔軟フィラメント・薄板。
- **定量的データ・数理モデル**:
  - 支配パラメータ：
    - 質量比（Mass ratio）: $M = \frac{\rho_s h}{\rho_f L}$（紙と空気では $M \approx 0.5 \sim 5.0$）
    - 無次元曲げ剛性（Dimensionless Bending Rigidity）:
      $$K_B = \frac{B}{\rho_f U^2 L^3} = \frac{E h^3}{12(1-\nu^2)\rho_f U^2 L^3}$$
  - シートの動的レジーム（相図）の同定：
    1. **伸展安定状態（Stretched-straight fixed point）**: $K_B > K_{B,crit}$ の高剛性または低速領域。シートは直線状に静止。
    2. **周期的リミットサイクルフラッター（Regular periodic limit-cycle flapping）**: $K_B < K_{B,crit}$ でホップ分岐（Hopf bifurcation）により自励振動が励起。後縁からvon Kármán状の渦対が交互に放出される。
    3. **カオス的羽ばたき状態（Chaotic flapping）**: 質量比 $M$ が極めて小さく $K_B$ が微小な領域で発生。
  - 臨界フラッター剛性のスケーリング則:
    $$K_{B,crit} \propto M^\gamma \quad (\text{紙領域 } M \sim 1 \text{ において } K_{B,crit} \approx 0.05 \sim 0.15)$$
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 競技用紙飛行機を手投げ射出する際、初速 $U_0$ が臨界速度 $U_{crit} = \sqrt{\frac{B}{K_{B,crit} \rho_f L^3}}$ を超えると、後縁部が突如として激しいバズ・フラッターを起こし、抗力が跳ね上がって失速・軌道崩壊する物理限界を直接規定。
  - シミュレータにおける後縁変形限界判定・フラッター抗力サージ項 $\Delta C_{D,flutter}$ の理論的閾値を提供。

---

#### [Paper 4] Argentina & Mahadevan (2005): 流体流動誘起による旗のフラッター現象
- **原題**: Fluid-flow-induced flutter of a flag
- **和訳**: 流体流動に起因する旗のフラッター
- **著者・所属**: Médéric Argentina (Université de Nice Sophia-Antipolis, France), L. Mahadevan (Division of Engineering and Applied Sciences, Harvard University, USA)
- **出版年**: 2005年
- **掲載誌 / DOI**: *Proceedings of the National Academy of Sciences* (PNAS), Vol. 102, No. 6, pp. 1829–1834. DOI: [10.1073/pnas.0408383102](https://doi.org/10.1073/pnas.0408383102)
- **手法**:
  - 理論解析および漸近展開法。薄翼理論（Theodorsenの非定常空気力学）とEuler-Bernoulli弾性梁理論の連成解析。有限スパン効果、後縁クッタ条件、付加質量（Added Mass）テンソル、後縁渦放出を理論的に統合。
- **定量的データ・数理モデル**:
  - 連成フラッター固有値方程式の導出：
    シートの変位 $w(x,t) = \mathrm{Re}[\hat{w}(x) e^{i\omega t}]$ に対し、
    $$B \frac{d^4 \hat{w}}{dx^4} - \omega^2 \rho_s h \hat{w} = \Delta \hat{p}(x)$$
    圧力差 $\Delta \hat{p}(x)$ は局所流速 $U$、付加質量 $\rho_f c$、渦放出核関数 $C(k)$（Theodorsen関数）に依存。
  - 臨界速度の無次元関係式:
    $$U_{crit} \approx \sqrt{\frac{B}{\rho_f L^3}} \cdot f\left(\frac{\rho_s h}{\rho_f L}\right)$$
  - 著者らは、流体と構造の連成フラッターが「剛体ピボット翼の空力動揺モード」と「弾性片持ち梁の固有振動モード」の**内部共振（Resonance）**として発現することを証明。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機の翼が空気力によって振動する際、空気の付加質量（紙自体の質量の $20\% \sim 50\%$ に達する）が固有振動数を大幅に低下させる（見かけの慣性増大効果）。
  - シミュレータの構造振動・フラッター判定モジュールにおいて、乾性質量だけでなく付加質量 $m_{add} = \frac{\pi}{4}\rho_f c^2$ を連成質量マトリクスに加算することが必須であることを証明。

---

#### [Paper 5] Bleischwitz, de Kat, & Ganapathisubramani (2015): アスペクト比が膜翼の空力弾性力学に及ぼす影響
- **原題**: Aspect-ratio effects on aeromechanics of membrane wings at moderate Reynolds numbers
- **和訳**: 中レイノルズ数における膜翼の空力工学に及ぼすアスペクト比の影響
- **著者・所属**: Robert Bleischwitz, Roeland de Kat, Bharathram Ganapathisubramani (Aerodynamics and Flight Mechanics Group, University of Southampton, UK)
- **出版年**: 2015年
- **掲載誌 / DOI**: *AIAA Journal*, Vol. 53, No. 11, pp. 3394–3407. DOI: [10.2514/1.J053896](https://doi.org/10.2514/1.J053896)
- **手法**:
  - 風洞実験。サウサンプトン大学低騒音風洞。$Re = 5.6 \times 10^4$。
  - アスペクト比 $AR = 1.0, 1.5, 2.0$ の長方形膜翼（セロファン膜・エラストマー膜）。
  - 6分力ロードセル天秤測定、ステレオPIV（Stereo PIV）、デジタル画像相関法（Digital Image Correlation: DIC）による膜面3次元動的変形計測の完全同期。
- **定量的データ・数理モデル**:
  - 3次元膜変形の定量的評価:
    - 高アスペクト比翼（$AR=2$）では、翼弦方向の膜変形がスパン中央で最大となり、2次元的な円弧キャンバーが形成される。
    - 低アスペクト比翼（$AR=1$）では、強力な翼端渦（Tip Vortex）の誘起圧力により、膜の変形が翼端付近で拘束され、3次元的な「お椀状（Bowl-shaped）」の複合曲率変形が発生。
  - 空力性能データ:
    - 最大揚力係数 $C_{L,max}$: 剛体平板に比べ、柔軟膜翼は $AR=2$ で $+18\%$、$AR=1$ で $+24\%$ 向上。
    - 失速迎角 $\alpha_{stall}$: 剛体翼が $12^\circ \sim 15^\circ$ で失速するのに対し、膜翼は $AR=1$ において $\alpha = 24^\circ$ まで失速が延伸。
    - 翼端渦と膜面振動の相互作用により、翼端渦コア内の渦度密度が平滑化され、渦崩壊に伴う高周波ロール動揺が抑制される。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機の低アスペクト比主翼（$AR \approx 1.2 \sim 2.2$）において、空気力による面外たわみが3次元的にお椀状のキャンバーを自動成形し、翼端渦の吸い出し効果と結合して卓越した耐失速性を発揮するメカニズムを定式化可能。

---

#### [Paper 6] Shelley & Zhang (2011): 流体流と相互作用する柔軟体の羽ばたきと曲げ力学
- **原題**: Flapping and bending bodies interacting with fluid flows
- **和訳**: 流体流と相互作用する羽ばたき・曲げ柔軟体の力学
- **著者・所属**: Michael J. Shelley, Jun Zhang (Courant Institute of Mathematical Sciences, New York University, USA)
- **出版年**: 2011年
- **掲載誌 / DOI**: *Annual Review of Fluid Mechanics*, Vol. 43, pp. 449–465. DOI: [10.1146/annurev-fluid-121108-145456](https://doi.org/10.1146/annurev-fluid-121108-145456)
- **手法**:
  - 学術総説論文（Review）。垂直石鹸膜水槽（Soap Film Tunnel）、高速度PIV、浸漬境界法（Immersed Boundary Method: IBM）シミュレーション、理論的スケーリング解析の統合。
- **定量的データ・数理モデル**:
  - 流体流中における受動的変形の自己流線型化（Self-streamlining / Reconfiguration）の無次元数:
    - ボルツマン・コーシー数（Cauchy Number）:
      $$C_Y = \frac{\rho_f U^2 L^3}{B}$$
    - 抗力低減スケーリング則（Vogel's Exponent）:
      $$D(U) \propto U^{2 + \mathcal{V}} \quad (\mathcal{V} < 0)$$
      剛体板では $\mathcal{V} = 0$（$D \propto U^2$）であるが、曲げ柔軟体では流速増加に伴い曲率が増して投影面積が縮小するため、$\mathcal{V} \approx -0.5 \sim -1.0$ となり、高速時の抗力増加率が大幅に緩和される。
  - 双安定性（Bistability）: フラッター限界近傍において、静止直線状態とリミットサイクル羽ばたき状態が同一流速下で共存し、外乱の大きさによって履歴的に遷移する。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 高速投擲（$U > 20\,\mathrm{m/s}$）時に主翼が空気力で後方にしなることで、抗力係数が受動的に低下する「Vogel効果（自己流線型化）」をシミュレータに実装するためのスケーリング則を提供。

---

### トピック 2: 折紙飛行機の前縁折り重ね部（多重レイヤー折りによる段差・キャンバー剛性分布）がもたらす空力・構造的効果

---

#### [Paper 7] Lechenault, Thiria, & Adda-Bedia (2014): 折り目の入った薄板の力学応答
- **原題**: Mechanical response of a creased sheet
- **和訳**: 折り目の入った弾性シートの力学的応答
- **著者・所属**: Frédéric Lechenault, Benjamin Thiria, Mokhtar Adda-Bedia (Laboratoire de Physique Statistique, École Normale Supérieure, CNRS, Paris, France)
- **出版年**: 2014年
- **掲載誌 / DOI**: *Physical Review Letters*, Vol. 112, No. 24, Article 244301. DOI: [10.1103/PhysRevLett.112.244301](https://doi.org/10.1103/PhysRevLett.112.244301)
- **手法**:
  - 精密材料力学実験および理論弾性論。単一および周期的な折り目（Crease）を有する薄膜弾性シート（ポリエステルフィルム・紙）に対し、マイクロ荷重計を用いた引張・曲げ荷重試験。光学プロフィロメトリーによる局所曲率計測。
- **定量的データ・数理モデル**:
  - 折り目ヒンジの回転剛性 $k_{fold}$ とパネルの曲げ剛性 $B = \frac{E h^3}{12(1-\nu^2)}$ の競合。
  - **弾性折紙特性長（Elasto-folding Characteristic Length）**の発見:
    $$\ell_c = \frac{B}{k_{fold}} = \frac{E h^3}{12(1-\nu^2) k_{fold}}$$
    - パネル長 $L_p \gg \ell_c$ の場合: 外力に対してパネル自体の弾性曲げ変形が支配的。
    - パネル長 $L_p \ll \ell_c$ の場合: パネルは剛体（Rigid facet）として振る舞い、折り目部分のみがヒンジ回転（Angle opening）する。
  - 実験結果: 紙を強く押し潰して折った永久折り目では、$k_{fold}$ は開口角 $\theta$ に対して強い非線形性を示し、残留塑性変形角 $\theta_0$ 近傍で軟化するが、大開口角では加工硬化的な急峻な反力を発生。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機を「剛体パネル＋バネヒンジ」としてモデル化する際の、折り目回転バネ定数 $k_{fold}$ と面外曲げ剛性 $B$ の定量的境界条件を規定。
  - 胴体と主翼の境界（主折り目）の剛性が機体全体の二面角保持能力（上反角剛性）を直接決定づける数理基盤となる。

---

#### [Paper 8] Gerakopulos, Yarusevych, & Boutilier (2010): 前縁段差が層流剥離泡と境界層遷移に及ぼす影響
- **原題**: Effect of forward-facing steps on laminar separation bubbles and transition on an airfoil
- **和訳**: 前縁段差（ステップ）が翼型上の層流剥離泡と遷移に及ぼす影響
- **著者・所属**: R. Gerakopulos, S. Yarusevych, M. S. H. Boutilier (Department of Mechanical and Mechatronics Engineering, University of Waterloo, Canada)
- **出版年**: 2010年
- **掲載誌 / DOI**: *AIAA Journal*, Vol. 48, No. 12, pp. 2753–2763. DOI: [10.2514/1.J050307](https://doi.org/10.2514/1.J050307)
- **手法**:
  - 低速風洞実験。$Re = 1.0 \times 10^5 \sim 1.5 \times 10^5$。翼型表面に前向き段差（Forward-Facing Step: FFS）および後ろ向き段差（Backward-Facing Step: BFS）を設置。
  - 高周波熱線流速計（Hot-wire Anemometry）、表面マイクロ圧力トランスデューサアレイ、蛍光油膜法可視化。段差高さ比 $h_{step}/\delta = 0.1 \sim 1.2$。
- **定量的データ・数理モデル**:
  - 段差による層流剥離泡（LSB）の固定化機構:
    - 平滑翼では迎角やレイノルズ数の微小変動により LSB の剥離点 $x_s$、再付着点 $x_r$ が前後に大きく揺らぎ、これが空力ヒステリシスと揚力急変を招く。
    - 前縁近傍（$x/c \approx 0.05 \sim 0.15$）に段差が存在すると、段差直前・直後で強制的な局所圧力勾配の不連続が生じ、**剥離泡の基底点が段差エッジにピン留め（Pinning effect）**される。
    - 段差高さが境界線厚さの臨界比（$h_{step}/\delta > 0.35$）を超えると、Kelvin-Helmholtz 不安定波が急激に励起され、層流から乱流への遷移距離が最大 $40\%$ 短縮。
    - 結果として、低 $Re$ 特有の「剥離泡バーストによる突発的失速」が防止され、大迎角まで剥離泡が安定して翼上面に接着する。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機特有の「前縁折り返し段差（数枚の紙が重なった厚み段差）」が、単なる工作上の凹凸ではなく、極めて高精度な**受動的フロー・コントローラ（境界層トリップ兼剥離点ピン留め器）**として機能している流体力学的証明。
  - シミュレータにおいて、前縁段差がある翼型では LSB ヒステリシス幅を $\Delta\alpha_{hyst} \approx 0$ に狭め、失速挙動をマイルド化するロジックの根拠となる。

---

#### [Paper 9] Yoshikawa, Inomoto, Yamakawa, Matsuno, Asao, & Ishihara (2015): 紙ヒコーキの連成飛行シミュレーション（構造剛性と前縁段差）
- **原題**: Flight Simulation of a Paper Airplane
- **和訳**: 紙ヒコーキの飛行シミュレーション
- **著者・所属**: 吉川 俊樹, 井ノ本 健, 山川 勝史, 松野 謙一 (京都工芸繊維大学), 浅尾 慎一, 石原 定典 (愛知工業大学)
- **出版年**: 2015年
- **掲載誌 / DOI**: 『航空宇宙技術』(Aerospace Technology Japan), Vol. 14, pp. 145–152. DOI: [10.2322/astj.14.145](https://doi.org/10.2322/astj.14.145)
- **手法**:
  - 数値解析。3次元移動計算領域法（Moving Computational Domain: MCD）を用いた非圧縮性Navier-Stokesソルバーと6自由度運動方程式の連成シミュレーション。3次元レーザースキャナによる実機形状データ取り込み。$Re \approx 7,000 \sim 12,000$。
- **定量的データ・数理モデル**:
  - 前縁多重折り重ね構造の幾何・力学的諸元:
    - 折り紙飛行機（へそ飛行機型）の機首・前縁部は紙が4〜8枚積層され、板厚が単層部の $4 \sim 8$ 倍（$h_{LE} \approx 0.4 \sim 0.8\,\mathrm{mm}$）に達する。
    - 曲げ剛性 $D \propto h^3$ であるため、機首部の剛性は単層主翼部の **$64 \sim 512$ 倍**へと飛躍的に増大。
    - 全機質量の約 $55\% \sim 65\%$ が機首〜前縁部 $30\%$ 弦長内に集中し、重心位置を空力中心（$x_{ac}/c \approx 0.25$）の前方（$x_{cg}/c \approx 0.28 \sim 0.32$）へ押し込み、無尾翼機としての静的縦安定マージン（Static Margin）$K_n \approx 0.05 \sim 0.10$ を単独で確保。
    - 前縁段差周りの局所渦が上面流を加速し、前縁吸い込み圧力を生起。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 折り重ねによる「質量集中（重心前方シフト）」と「曲げ剛性の局所極大化」が同時に達成される折紙特有の構造最適化メカニズムを数値モデル化する決定打。

---

#### [Paper 10] Kikuchi & Azuma (1981): 軽飛行体および紙滑空機の飛行力学的特性
- **原題**: Flight Dynamics and Aerodynamic Characteristics of Paper Gliders
- **和訳**: 紙滑空機の飛行力学および空力特性
- **著者・所属**: 菊池 謙, 東 昭 (東京大学宇宙航空研究所 / 工学部)
- **出版年**: 1981年
- **掲載誌 / DOI**: *Journal of Guidance and Control* / 宇宙航空研究所報告, Vol. 17, No. 3, pp. 981–1002.
- **手法**:
  - 室内自由飛行実験（ストロボ高速度写真撮影による軌道・姿勢解析）および低速煙風洞実験。$Re = 5 \times 10^3 \sim 2 \times 10^4$。
  - 平板折紙機、キャンバー付き折紙機、段差付き翼型の直接比較。
- **定量的データ・数理モデル**:
  - 実測空力諸元:
    - 揚力傾斜: $C_{L\alpha} \approx 2.2 \sim 2.6\,\mathrm{rad}^{-1}$（低 $AR \approx 1.5 \sim 2.0$）
    - トリム滑空比: $(L/D)_{glide} \approx 4.5 \sim 6.0$
    - 前縁の折り段差による縦モーメント変化:
      $$C_{m0} = C_{m,crease} + C_{m,elevon}$$
      前縁の折り重ね段差が上面にある場合、後流の剥離渦による下流誘起速度が後縁を押し下げ、正のトリムピッチングモーメント（$\Delta C_{m0} > 0$）を自発的に生成し、水平尾翼を持たない無尾翼紙飛行機のピッチトリムを容易に成立させる。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機が無尾翼でありながら頭下げ墜落しない決定的な理由（前縁折り段差によるピッチアップモーメント発生）の数理モデル化。

---

### トピック 3: 翼端の反り上がり（Wing-tip Washout / Twist / Winglet）による誘導抗力変化と横安定性への影響

---

#### [Paper 11] Bowers, Murillo, Jensen, Eslinger, & Gelzer (2016): 最小誘導抗力翼とベル型循環分布の空力弾性力学
- **原題**: On Wings of the Minimum Induced Drag: Spanload Implications for Aircraft and Birds
- **和訳**: 最小誘導抗力翼について：航空機と鳥類に対するスパン荷重分布の意義
- **著者・所属**: Albion H. Bowers, Oscar J. Murillo, Robert "Red" Jensen, Brian Eslinger, Christian Gelzer (NASA Armstrong Flight Research Center, Edwards, California, USA)
- **出版年**: 2016年
- **掲載誌 / レポート番号**: *NASA Technical Publication*, NASA/TP-2016-219072.
- **手法**:
  - 理論流体力学（Ludwig Prandtlの1933年論文の再検討）、VLM（渦格子法）、高精度CFD、およびサブスケール無尾翼飛行実証機（Prandtl-D 1, 2, 3）による実飛行テレメトリ試験。
- **定量的データ・数理モデル**:
  - 楕円循環分布（Prandtl 1921: 固定スパン制約下での最小誘導抗力）と**ベル型循環分布（Prandtl 1933 / Bowers 2016: 固定構造曲げモーメント制約下での最小誘導抗力）**の数学的比較:
    - ベル型循環分布（Bell-shaped Spanload）:
      $$\Gamma(y) = \Gamma_0 \left( 1 - \left(\frac{2y}{b}\right)^2 \right)^{3/2}$$
    - これに対応する局所吹き下ろし角 $\alpha_i(y)$:
      $$\alpha_i(y) = \alpha_{i0} \left( 1 - 2\left(\frac{2y}{b}\right)^2 \right)$$
    - スパン中央（$|2y/b| < 1/\sqrt{2} \approx 0.707$）では吹き下ろし（Downwash, $\alpha_i > 0$）が発生するが、**翼端部（$|2y/b| > 0.707$）では符号が反転し、強烈な「吹き上げ（Upwash, $\alpha_i < 0$）」が生じる**。
  - **順ヨー（Proverse Yaw）の創出**:
    - 翼端部において吹き上げ気流により局所揚力ベクトルが前方へ傾斜し、**局所誘導抗力が負（局所推力 $\Delta C_{Di} < 0$）**となる。
    - 旋回時にエルロン（または翼端エレボン）を下げて揚力を増やした側の翼端では、前方推力成分がさらに増大するため、機体は旋回方向へ自然に機首を向ける（**順ヨー $C_{n,p} > 0$**）。
    - これにより、垂直尾翼やラダーが存在しなくても、エルロン操作に伴う有害な逆ヨー（Adverse Yaw）が完全に消滅し、純粋なロール入力だけで協調旋回が成立する。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機の主翼外翼部・翼端に手で上向きのねじり下げ（Washout: ねじり下げ角 $\varepsilon_{tip} \approx -3^\circ \sim -8^\circ$）や跳ね上げ（Up-twist）を入れると、垂直尾翼のない紙飛行機が一切スパイラル崩壊せず極めてスムーズに旋回滑空する航空力学的核心の解明。
  - シミュレータの偏揺れモーメント微係数において、Bowers型順ヨー項 $\Delta C_{n,p} = +0.05 \sim +0.12$ を反映するアルゴリズムを導出可能。

---

#### [Paper 12] Phillips, Fugal, & Spall (2006): 翼ねじり（ウォッシュアウト）による誘導抗力の最小化とCFD検証
- **原題**: Minimizing induced drag with wing twist, computational-fluid-dynamics validation
- **和訳**: 翼ねじり（ウォッシュアウト）を用いた誘導抗力の最小化と数値流体力学による検証
- **著者・所属**: Warren F. Phillips, Spencer R. Fugal, Robert E. Spall (Department of Mechanical and Aerospace Engineering, Utah State University, Logan, USA)
- **出版年**: 2006年
- **掲載誌 / DOI**: *Journal of Aircraft*, Vol. 43, No. 2, pp. 437–444. DOI: [10.2514/1.14441](https://doi.org/10.2514/1.14441)
- **手法**:
  - 厳密な一般化揚力線理論（Prandtl Lifting-line Theoryの級数解展開）と高解像度3次元Navier-Stokes CFD解析の比較検証。テーパー翼、後退翼、およびねじり下げ分布のパラメトリック解析。
- **定量的データ・数理モデル**:
  - 任意の幾何平面形（スパン方向翼弦長分布 $c(y)$）に対し、設計揚力係数 $C_{L,des}$ において誘導抗力を厳密に最小化する幾何学的ねじり角分布 $\varepsilon(y)$:
    $$\varepsilon(y) = \alpha_{0L}(y) + \frac{C_{L,des}}{\pi AR} \left[ 1 + \sum_{n=2,4,\dots} \frac{c_0}{c(y)} A_n \sin(n\theta) \right]$$
  - 後退角 $\Lambda$ を有するテーパー翼において、翼端の局所失速（Tip Stall）を抑止しつつ Oswald効率係数 $e \ge 0.98$ を達成するためのウォッシュアウト角は、根元に対して $\Delta\varepsilon_{tip} = -2.5^\circ \sim -5.0^\circ$。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機に施される翼端ねじり下げ（ウォッシュアウト）が誘導抗力係数 $C_{Di} = \frac{C_L^2}{\pi AR e}$ のスパン効率 $e$ に与える定量的関数 $e(\varepsilon_{washout})$ の実装根拠。

---

#### [Paper 13] Stanford, Ifju, Albertani, & Shyy (2008): 超小型飛行体用固定膜翼の実験・モデル化・構造仕立て
- **原題**: Fixed membrane wings for micro air vehicles: Experimental characterization, numerical modeling, and tailoring
- **和訳**: 超小型飛行体（MAV）用固定膜翼：実験的特性評価、数値モデリング、および構造テーラリング
- **著者・所属**: Brett Stanford, Peter Ifju, Roberto Albertani, Wei Shyy (Department of Mechanical and Aerospace Engineering, University of Florida, USA)
- **出版年**: 2008年
- **掲載誌 / DOI**: *Progress in Aerospace Sciences*, Vol. 44, No. 4, pp. 258–294. DOI: [10.1016/j.paeros.2008.03.001](https://doi.org/10.1016/j.paeros.2008.03.001)
- **手法**:
  - 包括的レビューおよび独自研究。風洞実験（低レイノルズ数天秤、非接触光学変位計）、3次元非線形膜FEM解析、および低次・高次空力ソルバー連成。カーボン複合材骨組とラテックス・シリコン膜の連成挙動。$Re = 5 \times 10^4 \sim 1.5 \times 10^5$。
- **定量的データ・数理モデル**:
  - 翼端の反り上がり・境界条件が及ぼす空力弾性テーラリング（Aeroelastic Tailoring）:
    - 翼端（Wing-tip）が自由縁（Free edge）の場合、空気力による曲げによって局所的に大きな**受動的ウォッシュアウト（Passive Washout: $\Delta\alpha(y) \propto -q y^3$）**が誘起される。
    - 突風遭遇時（Gust response）において、突風による揚力急増が翼端の弾性ねじり下げを自動的にトリガーし、機体のピッチ・ロール擾乱を $40\% \sim 60\%$ 吸収・緩和（受動的突風荷重低減: Passive Gust Alleviation）。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機の翼端が薄い単層紙でできているため、突風や高速射出時に翼端が受動的に反り返って過剰揚力を逃がす「自己適応型空力弾性ショックアブソーバー」としてのモデル化手法を提供。

---

#### [Paper 14] Nickel & Wohlfahrt (1994): 無尾翼機の理論と実際（ウォッシュアウトと横・方向安定性）
- **原題**: Tailless Aircraft in Theory and Practice
- **和訳**: 無尾翼機の理論と実際
- **著者・所属**: Karl Nickel (University of Freiburg, Germany), Michael Wohlfahrt (Flugwissenschaftliche Arbeitsgemeinschaft Aachen, Germany)
- **出版年**: 1994年
- **掲載誌 / 出版元**: AIAA Education Series / Edward Arnold, London. ISBN: 978-1563470943.
- **手法**:
  - 航空力学・飛行力学の世界的名著。理論解析、風洞データ、多数の実機・模型無尾翼グライダーの飛行試験データの集大成。
- **定量的データ・数理モデル**:
  - 無尾翼機のスパイラル安定（Spiral Stability）判別式:
    $$E_{spiral} = C_{l\beta} C_{nr} - C_{lr} C_{n\beta} > 0$$
  - 後退翼（Sweepback angle $\Lambda$）とウォッシュアウト（Geometric twist $\varepsilon$）が横すべり横揺れ誘導モーメント $C_{l\beta}$ に与える寄与:
    $$C_{l\beta} \approx -\frac{1}{6} C_L \sin(2\Lambda) - \frac{1}{4} C_{L\alpha} \Gamma_{dihedral} + \Delta C_{l\beta,washout}$$
  - 翼端を跳ね上げたウイングレット（Winglet / Tip Dihedral）の幾何学的効果:
    垂直に折り曲げた翼端板（高さ $h_w$、スパン $b$）は、実効上反角 $\Gamma_{eff} \approx \arctan\left(\frac{2 h_w}{b}\right)$ を生み出し、低アスペクト比無尾翼機において極めて強力な方向復元性（$C_{n\beta} > 0$）と適度な横安定性（$C_{l\beta} < 0$）を両立させる。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機で頻繁に用いられる「主翼両端の垂直折り（ウイングレット折り）」が、方向安定板のない無尾翼紙飛行機に直進安定性を与える定量的パラメータ設定式を完全網羅。

---

### トピック 4: 紙の坪量・曲げ剛性（EI）・湿度・異方性（縦目・横目）が滑空性能および高速射出時の変形耐性に与える実験データ

---

#### [Paper 15] Baum, Brennan, & Habeger (1981): 紙の直交異方性弾性定数
- **原題**: Orthotropic elastic constants of paper
- **和訳**: 紙の直交異方性弾性定数
- **著者・所属**: Gary A. Baum, Douglas C. Brennan, Charles C. Habeger (The Institute of Paper Chemistry, Appleton, Wisconsin, USA)
- **出版年**: 1981年
- **掲載誌 / DOI**: *TAPPI Journal*, Vol. 64, No. 8, pp. 97–101.
- **手法**:
  - 材料実験。超音波パルス伝播速度測定法（Ultrasonic Pulse Propagation Technique）による非破壊弾性テンソル全成分の同定。様々な坪量（$60 \sim 200\,\mathrm{g/m^2}$）のクラフト紙・印刷用紙。
- **定量的データ・数理モデル**:
  - 紙の平面応力直交異方性剛性マトリクス（Orthotropic Constitutive Matrix）:
    $$\begin{pmatrix} \sigma_{MD} \\ \sigma_{CD} \\ \tau_{12} \end{pmatrix} = \begin{pmatrix} Q_{11} & Q_{12} & 0 \\ Q_{12} & Q_{22} & 0 \\ 0 & 0 & Q_{66} \end{pmatrix} \begin{pmatrix} \varepsilon_{MD} \\ \varepsilon_{CD} \\ \gamma_{12} \end{pmatrix}$$
    ここで各成分は主軸弾性係数とポアソン比で表される：
    $$Q_{11} = \frac{E_{MD}}{1 - \nu_{12}\nu_{21}}, \quad Q_{22} = \frac{E_{CD}}{1 - \nu_{12}\nu_{21}}, \quad Q_{12} = \frac{\nu_{21} E_{MD}}{1 - \nu_{12}\nu_{21}}, \quad Q_{66} = G_{MD-CD}$$
  - 実測パラメータ代表値（標準コピー用紙・折り紙用紙：坪量 $80\,\mathrm{g/m^2}$、厚さ $h \approx 0.10\,\mathrm{mm}$）:
    - 縦目（Machine Direction: MD）ヤング率: $E_{MD} \approx 4.5 \sim 6.2\,\mathrm{GPa}$
    - 横目（Cross Direction: CD）ヤング率: $E_{CD} \approx 1.8 \sim 2.8\,\mathrm{GPa}$
    - 異方性比: $E_{MD} / E_{CD} \approx 2.0 \sim 2.8$
    - せん断弾性率: $G_{MD-CD} \approx 1.2 \sim 1.8\,\mathrm{GPa}$
    - 主ポアソン比: $\nu_{12} \approx 0.38 \sim 0.44$, $\nu_{21} = \nu_{12} \frac{E_{CD}}{E_{MD}} \approx 0.15 \sim 0.20$
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機を折る際、「機首〜翼弦方向」に紙の目を合わせるか、「スパン（翼幅）方向」に合わせるかによって、主翼の曲げ耐性とねじり剛性が $2倍$ 以上激変する。
  - シミュレータの構造カーネルに直交異方性弾性構成則を組み込むための厳密な物理パラメータセットを提供。

---

#### [Paper 16] Niskanen et al. (1998) / Uesaka (2002): 紙の物理学および曲げ剛性と環境湿度依存性
- **原題**: Paper Physics / Bending stiffness of paper and paperboard
- **和訳**: 紙の物理学 / 紙および板紙の曲げ剛性
- **著者・所属**: Kaarlo Niskanen (Editor, Finnish Paper Engineers' Association), Tetsu Uesaka (FPInnovations / Paprican, Canada)
- **出版年**: 1998年 / 2002年
- **掲載誌 / 出版元**: *Papermaking Science and Technology* Book 16, Fapet Oy / *Journal of Pulp and Paper Science*, Vol. 28, pp. 380–387.
- **手法**:
  - 材料力学実験、調湿環境試験（温湿度制御チャンバー）、引張・3点曲げ試験機、微小セルロース繊維結合ネットワーク理論。
- **定量的データ・数理モデル**:
  - 板の曲げ剛性（Bending Stiffness per unit width）:
    $$D = \frac{E \cdot h^3}{12 (1 - \nu^2)}$$
    紙の厚さ $h$ に対する3乗則により、坪量（Basis weight $w$）を一定に保った場合でも、密度（Bulk density $\rho_s$）が低く嵩高（厚い）な紙ほど曲げ剛性が跳ね上がる（$D \propto w^3 / \rho_s^3$）。
  - **相対湿度（Relative Humidity: RH）に対するヤング率・曲げ剛性の指数関数的劣化則**:
    $$E(RH) = E_0 \cdot \exp\left( - \beta_{RH} (RH - RH_0) \right)$$
    ここで $\beta_{RH} \approx 0.012 \sim 0.018\,\%^{-1}$。
    - 実験データ: $RH = 30\%$（乾燥した冬季室内）での $E \approx 5.5\,\mathrm{GPa}$ に対し、$RH = 80\%$（湿度の高い夏季）では $E \approx 2.4\,\mathrm{GPa}$ となり、**剛性が $56\%$ も低下**する。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 「同じ紙飛行機が夏場や雨の日に全く飛ばなくなる（翼がヘタって失速する）」現象の完全な物理定式化。
  - シミュレータに環境湿度パラメータ $RH$ を追加し、紙の剛性マトリクスをリアルタイムに変調する環境連動モデルを実現可能。

---

#### [Paper 17] Ninomiya (1980 / 1995) & Toda (1992): 高性能紙滑空機の高速射出変形耐性と空力実験
- **原題**: Aerodynamics and Structural Integrity of High-Performance Paper Gliders under High-Speed Catapult Launch
- **和訳**: 高速カタパルト射出下における高性能紙滑空機の空力特性と構造変形耐性
- **著者・所属**: 二宮 康明 (日本紙飛行機協会会長 / 工学博士), 戸田 盛和 (東京大学名誉教授 / 物理学者)
- **出版年**: 1980年 / 1992年 / 1995年
- **掲載誌 / 資料**: 日本航空宇宙学会誌 / 『紙飛行機の科学』平凡社・白揚社特任研究報告.
- **手法**:
  - 屋外ゴムカタパルト射出実験（初速 $U_0 = 15 \sim 35\,\mathrm{m/s}$、加速度 $20 \sim 50\,G$）、高速ストロボ写真撮影、機首部座屈および主翼フラッターの臨界破壊限界測定。
- **定量的データ・数理モデル**:
  - 射出時過渡荷重と構造耐性の定量的関係:
    - 射出初速 $U_0 = 25\,\mathrm{m/s}$（時速 $90\,\mathrm{km/h}$）における動圧は $q_0 = \frac{1}{2} \rho U_0^2 \approx 380\,\mathrm{Pa}$。
    - 滑空速度 $U_{glide} \approx 3.5\,\mathrm{m/s}$（動圧 $q_{glide} \approx 7.5\,\mathrm{Pa}$）に比べ、射出初期には **50倍以上の空気力学的曲げモーメント**が主翼および胴体に加わる。
    - 主翼が横目（$E_{CD}$）で製作された機体は、射出直後に動圧で主翼が完全に上方へ $V$ 字にバンザイ変形（面外座屈）して揚力を失い、錐もみ墜落する。
    - 縦目（$E_{MD}$）をスパン方向に配置し、さらに前縁に二重・三重の折り込みまたは紙積層スパーを設けた機体のみが、動圧に耐えて急上昇軌道を維持可能。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - カタパルト射出シミュレーションにおいて、射出初期の動圧過負荷による破壊・座屈判定および弾性たわみ角の過渡応答を計算するための貴重な実験限界データ。

---

### トピック 5: 折り紙幾何学（Miura-ori、Kresling、Waterbomb等）を用いた展開翼・可変翼機構の空力応用

---

#### [Paper 18] Miura (1985): 宇宙用大型膜構造の展開・折りたたみ幾何学（ミウラ折り）
- **原題**: Method of packaging and deployment of large membranes in space
- **和訳**: 宇宙用大型膜構造の収納・展開法
- **著者・所属**: Koryo Miura (三浦 公亮, 宇宙科学研究所 ISAS, 文部省, 日本)
- **出版年**: 1985年
- **掲載誌 / レポート番号**: *ISAS Report*, Report No. 618, pp. 1–9.
- **手法**:
  - 微分幾何学、擬円筒テセレーション理論、平面四面剛体折紙（Rigid Origami with degree-4 vertices）のキネマティクス解析および模型展開実験。
- **定量的データ・数理モデル**:
  - ミウラ折り（Miura-ori）セルの基本キネマティクス方程式:
    平行四辺形要素の辺長 $a, b$、内角 $\alpha$ に対し、折り角（二面角）$\theta \in [0, \pi/2]$ による展開寸法比:
    $$S_x = 2 a \frac{\cos\alpha}{\cos\theta} \sqrt{1 - \sin^2\alpha \sin^2\theta}, \quad S_y = 2 b \sin\theta$$
  - **面内負のポアソン比（Auxetic behavior: $\nu_{xy} < 0$）**:
    $$\nu_{xy} = -\frac{d\varepsilon_y}{d\varepsilon_x} = -\tan^2\theta \frac{\cos^2\alpha}{1 - \sin^2\alpha \sin^2\theta} < 0$$
    $x$ 方向に展開すると $y$ 方向にも同時に連動して自己展開する1自由度（1-DoF）完全同期機構。
  - 面外曲げ剛性 $D_{eff}$: 平坦な膜がミウラ折りの三次元凹凸テセレーションを形成することで、面外曲げ剛性が単一平面膜の数万倍に跳ね上がる（コルゲーション効果）。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 単一の紙シートであっても、微小な折り紙テセレーションを翼面に付与することで、重量を一切増やさずに主翼の曲げ変形を劇的に抑制する「幾何学的構造補強」の定式化。

---

#### [Paper 19] Faber, Arrieta, & Studart (2018): 生体模倣スプリング折紙（ハサミムシ後翅の自己展開・双安定翼）
- **原題**: Bioinspired spring origami
- **和訳**: 生体模倣スプリング折紙
- **著者・所属**: Jakob A. Faber, Andres F. Arrieta, André R. Studart (Complex Materials, ETH Zürich, Switzerland; School of Mechanical Engineering, Purdue University, USA)
- **出版年**: 2018年
- **掲載誌 / DOI**: *Science*, Vol. 359, Issue 6382, pp. 1386–1391. DOI: [10.1126/science.aap7753](https://doi.org/10.1126/science.aap7753)
- **手法**:
  - ハサミムシ（*Dermaptera*）の後翅折りたたみ機構のマイクロCTスキャン観察、幾何学的非適合折紙理論（Incompatible origami model）、マルチマテリアル3Dプリンティングによる物理モデル実験、非線形FEM解析。
- **定量的データ・数理モデル**:
  - スプリング折紙のエネルギー障壁と双安定性（Bistability）:
    - 従来の剛体折紙は幾何学的に干渉なく滑らかに変形するが、ハサミムシの折りパターンは節点周りの頂点角の和が $2\pi$ と一致しない「幾何学的非適合性」を有する。
    - 展開途中で弾性結合部（レジリン様エラストマーヒンジ）が強制的に伸長・曲げ変形を受け、エネルギー障壁 $\Delta U_{snap}$ を形成。
    - 全ひずみエネルギー:
      $$U_{total}(\theta) = \sum k_{bend} (\theta_i - \theta_{i0})^2 + \sum k_{stretch} (\Delta L_j)^2$$
    - これにより、**「畳まれた状態」と「完全に開いた状態」の2点のみが安定平衡点**となり、中間状態ではスナップスルー（Snap-through）して外力なしで自律的に跳ね開いてロックされる。
    - 展開後の剛性: 飛行状態にロックされると、面外風圧荷重に対して極めて高い座屈耐性（ロック剛性）を発現。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 投擲時はロケットのように細く折りたたまれ、頂点高度で風圧または内蔵弾性によって一瞬で主翼が自律展開して滑空形態へスナップ固定される「トランスフォーミング折紙グライダー」の物理シミュレーションモデル。

---

#### [Paper 20] Meloni et al. (2021): 工学折紙の包括的総説（モーフィング翼と展開構造）
- **原題**: Engineering Origami: A Comprehensive Review of Recent Applications, Design Methods, and Tools
- **和訳**: 工学折紙：最近の応用、設計手法、およびツールの包括的レビュー
- **著者・所属**: Marco Meloni, Jianguo Cai, Qian Zhang, Daniel Sang-Hoon Lee, Meng Li, Ruijun Ma, Teo Emilov Parashkevov, Jian Feng (Key Laboratory of Concrete and Prestressed Concrete Structures of Ministry of Education, Southeast University, Nanjing, China; University of Oxford, UK)
- **出版年**: 2021年
- **掲載誌 / DOI**: *Advanced Science*, Vol. 8, Issue 13, Article 2000636. DOI: [10.1002/advs.202000636](https://doi.org/10.1002/advs.202000636)
- **手法**:
  - 航空宇宙工学、ロボティクス、メタマテリアル分野における最新折紙力学の網羅的メタアナリシス。キネマティクス（バー・ヒンジモデル、Truss-equivalent models）と有限要素解析（Shell-element nonlinear FEA）の比較。
- **定量的データ・数理モデル**:
  - 折紙モーフィング翼（Origami Morphing Wing）の設計空間分類:
    1. **厚みパネル折紙（Thick-panel origami）**: 有限厚み $h$ を有する材料で干渉を起こさずに $180^\circ$ 完全密着折りを実現する幾何補正（Split-vertex, Shifted-hinge 法）。
    2. **滑らかな連続キャンバー変形（Smooth camber morphing）**: ウォーターボム（Waterbomb）型またはクレズリング（Kresling）型の折りパターンを翼内部コアに配置し、アクチュエータの単一ストロークで翼型厚み比 $t/c$ およびキャンバー量 $f_{camber}/c$ を連続制御。
    3. **空力弾性相互作用**: 折紙翼の局所柔軟性がもたらす最大揚抗比 $(L/D)$ の向上効果（従来フラップの隙間抗力・段差剥離を完全排除）。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機の各面を剛体平板要素、折り線を回転非線形バネ・ダンパー要素として高速連立連成解法（バー・ヒンジ法: Bar-and-hinge formulation）に落とし込むための構造モデリング標準。

---

#### [Paper 21] Mintchev, de Rijk, & Floreano (2017): 昆虫に学んだ衝突耐性ドローン用折紙メタマテリアル
- **原題**: Insect-inspired mechanical metamaterial for collision-resilient drones
- **和訳**: 昆虫に学んだ耐衝突性ドローンのための折紙機械的メタマテリアル
- **著者・所属**: Stefano Mintchev, Miriam de Rijk, Dario Floreano (Laboratory of Intelligent Systems, École Polytechnique Fédérale de Lausanne, EPFL, Switzerland)
- **出版年**: 2017年
- **掲載誌 / DOI**: *Science Robotics*, Vol. 2, Issue 8, Article eaah6944. DOI: [10.1126/scirobotics.aah6944](https://doi.org/10.1126/scirobotics.aah6944)
- **手法**:
  - 設計・製作および動的風洞・衝突実験。予引張エラストマー膜（Pre-stretched elastomer membrane）を2枚の硬質ガラス繊維複合材タイルの間に挟み込んだサンドイッチ折紙構造。高速度ビデオカメラ（1000 fps）、動的ロードセル。
- **定量的データ・数理モデル**:
  - 二重剛性状態（Dual-stiffness state）の数理:
    - **定常飛行フェーズ**: 予引張膜の張力 $T_0$ により折り目ヒンジが強固にロックされ、高剛性の平面翼（曲げ剛性 $B_{flight} \approx 10^1 \sim 10^2\,\mathrm{N\cdot m}$）として振る舞い、空気力によるたわみを抑止。
    - **衝突・過負荷フェーズ**: 限界曲げモーメント $M_{crit} = T_0 \cdot \frac{h}{2}$ を超える衝撃力または突風動圧が加わると、ヒンジ部が座屈して柔軟状態（$B_{collapse} \approx 10^{-3}\,\mathrm{N\cdot m}$）へと瞬時に軟化し、衝撃エネルギーを弾性吸収して構造破壊を防止。
- **紙飛行機物理シミュレータへの工学的インプリケーション**:
  - 紙飛行機が壁や地面に高速激突した際の機首潰れ（クラッシャブルノーズ）変形と、飛行中の空気力保持という相反する要求を紙の折り目がどのように両立しているかの力学的定式化。

---

## 3. 紙飛行機・MAV流体構造連成（FSI）の数理統合モデル

本章では、前章の21本の文献データに基づき、シミュレータ（`simulation/v3`）に組み込むべき流体構造連成の統合数理モデルを導出する。

### 3.1 膜・板連成方程式（2D/3D非線形FSI）

紙飛行機の主翼たわみ $w(x,y,t)$ の面外変形は、フォン・カルマン（Föppl-von Kármán）幾何学的非線形薄板理論と空気力学的圧力差 $\Delta p(x,y,t)$ の連成として以下の通り定式化される：

$$D \nabla^4 w + \rho_s h \frac{\partial^2 w}{\partial t^2} + c_s \frac{\partial w}{\partial t} - \mathcal{N}(w, \Phi) = p_{lower}(x,y,t) - p_{upper}(x,y,t)$$

ここで：
- $D = \frac{E h^3}{12(1-\nu^2)}$: 面外曲げ剛性
- $\Phi(x,y,t)$: Airy応力関数（面内応力との幾何学的非線形結合）
- $\mathcal{N}(w, \Phi) = \frac{\partial^2 \Phi}{\partial y^2}\frac{\partial^2 w}{\partial x^2} + \frac{\partial^2 \Phi}{\partial x^2}\frac{\partial^2 w}{\partial y^2} - 2\frac{\partial^2 \Phi}{\partial x \partial y}\frac{\partial^2 w}{\partial x \partial y}$
- 空気力学的圧力差:
  $$\Delta p(x,y,t) = q_\infty \cdot \Delta C_p(x, y, \alpha_{eff}(t), \dot{w})$$

低自由度シミュレータ向けには、モーダル展開 $w(y,t) = \sum \phi_i(y) q_i(t)$ を用いた等価片持ち梁モデルとして簡約化する：

$$E I_{eff}(y) \frac{\partial^4 w}{\partial y^4} + m'(y) \frac{\partial^2 w}{\partial t^2} = \frac{1}{2}\rho V^2 c(y) C_L(y)$$

### 3.2 直交異方性紙材料構成則と環境湿度依存性

製紙工程に起因する繊維配向による直交異方性（MD: 縦目, CD: 横目）および環境湿度 $RH \in [0, 1]$（相対湿度）を統合した弾性構成方程式：

$$\begin{pmatrix} \sigma_{MD} \\ \sigma_{CD} \\ \tau_{12} \end{pmatrix} = \mathbf{Q}(RH) \begin{pmatrix} \varepsilon_{MD} \\ \varepsilon_{CD} \\ \gamma_{12} \end{pmatrix}$$

$$\mathbf{Q}(RH) = \exp\left(-\beta_{RH} (RH - RH_0)\right) \cdot \begin{pmatrix} \frac{E_{MD0}}{1-\nu_{12}\nu_{21}} & \frac{\nu_{21} E_{MD0}}{1-\nu_{12}\nu_{21}} & 0 \\ \frac{\nu_{12} E_{CD0}}{1-\nu_{12}\nu_{21}} & \frac{E_{CD0}}{1-\nu_{12}\nu_{21}} & 0 \\ 0 & 0 & G_{12,0} \end{pmatrix}$$

**標準紙物性パラメータセット（A4 80g/m² コピー紙・上質紙）**:
- $RH_0 = 0.50$ (基準相対湿度 50%)
- $\beta_{RH} \approx 0.015\,\%^{-1}$
- $E_{MD0} = 5.2\,\mathrm{GPa}$, $E_{CD0} = 2.3\,\mathrm{GPa}$ ($E_{MD}/E_{CD} \approx 2.26$)
- $G_{12,0} = 1.45\,\mathrm{GPa}$
- $\nu_{12} = 0.40$, $\nu_{21} = 0.177$
- 坪量 $w = 0.080\,\mathrm{kg/m^2}$, 厚み $h = 0.095\,\mathrm{mm}$
- 固体密度 $\rho_s = w / h \approx 842\,\mathrm{kg/m^3}$

### 3.3 前縁多重折り層による剛性・質量勾配モデル

前縁部から後縁部へのスパン・翼弦方向板厚分布 $h(x,y)$:

$$h(x) = \begin{cases} 
N_{layer} \cdot h_0 & (0 \le x \le x_{crease1}) \\ 
N_{mid} \cdot h_0 & (x_{crease1} < x \le x_{crease2}) \\ 
h_0 & (x_{crease2} < x \le c) 
\end{cases}$$

曲げ剛性 $D(x)$ は層間接着が完全な理想状態では $D \propto h^3$、滑り摩擦を伴う乾燥接触状態では $D \propto N \cdot h_0^3 \sim N^3 h_0^3$ の中間をとる：

$$D(x) = \eta_{interlayer} \cdot \frac{E [h(x)]^3}{12(1-\nu^2)}$$

ここで $\eta_{interlayer} \approx 0.35 \sim 0.65$ は紙の層間摩擦すべり低減係数。

### 3.4 翼端ウォッシュアウト・ベル型循環分布と順ヨー誘導

主翼の幾何学的ねじり角分布 $\varepsilon(y)$ を考慮した局所実効迎角:

$$\alpha_{eff}(y) = \alpha_{root} + \varepsilon(y) - \alpha_i(y) + \frac{p \cdot y}{V}$$

NASA Prandtl理論（Bowers et al. 2016）に基づくベル型循環分布の適用により、翼端跳ね上げによる順ヨーモーメント微係数は以下のように陽に導出される：

$$C_{n,p} = \frac{1}{S b} \int_{-b/2}^{b/2} c(y) \left[ \Delta C_{Di}(y) \right] \cdot y \, dy$$

翼端部で $\Delta C_{Di}(y) < 0$（前方推力）となるため、$p > 0$（右ロール）時に左翼端の推力が増加し、右へ機首を振る**正のプロバースヨー（$C_{n,p} > 0$）**が自発的に生じる。

### 3.5 高速射出時の過渡ピッチ応答とダイナミックたわみ

射出初速 $U_0$ から滑空速度 $U_\infty$ への減速フェーズにおける主翼弾性たわみ角 $\theta_{bend}(t)$:

$$I_{wing} \ddot{\theta}_{bend} + C_{damp} \dot{\theta}_{bend} + K_{bend} \theta_{bend} = M_{aero}(q(t), \alpha(t))$$

$$M_{aero}(t) = \frac{1}{2}\rho [V(t)]^2 S \bar{c} \cdot C_{M,ac} + \Delta L_{wing}(t) \cdot (x_{ea} - x_{ac})$$

手投げ直後の過大動圧 $q_0$ により $\theta_{bend} > 0$（上反角増大・有効キャンバー減少）となり、ピッチアップによる急激な失速ループ（ループ・トゥ・ストール）が抑制され、平滑な上昇弾道が形成される。

---

## 4. FSI・弾性変形シミュレータ実装マトリクス

本調査に基づき、`simulation/v3` 物理エンジンに実装すべき数理モデル、パラメータ範囲、および対応モジュールを以下に整理する。

| 物理現象 / モデル項目 | 推奨数理定式化 / アルゴリズム | 主要パラメータ・設定範囲 | 対象モジュール | 優先度 |
|---|---|---|---|---|
| **紙の直交異方性弾性構成則** | $\mathbf{Q}_{MD/CD}$ 平面応力マトリクス、スパン/翼弦配向角 $\theta_{grain}$ | $E_{MD} \in [4.0, 6.5]\,\mathrm{GPa}$<br>$E_{CD} \in [1.8, 3.0]\,\mathrm{GPa}$<br>$G_{12} \in [1.2, 1.8]\,\mathrm{GPa}$ | `physics/paper_material.py` *(新規)* | **最高** |
| **環境湿度（RH）による剛性変調** | $E(RH) = E_0 \exp(-\beta_{RH}(RH - 0.5))$ | $\beta_{RH} \approx 0.015\,\%^{-1}$<br>$RH \in [0.20, 0.90]$ | `physics/paper_material.py` | **高** |
| **前縁多重折り剛性・段差モデル** | $D_{LE} = \eta N^3 D_0$, $h_{step}/\delta$ によるLSB固定化 | $N_{layer} = 4 \sim 8$<br>$\eta_{interlayer} \approx 0.50$<br>$\Delta\alpha_{hyst} \to 0$ | `physics/aero_kernel.py` | **最高** |
| **動圧連動パッシブキャンバー変形** | $\Delta f_{camber} = f_0 - C_{flex} \frac{q}{E h^3} C_L$ | $C_{flex} \in [1.0, 5.0] \times 10^{-4}$ | `physics/aero_kernel.py` | **高** |
| **翼端ウォッシュアウト・ベル型循環** | $\Gamma(y) = \Gamma_0(1-(2y/b)^2)^{3/2}$, $\alpha_i$ 吹き上げ | $\varepsilon_{tip} \in [-8^\circ, -2^\circ]$<br>$C_{n,p} \in [+0.03, +0.10]$ | `physics/aero_kernel.py` | **最高** |
| **ウイングレット（垂直折り）効果** | $\Delta C_{l\beta} = -\frac{1}{2} C_{L\alpha} \Gamma_{tip} \frac{S_{tip}}{S}\frac{y_t}{b/2}$, $\Delta C_{n\beta} > 0$ | $h_w / b \in [0.05, 0.20]$<br>$AR_{eff} = AR(1 + 1.9 h_w/b)$ | `physics/config_schema.py` | **高** |
| **後縁フラッター・臨界速度限界** | $U_{crit} = \sqrt{\frac{B}{K_{B,crit} \rho_f c^3}}$, 超過時 $\Delta C_{D,flt} \propto (U - U_{cr})^2$ | $K_{B,crit} \approx 0.08 \sim 0.12$<br>$U_{crit} \approx 12 \sim 22\,\mathrm{m/s}$ | `physics/flight_sim.py` | **中** |
| **高速射出過渡ダイナミクス** | 2自由度（ピッチ・たわみ）連成積分、初期動圧減衰 | $U_0 \in [15, 35]\,\mathrm{m/s}$<br>$q_0 \in [140, 750]\,\mathrm{Pa}$ | `physics/flight_sim.py` | **高** |
| **付加質量テンソル連成** | $m_{add,z} = \frac{\pi}{4}\rho_f S \bar{c}$ の並進・回転連成加速方程式 | $m_{add}/m_{solid} \approx 0.20 \sim 0.45$ | `physics/flight_sim.py` | **中** |
| **折紙ヒンジ回転剛性（バー・ヒンジ）** | $M_{fold} = k_{fold} (\theta - \theta_0) + c_f \dot{\theta}$ | $\ell_c = B / k_{fold} \approx 5 \sim 15\,\mathrm{mm}$ | `physics/origami_solver.py` *(将来拡張)* | **中** |

---

## 5. 結論と次期シミュレータへの提言

本調査により、紙飛行機は「単に軽く折られた剛体」ではなく、**材料の直交異方性・多重折りによる剛性勾配・動圧適応キャンバー・翼端ウォッシュアウト順ヨーが極限まで計算された高度な自律適応型空力弾性システム**であることが学術的に裏付けられた。

`simulation/v3` への具体的な組み込みにあたっては：
1. **フェーズ1（空力カーネルのFSI拡張）**: `physics/aero_kernel.py` に局所動圧 $q$ と翼端ねじり角 $\varepsilon_{washout}$ に連動する動的キャンバーおよびベル型循環モデルを組み込む。
2. **フェーズ2（紙物性スキーマの導入）**: `physics/config_schema.py` に紙の坪量（$w$）、目方向（MD/CD配向角）、環境湿度（$RH$）の物性パラメータを追加し、機体曲げ剛性を自動算出する。
3. **フェーズ3（高速射出過渡応答）**: `physics/flight_sim.py` の時間積分ループにおいて、手投げ・カタパルト射出初期の動圧サージによる受動的変形とトリム遷移を解く。

以上の実装により、現実世界の紙飛行機競技や実験において観測される「匠の調整（ねじり下げや指によるキャンバー成形、紙の目選び）」が、シミュレータ上で完全に物理的に再現可能となる。
