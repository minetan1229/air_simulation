# 学術論文探査 統合マスターインデックス (Academic Literature Survey Master Index)

本インデックスは、超軽量小型滑空飛翔体（紙飛行機・MAV・無尾翼滑空機）の物理シミュレータ高度化、流体構造連成（FSI）、多目的進化的最適化（GA）、および実験計測・飛行同定（SysID）の数理基盤構築を目的として実施された、**全6分野・計115本**に及ぶ国際査読論文・古典的名著の網羅的調査結果を集約・体系化したものである。

---

## 1. 分野別レポート構成

| 分野ID | テーマ | 対象領域 | 精査論文数 | 詳細レポート正本 |
|---|---|---|---|---|
| **Domain 1** | **低レイノルズ数空力特性・薄翼・折紙翼型・LSB剥離泡・ヒステリシス・失速後モデル** | $Re = 10^3 \sim 10^5$, 低AR, 折紙段差 | **18本** | [`domain1_low_reynolds_aerodynamics_detailed.md`](domain1_low_reynolds_aerodynamics_detailed.md) |
| **Domain 2** | **6DoF飛翔力学・動的安定性・回転減衰・スパイラル・過渡減衰・付加質量** | 6自由度剛体運動, クォータニオン, 安定微係数 | **20本** | [`domain2_6dof_flight_dynamics_stability.md`](domain2_6dof_flight_dynamics_stability.md) |
| **Domain 3** | **大気境界層・マイクロ気象・サーマル・突風・地面効果・ダイナミックソアリング** | AGL $0 \sim 50\,\mathrm{m}$, 体育館微気流, 地面効果 | **18本** | [`domain3_atmospheric_boundary_layer_wind.md`](domain3_atmospheric_boundary_layer_wind.md) |
| **Domain 4** | **流体構造連成（FSI）・紙の弾性変形・翼端反り上がり・前縁折り段差・折紙空力弾性学** | $Re = 10^3 \sim 10^5$, 紙の坪量/剛性, 幾何非線形 | **21本** | [`domain4_fluid_structure_interaction_origami.md`](domain4_fluid_structure_interaction_origami.md) |
| **Domain 5** | **多目的最適化・遺伝的アルゴリズム・サロゲートモデル・空力逆設計・パレートフロンティア** | NSGA-II/III, CMA-ES, Kriging, 多忠実度 | **20本** | [`domain5_optimization_evolutionary_aerodynamics.md`](domain5_optimization_evolutionary_aerodynamics.md) |
| **Domain 6** | **実験計測・風洞試験・PIV可視化・飛行追跡・パラメータ同定（SysID）** | 微小天秤, PIV, MoCap (OptiTrack), OEM/EKF, 射出機 | **18本** | [`domain6_experimental_aerodynamics_identification.md`](domain6_experimental_aerodynamics_identification.md) |
| **合計** | **全6分野 網羅的学術文献** | **滑空全領域 ($Re$, 6DoF, 風, FSI, 最適化, 実験/SysID)** | **115本** | **本マスターインデックス** |

---

## 2. コア物理モデル & 数理定式化サマリー

### 2.1 Domain 1: 低レイノルズ数空力特性（Low-$Re$ Aerodynamics）
- **折紙段差・平板翼の非線形揚力**:
  岡本・東 (2011) および Pelletier & Mueller (2000) により、低$Re$薄板翼では前縁剥離渦（LEV）の寄与により揚力曲線が $C_L = C_{L\alpha}\alpha + k_{nl}\alpha^2$ となることが実証。
- **低アスペクト比（Low AR）翼端失速遅延**:
  Torres & Mueller (2004) により、翼端渦の強い吹き下ろしが剥離を抑制し、失速迎角が $\Delta\alpha_{stall} \approx 15.0^\circ / AR$ だけ遅延。
- **LSB空力ヒステリシスと外乱減衰**:
  Marchmanら (1988) のピッチ往復ヒステリシスループに対し、Hoffmann (1991) は主流乱流度 $Tu > 1.5\%$ でループが閉塞し一価関数化することを解明。

### 2.2 Domain 2: 6DoF飛翔力学と動的安定性（6DoF Flight Dynamics）
- **ピッチング減衰微係数 ($C_{mq}$)**:
  Li & Ristroph (2022) および Pelletier & Mueller (2000) より、標準的な紙飛行機・無尾翼滑空機では $C_{mq} \approx -0.8 \sim -2.5\,\mathrm{rad}^{-1}$（$2V$ 無次元化定義）。
- **ロール・偏揺れ減衰 ($C_{lp}, C_{nr}$)**:
  Waszak et al. (2001) の風洞動釣合実験より、$C_{lp} \approx -0.35 \sim -0.42\,\mathrm{rad}^{-1}$, $C_{nr} \approx -0.12 \sim -0.22\,\mathrm{rad}^{-1}$。
- **スパイラル安定判別式**:
  Nickel & Wohlfahrt (1994) による無尾翼機安定条件 $E = C_{l\beta} C_{nr} - C_{lr} C_{n\beta} > 0$。後退角による有効上反角 $\Delta C_{l\beta} \approx -1/6 C_L \sin 2\Lambda$。
- **付加質量（Added Mass）テンソル**:
  極軽量紙飛行機（$m \sim 2\,\mathrm{g}$）において、Newman (1977) および Andersen et al. (2005) の垂直付加質量 $m_{add,z} = \frac{\pi}{4}\rho S \bar{c}$ は固体質量の $15\% \sim 40\%$ に達し、高周波動的応答を抑制するダンパーとして機能。

### 2.3 Domain 3: 大気境界層・突風・地面効果（Atmosphere & Ground Effect）
- **超低高度大気境界層（AGL 0〜50m）**:
  Monin-Obukhov相似則および対数則 $U(z) = \frac{u_*}{\kappa} \ln\left(\frac{z + z_0}{z_0}\right)$（芝地 $z_0 = 0.01 \sim 0.03\,\mathrm{m}$）。
- **地面効果（Ground Effect, WIG）定量的定式化**:
  Wieselsberger (1921) および Phillips (2004) より、対地高度比 $h/b < 0.5$ で誘導抗力係数が $\sigma(h/b) = \frac{16 (h/b)^2}{1 + 16 (h/b)^2}$ に低減。
  クッション効果による揚力増加比 $\frac{\Delta C_L}{C_{L\infty}} \approx \frac{0.10}{(h/c)^{0.8}}$、および前縁吸出しに伴う頭下げモーメント変化 $\Delta C_m \approx -0.05 \left(\frac{h}{c}\right)^{-0.5} C_L$。
- **突風波長スケール効果**:
  極小スパン機体（$b \sim 0.2\,\mathrm{m}$）に対する大気乱流積分スケール（$L_u \sim 20 \sim 50\,\mathrm{m}$）では $L_u / b \gg 100$ となり、機体全体が一様な局所風ベクトル変化として受風。

### 2.4 Domain 4: 流体構造連成・弾性変形・折紙幾何学（FSI & Origami Aeroelasticity）
- **動圧連動パッシブキャンバー変形 & 失速マイルド化**:
  Rojratsirikul et al. (2009) および Gordnier (2009) より、膜面が風圧に応じて自律的にキャンバーを適応変形し、剥離泡の再付着を促進して失速角を $+4^\circ \sim +6^\circ$ 延伸。
- **前縁多重折りスパー効果 & 段差トリップ**:
  吉川ら (2015) および Gerakopulos et al. (2010) より、4〜8重の紙積層により機首曲げ剛性が単層部の $64 \sim 512$ 倍に増大して重心を前方（$x_{cg}/c \approx 0.28 \sim 0.32$）に固定し、折り段差がLSB基底点をピン留めして空力ヒステリシスを抑制。
- **翼端ウォッシュアウトとNASA Prandtlベル型循環（順ヨー $C_{n,p} > 0$）**:
  Bowers et al. (2016) および Nickel & Wohlfahrt (1994) より、翼端跳ね上げにより局所吹き上げ（Upwash）と負の誘導抗力（推力）を形成し、垂直尾翼のない無尾翼紙飛行機に順ヨー（Proverse Yaw）と旋回安定性を付与。
- **紙の直交異方性（MD/CD）と湿度（RH）応答**:
  Baum et al. (1981) および Niskanen / Uesaka (1998/2002) より、縦目ヤング率が横目の約 $2.2$ 倍（$E_{MD} \approx 5.2\,\mathrm{GPa}$ vs $E_{CD} \approx 2.3\,\mathrm{GPa}$）。高湿度（RH 80%）下で剛性が $50\%$ 以上低下する物理則の同定。

### 2.5 Domain 5: 多目的最適化・進化的空力逆設計（Optimization & Surrogate Modeling）
- **多目的遺伝的アルゴリズム（NSGA-II / NSGA-III / CMA-ES）**:
  Deb et al. (2002, 2014) および Hansen (2001) より、滑空距離（$L/D$）最大化と滞空時間（$C_L^{3/2}/C_D$）最大化の相反する要求を満たすパレートフロントの抽出。
- **多忠実度サロゲートモデル（Multi-Fidelity Kriging / Co-Kriging）**:
  Forrester et al. (2007) より、低忠実度計算（VLM / 平板空力核）と高忠実度計算（3D RANS / LES / 風洞実測）を階層的に結合し、計算負荷を $80\%$ 削減しながら高精度最適化を実現。

### 2.6 Domain 6: 実験計測・風洞試験・MoCap追跡・システム同定（Experimental & SysID）
- **極微小力天秤計測と支持干渉排除**:
  Feero et al. (2019) および Selig & McGranahan (2004) より、たわみヒンジ単分力天秤（分解能 $4.5\,\mu\mathrm{N}$）と後流運動量欠損積分（Wake Rake Traverse）により、微小抗力（$1 \sim 10\,\mathrm{mN}$）の支持ストラット干渉を完全補正。
- **低AR前縁渦（LEV）と翼端渦の立体結合PIV**:
  DeVoria & Mohseni (2017) および Zhang, Hain, & Kähler (2008) より、翼端渦の吹き下ろしが前縁剥離渦（LEV）を上面にピン留めし、失速迎角を $15^\circ$ 以上遅延させる流動構造の解明。
- **光学式MoCap自由飛行追跡による空力微係数同定**:
  Li et al. (2022), Stowers et al. (2011), Gill & D'Andrea (2019) より、超軽量反射マーカー（$< 0.05\,\mathrm{g}$）を用いた OptiTrack / Vicon 追跡により、風洞を使わずに自由滑空加速度から空力極線および減衰微係数（$C_{mq} \approx -1.2 \sim -1.8\,\mathrm{rad}^{-1}$）を同定。
- **射出装置（ランチャー）による手投げ外乱排除**:
  田辺ら (2012) より、リニアレール式カタパルトにより初速変動率を $\sigma_V/\bar{V} < 0.85\%$、初期角速度外乱を $< 0.4\,\mathrm{rad/s}$ に抑制し、手投げ時の指離脱偶力による過渡的失速・フューゴイド励振を完全排除。

---

## 3. シミュレータ（`simulation/v3`）への工学的実装マトリクス

| 物理現象 / モデル | 推奨数理定式化 | 対象モジュール | 優先度 |
|---|---|---|---|
| **低AR翼端失速遅延** | $\alpha_{stall}(AR) = \alpha_{stall,0} + \min(15^\circ / AR, 18^\circ)$ | `physics/aero_kernel.py` | 高 |
| **乱流度連動ヒステリシス抑制** | $\Delta\alpha_{hyst,eff} = \Delta\alpha_{hyst,0} \cdot \exp(-Tu / 0.015)$ | `physics/aero_kernel.py` | 高 |
| **動的安定微係数レンジ統一** | $C_{mq} \in [-2.5, -0.8]$, $C_{lp} \in [-0.45, -0.30]$, $C_{nr} \in [-0.25, -0.10]$ | `physics/config_schema.py` | 高 |
| **地面効果モデル標準化** | $C_{Di}(h) = \sigma(h/b) C_{Di}$, $\Delta C_L(h) = \frac{0.1 C_{L0}}{(h/c)^{0.8}}$, $\Delta C_m(h)$ | `physics/aero_kernel.py` | 高 |
| **付加質量（Added Mass）考慮** | 縦加速度運動方程式に $(m + m_{add,z})\dot{w}$ 適用 | `physics/flight_sim.py` | 中 |
| **高度0〜50m対数則風プロファイル** | $z_{up} = -z_{NED}$ でサンプリングする対数風・サーマルモデル | `physics/wind_domain.py` | 済 (確認) |
| **紙の直交異方性弾性構成則** | $\mathbf{Q}_{MD/CD}$, $E_{MD} \approx 5.2\,\mathrm{GPa}$, $E_{CD} \approx 2.3\,\mathrm{GPa}$ | `physics/paper_material.py` *(新規)* | **最高** |
| **翼端ウォッシュアウト・ベル型循環** | $\Gamma(y) = \Gamma_0(1-(2y/b)^2)^{3/2}$, 順ヨー $C_{n,p} > 0$ | `physics/aero_kernel.py` | **最高** |
| **前縁多重折り剛性・段差モデル** | $D_{LE} = \eta N^3 D_0$, $h_{step}/\delta$ によるLSB固定化 | `physics/aero_kernel.py` | **最高** |
| **動圧連動パッシブキャンバー変形** | $\Delta f_{camber} = f_0 - C_{flex} \frac{q}{E h^3} C_L$ | `physics/aero_kernel.py` | **高** |
| **環境湿度（RH）剛性変調** | $E(RH) = E_0 \exp(-\beta_{RH}(RH - 0.5))$ | `physics/paper_material.py` | **高** |
| **MoCap CSV 読み込み・座標変換** | OptiTrack Z-Up $\to$ 航空工学 NED 変換、四元数正規化、RTS 平滑化 | `physics/mocap_loader.py` *(新規)* | **高** |
| **飛行試験システム同定 (OEM)** | 出力誤差法 (Gauss-Newton 法) による $C_{L\alpha}, C_{D0}, C_{mq}$ 逆同定と CRLB 不確かさ判定 | `tools/system_identification.py` *(新規)* | **高** |
| **射出初期外乱モンテカルロ** | カタパルトモード ($\sigma_V < 1\%$) vs 手投げモード ($\sigma_V \sim 14\%$, $\omega_0 \sim 15\,\mathrm{rad/s}$) | `tools/monte_carlo_launch.py` | **中** |
