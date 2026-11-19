import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

issues_to_reopen = [
    (194, "架空コミット 8383b1d で閉じられていましたが、flight_sim.py:2764 で Munk/Coriolis 項が無効化（コメントアウト）されたまま未解決でした。第一原理に基づき再オープンして修正します。"),
    (193, "架空コミット c1cbab7 で閉じられていましたが、空力接合部および抗力の二重計上防止の検証・実装が未完了でした。再オープンします。"),
    (195, "架空コミット 49d1f0e で閉じられていましたが、空力弾性変形による空力中心・ピッチングモーメント連成の反映が未完了でした。再オープンします。"),
    (197, "架空コミット 54ed2d4 で閉じられていましたが、大迎角・極低速・逆流域における有界性の厳密保証が未完了でした。再オープンします。"),
    (178, "コミットを伴わずドキュメント追記のみで閉じられていました。Stengel PaperPlane.m / 低Re風洞ベンチマーク回帰テストを実装するため再オープンします。")
]

for num, msg in issues_to_reopen:
    subprocess.run(["gh", "issue", "reopen", str(num)], capture_output=True, text=True)
    body_text = f"【再オープン（監査指摘）】\n{msg}"
    subprocess.run(["gh", "issue", "comment", str(num), "--body", body_text], capture_output=True, text=True)
    print(f"Issue #{num} reopened and commented.")
