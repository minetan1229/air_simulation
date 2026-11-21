# Three.js GPGPU 風粒子・応力シェーダー・Google Material Design UI 仕様

## 1. 概要
フロントエンドの GPU アクセラレーション描画は以下の3モジュールで構成:
1. **GPGPU 風流線粒子システム**: `GPUComputationRenderer` (Texture Ping-Pong) で 100,000+ 粒子
2. **動的応力ヒートマップシェーダー**: 翼面頂点ごとの応力値をリアルタイムカラーマッピング
3. **Google Material Design 準拠 UI**: Google Clean Standard に従ったレイアウト・カラーシステム

---

## 2. GPGPU 風流線粒子システム

### 2.1 アーキテクチャ概要
```
[バックエンドAPI] → 風場グリッドデータ (JSON) → [フロントエンドstate]
                                                         ↓
[GPUComputationRenderer]
  ├── Position Texture (RGBA Float32): 各粒子の (x, y, z, lifetime)
  ├── Velocity Texture (RGBA Float32): 各粒子の (vx, vy, vz, speed)
  └── Fragment Shader (Compute): 風場力 + Curl Noise + 境界処理
                                                         ↓
[THREE.Points + Custom Vertex/Fragment Shader]
  ├── Vertex Shader: Position Texture をサンプリングして頂点を配置
  └── Fragment Shader: speed に応じた色・透明度・ポイントスプライト描画
```

### 2.2 粒子数スケーリング
| 品質設定 | 粒子数 (N) | テクスチャサイズ | GPU負荷 |
|---------|-----------|----------------|--------|
| Low | 4,096 | 64×64 | 軽 |
| Medium | 16,384 | 128×128 | 中 |
| High | 65,536 | 256×256 | 高 |
| Ultra | 262,144 | 512×512 | 極高 |

### 2.3 Position Update Fragment Shader（コア実装）
```glsl
uniform float uTime;
uniform float uDeltaTime;
uniform float uWindX;
uniform float uWindY;
uniform float uWindZ;
uniform float uTurbulence;
uniform float uThermalStrength;
uniform vec3 uThermalCenter;

float snoise(vec4 v); // Simplex 4D noise

void main() {
    vec2 uv = gl_FragCoord.xy / resolution.xy;
    vec4 posLife = texture2D(texturePosition, uv);
    vec4 velSpeed = texture2D(textureVelocity, uv);

    vec3 pos = posLife.xyz;
    float lifetime = posLife.w;
    vec3 vel = velSpeed.xyz;

    // 1. ベース風力
    vec3 windForce = vec3(uWindX, uWindY, uWindZ);

    // 2. Curl Noise 乱流
    float eps = 0.01;
    vec4 noiseCoord = vec4(pos * 0.03, uTime * 0.2);
    float n1 = snoise(noiseCoord + vec4(0, eps, 0, 0)) - snoise(noiseCoord - vec4(0, eps, 0, 0));
    float n2 = snoise(noiseCoord + vec4(0, 0, eps, 0)) - snoise(noiseCoord - vec4(0, 0, eps, 0));
    float n3 = snoise(noiseCoord + vec4(eps, 0, 0, 0)) - snoise(noiseCoord - vec4(eps, 0, 0, 0));
    float n4 = snoise(noiseCoord + vec4(0, 0, 0, eps)) - snoise(noiseCoord - vec4(0, 0, 0, eps));
    vec3 curlNoise = vec3(n1 - n4, n2 - n3, n3 - n2) * uTurbulence;

    // 3. 熱上昇気流
    vec2 thermalDist = pos.xz - uThermalCenter.xz;
    float thermalR = length(thermalDist);
    float thermalForce = uThermalStrength * exp(-thermalR * thermalR / (30.0 * 30.0));
    vec3 thermalWind = vec3(0, thermalForce, 0);

    // 速度・位置更新
    vel = windForce + curlNoise + thermalWind;
    pos += vel * uDeltaTime;
    lifetime -= uDeltaTime * 0.3;

    // 境界超過でリスポーン
    if (lifetime <= 0.0 || abs(pos.x) > 150.0 || pos.y < -5.0 || pos.y > 60.0) {
        float seed = uv.x + uv.y * 1000.0 + uTime;
        pos = vec3(
            (fract(sin(seed) * 43758.5) - 0.5) * 200.0,
            fract(sin(seed * 1.7) * 43758.5) * 30.0,
            (fract(sin(seed * 2.3) * 43758.5) - 0.5) * 200.0
        );
        lifetime = 0.5 + fract(sin(seed * 3.1) * 43758.5) * 0.5;
        vel = vec3(0);
    }

    gl_FragColor = vec4(pos, lifetime);
}
```

---

## 3. 応力ヒートマップシェーダー

### 3.1 Vertex Shader（翼面変形付き）
```glsl
attribute float aStress;        // 各頂点の正規化応力 [0, 1]
attribute vec3 aNormal;
uniform float uDeformScale;     // 空力弾性変形スケール
varying float vStress;
varying vec3 vNormal;

void main() {
    vStress = aStress;
    vNormal = normalMatrix * aNormal;

    // 高応力部位を法線方向にわずかに変位（空力弾性変形の視覚化）
    vec3 deformed = position + aNormal * aStress * uDeformScale;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(deformed, 1.0);
}
```

### 3.2 Fragment Shader（5段階カラーグラデーション）
```glsl
varying float vStress;
varying vec3 vNormal;

vec3 stressColor(float s) {
    // 0.0:青 → 0.25:緑 → 0.5:黄 → 0.75:橙 → 1.0:赤
    if (s < 0.25) return mix(vec3(0.0, 0.3, 1.0), vec3(0.0, 0.9, 0.3), s * 4.0);
    if (s < 0.5)  return mix(vec3(0.0, 0.9, 0.3), vec3(1.0, 0.95, 0.0), (s - 0.25) * 4.0);
    if (s < 0.75) return mix(vec3(1.0, 0.95, 0.0), vec3(1.0, 0.4, 0.0), (s - 0.5) * 4.0);
    return mix(vec3(1.0, 0.4, 0.0), vec3(1.0, 0.0, 0.0), (s - 0.75) * 4.0);
}

void main() {
    vec3 color = stressColor(vStress);
    // ランバート照明
    vec3 lightDir = normalize(vec3(1.0, 2.0, 1.5));
    float diffuse = max(dot(normalize(vNormal), lightDir), 0.1);
    gl_FragColor = vec4(color * diffuse, 1.0);
}
```

---

## 4. Google Material Design UI 仕様

### 4.1 カラーシステム
```css
:root {
  --bg-primary: #F8F9FA;       /* メイン背景 */
  --bg-card: #FFFFFF;          /* カード背景 */
  --accent-blue: #1A73E8;      /* Google Blue (Primary Action) */
  --accent-green: #34A853;     /* 正常状態 */
  --accent-yellow: #FBBC04;    /* 警告状態 */
  --accent-red: #EA4335;       /* 危険/破断状態 */
  --text-primary: #202124;     /* 主テキスト */
  --text-secondary: #5F6368;   /* 副テキスト */
  --shadow-card: 0 1px 3px rgba(60,64,67,.3), 0 4px 8px rgba(60,64,67,.15);
  --border-radius: 8px;
}
```

### 4.2 レイアウト構成
```
┌────────────────────────────────────────────────────────────────┐
│ HEADER BAR (64px)                                              │
│ [✈ Logo] [飛行モード▼] [風洞モード] [💾保存] [📤CSV] [📥Load]  │
├──────────┬─────────────────────────────────┬───────────────────┤
│ LEFT     │                                 │ RIGHT TELEMETRY   │
│ PANEL    │     THREE.JS 3D VIEWPORT        │ ┌─────────────┐   │
│ (280px)  │                                 │ │高度: 12.4m  │   │
│ ┌──────┐ │                                 │ │速度: 28.3m/s│   │
│ │機体  │ │                                 │ │迎え角: 8.2° │   │
│ │パラ  │ │                                 │ │L/D: 4.8    │   │
│ │メータ│ │                                 │ │Gforce: 2.1g│   │
│ └──────┘ │                                 │ │応力: 0.73  │   │
│ ┌──────┐ │                                 │ └─────────────┘   │
│ │環境  │ │                                 │ ┌ Chart.js ─────┐ │
│ │設定  │ │                                 │ │ リアルタイム   │ │
│ └──────┘ │                                 │ │ グラフ         │ │
│ ┌──────┐ │                                 │ └───────────────┘ │
│ │AoA   │ │                                 │                   │
│ │制御  │ │                                 │                   │
│ └──────┘ │                                 │                   │
├──────────┴─────────────────────────────────┴───────────────────┤
│ BOTTOM TIME CONTROL DOCK (72px)                                │
│ [⏮] [⏪0.25x] [⏪0.5x] [▶/⏸] [⏩2x] [⏩5x] [⏩10x] [⏭]        │
│ ━━━━━━━━━━━━━━━━━━━━━●━━━━━━━━━━━━━━━━━━━━━━  (タイムライン)  │
└────────────────────────────────────────────────────────────────┘
```

### 4.3 多重実行バグ防止の排他制御パターン
```javascript
class SimulationController {
    #isRunning = false;
    #abortController = null;

    async startSimulation() {
        if (this.#isRunning) {
            console.warn('[SIM] Already running, ignoring duplicate request');
            return;
        }
        this.#isRunning = true;
        this.#abortController = new AbortController();
        this.#setButtonsDisabled(true);

        try {
            await this.#runSimulationLoop(this.#abortController.signal);
        } finally {
            this.#isRunning = false;
            this.#setButtonsDisabled(false);
        }
    }

    stopSimulation() {
        this.#abortController?.abort();
    }

    #setButtonsDisabled(disabled) {
        document.querySelectorAll('[data-sim-action]').forEach(btn => {
            btn.disabled = disabled;
            btn.classList.toggle('btn--loading', disabled);
        });
    }
}
```
