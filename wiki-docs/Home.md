# 3D Paper Airplane High-Performance CFD Simulator Wiki

ようこそ！**3D Paper Airplane High-Performance CFD Simulator** の公式 GitHub Wiki へ。  
本プロジェクトは、流体力学（CFD）および航空工学理論に基づく高精度な紙飛行機フライトシミュレーターです。Flask による REST API バックエンドと、Three.js / WebGL による Google Clean スタイルの計器コンソール・気流可視化フロントエンドを統合しています。

---

## 🚀 主な機能・特徴

- **航空力学・環境モデル (Aero & Atmosphere)**:
  - ISA 標準大気モデル（高度に応じた気圧・温度・大気密度の変化）
  - Sutherland の式による動粘性係数・レイノルズ数計算
  - Viterna アルゴリズムによる大迎角・失速遷移モデリング
  - 地面効果（Ground Effect）による誘導抗力低減
  - 坪量・剛性に基づく受動的空力弾性モーフィング（Passive Aeroelastic Morphing）
  - 抗力極線（Drag Polar）の自動算出と最適滑空角解析
- **6自由度 (6DoF) 飛行力学 & 時間制御**:
  - 四元数（クォータニオン）姿勢表現と 4次ルンゲ＝クッタ法（RK4）による数値積分
  - 高速・急旋回時の適応サブステッピング（Adaptive Sub-stepping）による数値安定性確保
  - リングバッファによる履歴保持と、シーク・コマ送り・倍速再生（0.25x〜10x）
- **3D 風場・流体ダイナミクス (Fluid Dynamics)**:
  - 空間ベクトル場、Simplex/Perlin ノイズを用いた大気乱気流、熱上昇気流（サーマル）
  - GPU（CuPy / PyTorch）および NumPy による自動フォールバック並列計算
- **構造負荷・応力解析 (Structural Stress)**:
  - 動圧・Gフォースに基づく翼根曲げモーメント解析
  - 頂点単位の応力ヒートマップ（青〜赤）リアルタイム変形描画
- **3D メッシュインポート & パネル法 (Mesh Domain)**:
  - STL（バイナリ / ASCII）、OBJ、glTF 2.0 / GLB 形式のインポート
  - 表面積、翼幅、アスペクト比、重心（体積 / 面積）、慣性テンソルの自動幾何解析
  - パネル法による局所迎角・循環・揚力解析
- **多目的最適化エンジン (Optimization Engine)**:
  - Nelder-Mead、Differential Evolution、NSGA-II（パレート最適解導出）、Surrogate（代理モデル）
  - 非同期ジョブ実行、進捗率、リアルタイム ETA（完了予定時刻）計算
- **データ管理 & 永続化**:
  - 機体・環境パラメータの JSON インポート / エクスポート
  - 飛行履歴テレメトリの CSV 即時ダウンロード

---

## 🎯 対象ユーザー

- **航空工学・流体力学の教育者・学生**: 揚力・抗力・失速・地面効果・モーメントの挙動を 3D 視覚的に直感理解したい方
- **紙飛行機設計者・研究者**: 折り方や翼型パラメータ、材質（坪量）による滞空時間・飛行距離の最適化を探求したい方
- **Web 3D / CAE エンジニア**: Three.js によるリアルタイム物理描画や WebGL シェーダー、Flask REST API との数値連携を学びたい開発者

---

## 📚 ドキュメント構成

| ドキュメント | 概要 |
|:---|:---|
| [[環境構築と起動手順\|Getting-Started]] | 必要要件、Python 環境構築、起動コマンド、環境変数設定 |
| [[アーキテクチャ設計\|Architecture]] | システム構成図、データフロー、ディレクトリ構造、採用技術スタック |
| [[開発・運用ガイド\|Development-Guide]] | ブランチ戦略、Git 運用規約、テスト（pytest/smoke_test）実行手順、CI パイプライン |

---

## 🔗 関連リンク

- [GitHub リポジトリ](https://github.com/minetan1229/air_simulation)
- [REST API 仕様 (プロジェクト内 docs/api.md)](../docs/api.md)
