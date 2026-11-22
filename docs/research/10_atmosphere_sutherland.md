# ISA 標準大気モデル・Sutherland 粘性則・局所大気環境パラメータ

## 1. 概要
紙飛行機の飛行高度は通常 0〜50 m 程度と小さいが、温度・湿度・気圧を正確に
反映した大気環境パラメータは、Reynolds 数（Re）を通じて空力係数
$C_L(\alpha, Re)$、$C_D(\alpha, Re)$ に直接影響する。
特に屋外投擲実験（ギネス記録は屋外または格納庫内）では、室温・標高・
湿度の差が無視できない誤差を生む。

主要参考文献:
- ICAO Doc 7488 (1993). *Manual of the ICAO Standard Atmosphere*, 3rd ed.
- Sutherland, W. (1893). *The Viscosity of Gases and Molecular Force*. Phil. Mag., 36, 507–531.
- Buck, A. L. (1981). *New equations for computing vapor pressure and enhancement factor*. J. Appl. Meteor., 20, 1527–1532.

---

## 2. ISA 標準大気（International Standard Atmosphere）

### 2.1 対流圏（Troposphere: $h \le 11000\,\text{m}$）の状態量

**基準値（海面高度 $h = 0$ m）:**
| 量 | 記号 | 値 | 単位 |
|---|---|---|---|
| 基準温度 | $T_0$ | 288.15 | K |
| 基準気圧 | $p_0$ | 101325 | Pa |
| 基準密度 | $\rho_0$ | 1.225 | kg/m³ |
| 乾燥空気気体定数 | $R$ | 287.05 | J/(kg·K) |
| 重力加速度 | $g_0$ | 9.80665 | m/s² |
| 気温減率 | $\lambda$ | -0.0065 | K/m |

**高度依存式:**
$$T(h) = T_0 + \lambda h$$
$$p(h) = p_0 \left(\frac{T(h)}{T_0}\right)^{-g_0 / (R \lambda)}$$
$$\rho(h) = \frac{p(h)}{R \cdot T(h)}$$

**音速:**
$$a(h) = \sqrt{\gamma_{air} R T(h)}, \quad \gamma_{air} = 1.4$$

---

## 3. Sutherland の粘性則

動粘性 $\mu$ の温度依存性:
$$\mu(T) = \mu_{ref} \left(\frac{T}{T_{ref}}\right)^{3/2} \cdot \frac{T_{ref} + S}{T + S}$$

| パラメータ | 値 |
|---|---|
| $\mu_{ref}$ | $1.716 \times 10^{-5}$ Pa·s |
| $T_{ref}$ | 273.15 K |
| $S$（Sutherland 定数） | 110.4 K |

**動粘性係数:**
$$\nu(T, h) = \frac{\mu(T)}{\rho(h)}$$

**Reynolds 数（翼弦長 $c$ に基づく）:**
$$Re = \frac{V_t \cdot c}{\nu}$$

**実例（ダーツ型, $c = 0.21$ m, $T = 20°\text{C} = 293.15$ K）:**
- $\mu \approx 1.81 \times 10^{-5}$ Pa·s
- $\nu \approx 1.51 \times 10^{-5}$ m²/s
- $V_t = 5$ m/s → $Re \approx 69,500$
- $V_t = 30$ m/s → $Re \approx 417,000$

---

## 4. 局所大気パラメータ（湿度補正）

### 4.1 湿潤空気の有効気体定数
飽和水蒸気圧（Buck 式, $T$ in °C）:
$$e_s(T) = 611.21 \cdot \exp\left(\frac{(18.678 - T/234.5) \cdot T}{257.14 + T}\right) \quad [\text{Pa}]$$

実際の水蒸気分圧:
$$e = \phi \cdot e_s \quad (\phi: \text{相対湿度 [0,1]})$$

湿潤空気密度:
$$\rho_{moist} = \frac{p - 0.378 e}{R_{dry} \cdot T}$$

> 湿度 $\phi = 0.8$、$T = 25°\text{C}$ の場合、乾燥大気に対して密度が約 $0.8\%$ 減少。
> これが揚力・抗力の約 $0.8\%$ 低下として現れる。

---

## 5. REST API 実装仕様 (`POST /api/v1/environment/atmosphere`)

### リクエスト
```json
{
  "temperature_C": 20.0,
  "pressure_Pa": 101325,
  "humidity_rel": 0.5,
  "altitude_m": 0.0,
  "use_isa": true
}
```

### レスポンス
```json
{
  "density_kg_m3": 1.204,
  "dynamic_viscosity_Pa_s": 1.813e-5,
  "kinematic_viscosity_m2_s": 1.506e-5,
  "speed_of_sound_m_s": 343.2,
  "temperature_K": 293.15,
  "pressure_Pa": 101325,
  "backend": "cpu_numpy",
  "reynolds_per_meter": 66357
}
```

---

## 6. シミュレータ実装への反映ポイント

1. **`POST /api/v1/environment/atmosphere` の即時反映**: スライダー変更で全モジュールに伝播（密度・粘性・音速の即時再計算）
2. **Re 依存 $C_D$ 補正**: $C_{D0}(Re) = 2 \times 1.328/\sqrt{Re}$ を $aero\_domain.py$ に組み込み
3. **マッハ数チェック**: $Ma = V_t / a$。紙飛行機は通常 $Ma < 0.1$ で非圧縮流れを仮定可
4. **ISA vs カスタム大気の切替フラグ**: `use_isa: true` でISA自動計算、`false` で手動値優先
