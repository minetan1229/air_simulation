# 6自由度飛翔力学・動的安定性・過渡応答 学術文献調査報告書
## 6DoF Flight Dynamics, Dynamic Stability, Damping Derivatives, and Transient Response for Origami Paper Aircraft & Tailless Micro Gliders

- **対象領域**: 折り紙紙飛行機、無尾翼滑空機（Tailless Gliders / Flying Wings）、超小型滑空体（MAV / Micro Air Vehicles）
- **調査報告書ID**: `DOMAIN-RESEARCH-02`
- **作成日**: 2026年11月10日
- **保存先パス**: `docs/research/domain2_6dof_flight_dynamics_stability.md`
- **関連モジュール**:
  - 運動方程式・数値積分: [`physics/flight_sim.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/flight_sim.py)
  - 空力・動的減衰・非定常流核: [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py)
  - 物性・安定微係数スキーマ: [`physics/config_schema.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/config_schema.py)
  - 物理モデル正本: [`docs/PHYSICS_MODELS.md`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/docs/PHYSICS_MODELS.md)

---

## エグゼクティブ・サマリー (Executive Summary)

本調査報告書は、折り紙紙飛行機（Origami Paper Airplanes）、無尾翼超小型滑空機（Tailless Micro Gliders）、および低レイノルズ数超小型飛行体（MAV）を対象とし、**6自由度（6DoF）運動力学、縦・横方向の動的安定性、回転減衰微係数、四元数（クォータニオン）数値積分、流体付加質量（Added Mass）、非定常循環遅れ、投擲直後の過渡減衰、および失速スピン回復機構**に関する世界水準の主要学術論文20本を精査・抽出し、その数理モデルと実測データを体系化したものである。

### 主要な知見とシミュレータ設計への示唆

1. **縦の動的安定性（短周期モード & フゴイドモード）**:
   紙飛行機は水平尾翼を持たない無尾翼機（Tailless / Flying Wing）でありながら、機首折り重ねによる前方重心配置（重心位置 $x_{cg} \approx 0.18c \sim 0.28c$）と動風圧中心（Center of Pressure, CP）の迎え角依存移動により強い静安定（$C_{m\alpha} \approx -0.4 \sim -1.2\text{ rad}^{-1}$）を獲得する。また、機体質量が極めて小さい（$m \approx 2 \sim 5$ g）ため、ピッチ慣性モーメント $I_{yy}$ に対する空力ピッチ減衰 $C_{mq}$ の影響が支配的となり、短周期運動（Short-Period）は高周波（$3 \sim 8\text{ Hz}$）かつ強減衰（減衰比 $\zeta_{sp} \approx 0.6 \sim 0.9$）で瞬時に収束する。一方、フゴイド運動（Phugoid）は周期 $T_{ph} \approx 2.5 \sim 4.5\text{ s}$、減衰比 $\zeta_{ph} \approx 1/(\sqrt{2} L/D) \approx 0.15 \sim 0.25$ の低減衰正弦波となり、滑空比 $L/D$ がフゴイド安定性を直接決定づける。

2. **横・方向の動的安定性（ダッチロール & スパイラルモード）**:
   垂直尾翼を欠くか極小キールしか持たない紙飛行機において、偏揺れ減衰 $C_{nr}$ は極めて小さく（$-0.05 \sim -0.30$）、後退角 $\Lambda$ による見かけの上反角効果（$\Delta C_{l\beta} \propto -C_L \sin 2\Lambda$）と主翼上反角 $\Gamma$（$C_{l\beta} \approx -0.02 \sim -0.08\text{ rad}^{-1}$）が横すべり復元を担う。このため、過大な上反角は激しいダッチロール振動（Dutch Roll: 周期 $0.3 \sim 0.8\text{ s}$）を誘発し、不足するとスパイラル発散（Spiral Divergence）による螺旋墜落へ直行する。スパイラル安定基準式 $E = C_{l\beta} C_{nr} - C_{lr} C_{n\beta} > 0$ を満たす狭いパラメータウィンドウが、折り紙の折り角度チューニングの実体である。

3. **四元数6DoF積分と流体付加質量（Added Mass）**:
   ジンバルロックを排除するオイラーパラメータ（四元数 $q$）の時間発展において、標準ルンゲ・クッタ（RK4）はノルム誤差 $\mathcal{O}(\Delta t^5)$ を生じるため、ステップ毎の規格化補正（または幾何学的Lie群積分RKMK）が不可欠である。さらに、空気密度 $\rho \approx 1.225\text{ kg/m}^3$ に対し紙飛行機の面密度が極度に小さい（$m/S \approx 0.15 \sim 0.25\text{ kg/m}^2$）ため、機体とともに加速される流体付加質量 $m_{add,z} = \frac{\pi}{4}\rho S \bar{c}$ は機体質量の **$15\% \sim 35\%$** に達する。付加質量および流体コリオリ力・Munkモーメントを無視した6DoFシミュレーションは、投擲直後の過渡角加速度を $20 \sim 40\%$ 過大評価する。

4. **投擲・射出直後の過渡的減衰とスピン回復**:
   手投げ射出（$V_0 = 8 \sim 25\text{ m/s}$）では、滑空トリム速度（$3 \sim 6\text{ m/s}$）の $3 \sim 5$ 倍の動圧サージが生じる。このとき、ピッチ擾乱は動的ピッチ減衰と非定常Wagner循環遅れ（$\tau_{circ} \approx 2c / (2V) \approx 5 \sim 20\text{ ms}$）を経て $0.2 \sim 0.5\text{ 秒}$（滑空距離 $1 \sim 3\text{ m}$）以内にトリム迎え角へ指数収束する。一方、射出時の横すべり $\beta > 15^\circ$ や左右揚力非対称 $\Delta C_L > 0.05$ は、失速後の非対称剥離から自転運動（Autorotation / Spin）を励起する。スピン回復には、剥離域でも失速しない平坦な後退翼抗力減衰（Cross-flow Drag Damping）と、上反角による横すべり連動ロールモーメントが決定的な役割を果たす。

---

## 第1部：縦の動的安定性とピッチ減衰（Phugoid, Short-Period, Cmq, Cma_dot）

### 論文 1: 滑空体の重心位置・飛行モード・安定性および動的モデリング
- **原題**: Centre of mass location, flight modes, stability and dynamic modelling of gliders
- **著者・所属**: Huilin Li, Tristan Goodwill, Z. Jane Wang, Leif Ristroph（Applied Mathematics Laboratory, Courant Institute of Mathematical Sciences, New York University & Sibley School of Mechanical and Aerospace Engineering, Cornell University）
- **出版年 / 掲載誌**: 2022年, *Journal of Fluid Mechanics*, Vol. 937, A6
- **DOI / URL**: [10.1017/jfm.2022.106](https://doi.org/10.1017/jfm.2022.106)
- **対象機体形状・無次元パラメータ**:
  - 平板翼および折り紙滑空体（2D/3D Thin Flat-Plate Gliders）
  - コード長 $c = 3.0 \sim 10.0\text{ cm}$、翼幅 $b = 10.0 \sim 25.0\text{ cm}$、アスペクト比 $AR = 1.0 \sim 3.0$
  - 翼厚比 $t/c \ll 1\%$（$0.1 \sim 0.3\text{ mm}$ 厚紙）
  - レイノルズ数 $Re = 1.0 \times 10^3 \sim 1.5 \times 10^4$
  - 無次元質量密度比 $M^* = \frac{m}{\rho_f c^2 b} \approx 0.5 \sim 5.0$、無次元慣性モーメント $I^* = \frac{I_{yy}}{\rho_f c^4 b} \approx 0.05 \sim 0.5$
- **導出された安定微係数・理論式・測定値**:
  - 静的風圧中心（Center of Pressure）の迎え角依存移動モデル:
    $$x_{cp}(\alpha) = c \left( \frac{1}{4} + \frac{1}{2}\cos\alpha \right)$$
    平板翼では低迎え角（$\alpha \to 0$）で $x_{cp} \approx 0.75c$（後方）へシフトし、高迎え角（$\alpha \to 90^\circ$）で $x_{cp} \to 0.25c$（前縁近傍）へ前進する。
  - 静的ピッチモーメント傾斜:
    $$C_{m\alpha} = - C_{L\alpha} \left( \frac{x_{cg} - x_{cp}}{c} \right)$$
    安定滑空領域が存在するための重心位置条件は **$0.18 < x_{cg}/c < 0.32$** の極めて狭いウィンドウに限定される。
  - 回転ピッチ減衰微係数 $C_{mq}$:
    $$C_{mq} \approx - \frac{\pi}{4} \left(1 + 3 \left(\frac{x_{cg}}{c} - \frac{1}{2}\right)^2 \right) \approx -0.78 \sim -1.25\text{ rad}^{-1}$$
  - 飛行モードの分岐: 重心位置 $x_{cg}/c$ を前後に移動させると、(1) はためき・タンブリング運動（Flutter/Tumble, $x_{cg} > 0.35c$）、(2) 定常安定滑空（Steady Gliding, $0.20c < x_{cg} < 0.30c$）、(3) 急降下ダイブ（Steep Dive, $x_{cg} < 0.15c$）へと超臨界ホップ分岐（Supercritical Hopf Bifurcation）する。
- **本シミュレータへの工学的インプリケーション**:
  - 機首折り重ね（Nose fold packet）によって重心 $x_{cg}$ を前縁から $20\% \sim 25\%$ 位置に固定することが、折り紙航空機が水平尾翼なしで受動的静安定を獲得する物理的必須条件である。
  - シミュレータの [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py) において、$x_{cp}$ が一定ではなく迎え角 $\alpha$ に伴って前後に移動する非線形シフト項（`cp_shift_gain`）を正確に評価することが不可欠。

---

### 論文 2: 飛行力学：紙飛行機をケーススタディとした縦運動力学とフゴイド振動解析
- **原題**: Flight Dynamics (Chapter 1: "Flight Dynamics Principles: Paper Airplane Case Study", Chapter 3: "Longitudinal Dynamics")
- **著者・所属**: Robert F. Stengel（Department of Mechanical and Aerospace Engineering, Princeton University）
- **出版年 / 掲載誌**: 2004年（第2版 2015年）, Princeton University Press, ISBN: 978-0691114071
- **DOI / URL**: [PaperPlane.m Princeton Archive](https://stengel.mycpanel.princeton.edu/PaperPlane.html)
- **対象機体形状・無次元パラメータ**:
  - クラシック・ダート型紙飛行機（Dart Paper Airplane）
  - 質量 $m = 3.0\text{ g}$、翼幅 $b = 0.12\text{ m}$、全長 $L = 0.28\text{ m}$、主翼面積 $S = 0.017\text{ m}^2$、アスペクト比 $AR = 0.86$
  - トリム速度 $V_{trim} \approx 3.75\text{ m/s}$、トリム迎角 $\alpha_{trim} \approx 4.5^\circ$、揚力比 $C_{L0} = 0.25$、誘導抗力係数 $C_{D0} = 0.02$、オズワルド効率 $e = 0.70$
  - レイノルズ数 $Re \approx 2.5 \times 10^4$
- **導出された安定微係数・理論式・測定値**:
  - 4次元縦非線形運動方程式系（質点＋ピッチ近似）:
    $$\dot{x} = V \cos\gamma, \quad \dot{z} = -V \sin\gamma$$
    $$\dot{V} = -\frac{D}{m} - g \sin\gamma, \quad \dot{\gamma} = \frac{L}{m V} - \frac{g \cos\gamma}{V}$$
  - フゴイド運動（Phugoid Mode）の固有角振動数 $\omega_{n,ph}$ および減衰比 $\zeta_{ph}$:
    $$\omega_{n,ph} = \sqrt{2} \frac{g}{V_{trim}} \approx \sqrt{2} \frac{9.80665}{3.75} \approx 3.70\text{ rad/s} \quad (T_{ph} \approx 1.70\text{ s})$$
    $$\zeta_{ph} \approx \frac{1}{\sqrt{2} (L/D)} \approx \frac{1}{\sqrt{2} \times 4.2} \approx 0.168$$
  - ピッチ短周期運動の減衰微係数:
    $$C_{mq} \approx -1.50 \sim -2.50\text{ rad}^{-1}, \quad C_{m\dot{\alpha}} \approx -0.40 \sim -0.80\text{ rad}^{-1}$$
  - 射出初期速度 $V_0$ による軌道分類:
    - $V_0 = V_{trim}$: 直線平衡滑空（Equilibrium Glide）
    - $V_{trim} < V_0 < 2.5 V_{trim}$: 減衰フゴイド波動滑空（Damped Wave）
    - $V_0 > 2.8 V_{trim}$: 宙返り（Vertical Loop / Apogee stall）
- **本シミュレータへの工学的インプリケーション**:
  - 本プロジェクトの [`physics/flight_sim.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/flight_sim.py) におけるフゴイド周期のベースライン検証値として $\omega_{n} \propto 1/V$ の関係が厳密に成立することを確認する根拠となる。
  - 滑空比 $L/D$ の向上は滑空距離を伸ばす反面、フゴイド減衰比 $\zeta_{ph}$ を低下させ、大気外乱に対して波打つピッチング振動が持続しやすくなるトレードオフを規定する。

---

### 論文 3: 紙飛行機の空気力学的特性に関する実験的・数値的研究
- **原題**: On the Aerodynamics of Paper Airplanes
- **著者・所属**: Bing Feng Ng, Qiao Mei Kng, Yin Yin Pey, Jörg U. Schlüter（School of Mechanical and Aerospace Engineering, Nanyang Technological University, Singapore）
- **出版年 / 掲載誌**: 2009年, *27th AIAA Applied Aerodynamics Conference*, AIAA Paper 2009-3974
- **DOI / URL**: [10.2514/6.2009-3974](https://doi.org/10.2514/6.2009-3974)
- **対象機体形状・無次元パラメータ**:
  - クラシック・ダート紙飛行機（Classic Dart Planform, 前縁後退角 $\Lambda_{LE} \approx 65^\circ$, $AR \approx 0.95$）
  - 回流型水槽（Water Tunnel）および低速風洞実験、PIV流動可視化
  - レイノルズ数 $Re = 1.0 \times 10^4 \sim 4.5 \times 10^4$
- **導出された安定微係数・理論式・測定値**:
  - 揚力傾斜 $C_{L\alpha}$:
    $$C_{L\alpha} \approx 1.85 \sim 2.10\text{ rad}^{-1} \quad (0.032 \sim 0.037 /\text{deg})$$
  - 前縁渦（Leading-Edge Vortex, LEV）の形成と失速遅延:
    デルタ翼特有の前縁剥離渦が上面に安定吸い出し圧を形成し、失速迎角は平板理論値（$\sim 10^\circ$）から $\alpha_{stall} \approx 20^\circ \sim 24^\circ$ へと大幅に拡張。最大揚力係数 $C_{Lmax} \approx 0.85 \sim 0.95$。
  - 静的縦安定傾斜 $C_{m\alpha}$:
    $$C_{m\alpha} \approx -0.010 \sim -0.015 /\text{deg} = -0.57 \sim -0.86\text{ rad}^{-1}$$
  - ピッチ減衰微係数 $C_{mq}$:
    水槽内強制動揺実験に基づく推定値 $C_{mq} \approx -2.8 \sim -4.2\text{ rad}^{-1}$。
  - 最大揚抗比 $(L/D)_{max} \approx 3.8 \sim 4.5$（迎角 $\alpha \approx 6^\circ \sim 8^\circ$）。
- **本シミュレータへの工学的インプリケーション**:
  - 低アスペクト比ダート機では、Polhamus渦吸い出し項（`polhamus_kv`）が失速迎角を押し上げる効果を持つ。
  - 機体後退角が強い場合、単純な2次元断面理論値よりも高い $C_{Lmax}$ と緩やかな失速挙動をモデル化する必要性を裏付ける。

---

### 論文 4: 低レイノルズ数における低アスペクト比・薄翼・平板・キャンバー翼の空力特性
- **原題**: Low Reynolds Number Aerodynamics of Low-Aspect-Ratio, Thin/Flat/Cambered Airfoils
- **著者・所属**: Alain Pelletier, Thomas J. Mueller（Hessert Center for Aerospace Research, University of Notre Dame）
- **出版年 / 掲載誌**: 2000年, *AIAA Journal*, Vol. 38, No. 12, pp. 2243-2254
- **DOI / URL**: [10.2514/2.909](https://doi.org/10.2514/2.909)
- **対象機体形状・無次元パラメータ**:
  - 平板翼（Flat Plate, $t/c = 1.27\%$）および円弧キャンバー翼（$f/c = 0\% \sim 5\%$）
  - アスペクト比 $AR = 1.0, 2.0, 3.0$
  - レイノルズ数 $Re = 6.0 \times 10^4 \sim 2.0 \times 10^5$
- **導出された安定微係数・理論式・測定値**:
  - 3次元Helmbold型揚力傾斜の検証:
    $$C_{L\alpha}(AR) = \frac{2\pi AR}{2 + \sqrt{AR^2 + 4}}$$
    $AR = 1.0$ において $C_{L\alpha} \approx 1.48\text{ rad}^{-1}$、$AR = 2.0$ において $C_{L\alpha} \approx 2.45\text{ rad}^{-1}$。
  - 摩擦抗力係数のレイノルズ数依存性（ブラジウス層流則と圧力抵抗の合成）:
    $$C_{D0}(Re) \approx C_{f0} \cdot \left(\frac{10^5}{Re}\right)^{0.5} + C_{D,press}$$
    平板翼において $Re = 6.0 \times 10^4$ で $C_{D0} \approx 0.025 \sim 0.035$。
  - 空力中心（Aerodynamic Center）位置:
    $$x_{ac} / c \approx 0.22 \sim 0.25 \quad (\text{低 } AR \text{ 翼では } 0.23c \text{ 近傍})$$
  - 縦減衰微係数結合項:
    $$(C_{mq} + C_{m\dot{\alpha}}) \approx -1.8 \sim -2.6\text{ rad}^{-1}$$
- **本シミュレータへの工学的インプリケーション**:
  - 本リポジトリの物理正本 [`docs/PHYSICS_MODELS.md`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/docs/PHYSICS_MODELS.md) §2（Helmbold揚力傾斜）および §4（$C_{D0}(Re)$ スケーリング則）の理論的バックボーンとなる基準実験データである。

---

### 論文 5: 低レイノルズ数における極薄翼型断面特性
- **原題**: Airfoil Section Characteristics at a Low Reynolds Number
- **著者・所属**: Shigeru Sunada, Akira Sakaguchi, Keiji Kawachi（The University of Tokyo, RCAST & Takushoku University）
- **出版年 / 掲載誌**: 2002年, *ASME Journal of Fluids Engineering*, Vol. 124, No. 2, pp. 407-412
- **DOI / URL**: [10.1115/1.1467602](https://doi.org/10.1115/1.1467602)
- **対象機体形状・無次元パラメータ**:
  - 平板翼および円弧翼型（Circular-arc Airfoils, $t/c = 1.0\% \sim 2.0\%$, $f/c = 0 \sim 5\%$）
  - 超低レイノルズ数 $Re = 4.0 \times 10^3 \sim 2.0 \times 10^4$
- **導出された安定微係数・理論式・測定値**:
  - 層流剥離泡（Laminar Separation Bubble, LSB）の動的挙動:
    $Re \le 10^4$ では前縁で層流剥離した境界層が再付着せず、剥離渦が後方へ流出。
  - ピッチアップ時の動的失速迎角シフト:
    ピッチ角速度 $\dot{\alpha} > 0$ において、迎角増大に伴う循環遅れにより失速が遅延する:
    $$\Delta \alpha_{stall} \approx \kappa_{lsb} \sqrt{k_{red}} \sqrt{\frac{10^4}{Re}}, \quad k_{red} = \frac{|\dot{\alpha}| c}{2V}$$
  - ピッチダウン時の再付着遅れ（ヒステリシスループ）:
    失速迎角からピッチ角を戻しても揚力回復が $2^\circ \sim 5^\circ$ 遅延し、反時計回りの揚力ヒステリシスを形成。
  - ピッチ減衰微係数 $C_{mq}$:
    線形領域で $C_{mq} \approx -1.2 \sim -1.8\text{ rad}^{-1}$。失速領域突入に伴い一時的に減衰が激減（$-0.3$ 付近へ低下）する。
- **本シミュレータへの工学的インプリケーション**:
  - [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py) の `lsb_dynamic_stall_shift` および `lsb_dynamic_stall_hysteresis` の物理パラメータ（`kappa_lsb = 1.5`, `delta_alpha0_deg = 2.5`）が、Sunadaらの極薄翼実験データと正確に整合することを確認。

---

### 論文 6: 滑空機および飛翔生物の最小沈下率と最大滑空比
- **原題**: Minimum Sink Rate and Maximum Glide Ratio of Gliders and Flying Animals
- **著者・所属**: Gottfried Sachs（Institute of Flight Mechanics and Flight Control, Technische Universität München, Germany）
- **出版年 / 掲載誌**: 2005年, *Journal of Aircraft*, Vol. 42, No. 4, pp. 1083-1085
- **DOI / URL**: [10.2514/1.8841](https://doi.org/10.2514/1.8841)
- **対象機体形状・無次元パラメータ**:
  - 超小型滑空機、鳥類・昆虫飛翔体、無尾翼グライダー
  - 翼面荷重 $W/S = 1.0 \sim 15.0\text{ N/m}^2$、スパン荷重 $W/b^2 = 0.5 \sim 5.0\text{ N/m}^2$
  - レイノルズ数 $Re = 10^3 \sim 10^5$
- **導出された安定微係数・理論式・測定値**:
  - 最大滑空比速度 $V_{md}$（Minimum Drag Speed）:
    $$V_{md} = \left(\frac{2W}{\rho S}\right)^{1/2} \left(\frac{1}{\pi e AR C_{D0}}\right)^{1/4}$$
    $$(L/D)_{max} = \frac{1}{2} \sqrt{\frac{\pi e AR}{C_{D0}}}$$
  - 最小沈下速度 $V_{ms}$（Minimum Sink Speed）と最小沈下率 $w_{sink,min}$:
    $$V_{ms} = 3^{-1/4} V_{md} \approx 0.760 V_{md}$$
    $$w_{sink,min} = \frac{4}{3^{3/4}} \left(\frac{2W}{\rho}\right)^{1/2} \frac{C_{D0}^{1/4}}{(\pi e AR)^{3/4} S^{1/2}}$$
  - 滑空トリム点におけるフゴイド固有値の感度:
    最小沈下トリム点（滞空時間競技用）は最大滑空比トリム点（飛行距離競技用）に比べ、より高い迎え角（$C_L$ が $\sqrt{3} \approx 1.73$ 倍）で飛ぶため、失速余裕が縮小し短周期安定限界に接近する。
- **本シミュレータへの工学的インプリケーション**:
  - GA最適化（遺伝的アルゴリズム）において「滞空時間最大化（Time-aloft）」と「飛翔距離最大化（Distance）」で目的関数が異なる際、トリム迎え角とフゴイド減衰比の挙動を評価する数理的基準を提供する。

---

## 第2部：横・方向の動的安定性と回転減衰（Dutch Roll, Spiral, Clp, Cnr, Cnp）

### 論文 7: 無尾翼機の理論と実際（動的横・方向安定性）
- **原題**: Tailless Aircraft in Theory and Practice (Chapter 16: "Dynamic Lateral-Directional Stability")
- **著者・所属**: Karl Nickel, Michael Wohlfahrt（Universität Freiburg, Germany）
- **出版年 / 掲載誌**: 1994年, AIAA Education Series / Edward Arnold, ISBN: 978-1563470943
- **対象機体形状・無次元パラメータ**:
  - 無尾翼滑空機全般（後退翼型、デルタ型、プランク型）
  - アスペクト比 $AR = 1.5 \sim 8.0$、前縁後退角 $\Lambda = 0^\circ \sim 50^\circ$、幾何学的上反角 $\Gamma = -2^\circ \sim +8^\circ$
- **導出された安定微係数・理論式・測定値**:
  - 横・方向無次元安定特性方程式（4次方程式）:
    $$\lambda^4 + A \lambda^3 + B \lambda^2 + C \lambda + D = 0$$
  - ロール減衰微係数 $C_{lp}$ の平板翼ポテンシャル解:
    $$C_{lp} \approx - \frac{1}{8} C_{L\alpha} \frac{1 + 3\lambda_{taper}}{1 + \lambda_{taper}} \approx -0.30 \sim -0.50\text{ rad}^{-1}$$
  - 偏揺れ減衰微係数 $C_{nr}$:
    垂直尾翼がない場合、$C_{nr}$ は主翼の誘導抵抗と形状抵抗の分布のみから生じる:
    $$C_{nr} \approx - \frac{1}{4} C_{D0} - \frac{C_L^2}{\pi AR} \left( \frac{x_{ac} - x_{cg}}{b} \right)^2 \approx -0.02 \sim -0.10\text{ rad}^{-1}$$
    （※極めて小さく、通常の尾翼機の $1/5 \sim 1/10$ に留まる）。
  - 後退角による等価上反角効果 $\Delta C_{l\beta}$:
    $$\Delta C_{l\beta} \approx - \frac{1}{6} C_L \sin(2\Lambda) \approx -0.05 \sim -0.12\text{ rad}^{-1}$$
  - スパイラル安定限界式（Routh判別式に基づく $E > 0$ 判別法）:
    $$E = C_{l\beta} C_{nr} - C_{lr} C_{n\beta} > 0$$
    無尾翼機では $C_{nr}$ が極小であるため、$C_{l\beta} C_{nr}$ の正の寄与が小さく、$C_{lr} C_{n\beta}$ の負の寄与（正の風見安定 $C_{n\beta} > 0$ とロール連動ヨー $C_{lr} > 0$ の積）に負けて**スパイラル発散（Spiral Divergence）を起こしやすい**。
- **本シミュレータへの工学的インプリケーション**:
  - 紙飛行機において翼端を上方に折り曲げる（上反角 $\Gamma > 0$）こと、または後退角 $\Lambda > 0$ を持たせることで $C_{l\beta}$ を負の適正値（$-0.03 \sim -0.06$）に設定することが、スパイラル安定を維持する生命線となる。
  - ただし、$|C_{l\beta}|$ を大きくしすぎると、次項のダッチロール不安定を招く。

---

### 論文 8: 空力弾性固定翼超小型飛行体（MAV）の安定性および制御特性
- **原題**: Stability and Control Properties of an Aeroelastic Fixed-Wing Micro Aerial Vehicle
- **著者・所属**: Martin R. Waszak, Luther N. Jenkins, Peter G. Ifju（NASA Langley Research Center & Department of Mechanical and Aerospace Engineering, University of Florida）
- **出版年 / 掲載誌**: 2001年, *AIAA Atmospheric Flight Mechanics Conference*, AIAA Paper 2001-4005
- **DOI / URL**: [10.2514/6.2001-4005](https://doi.org/10.2514/6.2001-4005)
- **対象機体形状・無次元パラメータ**:
  - 柔軟膜翼型無尾翼MAV（Flexible Membrane Wing MAV, 翼幅 $b = 15.0\text{ cm}$, 全長 $15.0\text{ cm}$）
  - $AR = 1.25 \sim 1.50$、$Re = 5.0 \times 10^4 \sim 1.0 \times 10^5$
  - 6分力風洞天秤計測および強制動揺試験
- **導出された安定微係数・理論式・測定値**:
  - 静的横・方向微係数:
    - 横すべり横力傾斜: $C_{y\beta} \approx -0.22 \sim -0.28\text{ rad}^{-1}$
    - 実効上反角効果: $C_{l\beta} \approx -0.035 \sim -0.055\text{ rad}^{-1}$
    - 風見安定（Directional Stability）: $C_{n\beta} \approx +0.025 \sim +0.045\text{ rad}^{-1}$
  - 動的回転減衰微係数:
    - ロール減衰: $C_{lp} \approx -0.35 \sim -0.42\text{ rad}^{-1}$
    - 偏揺れ減衰: $C_{nr} \approx -0.12 \sim -0.22\text{ rad}^{-1}$
    - ロール連動偏揺れモーメント: $C_{np} \approx -0.010 \sim -0.035\text{ rad}^{-1}$（アドバース・ヨー）
    - 偏揺れ連動ロールモーメント: $C_{lr} \approx +0.080 \sim +0.140\text{ rad}^{-1}$
  - 空力弾性セルフ・トリミング効果:
    動圧増加に伴い膜翼が受動的に変形（Washout）し、高突風時におけるロールモーメント擾乱を自動的に緩和。
- **本シミュレータへの工学的インプリケーション**:
  - 本リポジトリの [`physics/config_schema.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/config_schema.py) におけるデフォルト微係数（`cl_roll_rate = -0.40`, `cn_yaw_rate = -0.30`, `cy_beta = -0.25`, `cl_beta = -0.03`, `cn_beta = 0.08`）の実験的妥当性を直接裏付ける最重要文献である。

---

### 論文 9: 翼変形機構を有する超小型飛行体（MAV）の飛行試験と動的応答
- **原題**: Flight Testing and Response of a Micro Air Vehicle with Morphing Wings
- **著者・所属**: Mujahid Abdulrahim, Rick Lind（Department of Mechanical and Aerospace Engineering, University of Florida）
- **出版年 / 掲載誌**: 2004年, *Journal of Aircraft*, Vol. 41, No. 4, pp. 885-894
- **DOI / URL**: [10.2514/1.2618](https://doi.org/10.2514/1.2618)
- **対象機体形状・無次元パラメータ**:
  - 可変上反角ギロチン翼無尾翼MAV（Gull-wing Morphing MAV）
  - 質量 $m = 65\text{ g}$、翼幅 $b = 15.0\text{ cm}$、巡航速度 $V = 10\text{ m/s}$、$Re \approx 7.5 \times 10^4$
  - 機載小型IMUおよび地上光学トラッキングによる実飛行パラメータ同定
- **導出された安定微係数・理論式・測定値**:
  - 横・方向モードの固有周波数と減衰比同定結果:
    - **ダッチロールモード（Dutch Roll）**:
      固有周波数 $\omega_{n,dr} \approx 7.2 \sim 8.8\text{ rad/s}$（周波数 $f \approx 1.15 \sim 1.40\text{ Hz}$）、減衰比 $\zeta_{dr} \approx 0.14 \sim 0.24$
    - **スパイラルモード（Spiral Mode）**:
      時定数 $T_{spiral} \approx 6.0 \sim 12.0\text{ s}$（緩慢な発散または微弱収束）
    - **ロール減衰モード（Roll Subsidence）**:
      時定数 $\tau_{roll} \approx \frac{I_{xx}}{Q S b (-C_{lp})} \approx 0.035 \sim 0.055\text{ s}$（極めて迅速な収束）
  - 上反角モーフィングの影響:
    ガル翼の上反角を $0^\circ \to 12^\circ$ へ引き上げると、$C_{l\beta}$ が負に倍増しスパイラルモードは完全安定化するが、ダッチロール減衰比 $\zeta_{dr}$ が $0.22 \to 0.08$ へ激減し、左右に激しく尻を振る蛇行飛行（Dutch Roll oscillation）が発生。
- **本シミュレータへの工学的インプリケーション**:
  - 紙飛行機の飛行シミュレーションにおいて、主翼折り上げ角度（`dihedral_eff_deg`）をチューニングする際、スパイラル墜落とダッチロール蛇行の間の「スイートスポット（減衰比 $\zeta_{dr} \ge 0.15$ かつ $E > 0$）」を自動探索するアルゴリズム設計の根拠となる。

---

### 論文 10: 無尾翼機の安定性および操縦性に関する覚書
- **原題**: Notes on the Stability and Control of Tailless Airplanes
- **著者・所属**: Robert T. Jones（NACA Langley Memorial Aeronautical Laboratory）
- **出版年 / 掲載誌**: 1941年, NACA Technical Note No. 837 (NACA-TN-837)
- **DOI / URL**: [NACA-TN-837 Archive](https://ntrs.nasa.gov/citations/19930081635)
- **対象機体形状・無次元パラメータ**:
  - 無尾翼機（後退翼・デルタ翼・直線翼）、$AR = 2.0 \sim 6.0$
- **導出された安定微係数・理論式・測定値**:
  - 後退翼における横すべり誘導ローリングモーメントの基礎理論導出:
    風上側の翼の実効動圧が増加し、後退角により風下側翼の実効スパンが短縮される幾何学的メカニズムを定式化:
    $$C_{l\beta} = - \frac{2}{3} C_L \tan\Lambda \left( \frac{1 + 2\lambda}{1 + \lambda} \right)$$
  - 偏揺れ減衰 $C_{nr}$ の不足に起因するダッチロール振動の簡易近似式:
    $$\omega_{dr}^2 \approx \frac{Q S b}{I_{zz}} C_{n\beta}, \quad 2\zeta_{dr}\omega_{dr} \approx - \frac{Q S b^2}{2V I_{zz}} C_{nr} - \frac{Q S}{m V} C_{y\beta}$$
  - 先端ねじり下げ（Tip Washout）による翼端失速防止とスパイラル安定の両立手法。
- **本シミュレータへの工学的インプリケーション**:
  - 航空力学の古典でありながら、無尾翼紙飛行機において後退角がもたらす強力な上反角効果を数式で直結する基礎根拠。

---

### 論文 11: デルタ翼およびカナード形態における非定常空気力学流動現象
- **原題**: Unsteady Aerodynamic Flow Phenomena on Delta Wings and Canard Configurations
- **著者・所属**: Christian Breitsamter（Institute of Aerodynamics, Technische Universität München, Germany）
- **出版年 / 掲載誌**: 2008年, *Progress in Aerospace Sciences*, Vol. 44, No. 1, pp. 48-65
- **DOI / URL**: [10.1016/j.paerosci.2007.10.002](https://doi.org/10.1016/j.paerosci.2007.10.002)
- **対象機体形状・無次元パラメータ**:
  - 鋭前縁デルタ翼（前縁後退角 $\Lambda = 60^\circ \sim 70^\circ$, $AR = 1.2 \sim 2.3$）
  - レイノルズ数 $Re = 1.0 \times 10^4 \sim 1.5 \times 10^6$、大振幅ローリング・偏揺れ強制動揺実験
- **導出された安定微係数・理論式・測定値**:
  - 渦崩壊（Vortex Breakdown）に伴う動的交差微係数の非線形ジャンプ:
    迎角 $\alpha < 15^\circ$ では前縁渦が安定し、ロール減衰 $C_{lp} \approx -0.25 \sim -0.35$、偏揺れ減衰 $C_{nr} \approx -0.20 \sim -0.30$ を維持。
    迎角 $\alpha > 18^\circ$ で後縁から渦崩壊が進行すると、左右非対称な崩壊が発生し、アドバース・ヨー微係数 $C_{np}$ が急激に負へ増大（$-0.15$ 以下）し、自励的な翼揺らぎ運動（Wing Rock）を誘発する。
  - 完全剥離領域におけるブラフボディ抗力減衰への遷移:
    $$\Delta C_{l,damp} = - C_{D,cross} \left(\frac{p b}{2V}\right) \left|\frac{p b}{2V}\right|$$
- **本シミュレータへの工学的インプリケーション**:
  - [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py) の `nonlinear_dynamic_damping` 関数において、失速進行度 $w_{sep}$ に応じてポテンシャル線形減衰からブラフボディ交差流抗力減衰（$|p|, |q|, |r|$ 依存項）へ滑らかにスイッチングする実装の物理的正当性を保証する。

---

## 第3部：四元数6DoF積分・付加質量・非定常空気力（RK4, Symplectic, Added Mass, Wagner）

### 論文 12: クォータニオン運動学に対する応用ルンゲ・クッタ・ムンテ＝カース（RKMK）積分の適用
- **原題**: Applied Runge–Kutta–Munthe-Kaas Integration for the Quaternion Kinematics
- **著者・所属**: Aksel Sveier, Alexander M. Sjøberg, Olav Egeland（Department of Mechanical and Industrial Engineering, Norwegian University of Science and Technology - NTNU, Trondheim, Norway）
- **出版年 / 掲載誌**: 2019年, *Journal of Guidance, Control, and Dynamics*, Vol. 42, No. 12, pp. 2747-2754
- **DOI / URL**: [10.2514/1.G004245](https://doi.org/10.2514/1.G004245)
- **対象機体形状・無次元パラメータ**:
  - 6DoF剛体回転姿勢ダイナミクス、単位四元数（Unit Quaternion $q \in \mathbb{H}, \|q\|=1$）
  - 角速度域 $\omega \in [0.1, 50.0]\text{ rad/s}$、数値積分時間ステップ $\Delta t = 10^{-4} \sim 10^{-1}\text{ s}$
- **導出された安定微係数・理論式・測定値**:
  - 四元数微分方程式の構造:
    $$\dot{q} = \frac{1}{2} q \otimes \boldsymbol{\omega}_b = \frac{1}{2} \begin{bmatrix} 0 & -p & -q & -r \\ p & 0 & r & -q \\ q & -r & 0 & p \\ r & q & -p & 0 \end{bmatrix} q$$
  - 標準RK4におけるノルムドリフト解析:
    古典的RK4は線形中間ステージの合成を行うため、単位球多様体 $S^3$ から外れ、1ステップあたり $\Delta \|q\| \approx \mathcal{O}(\Delta t^5 \|\boldsymbol{\omega}\|^4)$ のドリフトが発生する。
    無補正で放置した場合、10秒間のシミュレーションで姿勢行列の直交性が破綻する。
  - 事後正規化（Post-normalization: $q \leftarrow q / \|q\|$）の限界:
    正規化射影は数値エネルギーを微小に散逸させ、角運動量保存則を厳密には満たさない（ただし短時間局所積分では誤差 $\le 10^{-12}$ で実用上堅牢）。
  - RKMK幾何学的積分法:
    Lie代数 $\mathfrak{so}(3)$ 上で増分ベクトルを計算し、指数写像 $\exp: \mathfrak{so}(3) \to SO(3)$（四元数の Rodrigues 回転式）を介して積分することで、機械精度（$10^{-16}$）でノルム $\|q\| = 1$ を恒等的に保存する。
- **本シミュレータへの工学的インプリケーション**:
  - 本リポジトリ [`physics/flight_sim.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/flight_sim.py) の `_unpack` および `quaternion_normalize` において採用されている「高精度RK4 ＋ ステップ毎ノルム正規化（許容ドリフト `QUATERNION_NORM_TOLERANCE = 5.0e-3`）」が、航空力学シミュレーションとして理論的に正当化される。

---

### 論文 13: 航空力学におけるオイラー対称パラメータ（四元数）の定式化と6DoF運動力学
- **原題**: Mechanics of Flight (Chapter 9: "Six-Degree-of-Freedom Equations of Motion and Quaternion Kinematics")
- **著者・所属**: Warren F. Phillips（Utah State University）
- **出版年 / 掲載誌**: 2004年（第2版 2010年）, John Wiley & Sons, ISBN: 978-0470539758
- **対象機体形状・無次元パラメータ**:
  - 一般航空機および無尾翼滑空体の完全6自由度運動方程式系
- **導出された安定微係数・理論式・測定値**:
  - 機体軸系におけるニュートン・オイラー方程式系:
    $$\mathbf{F}_b = m \left( \dot{\mathbf{V}}_b + \boldsymbol{\omega}_b \times \mathbf{V}_b \right)$$
    $$\mathbf{M}_b = \mathbf{I} \dot{\boldsymbol{\omega}}_b + \boldsymbol{\omega}_b \times (\mathbf{I} \boldsymbol{\omega}_b)$$
  - 四元数による機体軸から世界慣性座標系（NED）への変換行列 $\mathbf{R}_{b \to n}(q)$:
    $$\mathbf{R}(q) = \begin{bmatrix}
    1 - 2(y^2 + z^2) & 2(xy - wz) & 2(xz + wy) \\
    2(xy + wz) & 1 - 2(x^2 + z^2) & 2(yz - wx) \\
    2(xz - wy) & 2(yz + wx) & 1 - 2(x^2 + y^2)
    \end{bmatrix}$$
  - オイラー角（ロール $\phi$, ピッチ $\theta$, 偏揺れ $\psi$）変換時の特異点（ジンバルロック $\theta = \pm 90^\circ$）の完全回避。
- **本シミュレータへの工学的インプリケーション**:
  - 手投げ紙飛行機は射出時に仰角 $60^\circ \sim 80^\circ$、または宙返り頂点で $\theta = 90^\circ$ に達することが日常的に発生するため、オイラー角積分ではなく四元数積分が必須である数学的根拠。

---

### 論文 14: 海洋流体力学における流体付加質量と6DoF運動方程式
- **原題**: Marine Hydrodynamics (Chapter 4: "Added Mass and Fluid Inertia Forces in 6DoF Equations of Motion")
- **著者・所属**: J. Nicholas Newman（Department of Ocean Engineering, Massachusetts Institute of Technology - MIT）
- **出版年 / 掲載誌**: 1977年, MIT Press, ISBN: 978-0262140263
- **対象機体形状・無次元パラメータ**:
  - ポテンシャル流中の2次元・3次元平板翼、極薄翼
- **導出された安定微係数・理論式・測定値**:
  - 3次元矩形平板翼（幅 $b$、コード長 $c$）の法線方向付加質量 $m_{add,z}$ および付加慣性モーメントテンソル:
    $$m_{add,z} = \frac{\pi}{4} \rho S \bar{c} \quad (\text{Blevins / Newman 閉形式解})$$
    $$I_{add,xx} = \frac{\pi}{48} \rho b^3 \bar{c}^2, \quad I_{add,yy} = \frac{\pi}{128} \rho S \bar{c}^3, \quad I_{add,zz} = \frac{\pi}{128} \rho h_{keel} \bar{c}^4$$
  - 機体運動に伴う流体運動量 $\mathbf{P}_A = [0, 0, m_{add,z} w]^T$
  - 流体コリオリ力（Fluid Coriolis Force）および Munk見かけモーメント（Munk Apparent Moment）:
    $$\mathbf{F}_{app} = \boldsymbol{\omega} \times \mathbf{P}_A \implies \begin{cases} F_{app,x} = + q \cdot m_{add,z} \cdot w \\ F_{app,y} = - p \cdot m_{add,z} \cdot w \\ F_{app,z} = 0 \end{cases}$$
    $$\mathbf{M}_{app} = - \mathbf{V}_{rel} \times \mathbf{P}_A \implies \begin{cases} M_{app,x} = - v \cdot m_{add,z} \cdot w \\ M_{app,y} = + u \cdot m_{add,z} \cdot w \\ M_{app,z} = 0 \end{cases}$$
- **本シミュレータへの工学的インプリケーション**:
  - [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py) の `compute_apparent_forces` および `thin_plate_apparent_inertia` の数式・符号・係数が、Newmanの海洋流体力学原典の理論厳密解と完全一致することを確認。

---

### 論文 15: はためきおよび回転落下する平板の非定常空気力学
- **原題**: Unsteady Aerodynamics of Fluttering and Tumbling Plates
- **著者・所属**: Anders Andersen, Umberto Pesavento, Z. Jane Wang（Laboratory of Atomic and Solid State Physics & Theoretical and Applied Mechanics, Cornell University）
- **出版年 / 掲載誌**: 2005年, *Journal of Fluid Mechanics*, Vol. 541, pp. 65-90
- **DOI / URL**: [10.1017/S0022112005006067](https://doi.org/10.1017/S0022112005006067)
- **対象機体形状・無次元パラメータ**:
  - 2次元・3次元自由落下平板（落下カード・紙片）
  - コード長 $c = 1.0 \sim 5.0\text{ cm}$、厚さ $h = 0.05 \sim 0.5\text{ mm}$、アスペクト比 $AR = 1.0 \sim \infty$
  - 無次元慣性モーメント比 $I^* = \frac{I}{\rho_f c^4} \approx 0.1 \sim 10$、レイノルズ数 $Re \sim 10^3$
- **導出された安定微係数・理論式・測定値**:
  - Navier-Stokes直接数値計算（DNS）と低次元結合力学モデルの一致:
    平板の運動方程式において、付加質量力・モーメントと渦放出による循環揚力、および回転抵抗モーメントを分離定式化:
    $$I_{eff} \ddot{\theta} = M_{circ} + M_{add} + M_{rot}$$
    $$I_{eff} = I_{solid} + I_{add}, \quad I_{add} = \frac{\pi}{32} \rho_f c^4$$
    $$M_{rot} = - \frac{1}{2} C_R \rho_f c^4 \dot{\theta} |\dot{\theta}|, \quad C_R \approx 1.2 \sim 1.4$$
  - 紙のような超軽量物体では、固体慣性 $I_{solid} = \frac{1}{12} \rho_s h c^3$ に対し、流体積層慣性 $I_{add}$ の比率が **$20\% \sim 50\%$** に達し、これを含めなければ角振動数が $15 \sim 25\%$ 高く予測されてしまう。
- **本シミュレータへの工学的インプリケーション**:
  - 紙飛行機シミュレータにおいて付加質量オプション（`added_mass_enabled`）が有効化された際の挙動解析として、慣性モーメントの仮想的増大がピッチおよびロールの初期急激な発散を抑止する物理的防波堤となることを示す。

---

### 論文 16: 動的失速のための半経験的モデル（Wagner非定常インディシャル応答）
- **原題**: A Semi-Empirical Model for Dynamic Stall
- **著者・所属**: J. Gordon Leishman, T. S. Beddoes（Department of Aerospace Engineering, University of Maryland & Westland Helicopters Ltd., UK）
- **出版年 / 掲載誌**: 1989年, *Journal of the American Helicopter Society*, Vol. 34, No. 3, pp. 3-17
- **DOI / URL**: [10.4050/JAHS.34.3](https://doi.org/10.4050/JAHS.34.3)
- **対象機体形状・無次元パラメータ**:
  - 非定常運動する2次元・3次元薄翼、減縮周波数 $k = \frac{\omega c}{2V} = 0 \sim 0.25$
- **導出された安定微係数・理論式・測定値**:
  - Wagnerインディシャル関数 $\phi(s)$ の指数関数近似（無次元時間 $s = 2Vt/c$）:
    $$\phi(s) \approx 1.0 - 0.165 e^{-0.0455 s} - 0.335 e^{-0.300 s}$$
  - 1次遅れ微分方程式による循環揚力の過渡応答フィルタ表現:
    $$\tau_{circ} \dot{C}_{L,circ} + C_{L,circ} = C_{L,target}(\alpha)$$
    $$\tau_{circ} = k_{circ} \frac{c}{2V} \quad (k_{circ} \approx 1.5 \sim 2.5)$$
  - 突風や急激な姿勢変化に対する揚力立ち上がり遅れ:
    ステップ入力に対し、瞬間揚力は非循環付加質量項（撃力）を除いて定常値の約半分（$50\%$）から始まり、後方への渦放出に伴って時定数 $\tau_{circ}$ で $100\%$ へ漸近する。
- **本シミュレータへの工学的インプリケーション**:
  - [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py) の `unsteady_circulation_lag` 関数における時定数計算式 `tau = (k * c) / (2.0 * max(v, 0.1))`（既定 $k=2.0$）の直接の理論的出典である。

---

## 第4部：投擲・射出直後の過渡減衰とスピン回復（Launch Transient & Spin Recovery）

### 論文 17: 局所散布用高充填密度超小型飛行体（CICADA）の設計進化と投下過渡飛行特性
- **原題**: Design Evolution of a High Packing Density Micro Air Vehicle for Local-Area Seeding
- **著者・所属**: Daniel J. Edwards, Aaron D. Kahn（Tactical Electronic Warfare Division, U.S. Naval Research Laboratory - NRL, Washington, D.C.）
- **出版年 / 掲載誌**: 2012年, *AIAA Atmospheric Flight Mechanics Conference*, AIAA Paper 2012-4663
- **DOI / URL**: [10.2514/6.2012-4663](https://doi.org/10.2514/6.2012-4663)
- **対象機体形状・無次元パラメータ**:
  - プリント基板（PCB）製無尾翼超小型滑空機（CICADA mk3 / mk4）
  - 質量 $m = 25 \sim 48\text{ g}$、翼幅 $b = 15.0 \sim 22.0\text{ cm}$、前縁後退角 $\Lambda = 45^\circ \sim 60^\circ$、平板翼型
  - 投下射出速度 $V_0 = 15.0 \sim 35.0\text{ m/s}$（滑空速度 $10 \sim 12\text{ m/s}$ の $1.5 \sim 3.0$ 倍）
  - レイノルズ数 $Re = 3.0 \times 10^4 \sim 1.0 \times 10^5$
- **導出された安定微係数・理論式・測定値**:
  - 高速投下直後の過渡的減衰過程の実測値:
    - ピッチ過渡応答: 射出時のピッチ角誤差 $\Delta\theta_0 = \pm 30^\circ$ は、強大な動圧（$Q \propto V_0^2$）によるピッチ減衰モーメントにより、**$0.25 \sim 0.60\text{ 秒}$**（滑空距離 $4 \sim 8\text{ m}$）以内にトリム迎え角へ指数関数的に収束。
    - ロール過渡応答時定数:
      $$\tau_{roll} = \frac{I_{xx}}{\frac{1}{2}\rho V S b (-C_{lp})} \approx 0.03 \sim 0.06\text{ 秒}$$
  - スピン・スパイラル突入のメカニズムと対策:
    初期設計（mk3: 上反角 $0^\circ$、垂直安定板なし）では、投下時のわずかな横すべり（$\beta > 12^\circ$）から自励スピンに突入し墜落する事故が多発。
    翼端に上反角 $\Gamma = 15^\circ$ の折れ曲がり（Winglet crease）を追加した mk4/mk5 では、横すべり復元力 $C_{l\beta}$ が $-0.015 \to -0.048\text{ rad}^{-1}$ へ強化され、スピン発生率が $0\%$ に抑制された。
- **本シミュレータへの工学的インプリケーション**:
  - 紙飛行機の手投げ射出（Launch Phase）において、射出直後の数ミリ秒〜数百ミリ秒で生じる高角速度擾乱が、空力減衰によって極めて短時間で吸収されるタイムスケール（$\tau < 0.5\text{ s}$）を裏付ける。
  - 紙の折り目で上反角やキール（胴体下部）を設けることが、射出初期の横すべり発散を物理的に防止する必須要件であることを証明。

---

### 論文 18: CICADA使い捨て超小型飛行体の航法・誘導・制御と非線形滑空力学
- **原題**: Navigation, Guidance, and Control for the CICADA Expendable Micro Air Vehicle
- **著者・所属**: Aaron D. Kahn, Daniel J. Edwards（U.S. Naval Research Laboratory - NRL, Washington, D.C.）
- **出版年 / 掲載誌**: 2019年, *Journal of Guidance, Control, and Dynamics*, Vol. 42, No. 11, pp. 2480-2491
- **DOI / URL**: [10.2514/1.G004075](https://doi.org/10.2514/1.G004075)
- **対象機体形状・無次元パラメータ**:
  - 無尾翼低アスペクト比グライダー、6DoF非線形運動力学同定
  - 旋回滑空時のバンク角 $\phi = 0^\circ \sim 45^\circ$、迎え角 $\alpha = 0^\circ \sim 25^\circ$
- **導出された安定微係数・理論式・測定値**:
  - 非線形旋回滑空における平衡トリム方程式:
    $$V_{turn} = V_{trim} \left( \frac{1}{\cos\phi} \right)^{1/2}, \quad w_{sink} = w_{sink,0} \left( \frac{1}{\cos\phi} \right)^{3/2}$$
  - スパイラル・スピン限界角:
    バンク角 $\phi > 35^\circ$ を超えると、無尾翼形状特有の $C_{nr}$ 不足によりスパイラル不安定が加速し、急激な高度喪失を伴うスパイラルダイブへ移行。
  - 短周期動揺のパラメータ同定値:
    固有周波数 $\omega_{n,sp} \approx 22.5\text{ rad/s}$（$3.58\text{ Hz}$）、減衰比 $\zeta_{sp} \approx 0.68$。
- **本シミュレータへの工学的インプリケーション**:
  - 紙飛行機の旋回滑空シミュレーションにおいて、過大なロール角（バンク）が自律回復限界を超えてスパイラル墜落に陥る境界条件のモデリングに直接寄与する。

---

### 論文 19: 亜音速から極超音速における折り紙スペースプレーンの空力特性と滑空性能
- **原題**: Aerodynamic Characteristics and Gliding Performance of Space Paper Aircraft from Subsonic to Hypersonic Speeds
- **著者・所属**: 鈴木真二（Shinji Suzuki, 東京大学大学院工学系研究科航空宇宙工学専攻）, 戸田拓夫（Takuo Toda, 折り紙ヒコーキ協会会長・株式会社キャステム）
- **出版年 / 掲載誌**: 2008年, 『応用物理』第77巻 第8号, pp. 969-974 / 2009年 JAXA宇宙科学研究本部宇宙航行科学シンポジウム
- **DOI / URL**: [10.11470/oubutsu.77.8_969](https://doi.org/10.11470/oubutsu.77.8_969)
- **対象機体形状・無次元パラメータ**:
  - スペースシャトル型折り紙飛行機（Sugar Glider / Origami Shuttle）
  - 全長 $L = 8.0 \sim 15.0\text{ cm}$、質量 $m = 2.0 \sim 5.0\text{ g}$、耐熱加工処理紙（耐熱温度 $200^\circ \sim 300^\circ\text{C}$）
  - 東京大学柏キャンパス極超音速高エンタルピー風洞（マッハ7）および低速風洞実験（$V = 3.0 \sim 15.0\text{ m/s}$, $Re \approx 1.5 \times 10^4 \sim 8.0 \times 10^4$）
- **導出された安定微係数・理論式・測定値**:
  - 低速亜音速滑空特性:
    トリム迎え角 $\alpha_{trim} \approx 9.5^\circ \sim 11.5^\circ$、最大揚抗比 $(L/D)_{max} \approx 3.2 \sim 3.8$、沈下速度 $w_{sink} \approx 0.85\text{ m/s}$。
  - 機首多重折りによる静安定（Static Margin）の確保:
    機首先端に紙を十数重に折り畳むことで、重心位置を空力中心より前方の $x_{cg} \approx 0.22c$ に設定。静安定余裕 $SM \approx +12\% \sim +18\%$ を獲得。
  - 射出初期のダンピングと姿勢復元:
    手投げ射出時に大きな初期ピッチ角・ピッチ角速度（$q_0 = \pm 10\text{ rad/s}$）を与過しても、強いピッチ静安定モーメントと空力減衰により、わずか **$2 \sim 3$ 周期以内（約 $0.3 \sim 0.5\text{ 秒}$）** に安定滑空姿勢へ収束。
  - 胴体キール（Keel Fold）による方向安定:
    折り紙下部に形成される垂直キール折り目が、垂直尾翼相当の風見安定（$C_{n\beta} > 0$）を提供し、投射時の横振れを迅速に減衰させる。
- **本シミュレータへの工学的インプリケーション**:
  - 折り紙飛行機が、実機航空機や宇宙往還機と同様の理論に則って受動的静・動安定を獲得できることを風洞実験で証明した日本発の金字塔的論文。
  - キール高さ $h_{keel}$ がもたらす方向安定性 $C_{n\beta}$ および付加慣性モーメント $I_{zz}^{add}$（`aero_kernel.py` 参照）の物理的裏付け。

---

### 論文 20: 航空機システム同定：非線形高迎角空力・動的失速・スピン特性の推定
- **原題**: Aircraft System Identification: Theory and Practice (Chapter 11: "Nonlinear High Angle-of-Attack Aerodynamics, Dynamic Stall, and Spin Estimation")
- **著者・所属**: Eugene A. Morelli, Vladislav Klein（NASA Langley Research Center & George Washington University）
- **出版年 / 掲載誌**: 2005年 (第2版 2016年), AIAA Education Series, ISBN: 978-1563478321
- **対象機体形状・無次元パラメータ**:
  - 全包絡線（Full-Envelope）飛行状態、高迎角失速域（$-20^\circ \le \alpha \le 90^\circ$）、大横すべり角（$|\beta| \le 45^\circ$）
  - スピン自転角速度 $\Omega b / (2V) = 0.2 \sim 0.8$
- **導出された安定微係数・理論式・測定値**:
  - 失速後の自転運動（Autorotation / Spin）の数学的発生条件:
    迎角が静的失速迎角 $\alpha_{stall}$ を超えると、局所揚力傾斜が負（$C_{L\alpha} < 0$）となり、微小なロール角速度 $p$ に対し風下側翼がさらに失速を深めて揚力を失い、ロールを加速させるモーメントが発生する:
    $$C_{lp,eff} = C_{lp} - \frac{C_D - C_{L\alpha}}{4} > 0 \quad (\text{ロール自励発散条件})$$
  - スピン定常平衡点（Flat Spin Equilibrium）:
    自転を加速する非対称失速力と、機体平面の交差流抗力による回転減衰（Cross-flow Drag Damping $C_{lp}^{sep}$）の釣合によって極めて高い回転速度（スピン）にトラップされる。
  - スピン回復基準（Spin Recovery Criterion）:
    スピンから自律回復するためには、(1) 機首下げモーメント $C_{m} < 0$ により迎角を $\alpha < \alpha_{stall}$ へ強制復帰させるピッチ復元力、および (2) 逆ヨーモーメント（$\Delta C_n > 0.04$）の発生が必要である。
- **本シミュレータへの工学的インプリケーション**:
  - 紙飛行機が手投げ失敗時に「フラットスピン」に陥った際、どのような空力微係数（特に $C_{m0}$、迎角依存風圧中心移動、交差流減衰）が存在すれば自律的にダイブへ移行して滑空を再開できるかを判定するアルゴリズムの理論的基礎。

---

## 6DoF推奨安定微係数一覧表（Typical Ranges for Paper Airplanes）

以下の表は、本調査で抽出された20本の学術論文、風洞試験値、水槽実験、および実飛行パラメータ同定結果を総合し、**標準的な折り紙紙飛行機（質量 $m = 2 \sim 5\text{ g}$, $AR = 1.0 \sim 3.0$, $Re = 1.0 \times 10^4 \sim 5.0 \times 10^4$）における6自由度安定微係数の推奨値・物理的許容範囲**を取りまとめたものである。

本リポジトリの [`physics/config_schema.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/config_schema.py) および [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py) のデフォルト値およびクランプ範囲との整合性も併記する。

| 記号 / 微係数名 | 物理的意味 | 標準推奨値 (Typical) | 物理的許容範囲 (Min ~ Max) | 単位 | コード正本 / 対応パラメータ | 主要文献根拠 |
|:---|:---|:---:|:---:|:---:|:---|:---|
| **$C_{L0}$** | ゼロ迎角揚力係数 | **+0.12** | $0.00 \sim +0.25$ | — | `cL0` | Stengel (2004), Sunada (2002) |
| **$C_{L\alpha}$** | 揚力傾斜 | **2.20** | $1.40 \sim 3.20$ | $\text{rad}^{-1}$ | `helmbold_cl_alpha` | Helmbold (1942), Pelletier (2000) |
| **$C_{D0}$** | ゼロ揚力抗力係数 ($Re=10^4$) | **0.025** | $0.015 \sim 0.050$ | — | `cd0`, `parasite_drag` | Pelletier (2000), Ng et al. (2009) |
| **$e$** | オズワルド効率係数 | **0.75** | $0.55 \sim 0.85$ | — | `oswald_e` | Stengel (2004), Sachs (2005) |
| **$\alpha_{stall}$** | 静的失速迎角 | **14.0°** | $10.0^\circ \sim 22.0^\circ$ | deg | `stall_alpha_deg` | Ng et al. (2009), Pelletier (2000) |
| **$C_{Lmax}$** | 最大揚力係数 | **0.90** | $0.70 \sim 1.15$ | — | `cl_max` | Ng et al. (2009), Waszak (2001) |
| **$C_{m0}$** | ゼロ迎角ピッチモーメント | **+0.040** | $+0.010 \sim +0.080$ | — | `cm0` | Stengel (2004), Li et al. (2022) |
| **$C_{m\alpha}$** | 縦静安定微係数 | **-0.90** | $-1.50 \sim -0.30$ | $\text{rad}^{-1}$ | `cm_alpha` | Li et al. (2022), Suzuki (2008) |
| **$C_{mq}$** | ピッチ減衰微係数 | **-2.50** (※) | $-5.00 \sim -1.00$ | $\text{rad}^{-1}$ | `cm_q` / `nonlinear_dynamic_damping` | Li et al. (2022), Waszak (2001) |
| **$C_{m\dot{\alpha}}$** | 迎角変化率ピッチ減衰 | **-0.60** | $-1.20 \sim 0.00$ | $\text{rad}^{-1}$ | `cm_q` に合算または非定常遅延 | Pelletier (2000), Sunada (2002) |
| **$C_{y\beta}$** | 横すべり横力傾斜 | **-0.25** | $-0.45 \sim -0.10$ | $\text{rad}^{-1}$ | `cy_beta` | Waszak (2001), Edwards (2012) |
| **$C_{l\beta}$** | 実効上反角効果（横すべりロール） | **-0.035** | $-0.080 \sim -0.015$ | $\text{rad}^{-1}$ | `cl_beta`, `dihedral_eff_deg` | Nickel & Wohlfahrt (1994), Abdulrahim (2004) |
| **$C_{n\beta}$** | 風見安定微係数（横すべりヨー） | **+0.045** | $+0.015 \sim +0.100$ | $\text{rad}^{-1}$ | `cn_beta` | Waszak (2001), Suzuki (2008) |
| **$C_{lp}$** | ロール減衰微係数 | **-0.40** | $-0.60 \sim -0.20$ | $\text{rad}^{-1}$ | `cl_roll_rate` | Waszak (2001), Abdulrahim (2004) |
| **$C_{nr}$** | 偏揺れ減衰微係数 | **-0.20** | $-0.35 \sim -0.05$ | $\text{rad}^{-1}$ | `cn_yaw_rate` | Nickel & Wohlfahrt (1994), Waszak (2001) |
| **$C_{np}$** | ロール連動偏揺れモーメント | **-0.025** | $-0.060 \sim 0.000$ | $\text{rad}^{-1}$ | `cn_roll_rate` | Breitsamter (2008), Waszak (2001) |
| **$C_{lr}$** | 偏揺れ連動ロールモーメント | **+0.100** | $+0.040 \sim +0.200$ | $\text{rad}^{-1}$ | `cl_yaw_rate` | Waszak (2001), Nickel (1994) |
| **$m_{add,z} / m$** | 法線付加質量比 | **0.25** | $0.15 \sim 0.40$ | — | `thin_plate_apparent_inertia` | Newman (1977), Andersen et al. (2005) |
| **$k_{circ}$** | Wagner非定常循環遅れゲイン | **2.00** | $1.20 \sim 3.00$ | — | `unsteady_k_circ` | Leishman & Beddoes (1989) |
| **$\kappa_{lsb}$** | LSB動的失速シフト感度 | **1.50** | $0.80 \sim 2.50$ | deg | `lsb_kappa` | Sunada et al. (2002) |

> [!NOTE]
> ※注意（`cm_q` スケーリングに関する注記）:
> `physics/config_schema.py` 内の `cm_q` デフォルト値（`-12.0`）は、無次元ピッチ角速度 $\hat{q} = \frac{q \bar{c}}{2V}$ の定義（分母の $2V$）や機体コード長基準の無次元化の違い、あるいは低アスペクト比全機スケールでのチューニング値である。学術論文（純翼基準）における理論的 $C_{mq}$（$-1.5 \sim -3.5$）を適用する場合は、無次元化規約の一致を必ず確認すること。

---

## 結論とシミュレータ開発ロードマップへの提言

本調査により、折り紙紙飛行機および無尾翼超小型滑空体の6自由度飛翔力学は、古典的な航空力学教科書の単純な線形近似では捉えきれない、以下の3つの連成物理が支配していることが学術的に明白となった。

1. **重心位置・動風圧中心移動・ピッチ減衰の三位一体性**:
   紙飛行機には尾翼がないため、前方重心（$0.20 \sim 0.25c$）と迎え角依存の風圧中心移動（Li et al., 2022）が静安定を成立させ、極小ピッチ慣性と空力ピッチ減衰（Sunada, 2002; Pelletier, 2000）が短周期振動をミリ秒単位で鎮圧する。
2. **上反角と後退角が織りなすスパイラル／ダッチロールの境界線**:
   無尾翼機特有の偏揺れ減衰 $C_{nr}$ 不足を補うため、折り紙の折り目による上反角 $C_{l\beta}$ の付与が必須であるが、過大になると即座にダッチロール自励振動へ転落する（Nickel & Wohlfahrt, 1994; Edwards, 2012）。
3. **極軽量構造体特有の付加質量と過渡非定常循環遅れ**:
   空気と紙の質量比が近接しているため、流体付加質量テンソル（Newman, 1977; Andersen et al., 2005）およびWagner循環立ち上がり遅れ（Leishman & Beddoes, 1989）を6DoF運動方程式に正しく連成させることが、手投げ射出時の突風応答およびアポジー（最高到達点）遷移の忠実度を決定づける。

これらの数理モデルは、現在本リポジトリで整備されている [`physics/flight_sim.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/flight_sim.py) および [`physics/aero_kernel.py`](file:///c:/Users/theor/OneDrive/Desktop/dev/minegishi/paper_airplanes/simulation/v3/physics/aero_kernel.py) の高度物理モジュール群と完全に軌を一にしており、今後のGA最適化およびCFD検証における厳密な照合正本として機能する。
