"""常駐プロセスの「使用中 / 不要」判定と回収 (Process Hygiene).

判定基準の正本: `.agents/skills/process-hygiene.md`
本ツールはその基準を機械的に適用し、判定理由と回収結果を必ず添える。

軸:
  K1 ホスト      : 現在のセッション基盤 (kilo serve / IDE) は保護する。
  K2 ホスト所有  : ホストが所有する MCP ツールプールは使用中として保護する。
  K3 明示指定    : --keep-pid / --keep-since は使用中として保護する。
  S1 ワーカー    : `kilo run --auto` の並列ワーカーは停止対象。
  S2 オーケストレータ: supervisor / run-worker / pipeline-check は停止対象。
  S3 孤児プール  : 所有者が停止対象または既に終了した MCP プールは停止対象。
  S4 残存実行物  : pytest / GA実行 / 一時スクリプト / 開発サーバは停止対象。
  S5 孤児ブラウザ: ms-playwright 配下の chromium は停止対象。

使用例:
  python tools/process_hygiene.py --list
  python tools/process_hygiene.py --apply --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

if sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # pragma: no cover - 環境依存のフォールバック
        pass

IS_WINDOWS = os.name == "nt"

# 判定対象に含まれるプロセス名（それ以外は本ツールの影響外=対象外）。
STACK_NAMES = {
    "node",
    "python",
    "pythonw",
    "powershell",
    "pwsh",
    "kilo",
    "chrome",
    "msedge",
    "cmd",
    "conhost",
}

# MCP ツールプールの識別子（コマンドライン一致）。
MCP_MARKERS = (
    "mcp/launch.mjs",
    "mcp\\launch.mjs",
    "mcp_server_fetch",
    "@mcp/server-memory",
    "@mcp/server-sequential-thinking",
    "@upstash/context7-mcp",
    "@playwright/mcp",
)

# 一時実行物・常駐サーバの識別子。
WORKER_MARKERS = ("kilo.exe\" run", "kilo run", "kilo.exe run", " run --auto ")
ORCH_MARKERS = ("\\orch\\", "/orch/", "orch\\supervisor", "orch/supervisor")
TASK_MARKERS = (
    "pytest",
    "run_ga_research.py",
    "app.py",
    "probe_ga",
    "bench_",
    "list_dirty",
    "measure_p118",
    "explore_arch",
    "find_corruption",
    "ga_progress_probe",
)
TEMP_SCRIPT_DIRS = ("\\temp\\kilo\\", "/temp/kilo/")
# 一時スクリプト（Temp/kilo 配下の .py / .ps1）呼び出し。
TEMP_SCRIPT_RE = re.compile(r"[\\/]temp[\\/]kilo[\\/][^\s\"']+\.(ps1|py)", re.IGNORECASE)

HOST_NAME = "kilo"
PLAYWRIGHT_BROWSER_MARKER = "ms-playwright"

# 「進行あり（使用中）」の判定閾値: サンプリング窓内の CPU 消費時間 (秒)。
IN_USE_CPU_THRESHOLD_S = 0.05
# 進行軸のサンプリング窓 (秒)。0 でサンプリングを省略する。
DEFAULT_PROGRESS_WINDOW_S = 3.0
# ポリシー: in-use-stop = 使用中も停止 (ユーザー指示「使用しているタスクも停止」)、
# keep = 進行中のプロセスは使用中として保護 (安全側の既定)。
POLICY_IN_USE_STOP = "in-use-stop"
POLICY_KEEP = "keep"
POLICIES = (POLICY_IN_USE_STOP, POLICY_KEEP)


@dataclass
class Proc:
    pid: int
    ppid: int
    name: str
    cmd: str
    started: str
    exe: str = ""

    @property
    def lower_cmd(self) -> str:
        return self.cmd.lower()

    @property
    def lower_exe(self) -> str:
        return self.exe.lower()

    @property
    def stem(self) -> str:
        """拡張子を除いたプロセス名 (kilo.exe -> kilo)."""
        return self.name.lower().removesuffix(".exe")

    @property
    def is_mcp(self) -> bool:
        # Windows の npm パスは `\` 区切り（例: `@playwright\mcp\cli.js`）のため、
        # 区切り文字を正規化してから照合する。
        norm = self.lower_cmd.replace("/", "\\")
        return any(m.replace("/", "\\") in norm for m in MCP_MARKERS)

    @property
    def is_host(self) -> bool:
        return self.stem == HOST_NAME and " serve" in self.lower_cmd

    @property
    def is_worker(self) -> bool:
        if self.stem != HOST_NAME:
            return False
        return any(m in self.lower_cmd for m in WORKER_MARKERS)

    @property
    def is_orchestrator(self) -> bool:
        return any(m in self.lower_cmd for m in ORCH_MARKERS)

    @property
    def is_task(self) -> bool:
        if any(m in self.lower_cmd for m in TASK_MARKERS):
            return True
        if TEMP_SCRIPT_RE.search(self.cmd) and ".kilo" not in self.lower_cmd:
            return True
        if self.stem in {"python", "pythonw"}:
            if any(d in self.lower_cmd for d in TEMP_SCRIPT_DIRS):
                return ".kilo" not in self.lower_cmd
        return False

    @property
    def is_playwright_browser(self) -> bool:
        if self.stem not in {"chrome", "msedge", "chromium"}:
            return False
        return PLAYWRIGHT_BROWSER_MARKER in self.lower_exe or PLAYWRIGHT_BROWSER_MARKER in self.lower_cmd


@dataclass
class Evidence:
    """4軸判定の観測結果 (所有以外の3軸の実測値)."""

    progress_cpu_s: float = 0.0
    listen: int = 0
    established: int = 0
    duplicates: int = 1
    sampled: bool = False

    def is_progressing(self, threshold: float = IN_USE_CPU_THRESHOLD_S) -> bool:
        """進行軸: CPU 時間が窓内で増加していれば「進行あり（使用中）」."""
        return self.sampled and self.progress_cpu_s > threshold

    def is_idle_resident(self) -> bool:
        """接続軸: LISTEN はあるが ESTABLISHED が 0 ならアイドル常駐."""
        return self.listen > 0 and self.established == 0

    def is_redundant(self) -> bool:
        """冗長軸: 同一コマンドが複数プロセスで並走している."""
        return self.duplicates > 1


@dataclass
class Verdict:
    proc: Proc
    decision: str  # KEEP / STOP
    reason: str
    free_mb: float = 0.0
    extra: dict = field(default_factory=dict)
    evidence: Evidence | None = None


def _ps_json() -> list[dict]:
    """スタック関連プロセスのメタ情報を取得する."""
    if IS_WINDOWS:
        script = (
            "$names = @('" + "','".join(sorted(STACK_NAMES)) + "');"
            "Get-CimInstance Win32_Process | Where-Object { $names -contains $_.Name.Replace('.exe','') } "
            "| ForEach-Object { "
            "[pscustomobject]@{ pid=$_.ProcessId; ppid=$_.ParentProcessId; name=$_.Name; "
            "cmd=($_.CommandLine -replace '\\s+',' '); started=$_.CreationDate.ToString('o'); "
            "exe=$_.ExecutablePath } } | ConvertTo-Json -Compress -Depth 3"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if out.returncode != 0 or not out.stdout.strip():
            raise RuntimeError(f"process enumeration failed: {out.stderr.strip()[:400]}")
        data = json.loads(out.stdout)
        if isinstance(data, dict):
            data = [data]
        return data
    return _procfs_entries()


def _procfs_entries() -> list[dict]:
    """Linux 環境用: /proc から同等の情報を読む."""
    entries: list[dict] = []
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            raw = (proc_dir / "cmdline").read_bytes()
            cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
            stat = (proc_dir / "stat").read_text()
            ppid = int(stat.rsplit(")", 1)[1].split()[1])
            exe = os.readlink(str(proc_dir / "exe")) if (proc_dir / "exe").exists() else ""
        except (OSError, ValueError):
            continue
        name = os.path.basename(exe) if exe else proc_dir.name
        entries.append(
            {
                "pid": int(proc_dir.name),
                "ppid": ppid,
                "name": name,
                "cmd": cmd,
                "started": "",
                "exe": exe,
            }
        )
    return entries


def _live_pids() -> set[int]:
    if IS_WINDOWS:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", "(Get-Process).Id"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        pids = set()
        for token in out.stdout.split():
            try:
                pids.add(int(token))
            except ValueError:
                continue
        return pids
    return {e["pid"] for e in _procfs_entries()}


def _working_set_mb(pids: list[int]) -> dict[int, float]:
    if IS_WINDOWS:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ids = @(" + ",".join(str(p) for p in pids) + ");"
                "$r = Get-Process -Id $ids -ErrorAction SilentlyContinue | "
                "ForEach-Object { \"$($_.Id):$([math]::Round($_.WorkingSet64/1MB,1))\" }; "
                "$r -join ','",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        usage: dict[int, float] = {}
        for item in out.stdout.strip().split(","):
            if ":" not in item:
                continue
            pid_s, mb_s = item.split(":", 1)
            try:
                usage[int(pid_s)] = float(mb_s)
            except ValueError:
                continue
        return usage
    usage = {}
    for pid in pids:
        try:
            statm = Path(f"/proc/{pid}/statm").read_text().split()
            pages = int(statm[1])
            usage[pid] = pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
        except (OSError, ValueError, IndexError):
            continue
    return usage


def _clock_ticks() -> float:
    """1 秒あたりのクロックティック数 (Linux のみ使用)。"""
    try:
        return float(os.sysconf("SC_CLK_TCK"))  # type: ignore[attr-defined]
    except (AttributeError, OSError, ValueError):
        return 100.0


def _cpu_seconds(pids: list[int]) -> dict[int, float]:
    """各プロセスの累積 CPU 時間 (秒) を取得する."""
    if not pids:
        return {}
    if IS_WINDOWS:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ids = @(" + ",".join(str(p) for p in pids) + ");"
                "$r = Get-Process -Id $ids -ErrorAction SilentlyContinue | "
                "ForEach-Object { if ($null -ne $_.CPU) { \"$($_.Id):$($_.CPU)\" } }; "
                "$r -join ','",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        usage: dict[int, float] = {}
        for item in out.stdout.strip().split(","):
            if ":" not in item:
                continue
            pid_s, cpu_s = item.rsplit(":", 1)
            try:
                usage[int(pid_s)] = float(cpu_s)
            except ValueError:
                continue
        return usage
    usage = {}
    tick = _clock_ticks()
    for pid in pids:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            fields = stat.rsplit(")", 1)[1].split()
            usage[pid] = (int(fields[11]) + int(fields[12])) / tick
        except (OSError, ValueError, IndexError):
            continue
    return usage


def _windows_connection_counts() -> dict[int, tuple[int, int]]:
    """netstat -ano から (LISTEN数, ESTABLISHED数) を所有者PIDごとに集計する."""
    out = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, timeout=180
    )
    counts: dict[int, tuple[int, int]] = {}
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        state = parts[3].upper()
        if state not in {"LISTENING", "ESTABLISHED"}:
            continue
        try:
            pid = int(parts[4])
        except ValueError:
            continue
        listen, est = counts.get(pid, (0, 0))
        if state == "LISTENING":
            listen += 1
        else:
            est += 1
        counts[pid] = (listen, est)
    return counts


def _linux_connection_counts() -> dict[int, tuple[int, int]]:
    """/proc/net/tcp(6) の inode を所有PIDへ対応させ (LISTEN数, ESTABLISHED数) を集計する."""
    inode_to_pid: dict[str, int] = {}
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            for fd in (proc_dir / "fd").iterdir():
                try:
                    target = os.readlink(str(fd))
                except OSError:
                    continue
                if target.startswith("socket:["):
                    inode_to_pid[target[8:-1]] = int(proc_dir.name)
        except OSError:
            continue
    counts: dict[int, tuple[int, int]] = {}
    for path in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        try:
            lines = path.read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            state = parts[3]
            pid = inode_to_pid.get(parts[9])
            if pid is None:
                continue
            listen, est = counts.get(pid, (0, 0))
            if state == "0A":
                listen += 1
            elif state == "01":
                est += 1
            else:
                continue
            counts[pid] = (listen, est)
    return counts


def _connection_counts(pids: list[int]) -> dict[int, tuple[int, int]]:
    """(LISTEN数, ESTABLISHED数) を所有者PIDごとに取得する."""
    if not pids:
        return {}
    try:
        counts = _windows_connection_counts() if IS_WINDOWS else _linux_connection_counts()
    except (OSError, subprocess.SubprocessError):
        return {}
    wanted = set(pids)
    return {pid: value for pid, value in counts.items() if pid in wanted}


def command_signature(cmd: str) -> str:
    """冗長判定用のコマンド署名（実行ファイルパスを正規化し小文字化する）."""
    text = re.sub(r"\s+", " ", cmd).strip()
    text = re.sub(r'^"?[A-Za-z]:\\[^"]*?pythonw?\.exe"?', "python", text, flags=re.IGNORECASE)
    text = re.sub(r'^"?[A-Za-z]:\\[^"]*?node\.exe"?', "node", text, flags=re.IGNORECASE)
    text = re.sub(r'^"?[A-Za-z]:\\[^"]*?kilo\.exe"?', "kilo", text, flags=re.IGNORECASE)
    return text.lower()


def collect_evidence(
    procs: list[Proc], window_s: float = DEFAULT_PROGRESS_WINDOW_S
) -> dict[int, Evidence]:
    """進行軸・冗長軸・接続軸を実測する."""
    pids = [p.pid for p in procs]
    evidence: dict[int, Evidence] = {pid: Evidence() for pid in pids}

    signatures: dict[str, int] = {}
    for p in procs:
        signatures[command_signature(p.cmd)] = signatures.get(command_signature(p.cmd), 0) + 1

    for p in procs:
        evidence[p.pid].duplicates = signatures.get(command_signature(p.cmd), 1)

    for pid, (listen, est) in _connection_counts(pids).items():
        evidence[pid].listen = listen
        evidence[pid].established = est

    if window_s > 0:
        before = _cpu_seconds(pids)
        time.sleep(window_s)
        after = _cpu_seconds(pids)
        for pid in pids:
            item = evidence[pid]
            item.progress_cpu_s = round(max(0.0, after.get(pid, 0.0) - before.get(pid, 0.0)), 3)
            item.sampled = True
    return evidence


def _evidence_suffix(ev: Evidence | None) -> str:
    """判定理由に4軸の実測値を添える."""
    if ev is None or not ev.sampled:
        return ""
    parts = [f"進行={ev.progress_cpu_s:.1f}s"]
    if ev.listen or ev.established:
        parts.append(f"接続=LISTEN{ev.listen}/EST{ev.established}")
    if ev.is_redundant():
        parts.append(f"冗長={ev.duplicates}件")
    if ev.is_progressing():
        parts.append("使用中")
    return " [" + " ".join(parts) + "]"


def collect(keep_pids: set[int], keep_since: str | None) -> tuple[list[Proc], set[int]]:
    raw = _ps_json()
    procs = [
        Proc(
            pid=int(r["pid"]),
            ppid=int(r["ppid"]),
            name=str(r.get("name") or ""),
            cmd=str(r.get("cmd") or ""),
            started=str(r.get("started") or ""),
            exe=str(r.get("exe") or ""),
        )
        for r in raw
    ]
    live = _live_pids()
    return procs, live


def _after(iso: str, cutoff: str) -> bool:
    if not iso or not cutoff:
        return False
    try:
        a = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        b = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    except ValueError:
        return False
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a >= b


def classify(
    procs: list[Proc],
    live: set[int],
    keep_pids: set[int],
    keep_since: str | None,
    evidence: dict[int, Evidence] | None = None,
    policy: str = POLICY_IN_USE_STOP,
) -> list[Verdict]:
    by_pid = {p.pid: p for p in procs}
    decided: dict[int, tuple[str, str]] = {}

    # 第1段: 基盤・ワーカー・オーケストレータ（所有関係の根）
    for p in procs:
        if p.pid in keep_pids:
            decided[p.pid] = ("KEEP", "K3 明示指定 (--keep-pid)")
        elif p.is_host:
            owner = by_pid.get(p.ppid)
            if owner is not None and (owner.is_task or owner.is_orchestrator):
                decided[p.pid] = ("STOP", f"S4 検証スクリプト起点の孤立サーバ (owner={owner.pid})")
            else:
                decided[p.pid] = ("KEEP", "K1 セッション基盤 (kilo serve)")
        elif p.is_worker:
            decided[p.pid] = ("STOP", "S1 並列ワーカーセッション")
        elif p.is_orchestrator:
            decided[p.pid] = ("STOP", "S2 オーケストレータ/監視スクリプト")

    # 第1.5段: K3 明示指定は子孫にも継承する（ラッパー指定で子孫まで保護する）
    if keep_pids:
        children: dict[int, list[int]] = {}
        for p in procs:
            children.setdefault(p.ppid, []).append(p.pid)
        stack = [pid for pid in keep_pids]
        seen: set[int] = set(stack)
        while stack:
            pid = stack.pop()
            for child in children.get(pid, []):
                if child in seen:
                    continue
                seen.add(child)
                stack.append(child)
                if child not in decided:
                    decided[child] = ("KEEP", "K3 明示指定 (子孫)")

    # 第2段: MCP ツールプール（所有者の判定を継承）
    def resolve_mcp(proc: Proc, depth: int = 0) -> tuple[str, str]:
        if proc.pid in decided:
            return decided[proc.pid]
        if depth > 8:
            return ("KEEP", "K2 ホスト所有のツールプール")
        parent = by_pid.get(proc.ppid)
        if parent is None:
            if proc.ppid in live:
                decided[proc.pid] = ("KEEP", "K2 ホスト所有のツールプール")
            else:
                decided[proc.pid] = ("STOP", f"S4 所有セッション終了 (owner={proc.ppid})")
            return decided[proc.pid]
        if parent.is_host:
            decided[proc.pid] = ("KEEP", "K2 ホスト所有のツールプール")
            return decided[proc.pid]
        if parent.is_mcp:
            p_decision, p_reason = resolve_mcp(parent, depth + 1)
            if p_decision == "STOP":
                decided[proc.pid] = ("STOP", f"S3 所有者の停止につき孤児化 (owner={parent.pid})")
            else:
                decided[proc.pid] = ("KEEP", p_reason)
            return decided[proc.pid]
        if parent.pid in decided:
            p_decision, p_reason = decided[parent.pid]
            if p_decision == "STOP":
                decided[proc.pid] = ("STOP", f"S3 所有者の停止につき孤児化 (owner={parent.pid})")
            else:
                decided[proc.pid] = ("KEEP", p_reason)
            return decided[proc.pid]
        if _after(proc.started, keep_since or ""):
            decided[proc.pid] = ("KEEP", "K3 保持基準時刻以降 (--keep-since)")
        else:
            decided[proc.pid] = ("STOP", f"S4 所有セッション不在 (owner={parent.pid})")
        return decided[proc.pid]

    for p in procs:
        if p.is_mcp and p.pid not in decided:
            resolve_mcp(p)

    # 第3段: ブラウザ（祖先链の判定を継承。ユーザー実ブラウザは対象外）
    for p in procs:
        if p.pid in decided or not p.is_playwright_browser:
            continue
        ancestor = by_pid.get(p.ppid)
        if ancestor is not None and ancestor.pid in decided:
            a_decision, a_reason = decided[ancestor.pid]
            decided[p.pid] = (
                ("KEEP", f"K2 ホスト所有ブラウザ ({a_reason})")
                if a_decision == "KEEP"
                else ("STOP", f"S5 所有者の停止につき孤児化 (owner={ancestor.pid})")
            )
        else:
            decided[p.pid] = ("STOP", "S5 孤児ブラウザ (ms-playwright)")

    # 第4段: 残存する実行物・常駐サーバ
    for p in procs:
        if p.pid in decided:
            continue
        if p.is_task:
            decided[p.pid] = ("STOP", "S4 残存する実行物/常駐サーバ")

    # ポリシー適用: keep の場合は「進行あり」の残存実行物を使用中として保護する
    # （S1/S2 は停止対象のまま。S1/S2 を停止に含める指示はユーザー明示時のみ）。
    if policy == POLICY_KEEP and evidence:
        for p in procs:
            if p.pid not in decided:
                continue
            decision, reason = decided[p.pid]
            ev = evidence.get(p.pid)
            if decision != "STOP" or ev is None or not ev.is_progressing():
                continue
            if not reason.startswith("S4"):
                continue
            decided[p.pid] = ("KEEP", "使用中 (進行あり) / 安全側の既定で保護")

    verdicts: list[Verdict] = []
    for p in procs:
        decision, reason = decided.get(p.pid, ("OUT", "対象外 (OS/IDE/ユーザーアプリ)"))
        ev = (evidence or {}).get(p.pid)
        if ev is not None and decision in {"STOP", "KEEP"}:
            reason = reason + _evidence_suffix(ev)
        verdicts.append(Verdict(p, decision, reason, evidence=ev))
    return verdicts


def _short(cmd: str, width: int = 96) -> str:
    cmd = re.sub(r"\s+", " ", cmd).strip()
    return cmd if len(cmd) <= width else cmd[: width - 3] + "..."


def _terminate(pid: int, tree: bool) -> tuple[bool, str]:
    if IS_WINDOWS:
        cmd = ["taskkill", "/PID", str(pid), "/F"] + (["/T"] if tree else [])
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return out.returncode == 0, (out.stdout or out.stderr).strip()
    try:
        os.kill(pid, 15)
        return True, "SIGTERM"
    except OSError as exc:
        return False, str(exc)


def _ancestor_pids(procs: list[Proc], pid: int) -> set[int]:
    """自分自身と祖先プロセスのPID集合（自己停止防止用）."""
    by_pid = {p.pid: p for p in procs}
    protected = {pid}
    cur = by_pid.get(pid)
    while cur is not None:
        protected.add(cur.pid)
        cur = by_pid.get(cur.ppid)
    return protected


def apply(verdicts: list[Verdict], dry_run: bool) -> dict:
    stops = [v for v in verdicts if v.decision == "STOP"]
    protected = _ancestor_pids([v.proc for v in verdicts], os.getpid())
    stops = [v for v in stops if v.proc.pid not in protected]
    stop_pids = {v.proc.pid for v in stops}
    roots = [v for v in stops if v.proc.ppid not in stop_pids]

    def priority(v: Verdict) -> int:
        if v.proc.is_orchestrator:
            return 0
        if v.proc.is_worker:
            return 1
        return 2

    roots.sort(key=lambda v: (priority(v), -v.free_mb))
    results: list[dict] = []
    for v in roots:
        if dry_run:
            results.append({"pid": v.proc.pid, "mode": "TREE", "ok": True, "detail": _short(v.proc.cmd, 60)})
            continue
        ok, detail = _terminate(v.proc.pid, tree=True)
        results.append({"pid": v.proc.pid, "mode": "TREE", "ok": ok, "detail": detail[:120]})
    if not dry_run:
        time.sleep(1.0)
        handled = {r["pid"] for r in results}
        for v in stops:
            if v.proc.pid in handled:
                continue
            ok, detail = _terminate(v.proc.pid, tree=False)
            results.append({"pid": v.proc.pid, "mode": "SINGLE", "ok": ok, "detail": detail[:120]})
    return {
        "stop_count": len(stops),
        "root_count": len(roots),
        "freed_mb": round(sum(v.free_mb for v in stops), 1),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="常駐プロセスの使用中判定と回収")
    parser.add_argument("--list", action="store_true", help="判定結果を一覧表示する")
    parser.add_argument("--apply", action="store_true", help="STOP 判定されたプロセスを停止する")
    parser.add_argument("--keep-pid", type=int, action="append", default=[], help="使用中として保護するPID")
    parser.add_argument("--keep-since", default="", help="この時刻以降に起動したプロセスを保護する (ISO8601)")
    parser.add_argument(
        "--policy",
        choices=POLICIES,
        default=POLICY_IN_USE_STOP,
        help="使用中プロセスの扱い (既定: in-use-stop = 使用中も停止)",
    )
    parser.add_argument(
        "--progress-window",
        type=float,
        default=DEFAULT_PROGRESS_WINDOW_S,
        help="進行軸の CPU サンプリング窓 (秒)。0 でサンプリングを省略",
    )
    parser.add_argument("--json", action="store_true", help="結果をJSONで出力する")
    args = parser.parse_args(argv)

    procs, live = collect(set(args.keep_pid), args.keep_since or None)
    evidence = collect_evidence(procs, window_s=max(0.0, args.progress_window))
    verdicts = classify(
        procs,
        live,
        set(args.keep_pid),
        args.keep_since or None,
        evidence=evidence,
        policy=args.policy,
    )

    usage = _working_set_mb([v.proc.pid for v in verdicts])
    for v in verdicts:
        v.free_mb = usage.get(v.proc.pid, 0.0)

    if args.apply:
        outcome = apply(verdicts, dry_run=False)
    else:
        outcome = apply(verdicts, dry_run=True)
        outcome["mode"] = "dry-run"

    if args.json:
        print(
            json.dumps(
                {
                    "verdicts": [
                        {
                            "pid": v.proc.pid,
                            "ppid": v.proc.ppid,
                            "name": v.proc.name,
                            "ws_mb": v.free_mb,
                            "decision": v.decision,
                            "reason": v.reason,
                            "cmd": _short(v.proc.cmd, 160),
                            "evidence": (
                                {
                                    "progress_cpu_s": v.evidence.progress_cpu_s,
                                    "listen": v.evidence.listen,
                                    "established": v.evidence.established,
                                    "duplicates": v.evidence.duplicates,
                                    "in_use": v.evidence.is_progressing(),
                                }
                                if v.evidence is not None
                                else None
                            ),
                        }
                        for v in verdicts
                    ],
                    "outcome": outcome,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    stops = [v for v in verdicts if v.decision == "STOP"]
    keeps = [v for v in verdicts if v.decision == "KEEP"]
    print(f"判定: STOP={len(stops)} KEEP={len(keeps)} 合計WS={round(sum(v.free_mb for v in verdicts), 1)} MB")
    for v in sorted(stops, key=lambda x: -x.free_mb):
        print(f"  STOP pid={v.proc.pid:<6} ws={v.free_mb:>7.1f}MB {v.reason:<42} {_short(v.proc.cmd, 70)}")
    for v in sorted(keeps, key=lambda x: -x.free_mb):
        print(f"  KEEP pid={v.proc.pid:<6} ws={v.free_mb:>7.1f}MB {v.reason:<42} {_short(v.proc.cmd, 70)}")
    print(f"回収予定: {outcome['stop_count']} プロセス / 想定解放 {outcome['freed_mb']} MB (mode={outcome.get('mode')})")
    failed = [r for r in outcome["results"] if not r["ok"]]
    if failed:
        print(f"停止失敗: {len(failed)} 件")
        for r in failed[:20]:
            print(f"  ! pid={r['pid']} {r['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
