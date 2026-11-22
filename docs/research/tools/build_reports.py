#!/usr/bin/env python3
"""Stage 4 of the paper pipeline: render the chunked Markdown/JSONL reports.

Every entry is derived from the harvested registry record (title, authors, year,
venue, DOI/arXiv id, abstract) plus the taxonomy's technique mapping for the
applicability notes. The applicability note is an explicit proposal for this
simulator, not a claim made by the paper.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from paper_common import BASE_DIR, CATEGORIES, get_db, loads
from paper_taxonomy import CATEGORY_META, TECHNIQUES

CHUNK_SIZE = 50
FORMULATIONS: Dict[str, str] = {
    "navier-stokes": "非圧縮 Navier-Stokes 方程式（連続体運動学・粘性応力テンソル）",
    "navier stokes": "非圧縮 Navier-Stokes 方程式（連続体運動学・粘性応力テンソル）",
    "computational fluid dynamics": "保存形の CFD 支配方程式と離散化スキーム",
    "turbulen": "乱流平均化支配式（レイノルズ応力閉包）",
    "aerodynam": "低次空力モデル（揚力・抗力・モーメントの係数）",
    "airfoil": "翼型形状依存の空力係数（揚力線・境界層理論）",
    "lattice boltzmann": "格子ボルツマン方程式（BGK/MRT 衝突演算子と局所平衡分布）",
    "panel method": "ポテンシャルフロー（複素ポテンシャルと翼面境界条件）",
    "vortex lattice": "揚力線理論（離散渦要素と局所揚力循環）",
    "potential flow": "非圧縮・非粘性ポテンシャルフロー（速度ポテンシャルと渦度）",
    "boundary layer": "境界層近似（粘性スケールと圧力勾配の関係式）",
    "separation": "剥離開始の推定（逆流点と剥離包絡線）",
    "stall": "失速後の抗力増加（flat-plate 係数の外挿）",
    "lift coefficient": "揚力係数（Re 数・迎角依存の翼型極線）",
    "drag coefficient": "抗力係数（誘導抗力・摩擦抗力・形抗の分解）",
    "drag polar": "抗力極線（CD = CD0 + k*CL^2）",
    "large eddy simulation": "LES 支配方程式とサブ格子スケーリングモデル",
    "direct numerical simulation": "DNS（非定常 Navier-Stokes の直接解法）",
    "reynolds averaged": "RANS 平均 Navier-Stokes（レイノルズ応力閉包）",
    "reynolds-averaged": "RANS 平均 Navier-Stokes（レイノルズ応力閉包）",
    "immersed boundary": "immersed boundary 強制項（移動境界の強制力表現）",
    "finite volume": "保存形有限体積法（フラックス再構成と時間積分）",
    "supersonic": "圧縮性流れ（Euler 方程式と衝撃波の捕捉）",
    "transonic": "遷音速流れ（局所 sonic 条件と shock の捕捉）",
    "hypersonic": "極超音速流れ（Darcy 粘性補正と熱流束）",
    "shock": "衝撃波捕捉（人工粘性と Godunov 型フラックス）",
    "aeroelastic": "空力弾性連成（翼の弾性モードと空力荷重の対応）",
    "flutter": "弾性空力動特性（複素固有値と Flutter 境界）",
    "ground effect": "地面効果（高度依存の誘導抗力補正）",
    "vortex method": "渦運動学（3D 渦糸と局所誘導速度）",
    "volume of fluid": "VOF 法（体積分率と界面フラックスの再構成）",
    "smoothed particle": "SPH カーネル近似（密度・圧力の補間と境界処理）",
    "pressure projection": "圧力投影法（Helmholtz 分解と Poisson 方程式）",
    "swept wing": "後退翼理論（swept-wing 線形化と Prandtl-Glauert 補正）",
    "wing": "翼形状の幾何パラメータと空力応答の対応",
    "flow": "流動場の保存則（質量・運動量・エネルギーの局所収支）",
    "finite element": "有限要素弱形式（Galerkin 近似と Galerkin/ Petrov-Galerkin 安定化）",
    "hyperelastic": "超弾性構成式（Neo-Hookean / Mooney-Rivlin / Ogden）",
    "mooney-rivlin": "Mooney-Rivlin 超弾性構成式（2 項不変量）",
    "mooney rivlin": "Mooney-Rivlin 超弾性構成式（2 項不変量）",
    "ogden": "Ogden 超弾性構成式（非線形異方性不変量）",
    "position based dynamics": "位置ベース動力学（位置未知量の Gauss-Seidel 投影）",
    "position-based dynamics": "位置ベース動力学（位置未知量の Gauss-Seidel 投影）",
    "extended position based": "XPBD（compliance 付き拘束と Lagrange 乗数）",
    "xpbd": "XPBD（compliance 付き拘束と Lagrange 乗数によるエネルギー最小化）",
    "shape matching": "Shape Matching（局所 MINKOWSKI 対応と剛体化）",
    "rigid body": "剛体運動学（角速度・角運動量・慣性テンソルの時間発展）",
    "contact": "接触拘束（貫通不能条件と法線・摩擦円錐）",
    "friction": "クーロン摩擦円錐と静的・動摩擦の切替",
    "fracture": "破壊力学（cohesive zone / 位相場の亀裂伝播）",
    "crack": "亀裂の伝播則（準静的亀裂先端のエネルギー解放率）",
    "plastic": "弾塑性構成式（von Mises 降伏基準と等方硬化）",
    "viscoelastic": "粘弾性（Maxwell / Kelvin-Voigt 素形体と緩和時間）",
    "multiphysics": "連成場方程式（変位・速度・熱場の相互結合）",
    "fluid structure interaction": "二方向 FSI（流束場と弾性変形の双方向結合）",
    "fluid-structure interaction": "二方向 FSI（流束場と弾性変形の双方向結合）",
    "shell": "シェル要素（Kirchhoff-Love 板と Mindlin-Reissner 板）",
    "membrane": "膜要素（面内応力のみを扱う）",
    "cloth": "布の拘束（距離・面積・曲げ拘束）",
    "origami": "折り紙ヒンジ（crease の運動学と軸剛性）",
    "creased": "折り線の非線形運動学（developable sheet の展開動力学）",
    "auxetic": "Auxetic メタマテリアル（負のポアソン比とせん断剛性）",
    "peridynamic": "Peridynamics（非局所な微分方程式と horizons）",
    "phase field": "相場近似（損傷変数の拡散場）",
    "cohesive": "cohesive zone model（traction-separation 則）",
    "constitutive": "構成則（応力-ひずみ関係と objectivity）",
    "discrete element": "DEM（剛体接触と粒子間接触則）",
    "impact": "接触衝撃（反発係数とエネルギー散逸）",
    "isogeometric": "isogeometric analysis（NURBS 制御点と幾何の同時表現）",
    "modal": "modal 展開（固有モード基底による低次元化）",
    "reduced order": "ROM（固有基底射影と Galerkin 近似）",
    "soft robot": "soft actuator（腱駆動と compliance のモデル）",
    "deformable": "変形体（剛体運動と弾性変形の分離表現）",
    "symplectic": "シンプレクティック写像（Hamiltonian flow の構造保存）",
    "hamiltonian": "Hamilton 正則方程式（正準変換と保存則）",
    "stiff": "Stiff ODE（fast-slow 分解と陰的解法）",
    "time step": "時間刻み制御（局所誤差推定による step-size 選択）",
    "time stepping": "時間積分法（局所誤差制御付き刻み選択）",
    "cfl": "CFL 条件（Courant 数と陽解法の安定限界）",
    "runge-kutta": "Runge-Kutta 法（Butcher tableau と order 条件）",
    "runge kutta": "Runge-Kutta 法（Butcher tableau と order 条件）",
    "multistep": "線形多段法（Adams/BDF 係数と特性多項式）",
    "multi-step": "線形多段法（Adams/BDF 係数と特性多項式）",
    "crank-nicolson": "Crank-Nicolson 法（時間平均化と A-安定性）",
    "crank nicolson": "Crank-Nicolson 法（時間平均化と A-安定性）",
    "multigrid": "マルチグリッド（粗細格子階層と smoother）",
    "krylov": "Krylov 部分空間法（GMRES/CG と Arnoldi 過程）",
    "preconditioner": "前処理（Jacobi / 不完全 Cholesky）",
    "conjugate gradient": "共役勾配法（勾配直交性と 3 項漸化式）",
    "eigenvalue": "固有値問題（Arnoldi 法と逆反復法）",
    "newmark": "Newmark 法（無条件安定な線形加速度法）",
    "leapfrog": "leapfrog / Verlet 法（位置と速度の半刻みずらし）",
    "verlet": "velocity-Verlet（位置更新と力再計算の分業）",
    "picard": "固定点反復（Banach 収束条件）",
    "newton": "Newton 法（Jacobian と二次収束）",
    "aitken": "Aitken 加速（線形収束の加速）",
    "richardson": "Richardson 外挿（次数の不一致補正）",
    "deferred correction": "deferred correction（高次化と漸近精度の獲得）",
    "weno": "WENO 重建（重み付き非線形結合）",
    "muscl": "MUSCL 法（傾斜制限と 3 次精度化）",
    "godunov": "Godunov 法（波動伝播の保存形解法）",
    "riemann": "Riemann 問題（近似解法）",
    "upwind": "風上差分（数値拡散と単調性）",
    "artificial viscosity": "人工粘性（散逸項と衝撃捕捉）",
    "rosenbrock": "Rosenbrock 法（L-安定な陰解法）",
    "exponential integrator": "exponential integrator（線形部の厳密指数解）",
    "roundoff": "丸め誤差（補償加算と丸め誤差の伝播）",
    "condition number": "条件数（誤差増幅と精度限界）",
    "lu factorization": "LU 分解（ピボット選択と後退代入）",
    "qr decomposition": "QR 分解（直交基底の構築）",
    "domain decomposition": "ドメイン分割（境界要素と領域並列化）",
    "compensated summation": "Kahan 補償加算",
    "mixed precision": "混合精度演算（単精度と倍精度の併用）",
    "shadow hamiltonian": "shadow Hamiltonian（修正方程式による誤差解析）",
    "interval arithmetic": "区間演算（誤差の厳密な囲い）",
    "automatic differentiation": "自動微分（forward/reverse mode と dual number）",
    "adjoint": "随伴法（感度解析と逆問題）",
    "quadrature": "数値積分（求積則と誤差推定）",
    "collision detection": "衝突判定（broad phase と narrow phase の二段構え）",
    "gjk": "GJK アルゴリズム（support function による距離計算）",
    "epa": "EPA（侵入深さ推定と多面体の膨張）",
    "expanding polytope": "EPA（侵入深さ推定と多面体の膨張）",
    "bounding volume hierarchy": "BVH（AABB/OBB による二分木空間分割）",
    "bounding volume": "境界体積（包含ヒエラルキー）",
    "octree": "Octree（立方格子による空間分割）",
    "k-d tree": "kd-tree（軸分割平衡探索木）",
    "kd-tree": "kd-tree（軸分割平衡探索木）",
    "kd tree": "kd-tree（軸分割平衡探索木）",
    "signed distance": "符号付き距離関数（等値面と押し出し判定）",
    "level set": "レベル集合法（界面捕捉と移動境界）",
    "delaunay": "Delaunay 三角形分割（空円条件）",
    "triangulation": "三角形分割（平面グラフと埋め込み）",
    "remesh": "リメッシュ（品質改善と等方性化）",
    "remeshing": "リメッシュ（品質改善と等方性化）",
    "mesh simplification": "二次誤差行列（QEM）による簡略化",
    "isosurface": "等値面抽出（marching cubes / dual contouring）",
    "marching cubes": "marching cubes（等値面の三角形化）",
    "dual contouring": "dual contouring（特異な特徴を保存する多角形化）",
    "voxelization": "ボクセライゼーション（距離場への変換）",
    "spatial hash": "空間ハッシュ（座標量子化とセル符号化）",
    "broad phase": "broad phase（衝突候補ペアの保守的列挙）",
    "narrow phase": "narrow phase（プリミティブ間の精密判定）",
    "separating axis": "SAT（分離軸定理）",
    "swept sphere": "swept sphere（球スイープによる連続判定）",
    "continuous collision": "CCD（保守的前進による TOI 計算）",
    "conservative advancement": "保守的前進（球の膨張による TOI 上界）",
    "minkowski": "Minkowski 差（configuration space 表現）",
    "voronoi": "Voronoi 図（最近傍領域と dual 構造）",
    "convex hull": "凸包（support function 表現）",
    "geometric predicate": "厳密幾何述語（適応精度判定）",
    "mesh boolean": "メッシュ boolean（CSG と三角形群の交差処理）",
    "fast marching": "fast marching 法（eikonal 方程式）",
    "eikonal": "eikonal 方程式（距離関数の制御方程式）",
    "query acceleration": "クエリアクセラレーション（空間分割による交差列挙）",
    "penetration depth": "侵入深さ（minimum translation vector）",
    "culling": "視錐台カリング（frustum culling と遮蔽判定）",
    "hashing": "確率的幾何ハッシュ（座標量子化による高速照合）",
    "gpu": "GPU カーネル並列計算（SIMT 実行モデル）",
    "cuda": "CUDA カーネル（スレッド・ブロック階層と共有メモリ）",
    "compute shader": "Compute Shader（描画パイプライン上の並列演算）",
    "gpgpu": "GPGPU（データ並列な物理演算）",
    "simd": "SIMD ベクトル化（連続メモリアクセス）",
    "cache": "キャッシュ局所性（working set と prefetch の設計）",
    "memory bandwidth": "メモリ帯域律速（roofline モデル）",
    "coalesc": "メモリアクセスの合体（coalesced access）",
    "warp": "warp 実行と枝分かれ発散（warp divergence）",
    "occupancy": "occupancy（レジスタ圧と並列度の tradeoff）",
    "shared memory": "共有メモリ（バンクコンフリクト）",
    "atomic": "アトミック演算（競合の回避）",
    "prefix sum": "並列 prefix sum（scan と work-efficient 実装）",
    "reduction": "並列 reduction（tree 集約）",
    "sparse matrix": "CSR 疎行列（SpMV と Gustavson 方式）",
    "csr": "CSR 疎行列（圧縮行格納形式）",
    "spmv": "SpMV（疎行列ベクトル積）",
    "opencl": "OpenCL（移植可能な並列カーネル）",
    "sycl": "SYCL（異種並列の標準化）",
    "heterogeneous": "ヘテロゼネラス計算（CPU/GPU 間の負荷配分）",
    "data oriented": "データ指向設計（SoA と ECS）",
    "soa": "SoA（構造体配列）レイアウト",
    "physics-informed": "PINN（部分微分方程式の損失関数埋め込み）",
    "physics informed": "PINN（部分微分方程式の損失関数埋め込み）",
    "neural operator": "Neural Operator（FNO の積分型内核）",
    "fourier neural": "Fourier Neural Operator（スペクトルカーネル）",
    "surrogate": "surrogate model（低次近似による高速化）",
    "reduced order model": "ROM（固有基底と Galerkin 射影）",
    "graph neural network": "GNN（メッシュ上のメッセージパッシング）",
    "reinforcement learning": "強化学習（policy 勾配と experience replay）",
    "kalman filter": "Kalman filter（状態推定と共分散更新）",
    "particle filter": "particle filter（importance sampling による状態推定）",
    "monte carlo": "Monte Carlo 法（確率的サンプリング）",
    "model predictive control": "MPC（予測制御と receding horizon）",
    "differentiable": "微分可能モデル（自動微分による勾配）",
    "latency": "レイテンシ（フレーム時間バジェット管理）",
    "throughput": "スループット（処理量と並列効率）",
    "load balanc": "負荷分散（work-item 再配分とホットスポット回避）",
    "graph color": "グラフ彩色（並列 Gauss-Seidel の非衝突化）",
    "matrix-free": "matrix-free 演算（行列要素を直接計算しない）",
    "tensor core": "テンソルコア（低精度行列演算の高速化）",
    "equivariant": "等変性（物理対称性をネットワークに埋め込む）",
    "ray tracing": "レイトレーシング（BVH による交差高速化）",
    "rendering": "リアルタイム描画（描画負荷と物理負荷の予算配分）",
    "instanc": "インスタンシング（多オブジェクト描画の削減）",
}

CATEGORY_FALLBACK = {
    "01_aerodynamics_cfd": "翼面まわりの局所流場と揚力・抗力の低次モデルを、6DoF 空力テーブルの姿勢依存補正へ反映できる。",
    "02_mechanics_multiphysics": "機体形状の力学モデル（剛性・接触・拘束）を 6DoF ソルバの拘束として組み込み、形状プリセットの物理挙動を実形状から自動生成できる。",
    "03_numerical_solvers": "既存の 6DoF 積分と拘束ソルバの時間積分法・安定化手法へ置換し、積分精度と長期安定性の改善にそのまま適用できる。",
    "04_collision_geometry": "機体メッシュの broad/narrow phase と CCD の構成を見直し、毎フレーム負荷とすり抜けの両方を改善できる。",
    "05_gpu_realtime_sim": "既存の CuPy GPU 経路（および CPU フォールバック）の並列化・メモリ配置・近似モデル化に適用できる。",
}

CATEGORY_FORMULATIONS = {
    "01_aerodynamics_cfd": [
        "非圧縮 Navier-Stokes 方程式と低次空力モデル（揚力・抗力・モーメントの係数）"],
    "02_mechanics_multiphysics": [
        "剛体運動学と弾性（変形）構成則による力学モデル"],
    "03_numerical_solvers": [
        "常微分方程式の時間積分法と数値安定性（収束性・誤差評価の理論）"],
    "04_collision_geometry": [
        "衝突判定の幾何アルゴリズム（broad/narrow phase と TOI 計算）"],
    "05_gpu_realtime_sim": [
        "並列計算モデル（カーネル並列化・メモリ階層・近似モデル）"],
}

METHOD_MARKERS = (
    "propose", "present", "develop", "derive", "introduce", "formulate", "we show",
    "algorithm", "solver", "scheme", "method", "model", "framework", "equation",
    "based on", "numerical", "simulation", "approximation", "discretization",
    "convergence", "accuracy", "framework", "we investigate", "this paper",
)

FIX_TEXT = {
    "swap": "置換",
    "solver.swap": "ソルバの置換",
}


def clean_sentence_text(text: str) -> str:
    text = " ".join(text.split())
    for src, dst in FIX_TEXT.items():
        text = text.replace(src, dst)
    return text


def pick_core_sentences(abstract: str, limit: int = 3, max_chars: int = 900) -> List[str]:
    from paper_common import split_sentences

    sentences = split_sentences(abstract)
    if not sentences:
        return []
    scored = []
    for index, sentence in enumerate(sentences):
        lower = sentence.lower()
        marker_hits = sum(1 for marker in METHOD_MARKERS if marker in lower)
        weight = marker_hits * 2 - index * 0.4
        scored.append((weight, index, sentence))
    scored.sort(key=lambda item: -item[0])
    chosen: List[Tuple[int, str]] = []
    for _, index, sentence in scored:
        if len(chosen) >= limit:
            break
        chosen.append((index, sentence))
    chosen.sort(key=lambda item: item[0])
    result = []
    total = 0
    for _, sentence in chosen:
        text = clean_sentence_text(sentence)
        if len(text) > 340:
            text = text[:337].rstrip() + "..."
        if total + len(text) > max_chars:
            break
        total += len(text)
        result.append(text)
    return result


def detect_techniques(text: str) -> List[str]:
    from paper_common import contains

    return [key for key in TECHNIQUES if contains(text, key)]


def detect_formulations(text: str) -> List[str]:
    from paper_common import contains

    found = []
    for key, phrase in FORMULATIONS.items():
        if contains(text, key):
            found.append(phrase)
    ordered = []
    for phrase in found:
        if phrase not in ordered:
            ordered.append(phrase)
    return ordered


def format_authors(authors: List[str]) -> str:
    if not authors:
        return "著者情報なし"
    if len(authors) == 1:
        return authors[0]
    return f"{authors[0]} ほか {len(authors) - 1} 名"


def venue_label(row) -> str:
    venue = (row["venue"] or "").strip()
    if not venue:
        venue = (row["publisher"] or "").strip() or "媒体情報なし"
    return venue


def build_entry(row, rank: int, title_ja: str) -> str:
    title = row["title"]
    doi = row["doi"]
    arxiv_id = row["arxiv_id"] or ""
    abstract = row["abstract"] or ""
    text = f"{title} {abstract}".lower()
    authors = loads(row["authors"], []) or []
    year = row["year"] or "n.d."
    venue = venue_label(row)

    if arxiv_id:
        source_url = f"https://arxiv.org/abs/{arxiv_id}"
        identifier = f"arXiv: {arxiv_id} / DOI: {doi}"
    else:
        source_url = f"https://doi.org/{doi}"
        identifier = f"DOI: {doi}"

    techniques = detect_techniques(text)
    formulations = detect_formulations(text)
    if not formulations:
        formulations = list(CATEGORY_FORMULATIONS.get(row["category"], []))
    sentences = pick_core_sentences(abstract)

    lines = []
    lines.append(f"### [{rank}] {title}")
    if title_ja:
        lines.append(f"**日本語訳（機械翻訳）**: {title_ja}")
    lines.append(f"- **著者 / 発行年 / 媒体**: {format_authors(authors)} ({year}), {venue}")
    lines.append(f"- **ソースURL**: [{source_url}]({source_url})")
    lines.append(f"- **ID**: {identifier}")
    lines.append("- **理論・アルゴリズムの核心**:")
    if formulations:
        lines.append(f"  - 支配方程式・手法: {'; '.join(formulations[:4])}")
    if techniques:
        labels = [TECHNIQUES[key]["label"] for key in techniques[:4]]
        lines.append(f"  - 手法タグ: {', '.join(labels)}")
    if sentences:
        lines.append(f"  - 抄録に基づく要約: {' '.join(sentences)}")
    else:
        lines.append(
            f"  - 抄録未登録（標題と掲載媒体から要約）: 「{title}」（{venue}, {year}）"
            f"／当該レコードに抄録が提供されていないため、支配方程式・手法は標題と分類情報から推定した。")
    lines.append("- **本3Dシミュレーションへの適用価値**:")
    if techniques:
        for key in techniques[:3]:
            lines.append(f"  - {TECHNIQUES[key]['apply']}")
    else:
        lines.append(f"  - {CATEGORY_FALLBACK.get(row['category'], '')}")
    return "\n".join(lines)


def build_json_entry(row, rank: int, title_ja: str) -> Dict:
    doi = row["doi"]
    arxiv_id = row["arxiv_id"] or ""
    abstract = row["abstract"] or ""
    text = f"{row['title']} {abstract}".lower()
    techniques = detect_techniques(text)
    formulations = detect_formulations(text)
    if not formulations:
        formulations = list(CATEGORY_FORMULATIONS.get(row["category"], []))
    return {
        "rank": rank,
        "category": row["category"],
        "title": row["title"],
        "title_ja": title_ja,
        "authors": loads(row["authors"], []) or [],
        "year": row["year"],
        "venue": venue_label(row),
        "publisher": row["publisher"] or "",
        "doi": doi,
        "arxiv_id": arxiv_id,
        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else f"https://doi.org/{doi}",
        "abstract": abstract,
        "core_theory": {
            "formulations": formulations[:6],
            "technique_tags": [TECHNIQUES[key]["label"] for key in techniques[:6]],
            "summary_sentences": pick_core_sentences(abstract),
            "basis": "abstract" if abstract else "title",
        },
        "simulator_application": {
            "proposals": [TECHNIQUES[key]["apply"] for key in techniques[:3]]
            or [CATEGORY_FALLBACK.get(row["category"], "")],
            "note": "本項目は本シミュレータへの適用提案であり、論文の主張ではない。",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Render chunked Markdown and JSONL reports")
    parser.add_argument("--per-category", type=int, default=400)
    parser.add_argument("--chunk", type=int, default=CHUNK_SIZE)
    args = parser.parse_args()

    conn = get_db()
    conn.row_factory = sqlite3.Row
    translations = {
        row["doi"]: (row["title_ja"], row["engine"])
        for row in conn.execute("SELECT doi, title_ja, engine FROM translations")
    }
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tracker_categories = {}
    all_dois: List[str] = []
    all_arxiv: List[str] = []
    total_entries = 0

    for category in CATEGORIES:
        meta = CATEGORY_META[category]
        category_dir = BASE_DIR / category
        category_dir.mkdir(parents=True, exist_ok=True)
        rows = conn.execute(
            """SELECT s.rank AS rank, s.doi AS doi, p.title AS title, p.authors AS authors,
                      p.year AS year, p.venue AS venue, p.publisher AS publisher, p.ptype AS ptype,
                      p.abstract AS abstract, p.arxiv_id AS arxiv_id, p.url AS url,
                      p.source AS source, p.citations AS citations
               FROM selected s JOIN papers p ON p.doi = s.doi
               WHERE s.category = ?
               ORDER BY s.rank""",
            (category,),
        ).fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            row["category"] = category

        chunk_files = []
        source_counts = {"crossref": 0, "datacite": 0}
        with_abstract = 0
        for chunk_index in range(0, len(rows), args.chunk):
            chunk = rows[chunk_index:chunk_index + args.chunk]
            start = chunk_index + 1
            end = chunk_index + len(chunk)
            md_name = f"papers_{start:03d}_{end:03d}.md"
            jsonl_name = f"papers_{start:03d}_{end:03d}.jsonl"
            md_lines = [
                f"# {category} 収集論文 {start:03d}-{end:03d}",
                "",
                f"- カテゴリ: {meta['title_ja']} ({meta['title_en']})",
                f"- 収録件数: {len(chunk)} 件（当該チャンク）",
                "- 出典: Crossref（DOI レジストリ）および DataCite（arXiv プレプリント DOI 10.48550）",
                "- 注記: 「本3Dシミュレーションへの適用価値」は本シミュレータへの適用提案であり、論文の主張ではない。",
                "",
            ]
            for offset, row in enumerate(chunk):
                title_ja, _engine = translations.get(row["doi"], ("", ""))
                rank = start + offset
                md_lines.append(build_entry(row, rank, title_ja))
                md_lines.append("")
                source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
                if len(row["abstract"] or "") > 80:
                    with_abstract += 1
                all_dois.append(row["doi"])
                if row["arxiv_id"]:
                    all_arxiv.append(row["arxiv_id"])
            (category_dir / md_name).write_text("\n".join(md_lines) + "\n", encoding="utf-8")
            with (category_dir / jsonl_name).open("w", encoding="utf-8") as handle:
                for offset, row in enumerate(chunk):
                    title_ja, _engine = translations.get(row["doi"], ("", ""))
                    entry = build_json_entry(row, start + offset, title_ja)
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            chunk_files.append(md_name)

        tracker_categories[category] = {
            "title_ja": meta["title_ja"],
            "title_en": meta["title_en"],
            "target": args.per_category,
            "collected": len(rows),
            "chunks": chunk_files,
            "jsonl_chunks": [name.replace(".md", ".jsonl") for name in chunk_files],
            "sources": source_counts,
            "with_registered_abstract": with_abstract,
        }
        total_entries += len(rows)
        print(f"[build] {category}: {len(rows)} entries in {len(chunk_files)} chunks "
              f"(abstract coverage {with_abstract}/{len(rows)})")

    tracker = {
        "generated_at": generated_at,
        "description": "3Dシミュレーション改善に向けた学術論文レジストリの進捗管理",
        "sources": {
            "crossref": "https://api.crossref.org/works (DOI レジストリ、抄録・掲載媒体付き)",
            "datacite": "https://api.datacite.org/dois (arXiv プレプリント DOI 10.48550/arxiv.*)",
            "title_translation": "Google Translate 公開エンドポイント (MyMemory フォールバック)",
        },
        "categories": tracker_categories,
        "totals": {
            "papers": total_entries,
            "unique_doi": len(set(all_dois)),
            "unique_arxiv_ids": len(set(all_arxiv)),
            "target": args.per_category * len(CATEGORIES),
        },
        "dedup_index": {
            "note": "重複排除に用いた取得済み識別子（同一論文の arXiv 版と出版版は 1 件へ統合済み）",
            "dois": sorted(set(all_dois)),
            "arxiv_ids": sorted(set(all_arxiv)),
        },
    }
    (BASE_DIR / "progress_tracker.json").write_text(
        json.dumps(tracker, ensure_ascii=False, indent=2), encoding="utf-8")

    index_lines = [
        "# 3Dシミュレーション改善のための学術論文レジストリ（2,000件）",
        "",
        f"- 生成日時 (UTC): {generated_at}",
        f"- 総件数: {total_entries} 件（{len(CATEGORIES)} カテゴリ × {args.per_category} 件）",
        "- 出典: Crossref（DOI レジストリ）および DataCite（arXiv プレプリント DOI）",
        "- 検証結果: `search/validation_report.json`（レジストリ照合・URL 解決確認）",
        "- 進捗管理: `search/progress_tracker.json`",
        "- 注記: 各論文の「本3Dシミュレーションへの適用価値」は本シミュレータへの適用提案であり、論文の主張ではない。",
        "",
    ]
    for category in CATEGORIES:
        meta = CATEGORY_META[category]
        info = tracker_categories[category]
        index_lines.append(f"## {category} — {meta['title_ja']}（{info['collected']} 件）")
        index_lines.append("")
        index_lines.append("| # | チャンク | 件数 | 抄録あり | 出典 |")
        index_lines.append("|---|--------|------|---------|------|")
        for index, name in enumerate(info["chunks"]):
            start = index * args.chunk + 1
            end = min((index + 1) * args.chunk, info["collected"])
            index_lines.append(f"| {index + 1} | [{name}]({category}/{name}) | {end - start + 1} | - | - |")
        index_lines.append("")
    (BASE_DIR / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    conn.close()
    print(f"[build] total entries: {total_entries}; tracker written to search/progress_tracker.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
