# physics/AGENTS.md — 物理・6DoF・空力ドメイン不変条件

## P0 不変条件（最優先封鎖）
- **エネルギー非増加性**: 無風・無推力滑空時の力学的エネルギー単調非増加（$dE/dt \le 0$）を破るモデル・離散化を禁止。
- **座標系・風高度パリティ**: 風場(+z上)⇔世界NED(+z下)の符号変換は $w_{NED} = -w_{wind}$。高度参照は `z_up = -z_ned` で 6DoF CPU 経路と GPU バッチ経路を完全統一する。
- **微擾乱安定性・ゼロ除算防護**: AR・速度ゼロ近傍・大迎角での NaN/Inf 発散や異常推力生成を禁止。
- **対症療法禁止**: 物理異常を評価ペナルティや根拠なき定数調整で隠さず、物理式・離散化を修正する。

## Non-Goals（P2 凍結・許容近似）
- 現行準定常係数・5ステーションストリップモデルの枠外となるフル非定常 RANS/LES・フル FSI との微細精度差は、ユーザー明示指示なき限り現行ループで深掘りしない。

## 検証コマンド
```bash
python tools/session_manager.py pytest -- tests/test_aero.py tests/test_flight.py tests/test_physical_laws_audit.py -q
```
