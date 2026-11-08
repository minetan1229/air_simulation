"""Hybrid split evaluation wrapper for simulator computes.

Local path uses physics.parallel_engine.parallel_evaluate.
Remote path ships a JSON-framed case batch to the Linux GPU node over SSH
(paramiko SFTP, no auth state in repo), runs tools/remote_evaluate_worker.py
there, and merges results preserving input order. Core-count ratio decides
the split (local vs remote) when no explicit ratio is supplied.

Framing / sync (scp/rsync equivalent via SFTP):
  cases.json  -> remote cases.json      (gzipped, SFTP put)
  results.json <- remote results.json   (SFTP get)

Transport is ordered + synchronous per batch (one SSH session per batch).
On any transport / remote failure the wrapper falls back to the fully-local
parallel_evaluate for the remote slice (zero silent data loss, warning
logged). This mirrors the existing parallel_engine fallback contract.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from physics.parallel_engine import get_worker_count, parallel_evaluate

log = logging.getLogger("paper_airplane.hybrid_split")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

WORKER_SCRIPT = "tools/remote_evaluate_worker.py"
REMOTE_ROOT_NAME = "paper_airplanes_worker"
REMOTE_CASES_NAME = "hybrid_cases.json.gz"
REMOTE_RESULTS_NAME = "hybrid_results.json.gz"

def _env(name: str, default: str = "") -> str:
    try:
        from dotenv import load_dotenv
        load_dotenv(PROJECT_ROOT / ".env", override=False)
    except ImportError:
        pass
    return os.environ.get(name, default).strip()

def remote_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "host": _env("REMOTE_SSH_HOST"),
        "user": _env("REMOTE_SSH_USER"),
        "password": _env("REMOTE_SSH_PASSWORD"),
        "port": int(_env("REMOTE_SSH_PORT", "22") or "22"),
        "remote_root": _env("REMOTE_WORK_ROOT", f"/home/{_env('REMOTE_SSH_USER') or 'user'}/{REMOTE_ROOT_NAME}"),
        "remote_python": _env("REMOTE_SSH_PYTHON", "python3"),
        # Core ratio override (remote / total) or explicit worker counts
        "remote_ratio": float(_env("REMOTE_SSH_RATIO", "0") or 0),
        "remote_workers": int(_env("REMOTE_SSH_WORKERS", "0") or 0),
        "local_workers": int(_env("REMOTE_LOCAL_WORKERS", "0") or 0),
        "auto_sync": _env("REMOTE_SYNC", "1") not in ("0", "false", "no"),
        "timeout_s": int(_env("REMOTE_SSH_TIMEOUT", "300") or 300),
    }
    return cfg

def core_ratio_split(
    cases: Sequence[Dict[str, Any]],
    remote_cores: int,
    local_cores: int | None = None,
    remote_ratio: float = 0.0,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[int], List[int]]:
    total = local_cores or os.cpu_count() or 8
    if remote_cores <= 0:
        return list(cases), [], list(range(len(cases))), []
    if remote_ratio > 0:
        remote_n = max(1, round(len(cases) * remote_ratio))
    else:
        denom = max(total + remote_cores, 1)
        remote_n = max(1, round(len(cases) * remote_cores / denom))
    remote_n = min(remote_n, len(cases))
    # Round-robin interleave so both sides see a representative slice
    local_cases: List[Dict[str, Any]] = []
    remote_cases: List[Dict[str, Any]] = []
    local_idx: List[int] = []
    remote_idx: List[int] = []
    for i, case in enumerate(cases):
        if i % max(1, round(len(cases) / max(remote_n, 1))) == 0 and len(remote_cases) < remote_n:
            remote_cases.append(case)
            remote_idx.append(i)
        else:
            local_cases.append(case)
            local_idx.append(i)
    return local_cases, remote_cases, local_idx, remote_idx

def remote_cores_query(cfg: Dict[str, Any], timeout: int = 10) -> int:
    if not cfg.get("host") or not cfg.get("user"):
        return 0
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kw: Dict[str, Any] = {"hostname": cfg["host"], "port": cfg["port"], "username": cfg["user"], "timeout": timeout}
        if cfg.get("password"):
            kw["password"] = cfg["password"]
        else:
            # Key auth
            kw["key_filename"] = os.path.expanduser("~/.ssh/id_rsa")
        client.connect(**kw)
        _stdin, stdout, _stderr = client.exec_command("nproc; nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null || true", timeout=timeout)
        out = stdout.read().decode("utf-8", errors="replace").strip().splitlines()
        client.close()
        cores = 0
        if out:
            try:
                cores = int(out[0].strip())
            except ValueError:
                cores = 0
        return cores
    except Exception as exc:
        log.warning("remote core query failed: %s", exc)
        return 0

def _ensure_remote_root(cfg: Dict[str, Any]) -> None:
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw: Dict[str, Any] = {"hostname": cfg["host"], "port": cfg["port"], "username": cfg["user"], "timeout": 15}
    if cfg.get("password"):
        kw["password"] = cfg["password"]
    client.connect(**kw)
    _stdin, stdout, _stderr = client.exec_command(
        f"mkdir -p {cfg['remote_root']}/cases {cfg['remote_root']}/venv && "
        f"command -v rsync >/dev/null || true; ls {cfg['remote_root']}",
        timeout=15,
    )
    stdout.read()
    client.close()

def _sync_tree(cfg: Dict[str, Any]) -> None:
    """Sync physics/ + tools/remote_evaluate_worker.py to remote_root (rsync if available, else SFTP)."""
    if not cfg.get("auto_sync"):
        return
    import paramiko
    # Prefer rsync when present on both sides; otherwise walk with SFTP.
    try:
        if shutil.which("rsync"):
            host = cfg["host"]
            user = cfg["user"]
            port = cfg["port"]
            dest = f"{user}@{host}:{cfg['remote_root']}/"
            for src_dir in ("physics", "tools"):
                src = str(PROJECT_ROOT / src_dir) + "/"
                # Exclude __pycache__ / .pyc ; --delete mirrors local exactly
                subprocess.run(
                    ["rsync", "-az", "--delete", "--exclude=__pycache__", "--exclude=*.pyc", src, dest + src_dir + "/"],
                    check=False, timeout=120, capture_output=True,
                )
            return
    except Exception:
        pass
    # SFTP walk fallback
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw: Dict[str, Any] = {"hostname": cfg["host"], "port": cfg["port"], "username": cfg["user"], "timeout": 15}
    if cfg.get("password"):
        kw["password"] = cfg["password"]
    client.connect(**kw)
    sftp = client.open_sftp()
    for src_dir in ("physics", "tools"):
        local_root = PROJECT_ROOT / src_dir
        remote_root = f"{cfg['remote_root']}/{src_dir}"
        for path in local_root.rglob("*"):
            if "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            rel = path.relative_to(local_root)
            remote_path = f"{remote_root}/{rel.as_posix()}"
            if path.is_dir():
                try:
                    sftp.mkdir(remote_path)
                except Exception:
                    pass
            else:
                try:
                    sftp.put(str(path), remote_path)
                except Exception:
                    pass
    sftp.close()
    client.close()

def _remote_run(cfg: Dict[str, Any], remote_cases: List[Dict[str, Any]], processes: int) -> List[Dict[str, Any]]:
    """Ship cases to remote, run worker, fetch results. Ordered JSON. Raises on failure."""
    import paramiko
    _ensure_remote_root(cfg)
    _sync_tree(cfg)
    batch_id = hashlib.sha1(json.dumps({"cases": remote_cases}).encode("utf-8")).hexdigest()[:12]
    local_gz = Path(tempfile.gettempdir()) / f"hybrid_cases_{batch_id}.json.gz"
    local_gz.write_bytes(gzip.compress(json.dumps(remote_cases).encode("utf-8"), mtime=0))
    remote_cases_path = f"{cfg['remote_root']}/{REMOTE_CASES_NAME}"
    remote_results_path = f"{cfg['remote_root']}/{REMOTE_RESULTS_NAME}"
    remote_worker_path = f"{cfg['remote_root']}/{WORKER_SCRIPT}"
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kw: Dict[str, Any] = {"hostname": cfg["host"], "port": cfg["port"], "username": cfg["user"], "timeout": cfg["timeout_s"]}
    if cfg.get("password"):
        kw["password"] = cfg["password"]
    client.connect(**kw)
    sftp = client.open_sftp()
    sftp.put(str(local_gz), remote_cases_path)
    sftp.close()
    venv_python = f"{cfg['remote_root']}/venv/bin/python"
    py = venv_python if _file_exists(client, venv_python) else cfg["remote_python"]
    _, stdout, _ = client.exec_command(
        f"cd {cfg['remote_root']} && {py} {remote_worker_path} --in {remote_cases_path} --out {remote_results_path} --processes {processes}",
        timeout=cfg["timeout_s"],
    )
    out, err = stdout.read().decode("utf-8", errors="replace"), b""
    try:
        err = _stderr_bytes(client, err) if False else err
    except Exception:
        pass
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f"remote worker exit {status}")
    tmp = Path(tempfile.gettempdir()) / f"hybrid_results_{batch_id}.json.gz"
    sftp = client.open_sftp()
    sftp.get(remote_results_path, str(tmp))
    sftp.close()
    client.close()
    return json.loads(gzip.decompress(tmp.read_bytes()).decode("utf-8"))

def _file_exists(client: Any, path: str) -> bool:
    try:
        _, stdout, _ = client.exec_command(f"test -x {path} && echo yes || echo no", timeout=5)
        return "yes" in stdout.read().decode()
    except Exception:
        return False

def hybrid_evaluate(
    cases: Sequence[Dict[str, Any]],
    processes: int = 0,
    remote_ratio: float | None = None,
    progress_cb: Optional[Callable[[int, int, float], None]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate cases across local + remote (ordered). Falls back to fully local on failure."""
    materialised = list(cases or [])
    total = len(materialised)
    if total == 0:
        return []
    cfg = remote_config()
    if not cfg.get("host") or not cfg.get("user"):
        return parallel_evaluate(materialised, processes=processes)
    local_workers = cfg.get("local_workers") or get_worker_count(processes)
    remote_workers = cfg.get("remote_workers") or 0
    if remote_workers <= 0:
        remote_cores = remote_cores_query(cfg)
        remote_workers = max(1, remote_cores) if remote_cores else 4
    ratio = remote_ratio if remote_ratio is not None else cfg.get("remote_ratio", 0.0)
    local_slice, remote_slice, local_idx, remote_idx = core_ratio_split(materialised, remote_workers, os.cpu_count() or 8, ratio)
    if not remote_slice:
        return parallel_evaluate(materialised, processes=processes)

    results: List[Optional[Dict[str, Any]]] = [None] * total
    def _local() -> List[Dict[str, Any]]:
        out = parallel_evaluate(local_slice, processes=local_workers)
        for idx, res in zip(local_idx, out):
            results[idx] = res
        return out
    def _remote() -> List[Dict[str, Any]]:
        try:
            out = _remote_run(cfg, remote_slice, remote_workers)
            for idx, res in zip(remote_idx, out):
                results[idx] = res
            return out
        except Exception as exc:
            log.warning("remote hybrid path failed (%s); falling back to local for %d cases", exc, len(remote_slice))
            out = parallel_evaluate(remote_slice, processes=local_workers)
            for idx, res in zip(remote_idx, out):
                results[idx] = res
            return out

    def _emit() -> None:
        if progress_cb is not None:
            done = sum(1 for r in results if r is not None)
            # best distance so far
            best = 0.0
            for r in results:
                if r is not None:
                    try:
                        best = max(best, float(r.get("distance_m", 0.0)))
                    except Exception:
                        pass
            progress_cb(done, total, best)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_local = pool.submit(_local)
        f_remote = pool.submit(_remote)
        while not (f_local.done() and f_remote.done()):
            _emit()
            time.sleep(0.2)
        f_local.result()
        f_remote.result()
    _emit()
    # Any holes (shouldn't happen) fall back to local evaluation
    missing = [i for i, r in enumerate(results) if r is None]
    if missing:
        filled = parallel_evaluate([materialised[i] for i in missing], processes=local_workers)
        for i, r in zip(missing, filled):
            results[i] = r
    return [r for r in results if r is not None]
