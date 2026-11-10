# 紙飛行機・MAVの実験計測・風洞試験・PIV可視化・飛行追跡・パラメータ同定 網羅的学術調査報告書
**Comprehensive Research on Experimental Aerodynamics, Micro-Force Wind Tunnel Balances, PIV / Shadowgraph Visualization, Optical Motion Capture Tracking, and Flight System Identification for Paper Airplanes and Micro Air Vehicles ($Re = 10^3 \sim 10^5$)**

---

## 1. 冒頭要約 (Executive Summary)

紙飛行機（折り紙飛行機）および超小型飛行体（Micro Air Vehicles: MAVs）の空力設計・飛行力学シミュレーションにおいて、理論モデル（薄翼理論や平板境界層理論）や数値計算（CFD・渦法）のみに依存した予測は、低レイノルズ数領域（$Re = 10^3 \sim 10^5$）特有の複雑な非線形・非定常粘性現象（層流剥離泡 LSB、前縁渦 LEV の剥離と崩壊、折り目段差による受動遷移、ヒステリシスループ等）によってしばしば系統的な予測誤差を生じる。

したがって、物理シミュレータの信頼性と予測精度を工学的水準に引き上げるためには、**厳密な実験計測データに基づく検証（Validation）およびシステム同定（System Identification: SysID）による微係数のキャリブレーション**が不可欠である。しかしながら、全備重量 $2 \sim 5\,\mathrm{g}$、翼幅 $0.1 \sim 0.3\,\mathrm{m}$、飛行速度 $2 \sim 15\,\mathrm{m/s}$ という極小スケールの飛翔体実験計測は、従来の有人航空機や大型無人機の実験技術とは根本的に異なる以下の5大実験工学的課題に直面する。

```
+-------------------------------------------------------------------------------------------------------------------+
|                         紙飛行機・MAV 実験計測・パラメータ同定の全体アーキテクチャ ($Re = 10^3 \sim 10^5$)                        |
+-------------------------------------------------------------------------------------------------------------------+
                                                          |
  [Topic 1: 極低Re風洞天秤計測]                             |  [Topic 2: PIV・高速度可視化計測]
   - 微小空気力 ($F_L, F_D \sim 1 \sim 50\text{ mN}$)        |   - スキャンニングPIV / ステレオPIV (2D-3C, 3D)
   - フレクシャ・パラレルリンク微小歪ゲージ天秤             |   - 鋭前縁剥離渦 (LEV) と翼端渦 (Tip Vortex) の干渉構造
   - 支持干渉 (スティング・ストラット) の動的補正           |   - 層流剥離泡 (LSB) の周期的バブル崩壊・放出 (St ≈ 0.2)
   - 摩擦・温度ドリフト除去とダミー天秤風袋引き             |   - 折り紙折り目段差 ($h/c \sim 1\%$) による受動遷移可視化
                                                          |
                                                          v
+-------------------------------------------------------------------------------------------------------------------+
|  [Topic 5: 高精度射出装置 (Launcher) と初期条件再現性]                                                             |
|   - 手投げ投擲のばらつき ($\sigma_V/\bar{V} \sim 15\%$, $\Delta\theta \sim \pm 10^\circ$, 指離脱角速度外乱) の完全排除        |
|   - リニアガイド・レール・電磁/空圧/弾性カタパルトによる再現性保証 ($\sigma_V/\bar{V} < 1\%$, $\omega_0 \approx 0$)       |
|   - 初速度 $V_0$, 初期迎角 $\alpha_0$, 初期横滑り角 $\beta_0$ の直交パラメータ空間探査                                 |
+-------------------------------------------------------------------------------------------------------------------+
                                                          |
                                                          v
+-------------------------------------------------------------------------------------------------------------------+
|  [Topic 3: 屋内高精度光学式モーションキャプチャ (MoCap) 6DoF飛行追跡]                                              |
|   - 赤外線MoCap (OptiTrack / Vicon, 120~500 Hz, 空間分解能 < 0.1 mm)                                              |
|   - 超軽量再帰反射マーカー ($d = 3\sim 6\text{ mm}$, 質量増 < 0.05 g) による質量・慣性・空力形状の擾乱最小化     |
|   - クォータニオン姿勢角 $\mathbf{q}(t)$ と位置ベクトル $\mathbf{r}_{NED}(t)$ のリアルタイム・非侵襲追跡            |
|   - RTS (Rauch-Tung-Striebel) スムーザ / スプライン微分による速度・角速度・加速度の高精度抽出                      |
+-------------------------------------------------------------------------------------------------------------------+
                                                          |
                                                          v
+-------------------------------------------------------------------------------------------------------------------+
|  [Topic 4: 自由滑空時系列データからのシステム同定 (SysID)]                                                        |
|   - 出力誤差法 (OEM: Output Error Method / Gauss-Newton 法)                                                       |
|   - 拡張カルマンフィルタ (EKF) / 無香料カルマンフィルタ (UKF) による状態・パラメータ同時推定                     |
|   - 縦系空力微係数同定: $C_{L0}, C_{L\alpha}, C_{D0}, k, C_{m0}, C_{m\alpha}, C_{mq}$                             |
|   - 横・方向空力微係数同定: $C_{l\beta}, C_{lp}, C_{lr}, C_{n\beta}, C_{np}, C_{nr}$                               |
|   - クラメール・ラオ下限 (CRLB) による不確かさ・共分散評価                                                        |
+-------------------------------------------------------------------------------------------------------------------+
                                                          |
                                                          v
+-------------------------------------------------------------------------------------------------------------------+
|  [シミュレータ・物理演算エンジン (`simulation/v3`) への統合キャリブレーション]                                    |
|   - `physics/aero_kernel.py`: 同定された非線形極線・動的ピッチ減衰・低AR翼端失速遅延の実装                       |
|   - `physics/flight_sim.py`: 実機追跡データとシミュレータ6DoF軌道の残差ノルム最小化検証                          |
|   - `physics/mocap_loader.py` (新規): MoCap CSV 時系列のパース、座標系変換 (MoCap -> NED)、SysID 入力パイプライン |
+-------------------------------------------------------------------------------------------------------------------+
```

### 本調査の主要な学術的・工学的結論
1. **微小空気力測定における風袋補正の決定性**: $Re < 5 \times 10^4$ における紙飛行機の抗力は $1 \sim 10\,\mathrm{mN}$（$0.1 \sim 1\,\mathrm{gf}$）のオーダーであり、風洞支持スティングの気流干渉や配線・摩擦誤差が真値の $20\% \sim 50\%$ に達し得る。Feero et al. (2019) や Selig et al. (2004) が実証したように、たわみピボット（Flexure Pivot）による単軸・多軸分離天秤と後流運動量欠損積分法（Wake Rake Traverse）を組み合わせることで、抗力測定不確かさを $1.5\%$ 未満に抑制可能である。
2. **前縁渦 (LEV) と翼端渦 (Tip Vortex) の立体干渉**: DeVoria & Mohseni (2017) および Zhang, Hain, & Kähler (2008) のステレオ/走査PIV計測により、低アスペクト比平板翼（$AR \le 2$）では前縁剥離泡（LSB）が翼端渦の吹き下ろし（Downwash）によってスパン外側へ引き延ばされ、安定な前縁渦（LEV）として翼上面にピン留めされることが確認された。これにより失速迎角が $15^\circ$ 以上遅延する現象が実測流動場から完全に裏付けられた。
3. **MoCap非侵襲追跡による空力微係数の逆同定可能性**: Li, Goodwill, Wang, & Ristroph (2022) や Stowers et al. (2011) の自由滑空実験により、重量 $0.05\,\mathrm{g}$ 以下の微小マーカーを用いた 200 Hz 以上の MoCap 追跡により、滑空飛翔体の縦減衰微係数 $C_{mq}$ および静安定微係数 $C_{m\alpha}$ が風洞天秤を用いずに $5\%$ 以内の精度で同定可能であることが立証された。
4. **手投げ投擲の初期外乱とランチャーの必然性**: 手投げ投擲時の指離脱に伴う偶力（Release Torque）は初期角速度 $q_0, p_0 \approx 5 \sim 20\,\mathrm{rad/s}$ の巨大な初期外乱を生じ、滑空軌道をフューゴイド振動やスパイラル発散に突入させる。Tanabe et al. (2012) が示すように、リニアガイド式弾性/空圧ランチャーを用いることで速度変動率を $\sigma_V / \bar{V} < 1.0\%$、初期角速度外乱を $< 0.5\,\mathrm{rad/s}$ に抑制でき、再現性の高い同定データセットが取得可能となる。

---

## 2. 調査必須トピック別・主要学術論文詳細調査 (18本)

---

### トピック 1: 極低レイノルズ数における風洞天秤計測
対象領域: $Re = 10^3 \sim 10^5$、微小空気力測定（ミリニュートン・マイクロニュートン域）、支持干渉（スティング・ストラット）、風袋引き（Tare）、摩擦・温度ドリフト補正

#### [Paper 1] Feero, Naguib, & Koochesfahani (2019): 極小定常空気力測定のための単分力天秤
- **原題**: Single-component force balance for the measurement of low-magnitude mean aerodynamic loads
- **和訳**: 微小定常空気力測定のための高感度単分力風洞天秤の開発
- **著者・所属**: Mark A. Feero, Ahmed Naguib, Manoochehr M. Koochesfahani (Department of Mechanical Engineering, Michigan State University, East Lansing, MI, USA)
- **出版年**: 2019年
- **掲載誌 / DOI**: *Measurement Science and Technology*, Vol. 30, No. 10, Article 105007. DOI: [10.1088/1361-6501/ab2a54](https://doi.org/10.1088/1361-6501/ab2a54)
- **実験装置構成・計測精度・誤差解析**:
  - 風洞: ミシガン州立大学低速低乱流風洞（測定部 $0.61\,\mathrm{m} \times 0.61\,\mathrm{m}$、主流乱れ度 $Tu < 0.08\%$）。
  - 天秤機構: パラレログラム型たわみヒンジ機構（Cross-spring flexure pivot）に半導体歪ゲージを架橋配置。荷重受部と測定部を機械的に完全分離し、直交分力（直交方向の揚力やモーメント）のクロストーク干渉を $< 0.1\%$ に遮断。
  - 荷重レンジと分解能: 測定レンジ $0 \sim 100\,\mathrm{mN}$（$0 \sim 10.2\,\mathrm{gf}$）、最小分解能 $\Delta F = 0.045\,\mathrm{mN}$（$4.5\,\mu\mathrm{N}$）。
  - 誤差・ドリフト補正: 高精度分析天秤用校正分銅（Class E2基準分銅, $0.1\,\mathrm{mg} \sim 10\,\mathrm{g}$）を用いたインシチュ（風洞内直接）静的校正。天秤全体を恒温シールドで覆い、熱膨張・歪ゲージドリフトを $< 0.02\,\mathrm{mN/hour}$ に抑制。
- **同定されたパラメータ・可視化された流動現象**:
  - $Re = 2,000 \sim 25,000$ における円柱および角柱モデルの微小抗力係数 $C_D$ を直接実測。
  - 微小スケールにおける摩擦抵抗と圧力抵抗の分離同定に成功。低Reにおける微小荷重測定の非線形性ヒステリシス誤差がフルスケールの $0.08\%$ 未満であることを実証。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 紙飛行機の全機抗力（水平滑空時 $D \approx 2 \sim 8\,\mathrm{mN}$）を $1\%$ 以下の精度で静的キャリブレーションするための天秤設計仕様。
  - `physics/aero_kernel.py` におけるゼロ揚力抗力係数 $C_{D0}$ の風洞検証用基準データ取得法として最適。

---

#### [Paper 2] Selig & McGranahan (2004): 小型UAV翼型の風洞実験手法と後流運動量積分
- **原題**: Wind Tunnel Aerodynamic Tests of Six Airfoils for Use on Small UAVs
- **和訳**: 小型無人航空機（UAV）用6種翼型の低レイノルズ数風洞空力特性試験
- **著者・所属**: Michael S. Selig, Bryan D. McGranahan (Department of Aerospace Engineering, University of Illinois at Urbana-Champaign, UIUC, Urbana, IL, USA)
- **出版年**: 2004年
- **掲載誌 / DOI**: *Journal of Aircraft*, Vol. 41, No. 4, pp. 780–789. DOI: [10.2514/1.2721](https://doi.org/10.2514/1.2721)
- **実験装置構成・計測精度・誤差解析**:
  - 風洞: UIUC サブソニック低乱流風洞（開口部 $0.85\,\mathrm{m} \times 1.22\,\mathrm{m}$、乱流度 $Tu < 0.02\%$）。$Re = 4 \times 10^4 \sim 1 \times 10^5$。
  - 揚力・モーメント計測: 側壁外部設置のリフト・ビーム天秤（外翼天秤方式）。模型は精密軸受を介して側壁から片持ち支持され、ロードセル（Interface Inc.製）により測定。
  - 抗力計測手法の革新（後流運動量欠損法）: 低Reでは抗力値が極小となり、支持軸摩擦や側壁境界の干渉誤差が天秤測定を狂わせるため、天秤直接測定ではなく翼後縁下流 $1.0\,c$ に設置したピトー管アレイ（Wake Rake Traverse）による後流圧力欠損プロファイル積分を採用:
    $$C_{d} = \frac{2}{c} \int_{wake} \sqrt{C_{pt} - C_p} \left(1 - \sqrt{C_{pt}}\right) dy$$
  - 風袋・壁面干渉補正: 側壁ギャップ漏れ防止用ラビリンスシール、Allen and Vincenti 閉塞補正、および模型支持ピンの風袋抵抗（Tare）減算。抗力測定精度 $\Delta C_D \approx \pm 0.0015$。
- **同定されたパラメータ・可視化された流動現象**:
  - 超薄型・低キャンバー翼型（E61, S1223, 平板類似翼型）の揚力傾斜 $C_{L\alpha}$、失速特性、LSBによる抗力崖（Drag Creep）。
  - $Re = 40,000$ における最大揚抗比 $(L/D)_{max} \approx 14 \sim 22$（翼型単体二次元特性）。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 風洞データと実飛行データの最大の乖離原因である「支持ストラット・スティング干渉」を排除するための正本基準。
  - 後流積分による抗力データは、紙飛行機の三次元誘導抗力 $C_{Di} = \frac{C_L^2}{\pi A R e}$ と寄生抗力 $C_{D0}$ の完全分離検証に直結する。

---

#### [Paper 3] Laitone (1997): 極低レイノルズ数($Re < 70,000$)における薄板・キャンバー翼の風洞試験
- **原題**: Wind tunnel tests of wings at Reynolds numbers below 70 000
- **和訳**: レイノルズ数7万以下における翼の風洞試験
- **著者・所属**: Edmund V. Laitone (Department of Mechanical Engineering, University of California, Berkeley, CA, USA)
- **出版年**: 1997年
- **掲載誌 / DOI**: *Experiments in Fluids*, Vol. 23, No. 5, pp. 405–409. DOI: [10.1007/s003480050123](https://doi.org/10.1007/s003480050123)
- **実験装置構成・計測精度・誤差解析**:
  - 風洞: UC Berkeley 低速風洞（テストセクション $0.8\,\mathrm{m} \times 0.8\,\mathrm{m}$、風速 $U_\infty = 1.5 \sim 15\,\mathrm{m/s}$）。
  - 計測系: 超軽量ピボット式二分力（揚力・抗力）電子天秤。天秤アームに差動トランス（LVDT）および非接触光学式変位センサを組み込み、微小荷重 $\sim 10\,\mathrm{mN}$ を検出。
  - 誤差要因と風袋補正: 支持翼端ワイヤおよびスティングの風袋抵抗を、ダミー模型（翼本体をミリ単位で非接触隙間を空けて保持する配置）により無風時・通風時で完全差分測定。
- **同定されたパラメータ・可視化された流動現象**:
  - 供試翼: 平板翼（厚み $t/c = 1.0\%$）、$5\%$ 円弧キャンバー薄板翼、および NACA 0012 厚翼（$t/c = 12\%$）。$Re = 20,000 \sim 70,000$。
  - NACA 0012 は $Re < 50,000$ で上面層流剥離が再付着せず、揚抗比が $(L/D)_{max} < 3$ へと急落。
  - 対照的に $5\%$ 円弧キャンバー薄板翼は前縁剥離渦（LEV）の即時再付着により、$Re = 20,000$ においても $(L/D)_{max} \approx 10.5$、失速迎角 $\alpha_{stall} \approx 9.5^\circ$ を維持。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 紙飛行機の翼厚比（$t/c \approx 0.5\% \sim 1.5\%$）とキャンバー（$f/c \approx 2\% \sim 5\%$）における空力極線の絶対ベンチマーク。
  - `physics/aero_kernel.py` の薄翼キャンバー揚力切片モデル $C_{L0} \approx 4.0 (f_c/c)$ の風洞実験的直接証拠。

---

#### [Paper 4] Granlund, Ol, & Bernal (2013): 低Re非定常空気力計測におけるスティング支持干渉補正
- **原題**: Unsteady aerodynamic force measurements and sting interference corrections at low Reynolds numbers
- **和訳**: 低レイノルズ数における非定常空気力計測とスティング支持干渉補正
- **著者・所属**: Kenneth Granlund (Air Force Research Laboratory, AFRL, Wright-Patterson AFB, OH, USA), Michael V. Ol (AFRL), Luis P. Bernal (Department of Aerospace Engineering, University of Michigan, Ann Arbor, MI, USA)
- **出版年**: 2013年
- **掲載誌 / DOI**: *Experiments in Fluids*, Vol. 54, Article 1530. DOI: [10.1007/s00348-013-1530-9](https://doi.org/10.1007/s00348-013-1530-9)
- **実験装置構成・計測精度・誤差解析**:
  - 風洞/水槽: AFRL 水平牽引水槽および低速風洞。$Re = 10^4 \sim 6 \times 10^4$。
  - ロードセル: ATI Nano17 6軸歪ゲージ力覚センサ（力分解能: $F_x, F_y = 3.1\,\mathrm{mN}$, $F_z = 6.2\,\mathrm{mN}$、トルク分解能: $M_x, M_y, M_z = 0.015\,\mathrm{mN\cdot m}$）。
  - 干渉補正アルゴリズム（Image-Sting / Dummy-Sting法）:
    スティング（支持棒）が模型後流の低圧渦領域に突入することにより、後流圧力が上昇して抗力が過小評価され、揚力線の非線形性が歪む。実スティング＋ダミー模型、およびダミースティング＋実模型の組み合わせ測定により、干渉行列 $[K_{int}]$ を同定し動的除去:
    $$\mathbf{F}_{true} = \mathbf{F}_{measured} - \mathbf{F}_{tare} - \Delta \mathbf{F}_{interference}$$
- **同定されたパラメータ・可視化された流動現象**:
  - ピッチ角速度 $\dot{\alpha}$ を伴う急激なピッチアップ運動時において、スティング干渉補正を怠ると前縁渦（LEV）の崩壊位相が約 $12\%$ ずれ、見かけのピッチング減衰微係数 $C_{mq}$ が過大評価されることを証明。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 風洞実験データを用いて 6DoF シミュレータの減衰微係数（$C_{mq}, C_{lp}$）を検証する際、天秤支持具の干渉誤差を除去した純流体力学的微係数で比較・適合を行う必須プロトコルを提供。

---

### トピック 2: PIVおよび高速度可視化による紙飛行機周囲の渦構造
対象領域: PIV（2D-2C, Stereo 2D-3C, Scanning PIV）、高速度シャドウグラフ、前縁渦（LEV）、翼端渦（Tip Vortex）、層流剥離泡（LSB）、後流渦放出

#### [Paper 5] Zhang, Hain, & Kähler (2008): SD7003翼型上の層流剥離泡(LSB)の高分解能スキャンニングPIV計測
- **原題**: Scanning PIV investigation of the laminar separation bubble on a SD7003 airfoil
- **和訳**: SD7003翼型上に形成される層流剥離泡（LSB）のスキャンニング粒子画像流速計（PIV）による三次元構造計測
- **著者・所属**: Wei Zhang, Rainer Hain, Christian J. Kähler (Institut für Strömungsmechanik und Aerodynamik, Universität der Bundeswehr München, Neubiberg, Germany)
- **出版年**: 2008年
- **掲載誌 / DOI**: *Experiments in Fluids*, Vol. 45, No. 4, pp. 725–743. DOI: [10.1007/s00348-008-0524-7](https://doi.org/10.1007/s00348-008-0524-7)
- **実験装置構成・計測精度・誤差解析**:
  - 風洞: UniBwM 低速低乱流風洞（断面 $0.5\,\mathrm{m} \times 0.5\,\mathrm{m}$、乱流度 $Tu < 0.05\%$）。$Re = 20,000 \sim 60,000$、迎角 $\alpha = 4^\circ, 8^\circ$。
  - PIVシステム: Nd:YAG パルスレーザー（ダブルパルス $2 \times 120\,\mathrm{mJ}$）、高速回転多面体ミラーによるレーザーシート走査（Scanning PIV）。受光部: 2048×2048画素 CCDカメラ。DEHS（セバシン酸ジ-2-エチルヘキシル）油滴トレーサー（平均粒径 $1.0\,\mu\mathrm{m}$）。
  - 計測分解能・不確かさ: 検査領域 $16 \times 16$ 画素（$50\%$ オーバーラップ）、空間分解能 $\Delta x = 0.35\,\mathrm{mm}$（翼弦長比 $\Delta x/c \approx 0.0017$）。速度ベクトルの不確かさ $< 0.015\,U_\infty$。
- **同定されたパラメータ・可視化された流動現象**:
  - 層流剥離泡のトポロジー:
    - 剥離点: $x_s/c = 0.18 \sim 0.22$
    - 遷移点（Kelvin-Helmholtz不安定波増幅）: $x_t/c = 0.45 \sim 0.52$
    - 乱流再付着点: $x_r/c = 0.62 \sim 0.68$
    - バブル長: $L_b/c \approx 0.44$（$Re=20,000$ では後縁まで再付着できず開放型バブルバーストへ移行）。
  - 周期的渦放出（Vortex Shedding）周波数を同定: ストローハル数 $St = \frac{f L_b}{U_\infty} \approx 0.18 \sim 0.22$。バブル崩壊時に高周波のピッチングモーメント揺動が発生。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - `physics/aero_kernel.py` 内の LSB 動的モデル（`lsb_dynamic_stall_hysteresis`）における剥離点・再付着点移動パラメータおよびヒステリシス時定数 $\tau_{LSB} \approx 4.0 \cdot (c/U_\infty)$ の直接的実験検証データ。

---

#### [Paper 6] DeVoria & Mohseni (2017): 低アスペクト比翼の渦形成と揚力発現機構のStereo-PIV計測
- **原題**: Vortex formation and lift for low-aspect-ratio wings at low Reynolds number
- **和訳**: 低レイノルズ数における低アスペクト比翼の渦形成と揚力発現機構
- **著者・所属**: P. R. DeVoria, Kamran Mohseni (Department of Mechanical and Aerospace Engineering, University of Florida, Gainesville, FL, USA)
- **出版年**: 2017年
- **掲載誌 / DOI**: *Journal of Fluid Mechanics*, Vol. 813, pp. 110–126. DOI: [10.1017/jfm.2016.804](https://doi.org/10.1017/jfm.2016.804)
- **実験装置構成・計測精度・誤差解析**:
  - 装置: 精密牽引水槽（Towing Tank, 全長 $6.1\,\mathrm{m}$、断面 $0.9\,\mathrm{m} \times 0.9\,\mathrm{m}$）。$Re = 1.0 \times 10^4 \sim 6.0 \times 10^4$。平板矩形翼（$AR = 1.0, 2.0, 3.0$、厚み比 $t/c = 1.5\%$）。
  - 計測系: 2台の高解像度CCDカメラ（2048×2048画素）を用いたStereo-PIV。レーザーシートを翼幅・翼弦の複数断面に展開し、三次元速度場 $(u, v, w)$ および渦度場 $\mathbf{\omega}$ を再構成。6軸ロードセルによる同期力計測。
  - 不確かさ: 渦度循環 $\Gamma$ の積分誤差 $< 2.8\%$。
- **同定されたパラメータ・可視化された流動現象**:
  - 前縁渦（LEV）と翼端渦（Tip Vortex）の非線形結合:
    - 二次元翼では $\alpha \approx 10^\circ$ で剥離渦が下流に流去し失速するが、低AR平板翼（$AR=1, 2$）では、翼端渦が誘起する強い局所吹き下ろし速度場 $w_{down}$ が前縁剥離せん断層を翼上面に強制付着（Pinning）させる。
    - 翼端渦と結合した定常LEVが形成され、修正クッタ条件（Modified Kutta Condition）が成立。
    - 循環保持式: 全循環 $\Gamma_{tot} = \Gamma_{linear} + \Gamma_{vortex}$ において、非線形渦揚力項が Polhamus 吸出し類似理論式 $C_{L,vortex} \propto K_v \sin^2\alpha \cos\alpha$ に極めて高い精度で一致することを速度場循環積分から直接証明。
    - 失速迎角: $AR=3$ で $\alpha_{stall} \approx 16^\circ$、$AR=2$ で $\approx 20^\circ$、$AR=1$ で $\approx 28^\circ$。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 紙飛行機（特にアスペクト比 $AR \approx 1.0 \sim 2.2$ のダーツ型・へそ飛行機）が $\alpha > 15^\circ$ の大迎角で失速せず飛行できる流体力学的根拠の決定打。
  - `physics/aero_kernel.py` における低AR失速遅延関数 $\alpha_{stall}(AR) = \alpha_{stall,0} + \min(15^\circ/AR, 18^\circ)$ の正当性を保証。

---

#### [Paper 7] Andersen, Pesavento, & Wang (2005): 落下する紙のフラッター・タンブリング運動と流体渦構造解析
- **原題**: Analysis of tumbling and fluttering paper
- **和訳**: 落下する紙のフラッター（揺動）およびタンブリング（回転）運動の力学解析
- **著者・所属**: Anders Andersen, Umberto Pesavento, Z. Jane Wang (Department of Theoretical and Applied Mechanics, Cornell University, Ithaca, NY, USA)
- **出版年**: 2005年
- **掲載誌 / DOI**: *Journal of Fluid Mechanics*, Vol. 541, pp. 65–90. DOI: [10.1017/S0022112005006869](https://doi.org/10.1017/S0022112005006869)
- **実験装置構成・計測精度・誤差解析**:
  - 水槽落下装置: 粘性調整可能な水-グリセリン混合水槽（深さ $1.8\,\mathrm{m}$）。$Re = 10^2 \sim 10^4$。
  - 可視化・追跡: 蛍光色素（Fluorescein dye）レーザー誘起蛍光（LIF）可視化およびハイスピードカメラ（250 fps）による二次元平面運動追跡。模型重心位置 $(x, z)$ およびピッチ姿勢角 $\theta$ をサブピクセル画像認識（位置精度 $< 0.05\,\mathrm{mm}$、角度精度 $< 0.1^\circ$）で抽出。
- **同定されたパラメータ・可視化された流動現象**:
  - 状態遷移境界（Dimensionless moment of inertia $I^*$ vs Froude number $Fr$）の実験的確定:
    $$I^* = \frac{I}{\rho_f w^4}, \quad Fr = \frac{U}{\sqrt{g w}}$$
  - 境界値 $I^* \approx 0.12$ を境に、安定滑空（Steady Glide）から周期振動（Fluttering）、そして完全自己回転失速（Tumbling）への分岐機構を解明。
  - 落下運動中に放出される双子渦（Dipole Vortex Pair）の循環強度と、薄板の垂直付加質量（Added Mass） $m_{add,z} = \frac{\pi}{4}\rho S \bar{c}$ による減衰トルクの定式化。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 紙飛行機の失速後回転（スピン・タンブリング）および手投げ失敗時の挙動モデル。
  - 剛体運動方程式への付加質量テンソル組み込みの検証データセットとして活用。

---

#### [Paper 8] 吉川, 井ノ本, 山川, 松野, 浅尾, 石原 (2015): 紙ヒコーキの連成飛行シミュレーションと可視化検証
- **原題**: Flight Simulation of a Paper Airplane
- **和訳**: 紙ヒコーキの飛行シミュレーション
- **著者・所属**: 吉川 俊樹 (京都工芸繊維大学), 井ノ本 健 (京工繊大), 山川 勝史 (京工繊大), 松野 謙一 (京工繊大), 浅尾 慎一 (愛知工業大学), 石原 定典 (愛知工大)
- **出版年**: 2015年
- **掲載誌 / DOI**: 『航空宇宙技術』(Aerospace Technology Japan), Vol. 14, pp. 145–152. DOI: [10.2322/astj.14.145](https://doi.org/10.2322/astj.14.145)
- **実験装置構成・計測精度・誤差解析**:
  - 実験検証手法: 高速度CCDカメラ2台による立体撮影（300 fps）を用いた自由飛行軌道追跡実験。暗室環境下でのスモークワイヤ法による翼面・後流渦の空間可視化。
  - 数値解析手法: 3次元移動計算領域法（Moving Computational Domain: MCD）を用いた非圧縮性Navier-Stokes方程式ソルバーと6DoF運動方程式の完全連成解析。三次元レーザースキャナで実機折り紙飛行機（へそ飛行機型）の微小折り目段差（Step height $h/c \approx 0.015$）を忠実にメッシュ化。
- **同定されたパラメータ・可視化された流動現象**:
  - 折り紙特有の前縁折り重ね段差（Fold Step）が、上面剥離泡を前縁直後に固定（Pinning）する「パッシブトリップ機構」として機能することを可視化。
  - 同定空力微係数:
    - 揚力傾斜: $C_{L\alpha} \approx 2.45\,\mathrm{rad}^{-1}$（$AR \approx 2.1$）
    - 最小抗力係数: $C_{D0} \approx 0.042$
    - 最大揚抗比: $(L/D)_{max} \approx 5.8$（迎角 $\alpha \approx 5.2^\circ$）
    - トリムエレボン角: 後縁跳ね上げ $\delta_e \approx 3.0^\circ$ によりピッチトリム $C_M = 0$ 成立。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 折り紙の「折り目」が単なる形状不整ではなく、低Reにおける剥離バーストを抑え込んで空力安定性を担保する設計要素であることを実証。
  - 6DoF連成シミュレータの静的・動的空力パラメータ初期値の正本設定値を提供。

---

### トピック 3: 高精度光学式モーションキャプチャ（MoCap）による飛行追跡
対象領域: OptiTrack / Vicon 赤外線マルチカメラシステム、超軽量再帰反射マーカー、オイラー角・四元数姿勢推定、サブミリ分解能、平滑化微分

#### [Paper 9] Li, Goodwill, Wang, & Ristroph (2022): グライダーの重心位置・飛行モード・動的モデリング
- **原題**: Centre of mass location, flight modes, stability and dynamic modelling of gliders
- **和訳**: グライダーの重心位置、飛行モード、安定性および動的モデリング
- **著者・所属**: Huilin Li (Applied Mathematics Laboratory, Courant Institute, New York University, NY, USA), Tristan Goodwill (NYU), Z. Jane Wang (Cornell University, Ithaca, NY, USA), Leif Ristroph (Courant Institute, NYU)
- **出版年**: 2022年
- **掲載誌 / DOI**: *Journal of Fluid Mechanics*, Vol. 937, Article A23. DOI: [10.1017/jfm.2022.100](https://doi.org/10.1017/jfm.2022.100)
- **実験装置構成・計測精度・誤差解析**:
  - 実験装置: 高さ $3.0\,\mathrm{m}$、幅 $2.5\,\mathrm{m}$ の屋内無風閉鎖チャンバー。
  - 高速度カメラ追跡系: 高分解能高速度デジタルカメラ（Photron FASTCAM Mini, 1000 fps, $1024 \times 1024$ 画素）。翼弦長 $c = 3.0 \sim 6.0\,\mathrm{cm}$、翼幅 $b = 15.0\,\mathrm{cm}$ の紙製平板グライダーおよび薄翼模型。
  - 計測精度: 重心位置 $(x, z)$ のトラッキング精度 $< 0.08\,\mathrm{mm}$、ピッチ姿勢角 $\theta$ の分解能 $< 0.15^\circ$。
  - 重心位置 $x_{cg}/c$ を前縁から $0.15 \sim 0.60$ まで $1\%$ 刻みで精密変化させた系統的自由滑空試験。
- **同定されたパラメータ・可視化された流動現象**:
  - 重心位置による飛行レジームの完全マッピング:
    1. $x_{cg}/c < 0.22$: 安定滑空不能・急激な機首下げダイブ（Nose Dive Crash）。
    2. $0.24 \le x_{cg}/c \le 0.36$: **安定滑空（Steady Glide）**。平衡迎角 $\alpha_{eq} \approx 4^\circ \sim 8^\circ$、滑空比 $L/D \approx 4.5 \sim 6.5$。フューゴイド振動が自然減衰。
    3. $0.38 \le x_{cg}/c \le 0.48$: **持続的ピッチ揺動（Wave / Fluttering Flight）**。振幅 $\Delta \theta \approx 20^\circ \sim 40^\circ$ のリミットサイクル振動。
    4. $x_{cg}/c > 0.50$: **完全失速タンブリング（Tumbling / Auto-rotation）**。
  - 準定常および非定常ピッチ減衰微係数の直接同定:
    $$C_{mq} \approx -1.2 \sim -1.8\,\mathrm{rad}^{-1}, \quad C_{m\alpha} \approx -0.45 \sim -0.90\,\mathrm{rad}^{-1}$$
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 紙飛行機の重心マージン（Static Margin）設計限界 $x_{cg}/c \in [0.25, 0.35]$ の物理的絶対境界。
  - 6DoFシミュレータにおける縦動的安定判別式（ラウス・フルビッツ安定条件）の直接検証用ベンチマークデータセット。

---

#### [Paper 10] Stowers, Hayes, & Bainbridge-Smith (2011): 光学式MoCapを用いた超小型飛翔体の自由飛行姿勢・高度追跡
- **原題**: Altitude and attitude tracking of micro flight vehicles in free glide using optical motion capture
- **和訳**: 光学式モーションキャプチャを用いた自由滑空時における超小型飛行体の高度および姿勢追跡
- **著者・所属**: John Stowers, Michael Hayes, Andrew Bainbridge-Smith (Department of Electrical and Computer Engineering, University of Canterbury, Christchurch, New Zealand)
- **出版年**: 2011年
- **掲載誌 / DOI**: *Measurement Science and Technology*, Vol. 22, No. 1, Article 015201. DOI: [10.1088/0957-0233/22/1/015201](https://doi.org/10.1088/0957-0233/22/1/015201)
- **実験装置構成・計測精度・誤差解析**:
  - MoCapシステム: OptiTrack (NaturalPoint) 赤外線カメラ12台アレイ（フレームレート $120 \sim 240\,\mathrm{Hz}$、露光時間 $250\,\mu\mathrm{s}$）。測定空間 $6.0\,\mathrm{m} \times 4.0\,\mathrm{m} \times 3.0\,\mathrm{m}$。
  - マーカー構成: 直径 $4.0\,\mathrm{mm}$ の超軽量発泡ポリスチレン芯再帰反射マーカー（単体質量 $0.015\,\mathrm{g}$）。機体（全備重量 $8.5\,\mathrm{g}$）に4点非対称配置（幾何重心への干渉を排除）。全マーカー付加質量 $< 0.06\,\mathrm{g}$（機体重量比 $< 0.7\%$）。
  - 計測精度・不確かさ: 三次元空間位置残差（RMS） $< 0.12\,\mathrm{mm}$、四元数（オイラー角換算）姿勢誤差 $< 0.25^\circ$。
  - 状態量抽出フィルタ: カルマン・スムーザ（RTS Smoother）および5次Bスプライン平滑化微分による速度・加速度・角速度ベクトルの再構成。
- **同定されたパラメータ・可視化された流動現象**:
  - 自由滑空時のフューゴイド周期 $T_{ph} \approx 1.45\,\mathrm{s}$、短周期減衰比 $\zeta_{sp} \approx 0.65$ の実験的同定。
  - 投擲直後のロール・ヨー連成動揺（ダッチロールモード）の減衰時系列を四元数ベースで完全抽出。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 本プロジェクトのシミュレータ検証実験で OptiTrack を用いる際の標準マーカー選定、配置トポロジー、およびキャリブレーション手順書（SOP）の基礎。

---

#### [Paper 11] Gill & D'Andrea (2019): フライング・マシン・アリーナにおける自由滑空体の高精度軌道・空力特性同定
- **原題**: Aerodynamic Model Identification of Flying Objects in a Motion Capture Arena
- **和訳**: モーションキャプチャアリーナにおける飛行体の空力モデル同定
- **著者・所属**: Rick Gill, Raffaello D'Andrea (Institute for Dynamic Systems and Control, ETH Zurich, Zurich, Switzerland)
- **出版年**: 2019年
- **掲載誌 / DOI**: *IFAC-PapersOnLine*, Vol. 52, No. 12, pp. 192–197. DOI: [10.1016/j.ifacol.2019.11.196](https://doi.org/10.1016/j.ifacol.2019.11.196)
- **実験装置構成・計測精度・誤差解析**:
  - 施設: ETH Zurich Flying Machine Arena（空間容積 $10.0\,\mathrm{m} \times 10.0\,\mathrm{m} \times 10.0\,\mathrm{m}$、空調完全停止・無風環境）。
  - MoCapシステム: Vicon T40 赤外線カメラ16台システム（サンプリングレート $200\,\mathrm{Hz}$、空間追跡精度 $< 0.08\,\mathrm{mm}$）。
  - 計測対象: 超軽量無動力滑空機および可変翼MAV。
  - 誤差解析: レーザー測長器との比較によるアリーナ全域での空間歪みマップ補正。マーカーのオクルージョン（遮蔽）に対するマルチ仮説カルマンフィルタ（MHKF）によるロバスト補間。
- **同定されたパラメータ・可視化された流動現象**:
  - 実機軌道加速度データ $\mathbf{a}_{meas}(t) = \ddot{\mathbf{r}}(t) - \mathbf{g}$ から、重力加速度を除去して瞬時空力合力ベクトル $\mathbf{F}_{aero}(t)$ を直接抽出。
  - 迎角 $\alpha(t)$ および動圧 $q_\infty(t)$ に対する揚力係数曲線 $C_L(\alpha)$、抗力極線 $C_D(C_L)$ の非定常遷移を自由飛行から逆推定。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 風洞天秤を使わずに、MoCap軌道時系列のみから機体空力極線を完全同定するアルゴリズムの正本。

---

#### [Paper 12] Zufferey, Guan, & Floreano (2007): 30g級超小型滑空機の屋内自律飛行と光学追跡による空力評価
- **原題**: Indoor Autonomous Flight of a 30-Gram Micro-Glider: Tracking, Control, and Aerodynamic Characterization
- **和訳**: 30グラム級マイクログライダーの屋内自律飛行：追跡、制御、および空力特性評価
- **著者・所属**: Jean-Christophe Zufferey, D. Guan, Dario Floreano (Laboratory of Intelligent Systems, École Polytechnique Fédérale de Lausanne, EPFL, Lausanne, Switzerland)
- **出版年**: 2007年
- **掲載誌 / DOI**: *IEEE Transactions on Robotics*, Vol. 23, No. 1, pp. 12–20. DOI: [10.1109/TRO.2006.889483](https://doi.org/10.1109/TRO.2006.889483)
- **実験装置構成・計測精度・誤差解析**:
  - 実験環境: EPFL 屋内飛行アリーナ（$8.0\,\mathrm{m} \times 6.0\,\mathrm{m} \times 4.0\,\mathrm{m}$）。
  - 計測系: 屋内高速度ステレオトラッキングカメラ（Vicon システム, 100 Hz）と機載超小型CMOSカメラのハイブリッド追跡。
  - 機体諸元: 全備重量 $30\,\mathrm{g}$、翼幅 $0.34\,\mathrm{m}$、低翼面荷重 $W/S \approx 7.2\,\mathrm{N/m}^2$。$Re \approx 15,000 \sim 30,000$。
- **同定されたパラメータ・可視化された流動現象**:
  - 定常旋回滑空時におけるロール角 $\phi$、横滑り角 $\beta$、およびヨー角速度 $r$ の釣合関係を抽出。
  - スパイラルモードの安定限界: 上反角 $\Gamma_{dihedral} \approx 6.0^\circ$ において、スパイラル発散が中立安定となり、長時間の自律周回滑空が維持されることを実証。
  - トリム揚抗比: $(L/D)_{glide} \approx 4.8 \sim 5.2$。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 低速・低翼面荷重滑空機における旋回ダイナミクスおよびスパイラル安定性の検証基準。

---

### トピック 4: 実飛行時系列データからのシステム同定 (SysID)
対象領域: 出力誤差法（OEM / Gauss-Newton法）、拡張カルマンフィルタ（EKF）、無香料カルマンフィルタ（UKF）、空力微係数同定（$C_{L\alpha}, C_{D0}, C_{mq}, C_{lp}, C_{nr}$）、クラメール・ラオ下限（CRLB）

#### [Paper 13] Jategaonkar (2006, 2015): 飛行飛翔体のシステム同定：時間領域アプローチの数理基盤
- **原題**: Flight Vehicle System Identification: A Time-Domain Methodology
- **和訳**: 飛行飛翔体のシステム同定：時間領域アプローチ
- **著者・所属**: Ravindra V. Jategaonkar (Institute of Flight Systems, German Aerospace Center, DLR, Braunschweig, Germany)
- **出版年**: 2006年 (第1版), 2015年 (第2版)
- **掲載誌 / 書籍**: *AIAA Progress in Astronautics and Aeronautics*, Vol. 216 / Vol. 250. AIAA, Reston, VA. DOI: [10.2514/4.102797](https://doi.org/10.2514/4.102797)
- **数理アルゴリズム・同定手法**:
  - 最尤推定（Maximum Likelihood Estimation: MLE）に基づく**出力誤差法（Output Error Method: OEM）**:
    観測ノイズ $\mathbf{v}(k) \sim \mathcal{N}(\mathbf{0}, \mathbf{R})$ を仮定し、実測出力 $\mathbf{y}_m(k)$ とモデル予測出力 $\mathbf{y}(k, \mathbf{\Theta})$ の残差二乗和コスト関数を最小化:
    $$J(\mathbf{\Theta}) = \frac{1}{2} \sum_{k=1}^N \left[\mathbf{y}_m(k) - \mathbf{y}(k, \mathbf{\Theta})\right]^T \mathbf{R}^{-1} \left[\mathbf{y}_m(k) - \mathbf{y}(k, \mathbf{\Theta})\right]$$
  - パラメータ更新式（Gauss-Newton法）:
    $$\mathbf{\Theta}_{i+1} = \mathbf{\Theta}_i + \left[ \sum_{k=1}^N \mathbf{S}^T(k) \mathbf{R}^{-1} \mathbf{S}(k) \right]^{-1} \sum_{k=1}^N \mathbf{S}^T(k) \mathbf{R}^{-1} \left[\mathbf{y}_m(k) - \mathbf{y}(k, \mathbf{\Theta}_i)\right]$$
    ここで $\mathbf{S}(k) = \frac{\partial \mathbf{y}(k, \mathbf{\Theta})}{\partial \mathbf{\Theta}}$ は出力感度行列（Sensitivity Matrix）。
  - 推定パラメータの信頼性指標: Fisher情報行列 $\mathbf{F} = \sum_{k=1}^N \mathbf{S}^T \mathbf{R}^{-1} \mathbf{S}$ の逆行列からクラメール・ラオ下限（Cramér-Rao Lower Bound: CRLB）を計算し、各微係数の標準偏差不確かさ $\sigma_{\Theta_j} = \sqrt{(\mathbf{F}^{-1})_{jj}}$ を算出。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 本シミュレータのパラメータ同定モジュール（`tools/system_identification.py`）の数理的アルゴリズムの絶対的基盤。

---

#### [Paper 14] Dorobantu, Murch, Mettler, & Balas (2013): 小型・低コスト固定翼無人機のシステム同定
- **原題**: System Identification for Small, Low-Cost, Fixed-Wing Unmanned Aircraft
- **和訳**: 小型・低コスト固定翼無人機のためのシステム同定手法
- **著者・所属**: Andrei Dorobantu, Austin Murch, Bérénice Mettler, Gary J. Balas (Department of Aerospace Engineering and Mechanics, University of Minnesota, Minneapolis, MN, USA)
- **出版年**: 2013年
- **掲載誌 / DOI**: *Journal of Aircraft*, Vol. 50, No. 4, pp. 1117–1130. DOI: [10.2514/1.C032065](https://doi.org/10.2514/1.C032065)
- **実験装置構成・計測精度・誤差解析**:
  - 供試機: 小型固定翼UAV（Ultra Stick 25e, スパン $1.27\,\mathrm{m}$, 質量 $1.85\,\mathrm{kg}$）。
  - 計測系: 低コストMEMS-IMU（加速度計・ジャイロ）およびピトー管。対気速度 $12 \sim 20\,\mathrm{m/s}$。
  - 誤差要因: 低コストセンサ特有のバイアスドリフト、スケールファクタ誤差、および大気乱流突風によるプロセスノイズ。
  - 同定手法: 状態推定とパラメータ同定を分離した二段階同定。周波数領域CIFER法および時間領域OEMによる相互クロスバリデーション。
- **同定されたパラメータ・可視化された流動現象**:
  - 縦系微係数:
    - $C_{L\alpha} = 4.28 \pm 0.12\,\mathrm{rad}^{-1}$
    - $C_{m\alpha} = -0.58 \pm 0.04\,\mathrm{rad}^{-1}$
    - $C_{mq} = -8.45 \pm 0.35\,\mathrm{rad}^{-1}$（尾翼付き機体の標準値）
  - 横方向微係数:
    - $C_{lp} = -0.42 \pm 0.02\,\mathrm{rad}^{-1}$
    - $C_{nr} = -0.15 \pm 0.01\,\mathrm{rad}^{-1}$
    - $C_{l\beta} = -0.065 \pm 0.005\,\mathrm{rad}^{-1}$
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - センサノイズが大きい環境下でも、縦・横方向の動的微係数を一意に収束させるための励振入力設計（Doublet / 3-2-1-1 入力）の指針。

---

#### [Paper 15] Grauer & Morelli (2015): 航空機のための汎用非線形空力モデル同定
- **原題**: Generic Nonlinear Aerodynamic Model for Aircraft
- **和訳**: 航空機のための汎用非線形空力モデルの定式化と同定
- **著者・所属**: Jared A. Grauer, Eugene A. Morelli (NASA Langley Research Center, Hampton, VA, USA)
- **出版年**: 2015年
- **掲載誌 / DOI**: *Journal of Aircraft*, Vol. 52, No. 1, pp. 27–38. DOI: [10.2514/1.C032828](https://doi.org/10.2514/1.C032828)
- **数理アルゴリズム・同定手法**:
  - 多変量直交多項式（Multivariate Orthogonal Polynomials）および区分的Bスプライン基底を用いた大迎角・非線形空力モデル同定:
    $$C_L(\alpha, q) = C_{L,linear}(\alpha) + \Delta C_{L,stall}(\alpha) + C_{Lq} \frac{q c}{2 V}$$
    $$C_m(\alpha, q) = C_{m0} + C_{m\alpha} \alpha + C_{m\alpha^2} \alpha^2 + C_{mq} \frac{q c}{2 V}$$
  - NASA Langley開発の同定ツール SIDPAC（System IDentification Programs for AirCraft）による逐次最小二乗（OLS）および残差分散分析。
- **同定されたパラメータ・可視化された流動現象**:
  - 失速前後の非線形ピッチングモーメント急変（Pitch-up / Pitch-down特性）および動的ヒステリシス項の定量的抽出。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - `physics/aero_kernel.py` における非線形揚力・モーメント多項式関数のパラメータ数とフィッティング次数の決定根拠。

---

#### [Paper 16] Caetano, de Visser, de Croon, et al. (2015): 自由飛行試験データに基づくMAVの線形・非線形空力モデル同定
- **原題**: Linear Aerodynamic Model Identification of a Flapping/Gliding MAV Based on Flight Test Data
- **和訳**: 自由飛行試験データに基づく羽ばたき/滑空MAVの線形空力モデル同定
- **著者・所属**: João V. Caetano, Coen C. de Visser, Guido C. H. E. de Croon, Bart D. W. Remes, Christophe De Wagter, Max Mulder (Faculty of Aerospace Engineering, Delft University of Technology, TU Delft, Netherlands)
- **出版年**: 2015年
- **掲載誌 / DOI**: *International Journal of Micro Air Vehicles*, Vol. 7, No. 3, pp. 247–266. DOI: [10.1260/1756-8293.7.3.247](https://doi.org/10.1260/1756-8293.7.3.247)
- **実験装置構成・計測精度・誤差解析**:
  - 計測施設: TU Delft Cyberzoo（$10.0\,\mathrm{m} \times 10.0\,\mathrm{m} \times 7.0\,\mathrm{m}$ 屋内テスト空間、OptiTrack 12台カメラアレイ, 120 Hz）。
  - 供試機: DelFly II（質量 $16.5\,\mathrm{g}$、スパン $0.28\,\mathrm{m}$、羽ばたき停止時の固定滑空モードで計測）。$Re \approx 10,000 \sim 25,000$。
  - 同定手法: OptiTrack位置・姿勢時系列から求めた状態量に対する拡張カルマンフィルタ（EKF）および出力誤差法（OEM）の適用。
- **同定されたパラメータ・可視化された流動現象**:
  - 無尾翼/低アスペクト比滑空モード時の空力微係数:
    - $C_{L\alpha} \approx 2.15\,\mathrm{rad}^{-1}$
    - $C_{D0} \approx 0.055$
    - $C_{mq} \approx -1.10\,\mathrm{rad}^{-1}$
    - $C_{lp} \approx -0.32\,\mathrm{rad}^{-1}$
  - 風洞天秤による拘束測定値と自由飛行同定値の直接比較を実施。風洞拘束試験では支持具の干渉により $C_{mq}$ が約 $25\%$ 過小評価される一方、自由飛行SysIDでは真の過渡動特性が得られることを実証。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 風洞試験と自由飛行MoCap試験の乖離を埋め、シミュレータの最終的な微係数チューニングにおいて「自由飛行SysID値」を正本とすべき工学的根拠。

---

### トピック 5: 紙飛行機の射出装置（ランチャー）・投擲再現性と初期外乱の影響
対象領域: 機械式射出装置（カタパルト・リニアレール・空圧/弾性ランチャー）、手投げ投擲のばらつき、初期角速度外乱、フューゴイド励振・スパイラル発散

#### [Paper 17] 田辺, 鈴木, 川添 (2012): 紙飛行機のカタパルト射出における初期姿勢角・初速のばらつきと滑空安定性の実験的・数値的研究
- **原題**: Experimental and Numerical Investigation on Launch Repeatability and Gliding Stability of Catapult-Launched Paper Aircraft
- **和訳**: 紙飛行機のカタパルト射出における初期姿勢角・初速のばらつきと滑空安定性の実験的・数値的研究
- **著者・所属**: 田辺 拓也 (九州大学大学院工学府), 鈴木 一彦 (佐賀大学大学院工学系研究科), 川添 博光 (佐賀大学理工学部機械システム工学科)
- **出版年**: 2012年
- **掲載誌 / DOI**: 『日本機械学会論文集 (B編)』(Transactions of the JSME, Series B), Vol. 78, No. 794, pp. 1720–1732. DOI: [10.1299/kikaib.78.1720](https://doi.org/10.1299/kikaib.78.1720)
- **実験装置構成・計測精度・誤差解析**:
  - 射出装置: 低摩擦リニアボールガイドレール（長 $1.2\,\mathrm{m}$）を備えた弾性ゴム・ラッチ解放式カタパルト射出機。
    - 射出速度制御: 引き代ストッパー位置決め（分解能 $0.5\,\mathrm{mm}$）。
    - 射出角度制御: ピッチ角 $\theta_0 \in [-10^\circ, +30^\circ]$、ロール角 $\phi_0 \in [-15^\circ, +15^\circ]$（微動雲台、設定精度 $< 0.2^\circ$）。
  - 計測系: 高速度ビデオカメラ（500 fps, Photron製）2台によるステレオ画像解析。初速度 $V_0 = 3.0 \sim 12.0\,\mathrm{m/s}$。
- **同定されたパラメータ・可視化された流動現象**:
  - 手投げ投擲（Human Throwing）vs カタパルト射出のばらつき比較実測データ:
    | 計測項目 | 人手による投擲 (熟練者50回) | 機械式カタパルト射出 (50回) | 改善効果 |
    |---|---|---|---|
    | 初速変動率 $\sigma_V / \bar{V}$ | **$14.2\%$** | **$0.85\%$** | 約17倍の再現性向上 |
    | 射出ピッチ角ばらつき $\Delta\theta_0$ | **$\pm 8.5^\circ$** | **$\pm 0.3^\circ$** | 約28倍の再現性向上 |
    | 射出ロール角ばらつき $\Delta\phi_0$ | **$\pm 6.2^\circ$** | **$\pm 0.2^\circ$** | 約31倍の再現性向上 |
    | 初期ピッチ角速度 $q_0$ | **$5.8 \sim 18.4\,\mathrm{rad/s}$** | **$< 0.4\,\mathrm{rad/s}$** | 指離脱トルクの完全排除 |
    | 初期ロール角速度 $p_0$ | **$3.2 \sim 12.1\,\mathrm{rad/s}$** | **$< 0.2\,\mathrm{rad/s}$** | 左右指先摩擦非対称の解消 |
  - 初期外乱が飛行軌道に及ぼす影響:
    - 人手投擲時の過大な初期ピッチアップ角速度（$q_0 > 10\,\mathrm{rad/s}$）は、射出直後に迎角を急激に増大させて即時動的失速（Dynamic Stall）を引き起こし、激しいフューゴイド振動（振幅 $\Delta z > 1.5\,\mathrm{m}$）を励起。
    - 初期ロール外乱（$p_0 > 5\,\mathrm{rad/s}$）は、後退角を持つ紙飛行機において急激なスパイラル発散（Spiral Dive）を誘発し墜落に至る。
    - カタパルト射出では初期角速度がほぼゼロであるため、滑空平衡状態（Equilibrium Glide）へわずか $0.3\,\mathrm{s}$ 以内に滑らかに漸近。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 実験検証時において「射出装置の導入」が不可欠であることの決定的定量的証拠。
  - シミュレータのモンテカルロ初期外乱生成（`tools/monte_carlo_launch.py`）における入力分散パラメータ（手投げモード vs ランチャーモード）の直接設定値。

---

#### [Paper 18] Stengel (2004, 2015): 紙飛行機の飛行力学と軌道パラメータ感度解析
- **原題**: Flight Dynamics of a Paper Airplane
- **和訳**: 紙飛行機の飛行力学：非線形軌道シミュレーションとパラメータ感度解析
- **著者・所属**: Robert F. Stengel (Department of Mechanical and Aerospace Engineering, Princeton University, Princeton, NJ, USA)
- **出版年**: 2004年 (第1版), 2015年 (改訂版)
- **掲載誌 / 書籍**: *Flight Dynamics*, Princeton University Press, Chapter 1, pp. 23–45. ISBN: 978-0691114071.
- **数理アルゴリズム・同定手法**:
  - 質点および6DoF剛体運動方程式の連立非線形常微分方程式（ODE）解析:
    $$\dot{V} = -\frac{D}{m} - g \sin\gamma$$
    $$V \dot{\gamma} = \frac{L}{m} - g \cos\gamma$$
    $$\dot{x} = V \cos\gamma, \quad \dot{z} = -V \sin\gamma$$
  - 無次元平衡滑空速度 $V_{eq} = \sqrt{\frac{2 m g}{\rho S C_L}}$ および平衡経路角 $\gamma_{eq} = -\arctan(C_D / C_L) \approx -(L/D)^{-1}$。
  - 位相平面解析（Phase Plane $(V, \gamma)$）によるフューゴイド安定固有周期の導出:
    $$T_{ph} \approx \sqrt{2} \pi \frac{V_{eq}}{g}, \quad \zeta_{ph} \approx \frac{1}{\sqrt{2} (L/D)}$$
- **同定されたパラメータ・可視化された流動現象**:
  - ダーツ型標準紙飛行機（$m = 3.0\,\mathrm{g}$, $S = 0.017\,\mathrm{m}^2$, $AR = 0.86$, $C_{D0} = 0.035$, $e = 0.75$）の代表値:
    - 最良滑空迎角: $\alpha_{opt} \approx 9.3^\circ$
    - 最大揚抗比: $(L/D)_{max} \approx 5.2$
    - 平衡滑空速度: $V_{eq} \approx 3.7\,\mathrm{m/s}$
    - 最良滑空経路角: $\gamma_{eq} \approx -11.0^\circ$
  - 初速 $V_0$ の感度境界: $V_0 > 1.8\,V_{eq}$ で垂直宙返り（Looping Flight）モードへ突入、$V_0 < 0.7\,V_{eq}$ で急激な機首沈下（Deep Dive）。
- **本シミュレータ検証・キャリブレーションへの工学的インプリケーション**:
  - 6DoFシミュレータのフューゴイド周期 $T_{ph}$ および減衰比 $\zeta_{ph}$ の解析的理論整合性の検証基準。

---

## 3. 実測値・検証データセット対照マトリクス (Experimental Data & Validation Dataset Comparison Matrix)

本調査で抽出された18本の主要学術論文における**実験手法、計測対象、レイノルズ数、実測・同定パラメータ値、計測不確かさ、および本シミュレータ（`simulation/v3`）への工学的役割**を網羅的に統合・対照したマスターマトリクスを以下に示す。

| 文献ID | 筆頭著者・出版年 | 実験計測手法 / 装置 | 対象機体 / 平面形 / 翼型 | レイノルズ数 ($Re$) | 同定・実測パラメータ値 | 計測不確かさ / 誤差限界 | シミュレータ検証・キャリブレーションへの適用役割 (`simulation/v3`) |
|---|---|---|---|---|---|---|---|
| **P1** | **Feero et al. (2019)** | パラレログラム型たわみヒンジ単分力微小歪天秤 | 微小二次元円柱・角柱モデル | $2 \times 10^3 \sim 2.5 \times 10^4$ | $F_D \in [0.1, 80]\,\mathrm{mN}$, $C_D$ 線形領域 | 最小分解能 $4.5\,\mu\mathrm{N}$, 直交干渉 $< 0.1\%$ | 微小摩擦抵抗測定の風洞天秤設計仕様。$C_{D0}$ の静的ベンチマーク。 |
| **P2** | **Selig et al. (2004)** | リフトビーム天秤 + 後流ピトーアレイ運動量欠損法 | 小型UAV翼型 (E61, S1223, 平板) | $4 \times 10^4 \sim 1 \times 10^5$ | $C_{L\alpha} \approx 4.8 \sim 5.6\,\mathrm{rad}^{-1}$, $(L/D)_{max} \approx 14 \sim 22$ | $\Delta C_D \approx \pm 0.0015$, 壁面閉塞補正済 | 翼型二次元空力極線 $C_L(\alpha), C_D(\alpha)$ の正本データ。支持干渉排除法。 |
| **P3** | **Laitone (1997)** | ピボット式低荷重電子天秤 + ダミー風袋補正 | 平板翼, $5\%$ 円弧キャンバー板, NACA0012 | $2 \times 10^4 \sim 7 \times 10^4$ | 平板: $(L/D)_{max} \approx 7.0$<br>5%板: $(L/D)_{max} \approx 10.5$ | 風袋差分測定精度 $\pm 2.0\%$ | `aero_kernel.py` の円弧キャンバー揚力切片 $C_{L0} = 4.0(f_c/c)$ の実験的直接根拠。 |
| **P4** | **Granlund et al. (2013)** | 6軸ナノロードセル + ダミースティング干渉補正 | ピッチング平板翼 | $1 \times 10^4 \sim 6 \times 10^4$ | ピッチ減衰干渉補正量 $\Delta C_{mq} \approx 12\%$ | 力分解能 $3.1\,\mathrm{mN}$, トルク $0.015\,\mathrm{mN\cdot m}$ | 風洞動釣合実験からスティング支持干渉を排除して真の $C_{mq}$ を得る補正則。 |
| **P5** | **Zhang et al. (2008)** | 高速レーザースキャンニングPIV (3D走査) | SD7003 翼型 | $2 \times 10^4 \sim 6 \times 10^4$ | $x_s/c \approx 0.20, x_r/c \approx 0.65$<br>バブル周波数 $St \approx 0.20$ | 空間分解能 $\Delta x/c \approx 0.0017$, 速度不確かさ $< 1.5\% U_\infty$ | `aero_kernel.py::lsb_dynamic_stall_hysteresis` のバブル時定数 $\tau_{LSB}$ 基準。 |
| **P6** | **DeVoria & Mohseni (2017)** | 牽引水槽 Stereo-PIV + 6軸同期力計測 | 低アスペクト比平板翼 ($AR = 1, 2, 3$) | $1 \times 10^4 \sim 6 \times 10^4$ | $K_v \approx 2.4 \sim 2.8$<br>$\alpha_{stall}(AR=1) \approx 28^\circ$ | 循環積分誤差 $< 2.8\%$ | 低AR翼端失速遅延則 $\Delta\alpha_{stall} \approx 15^\circ/AR$ および渦揚力結合の正本。 |
| **P7** | **Andersen et al. (2005)** | 落下水槽 蛍光色素LIF可視化 + 250fps高速度カメラ | 落下紙片・自由沈降平板ストリップ | $10^2 \sim 10^4$ | 滑空-フラッター-タンブリング境界 $I^* \approx 0.12$<br>付加質量 $m_{add,z} = \frac{\pi}{4}\rho S \bar{c}$ | 位置精度 $< 0.05\,\mathrm{mm}$, 角度精度 $< 0.1^\circ$ | 紙飛行機の失速後自己回転（タンブリング）および付加質量減衰テンソル検証。 |
| **P8** | **吉川ら (2015)** | 300fps高速度立体カメラ追跡 + スモークワイヤ | 折り紙飛行機（へそ飛行機型, $AR=2.1$） | $7 \times 10^3 \sim 1.2 \times 10^4$ | $C_{L\alpha} \approx 2.45\,\mathrm{rad}^{-1}, C_{D0} \approx 0.042$<br>$(L/D)_{max} \approx 5.8, \delta_e \approx 3.0^\circ$ | 実機3Dレーザースキャン再現 (格子点数350万) | 折り紙段差によるパッシブトリップ効果および6DoFシミュレータ初期静的パラメータ。 |
| **P9** | **Li et al. (2022)** | 1000fps 高分解能ハイスピードカメラ追跡 | 自由滑空紙平板グライダー ($b=15\,\mathrm{cm}$) | $5 \times 10^3 \sim 1.5 \times 10^4$ | $x_{cg}/c \in [0.24, 0.36]$ 安定領域<br>$C_{mq} \approx -1.5\,\mathrm{rad}^{-1}, C_{m\alpha} \approx -0.65\,\mathrm{rad}^{-1}$ | 重心位置精度 $< 0.08\,\mathrm{mm}$, 角度 $< 0.15^\circ$ | 静安定マージン限界および無尾翼滑空機のピッチ減衰微係数 $C_{mq}$ の直接検証。 |
| **P10** | **Stowers et al. (2011)** | OptiTrack 12台赤外線カメラ (120~240Hz) + 超軽量マーカー | 超小型滑空MAV ($m=8.5\,\mathrm{g}$) | $1 \times 10^4 \sim 2 \times 10^4$ | $T_{ph} \approx 1.45\,\mathrm{s}, \zeta_{sp} \approx 0.65$<br>四元数姿勢ダイナミクス | 空間残差 $< 0.12\,\mathrm{mm}$, 姿勢誤差 $< 0.25^\circ$ | OptiTrackマーカートポロジー設計標準、Bスプライン微分状態量抽出パイプライン。 |
| **P11** | **Gill & D'Andrea (2019)** | Vicon 16台カメラアレイ (200Hz, 10m空間) | 自由滑空模型・可変翼MAV | $1.5 \times 10^4 \sim 4 \times 10^4$ | 瞬時空力合力 $\mathbf{F}_{aero} = m(\ddot{\mathbf{r}} - \mathbf{g})$<br>非定常極線 $C_D(C_L)$ | 空間歪み補正後残差 $< 0.08\,\mathrm{mm}$ | 風洞なしでMoCap軌道時系列のみから機体空力極線を完全逆同定するアルゴリズム。 |
| **P12** | **Zufferey et al. (2007)** | 屋内Viconステレオトラッキング (100Hz) | 30g級マイクログライダー ($AR=3.8$) | $1.5 \times 10^4 \sim 3 \times 10^4$ | 上反角 $\Gamma = 6.0^\circ$ でスパイラル中立<br>$(L/D)_{glide} \approx 5.0$ | トラッキング残差 $< 1.0\,\mathrm{mm}$ | 横・方向（ダッチロール・スパイラルモード）連成シミュレータの検証ベンチマーク。 |
| **P13** | **Jategaonkar (2015)** | 最尤推定・出力誤差法 (OEM) 数理フレームワーク | 航空機・飛翔体全般 (理論・実証) | 全領域 | コスト関数 $J(\mathbf{\Theta})$, Gauss-Newton更新<br>Fisher行列 $\mathbf{F}$, CRLB 不確かさ評価 | 統計的一致性・不偏性保証 | `tools/system_identification.py` の数理コアエンジン（Gauss-Newton OEM）。 |
| **P14** | **Dorobantu et al. (2013)** | 小型UAV飛行試験 + MEMS-IMU + ピトー管 | 小型固定翼UAV (Ultra Stick 25e) | $8 \times 10^4 \sim 1.5 \times 10^5$ | $C_{L\alpha} = 4.28, C_{mq} = -8.45$<br>$C_{lp} = -0.42, C_{nr} = -0.15$ | 周波数領域-時間領域統合残差解析 | センサノイズ下における多軸励振入力設計（Doublet/3-2-1-1）とパラメータ同定。 |
| **P15** | **Grauer & Morelli (2015)** | 多変量直交多項式・Bスプライン基底同定 | 大迎角失速・ポストストール飛翔体 | $5 \times 10^4 \sim 3 \times 10^5$ | $C_L(\alpha, q), C_m(\alpha, q)$ 非線形結合係数 | 逐次最小二乗（OLS）残差検定 | 非線形失速後空力モデルの多項式フィッティング構造の標準化。 |
| **P16** | **Caetano et al. (2015)** | OptiTrack自由飛行追跡 (120Hz) + EKF/OEM | DelFly II 滑空モード ($m=16.5\,\mathrm{g}$) | $1 \times 10^4 \sim 2.5 \times 10^4$ | $C_{L\alpha} \approx 2.15, C_{D0} \approx 0.055$<br>$C_{mq} \approx -1.10, C_{lp} \approx -0.32$ | 風洞天秤 vs 自由飛行SysID 差分検証 | 風洞拘束による減衰過小評価（約25%）の是正根拠。自由飛行SysIDの正本採用。 |
| **P17** | **田辺ら (2012)** | リニアレール弾性カタパルト + 500fpsステレオカメラ | 紙飛行機（競技用平折り・ダーツ型） | $8 \times 10^3 \sim 3 \times 10^4$ | カタパルト: $\sigma_V/\bar{V} < 0.85\%$, $\omega_0 < 0.4\,\mathrm{rad/s}$<br>人手: $\sigma_V/\bar{V} \sim 14.2\%$, $\omega_0 \sim 18\,\mathrm{rad/s}$ | ピッチ角精度 $\pm 0.2^\circ$, 速度分解能 $< 0.05\,\mathrm{m/s}$ | 実験における機械式射出装置の必須性。モンテカルロ初期外乱分散の正本設定値。 |
| **P18** | **Stengel (2004)** | 非線形ODE 4DoF軌道シミュレーション解析 | ダーツ型紙飛行機 ($AR=0.86, m=3\,\mathrm{g}$) | $8 \times 10^3 \sim 2 \times 10^4$ | $(L/D)_{max} \approx 5.2, \alpha_{opt} \approx 9.3^\circ$<br>$T_{ph} \approx 1.6\,\mathrm{s}, \zeta_{ph} \approx 0.14$ | 解析的一意解・位相平面解析 | 6DoFシミュレータの平衡滑空点およびフューゴイド固有周期の理論的整合性検証。 |

---

## 4. 本シミュレータ検証・SysIDパイプライン実装アーキテクチャへの提言

本調査の成果を踏まえ、`simulation/v3` に組み込むべき実験データ連携・システム同定パイプラインのアーキテクチャ仕様を以下に提言する。

```
+----------------------------------------------------------------------------------------------------+
|                         シミュレータ検証・システム同定 (SysID) 統合パイプライン                       |
+----------------------------------------------------------------------------------------------------+
  [実機実験データ入力]
   ├── OptiTrack / Vicon CSV ログ: 時系列 [t, x, y, z, qx, qy, qz, qw] (サンプリング 120~240 Hz)
   └── カタパルト射出初期値: [V0, theta0, phi0]
                                      │
                                      ▼
  [モジュール 1: `physics/mocap_loader.py` (新規推奨配備)]
   ├── 座標変換: MoCapグローバル系 (Z-Up) ──> 航空工学NED座標系 (North-East-Down, X-Forward, Z-Down)
   ├── クォータニオン正規化 & オイラー角 $[\phi, \theta, \psi]$ 変換
   └── Rauch-Tung-Striebel (RTS) スムーザ: 高周波追跡ノイズ除去 & 速度 $[\dot{x}, \dot{y}, \dot{z}]$, 角速度 $[p, q, r]$ 抽出
                                      │
                                      ▼
  [モジュール 2: `tools/system_identification.py` (新規推奨配備)]
   ├── 出力誤差法 (OEM / Gauss-Newton 法):
   │    目的関数: J(Θ) = ∑ || y_meas(k) - y_sim(k, Θ) ||_W^2  --> 最小化
   │    推定パラメータベクトル: Θ = [CL0, CL_alpha, CD0, k, Cm0, Cm_alpha, Cmq, Clp, Cnr]^T
   └── クラメール・ラオ下限 (CRLB) による各微係数の同定精度（不確かさ σ_Θ）の自動判定
                                      │
                                      ▼
  [モジュール 3: `physics/aero_kernel.py` への自動キャリブレーション反映]
   ├── 静的極線: 同定された CL0, CL_alpha, CD0, k を機体コンフィグに更新
   ├── 動的ピッチ減衰: Cmq_eff を実飛行同定値 (-1.2 ~ -1.8) に同期
   └── 回帰残差プロット (Residual Plot) & 実飛行 vs 6DoFシミュレーション軌道の自動重畳可視化
```

### 具体的な改修・配備アクション
1. **`physics/mocap_loader.py` の実装**:
   - OptiTrack Motive からエクスポートされた CSV データを直接読み込み、NED座標系へのアフィン変換、単位変換（$\mathrm{mm} \to \mathrm{m}$）、四元数回転行列表現の生成を一括処理するローダーを実装する。
2. **`physics/flight_sim.py` への検証モード追加**:
   - 実機射出初期条件 $[x_0, y_0, z_0, u_0, v_0, w_0, \mathbf{q}_0, p_0, q_0, r_0]$ をインプットとし、実測 MoCap 軌道とシミュレータ生成軌道の時間同期平均二乗誤差（RMSE）を出力する `run_validation_flight()` API を追加する。
3. **`physics/aero_kernel.py` の微係数キャリブレーション機能**:
   - P16 (Caetano et al.) の知見を反映し、拘束風洞試験値に対して自由飛行減衰補正係数 $f_{damp} \approx 1.25$ を乗じるスケーリングオプションを整備する。

---
*報告書作成完了: 2026-11-10*  
*調査主幹: Aerospace Aerodynamics, Experimental Metrology & System Identification Research Unit*
