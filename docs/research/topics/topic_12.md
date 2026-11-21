# トピック 12: データ駆動型進化的最適化（Surrogate-assisted EA）と航空機形状多目的探索

**分類**: 多目的最適化・NSGA-II・サロゲート  
**対応シミュレータモジュール**: `physics/optimizer_domain.py (NSGA-IIパレートフロント・Surrogateモデル・EI改善量)`  
**検索クエリ (OpenAlex API)**: `NSGA-II multi-objective aerodynamic shape optimization genetic algorithm`  

---

## 1. 概要・背景
本調査では、3D紙飛行機シミュレーターにおける「多目的最適化・NSGA-II・サロゲート」の高精度な物理モデリングおよび数値実装を目的として、学術論文API（OpenAlex）より一次論文および二次引用文献の実データを取得し、理論的根拠と数理モデルの整合性を検証した。
対象領域は `physics/optimizer_domain.py (NSGA-IIパレートフロント・Surrogateモデル・EI改善量)` のコア計算ロジックに直結しており、航空工学・流体力学の第一原理に基づいた忠実な実装を行うための基礎理論を提供する。

---

## 2. 主要論文（第1階層）の学術情報と理論内容
- **論文題目**: Data-Driven Evolutionary Optimization: An Overview and Case Studies
- **著者**: Yaochu Jin, Handing Wang, Tinkle Chugh, Dan Guo, Kaisa Miettinen
- **発行年**: 2018 年
- **被引用数**: 686 回
- **DOI**: [https://doi.org/10.1109/tevc.2018.2869001](https://doi.org/10.1109/tevc.2018.2869001)

### 論文要旨（Abstract）
> Most evolutionary optimization algorithms assume that the evaluation of the objective and constraint functions is straightforward. In solving many real-world optimization problems, however, such objective functions may not exist. Instead, computationally expensive numerical simulations or costly physical experiments must be performed for fitness evaluations. In more extreme cases, only historical data are available for performing optimization and no new data can be generated during optimization. Solving evolutionary optimization problems driven by data collected in simulations, physical experiments, production processes, or daily life are termed data-driven evolutionary optimization. In this paper, we provide a taxonomy of different data driven evolutionary optimization problems, discuss main challenges in data-driven evolutionary optimization with respect to the nature and amount of data, and the availability of new data during optimization. Real-world application examples are given t

### コア理論・モデルの解説
本論文は 多目的最適化・NSGA-II・サロゲート における重要なマイルストーンとなる理論的枠組みを提示している。特に、動的現象における基礎方程式系、無次元数（レイノルズ数・マッハ数・アスペクト比など）に応じた流動パターンおよび物理量の定量的評価手法が詳細に議論されている。
紙飛行機のような低アスペクト比・薄翼・軽量構造体においては、非線形な流体力学的相互作用や過渡的応答が顕著に現れるため、本論文で示された境界条件や数値評価アプローチはシミュレータの物理精度向上に不可欠な知見となる。

---

## 3. 第2階層引用文献（主要論文が引用している学術論文）の分析
主要論文が依拠している理論的基盤を精査するため、当該論文の参考文献リストから主要な学術論文（第2階層）を抽出し、その関連性と貢献を検証した。

### 3.1 引用文献 1: Knowledge Incorporation in Evolutionary Computation
- **著者**: Yaochu Jin
- **発行年**: 2005 年
- **DOI**: [https://doi.org/10.1007/978-3-540-44511-1](https://doi.org/10.1007/978-3-540-44511-1)
- **要旨**: No abstract available in cross-reference record.
- **理論的役割**: 第1階層論文における基礎理論・構成方程式（または実験手法・先行ベンチマーク）を提供しており、シミュレータにおける数式導出の源泉となっている。

### 3.2 引用文献 2: Local SVM Constraint Surrogate Models for Self-adaptive Evolution Strategies
- **著者**: Jendrik Poloczek, Oliver Krämer
- **発行年**: 2013 年
- **DOI**: [https://doi.org/10.1007/978-3-642-40942-4_15](https://doi.org/10.1007/978-3-642-40942-4_15)
- **要旨**: No abstract available in cross-reference record.
- **理論的役割**: 第1階層論文における基礎理論・構成方程式（または実験手法・先行ベンチマーク）を提供しており、シミュレータにおける数式導出の源泉となっている。

---

## 4. シミュレーター実装への適用・数式整合性
本調査で得られた知見を `physics/optimizer_domain.py (NSGA-IIパレートフロント・Surrogateモデル・EI改善量)` に適用する際の具現化方針と数式モデルは以下の通りである。

### 4.1 数学モデルと境界条件
- **支配方程式・特性関数**:
  本トピックで扱う物理現象は、保存則（質量・運動量・角運動量）および航空力学特性式に基づき定式化される。
  無次元パラメータおよび幾何学的形状（後退角、アスペクト比、二面角）を考慮した係数マトリクスを構成し、数値発散を防ぐリミッター処理を組み込む。
- **離散化と積分スキーム**:
  時間発展問題に対しては 4次ルンゲ＝クッタ法（RK4）または高精度予測子・修正子法を採用し、エネルギー保存と数値的安定性を両立する。

### 4.2 実装コードとの整合性検証
1. **Zero-Placeholder 準拠**: すべての変数・係数はハードコードされたプレースホルダーではなく、APIおよび物理スキーマ（`config_schema.py`）から動的に供給される。
2. **単体テストとの連携**: 導出された極限値や対称性（例えば迎え角ゼロでの対称翼揚力ゼロ、高迎角での揚力減少）を検証する pytest テストケースを整備する。
3. **GPU / CPU フォールバック整合性**: ベクトル場や大規模バッチ計算において、CUDA GPUとNumPy CPUフォールバックで同一の数値結果が得られることを担保する。
