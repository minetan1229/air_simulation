# 3D 風場ベクトル場・乱流モデル・熱上昇気流 (Thermal Updraft)

## 1. 概要
流体力学的に妥当な風場モデルは、以下の3成分を加算合成した3Dベクトル場として定義する:
$$\mathbf{V}_{wind}(\mathbf{p}, t) = \mathbf{V}_{base}(\mathbf{p}) + \mathbf{V}_{thermal}(\mathbf{p}) + \mathbf{V}_{turb}(\mathbf{p}, t)$$

単純な一様流場（Uniform Wind Vector）ではなく、境界層・乱流・熱上昇気流の
物理的挙動を反映した空間変化場として実装する必要がある。

主要参考文献:
- Bridson, R. (2007). *Curl-noise for procedural fluid flow*. ACM SIGGRAPH, 46(3).
- Lenschow, D. H. (1986). *Aircraft Measurements in the Boundary Layer*. Probing the Atmospheric Boundary Layer.
- Stull, R. B. (1988). *An Introduction to Boundary Layer Meteorology*. Springer.

---

## 2. ベース風（境界層風速プロファイル）

### 2.1 べき乗則風速プロファイル（Power Law）
地表摩擦による大気境界層（Planetary Boundary Layer: PBL）内の風速高度変化:
$$V_{base}(z) = V_{ref} \cdot \left(\frac{z}{z_{ref}}\right)^{\alpha_{wind}}$$
- $V_{ref}$: 基準高度 $z_{ref}$ での風速（例: 10 m 高度で $5$ m/s）
- $\alpha_{wind} \approx 1/7 = 0.143$（標準中立大気）

### 2.2 対数則風速プロファイル（Logarithmic Law）
より厳密な表現（地表粗度 $z_0$ を考慮）:
$$V_{base}(z) = \frac{u_*}{\kappa} \ln\left(\frac{z}{z_0}\right)$$
- $u_*$: 摩擦速度（Friction Velocity）
- $\kappa = 0.41$: Karman 定数
- $z_0$: 地表粗度長（屋内床: $0.001$ m、短草地: $0.01$ m、都市: $0.5$ m）

---

## 3. 熱上昇気流（Thermal Updraft）モデル

熱上昇気流（Thermal）は、太陽放射で加熱された地面から上昇する温暖な空気の柱。
紙飛行機がサーマルに入ると上昇分速 $W_{thermal}$ が加算され、滑空時間が延びる。

### 3.1 Gaussian プロファイルモデル
中心座標 $(x_c, y_c)$、最大上昇速度 $W_{max}$、半径 $R_{thermal}$:
$$W_{thermal}(r, z) = W_{max} \cdot \exp\left(-\frac{r^2}{R_{thermal}^2}\right) \cdot f_{alt}(z)$$

高度依存係数（地表〜逆転層頂部）:
$$f_{alt}(z) = \frac{z}{H_{inversion}} \left(1 - \frac{z}{H_{inversion}}\right)^2 \cdot 4$$
- $H_{inversion}$: 逆転層高度（典型: $500 \sim 2000$ m）

### 3.2 典型的なパラメータ値
| 環境 | $W_{max}$ (m/s) | $R_{thermal}$ (m) |
|------|-----------------|-------------------|
| 晴天・屋外 | 1.0 〜 4.0 | 20 〜 100 |
| 強い日射 | 3.0 〜 8.0 | 50 〜 200 |
| 屋内環境 | 0.0 | — |

---

## 4. 乱流風場（Simplex Noise Curl Field）

### 4.1 Curl Noise（渦なし・質量保存）
通常の Simplex Noise をベクトル場に直接使うと発散（$\nabla \cdot \mathbf{V} \ne 0$）が生じ、
粒子が溜まる・消える等の非物理挙動が起きる。
Bridson (2007) の Curl Noise はベクトルポテンシャル $\boldsymbol{\Psi}$ のカールを取ることで
$\nabla \cdot \mathbf{V} = 0$（非圧縮条件）を自動的に満たす:

$$\mathbf{V}_{turb} = \nabla \times \boldsymbol{\Psi}$$
$$\boldsymbol{\Psi} = [\psi_1(\mathbf{p}, t),\, \psi_2(\mathbf{p}+\Delta_1, t),\, \psi_3(\mathbf{p}+\Delta_2, t)]$$

数値カール（中心差分近似, ステップ $\epsilon = 0.01$）:
$$(\nabla \times \boldsymbol{\Psi})_x = \frac{\psi_3(\mathbf{p}+\epsilon\hat{y}) - \psi_3(\mathbf{p}-\epsilon\hat{y}) - \psi_2(\mathbf{p}+\epsilon\hat{z}) + \psi_2(\mathbf{p}-\epsilon\hat{z})}{2\epsilon}$$

### 4.2 フラクタルブラウン運動（fBm）による多重スケール乱流
大規模うねりと小スケール乱れを同時に表現:
$$\mathbf{V}_{turb} = \sum_{i=1}^{N_{oct}} \frac{A}{2^i} \cdot \text{CurlNoise}(2^i \mathbf{p} + \mathbf{o}_i,\, t \cdot f_{time,i})$$
- $N_{oct}$: オクターブ数（典型: 4〜6）
- $A$: 乱流強度スケール
- $\mathbf{o}_i$: 各オクターブのシード offset

---

## 5. GPU 実装アーキテクチャ

### 5.1 バックエンド（Python / CuPy）
```python
def compute_wind_field(grid_coords, base_wind, thermals, turb_params, t, xp=np):
    """
    grid_coords: (N, 3) - サンプル点の3D座標
    戻り値: (N, 3) - 各点での風ベクトル
    """
    N = grid_coords.shape[0]
    W = xp.zeros((N, 3), dtype=xp.float32)

    # ベース風（高度依存）
    z = xp.maximum(grid_coords[:, 2], 0.01)
    z_factor = (z / 10.0) ** 0.143
    W[:, 0] += base_wind[0] * z_factor
    W[:, 1] += base_wind[1] * z_factor

    # 各サーマルの寄与を加算
    for thermal in thermals:
        cx, cy = thermal['x'], thermal['y']
        r2 = (grid_coords[:, 0] - cx)**2 + (grid_coords[:, 1] - cy)**2
        R2 = thermal['radius']**2
        strength = thermal['wmax'] * xp.exp(-r2 / R2)
        W[:, 2] += strength  # 上昇気流（+Z方向）

    # 乱流（Curl Noise 近似）
    freq = turb_params['frequency']
    amp = turb_params['amplitude']
    phase = t * turb_params['time_speed']
    W[:, 0] += amp * xp.sin(freq * grid_coords[:, 1] + phase)
    W[:, 1] += amp * xp.cos(freq * grid_coords[:, 0] + phase)
    W[:, 2] += amp * 0.3 * xp.sin(freq * grid_coords[:, 0] + freq * grid_coords[:, 1])

    return W
```

### 5.2 フロントエンド GPGPU（Three.js Shader）
```glsl
// Fragment Shader (Position Update)
uniform float uTime;
uniform float uWindX;
uniform float uWindZ;
uniform float uTurbulenceIntensity;

void main() {
    vec2 uv = gl_FragCoord.xy / resolution.xy;
    vec4 pos = texture2D(texturePosition, uv);

    // Curl Noise Turbulence
    vec3 coord = pos.xyz * 0.05 + vec3(0.0, 0.0, uTime * 0.3);
    float nx = snoise(coord);
    float ny = snoise(coord + vec3(1.7, 9.2, 0.0));
    float nz = snoise(coord + vec3(8.3, 2.8, 0.0));
    vec3 turbulence = vec3(ny - nz, nz - nx, nx - ny) * uTurbulenceIntensity;

    // 粒子速度更新（風場 + 乱流）
    vec3 windForce = vec3(uWindX, 0.0, uWindZ);
    pos.xyz += (windForce + turbulence) * 0.016;

    // 境界から出た粒子を反対側にラップ
    pos.x = mod(pos.x + 100.0, 200.0) - 100.0;
    pos.z = mod(pos.z + 100.0, 200.0) - 100.0;
    pos.w -= 0.005; // lifespan 減衰

    if (pos.w <= 0.0) {
        // スポーン: 風上端にリセット
        pos.xyz = vec3((rand(uv) - 0.5) * 200.0, rand(uv + 0.5) * 30.0, -100.0 + rand(uv + 1.0) * 10.0);
        pos.w = 1.0;
    }

    gl_FragColor = pos;
}
```

---

## 6. 風洞モードでの特別扱い

風洞モードでは:
- **一様流入 (Uniform Inflow)**: 入口断面に均一な $V_\infty$ を設定
- **乱流強度 $TI$ 制御**: スライダーで $0.1\% \sim 5\%$ まで可変
- **壁面閉塞補正**: 機体断面積比から局所流速を補正（第03ドキュメント参照）
- **後流 (Wake)**: 機体後方 2〜5 翼弦長で Wake 速度欠損を Gaussian モデルで表現
