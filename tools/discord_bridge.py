"""
Discord Remote Bridge for Antigravity (AGY)
Allows full remote control of the local Antigravity agent from Discord.

Features:
- Workspace strictly locked to simulation/v3 directory
- Real-time live status updates (Thinking progress, Tool executions, Subagent spawns & completion)
- Interactive multi-turn chat (DM or dedicated channel without repeated mentions)
- Chat List & History inspection (!chats / /chats) with resumption (!resume / /resume)
- Direct chat command (!chat <prompt> / /chat <prompt>)
- Dynamic model selection via slash commands (/model) or prefix (!model, !m)
- Model catalogue refresh from the live `agy models` CLI (!models refresh)
- Process cancellation / abort (!abort, /abort, or the stop reaction)
- User / Channel access control
- Defensive hardening: bounded concurrency, execution timeouts, input validation,
  stderr deadlock-free process handling and sanitized error reporting
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple, cast
from urllib.parse import urlparse

import discord
from discord import app_commands
from dotenv import load_dotenv

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    psutil = None  # type: ignore[assignment]


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("DiscordBridge")

# Load environment variables
load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read an integer environment variable, falling back to `default` on error."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Environment variable %s='%s' is not an integer. Using default %d.", name, raw, default)
        return default
    if value < minimum:
        logger.warning("Environment variable %s=%d is below the minimum %d. Using %d.", name, value, minimum, minimum)
        return minimum
    return value


def _env_id_set(name: str) -> Set[int]:
    """Parse a comma separated list of Discord snowflakes into a set of ints."""
    raw = os.getenv(name, "").strip()
    result: Set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            logger.warning("Ignoring non-numeric entry '%s' in %s.", token, name)
            continue
        result.add(int(token))
    return result


DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
ALLOWED_USER_IDS: Set[int] = _env_id_set("ALLOWED_USER_IDS")
ALLOWED_CHANNEL_IDS: Set[int] = _env_id_set("ALLOWED_CHANNEL_IDS")
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-3.8-flash-low").strip()

# Path to the Antigravity CLI (resolved against PATH when it is not an absolute path).
AGY_CLI = os.getenv("AGY_CLI_PATH", "agy").strip() or "agy"

# Maximum number of characters accepted for a single prompt.
# Windows caps a command line at 32767 characters, so a conservative limit keeps
# the child process launchable on every supported platform.
MAX_PROMPT_CHARS = _env_int("BRIDGE_MAX_PROMPT_CHARS", 8000, minimum=1)

# Concurrency limits (per channel and global) protecting the host from runaway agents.
MAX_TASKS_PER_CHANNEL = _env_int("BRIDGE_MAX_TASKS_PER_CHANNEL", 3, minimum=1)
MAX_TASKS_GLOBAL = _env_int("BRIDGE_MAX_TASKS_GLOBAL", 8, minimum=1)

# Wall clock limit for a single agent turn. The same value is forwarded to the CLI
# via --print-timeout so the child process stops on its own.
TASK_TIMEOUT_SECONDS = _env_int("BRIDGE_TASK_TIMEOUT_SECONDS", 1800, minimum=10)

# Minimum interval between two live embed edits (Discord rate limits).
LIVE_EDIT_INTERVAL_SECONDS = 1.5

# Number of recent conversations registered as persistent views on startup so that
# the reply button keeps working after a bot restart.
PERSISTENT_VIEW_SESSIONS = _env_int("BRIDGE_PERSISTENT_VIEW_SESSIONS", 50, minimum=0)

# Maximum number of characters per Discord message chunk.
DISCORD_CHUNK_LIMIT = 1900

# Responses longer than this are delivered as an attached file instead of many chunks.
DELIVERY_ATTACH_THRESHOLD = 12000

# Strictly lock workspace to simulation/v3
WORKSPACE_DIR = Path(__file__).resolve().parent.parent

# Brain directories to discover conversation sessions
CLI_BRAIN_DIR = Path(os.environ.get("USERPROFILE", "")) / ".gemini" / "antigravity-cli" / "brain"
APP_BRAIN_DIR = Path(os.environ.get("USERPROFILE", "")) / ".gemini" / "antigravity" / "brain"

# Available Models in AGY (refreshable at runtime via `agy models`)
AVAILABLE_MODELS: Dict[str, str] = {
    "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low - High Speed)",
    "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
    "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}

# URL schemes accepted by the /chrome helper.
ALLOWED_URL_SCHEMES = ("http", "https")


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------


class Resolution(NamedTuple):
    """Outcome of a validation / resolution step."""

    value: Optional[str] = None
    message: str = ""
    ok: bool = True


def make_error_id() -> str:
    """Short, non sensitive identifier used to correlate a user report with a log entry."""
    return uuid.uuid4().hex[:8]


def new_task_id(channel_id: int) -> str:
    """Generate a collision free task identifier."""
    return f"{channel_id}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def format_duration(seconds: float) -> str:
    """Render a duration as a compact human readable string."""
    total = int(max(0.0, seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def split_text_for_discord(text: str, limit: int = DISCORD_CHUNK_LIMIT) -> List[str]:
    """Split long text into Discord-safe chunks, preferring natural boundaries."""
    if limit <= 0:
        return [text]
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = -1
        for separator in ("\n\n", "\n", " "):
            index = window.rfind(separator)
            if index > limit // 2:
                cut = index + len(separator)
                break
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def validate_prompt(prompt: str) -> Resolution:
    """Validate a user supplied prompt before handing it to the CLI."""
    text = prompt.strip()
    if not text:
        return Resolution(None, "プロンプトが空です。内容を入力してください。", False)
    if len(text) > MAX_PROMPT_CHARS:
        return Resolution(
            None,
            f"プロンプトが長すぎます（{len(text)}文字 / 上限 {MAX_PROMPT_CHARS}文字）。"
            "内容を分割して送信してください。",
            False,
        )
    return Resolution(text)


def resolve_model_id(query: str) -> Resolution:
    """Resolve a user supplied model name to a known model id."""
    needle = query.strip().lower()
    if not needle:
        return Resolution(None, "モデル ID が指定されていません。", False)

    exact = [model_id for model_id in AVAILABLE_MODELS if model_id.lower() == needle]
    if exact:
        return Resolution(exact[0])

    partial = [model_id for model_id in AVAILABLE_MODELS if needle in model_id.lower()]
    if len(partial) == 1:
        return Resolution(partial[0])
    if len(partial) > 1:
        return Resolution(
            None,
            f"'{query}' は複数のモデルに一致します: {', '.join(partial)}",
            False,
        )
    return Resolution(
        None,
        f"未知のモデル: `{query}`\nAvailable: {', '.join(f'`{m}`' for m in AVAILABLE_MODELS)}",
        False,
    )


def resolve_conversation_id(query: str, sessions: Sequence[Dict[str, Any]]) -> Resolution:
    """Resolve a (possibly abbreviated) session id against the known session list."""
    needle = query.strip().lower()
    if not needle:
        return Resolution(None, "セッション ID が指定されていません。", False)

    for session in sessions:
        if session["id"].lower() == needle:
            return Resolution(session["id"])

    matches = [session for session in sessions if session["id"].lower().startswith(needle)]
    if len(matches) == 1:
        return Resolution(matches[0]["id"])
    if len(matches) > 1:
        candidates = ", ".join(session["short_id"] for session in matches[:5])
        return Resolution(
            None,
            f"'{query}' に一致するセッションが複数あります: {candidates}（短縮IDをExtended指定してください）",
            False,
        )

    # Unknown ids are still honoured (the CLI owns the session store) but the user
    # is warned because a typo is the most likely cause.
    return Resolution(
        query.strip(),
        f"セッション一覧に '{query}' が見つかりません。IDを打ち間違えた可能性があります。",
    )


def validate_browser_url(url: str) -> Resolution:
    """Allow only plain http(s) URLs to be opened in the host browser."""
    candidate = url.strip()
    if not candidate:
        return Resolution(None, "URLが空です。", False)

    parsed = urlparse(candidate)
    if parsed.scheme.lower() not in ALLOWED_URL_SCHEMES:
        return Resolution(
            None,
            f"許可されていないスキームです（{'/'.join(ALLOWED_URL_SCHEMES)} のみ許可）: "
            f"{parsed.scheme or '(none)'}",
            False,
        )
    if not parsed.netloc:
        return Resolution(None, "URLにホスト名が含まれていません。", False)
    if parsed.username or parsed.password:
        return Resolution(None, "認証情報を含むURLは許可されていません。", False)
    return Resolution(candidate)


def parse_agy_models(output: str) -> Dict[str, str]:
    """Parse the `agy models` output into a {model_id: display_name} mapping."""
    models: Dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "\t" in line:
            model_id, display = line.split("\t", 1)
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            model_id, display = parts
        model_id = model_id.strip()
        display = display.strip()
        if model_id:
            models[model_id] = display or model_id
    return models


def resolve_agy_executable() -> Optional[str]:
    """Resolve the Antigravity CLI to an absolute path, or None when unavailable."""
    candidate = Path(AGY_CLI)
    if candidate.is_file():
        return str(candidate)
    return shutil.which(AGY_CLI)


def terminate_process_tree(proc: Any) -> None:
    """Terminate a child process together with its descendants (best effort)."""
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=15,
                )
            else:
                # Only signal the whole group when the child owns a dedicated one,
                # otherwise the parent process group would be killed as well.
                pgid = os.getpgid(pid)
                if pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
        except Exception as exc:  # pragma: no cover - platform dependent
            logger.warning("Failed to terminate process tree (pid=%s): %s", pid, exc)
    try:
        if getattr(proc, "returncode", None) is None:
            proc.kill()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to kill process (pid=%s): %s", pid, exc)


# ---------------------------------------------------------------------------
# Host helpers
# ---------------------------------------------------------------------------


def _windows_memory_status() -> Optional[Tuple[int, int]]:
    """Return (total_bytes, available_bytes) using the Windows kernel API."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.debug("Windows memory status unavailable: %s", exc)
        return None


def get_system_metrics() -> Dict[str, Any]:
    """Gather CPU, RAM, and GPU status."""
    metrics = {
        "cpu": "N/A",
        "cpu_percent": 0.0,
        "memory": "N/A",
        "memory_percent": 0.0,
        "gpu": "GPU: 未検出 / 利用不可",
    }
    if HAS_PSUTIL and psutil:
        try:
            cpu_pct = psutil.cpu_percent(interval=0.1)
            cpu_count = psutil.cpu_count(logical=True)
            metrics["cpu_percent"] = cpu_pct
            metrics["cpu"] = f"{cpu_pct:.1f}% ({cpu_count} Threads)"

            mem = psutil.virtual_memory()
            mem_used_gb = mem.used / (1024 ** 3)
            mem_tot_gb = mem.total / (1024 ** 3)
            metrics["memory_percent"] = mem.percent
            metrics["memory"] = f"{mem_used_gb:.1f}GB / {mem_tot_gb:.1f}GB ({mem.percent}%)"
        except Exception as e:
            logger.warning(f"Failed to gather psutil metrics: {e}")
    else:
        cpu_count = os.cpu_count()
        metrics["cpu"] = f"N/A (psutil未インストール, {cpu_count} Threads)"
        status = _windows_memory_status()
        if status is not None:
            total_bytes, available_bytes = status
            used_bytes = max(0, total_bytes - available_bytes)
            percent = (used_bytes / total_bytes * 100.0) if total_bytes else 0.0
            metrics["memory_percent"] = percent
            metrics["memory"] = (
                f"{used_bytes / (1024 ** 3):.1f}GB / {total_bytes / (1024 ** 3):.1f}GB ({percent:.1f}%)"
            )
        else:
            metrics["memory"] = "N/A (psutil未インストール)"

    # Query GPU via nvidia-smi if available
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            res = subprocess.run(
                [smi, "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
            )
            if res.returncode == 0 and res.stdout.strip():
                gpus = []
                for line in res.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 4:
                        name, util, m_used, m_tot = parts[0], parts[1], parts[2], parts[3]
                        try:
                            m_used_f = float(m_used) / 1024.0
                            m_tot_f = float(m_tot) / 1024.0
                            gpus.append(f"{name} | 負荷: {util}% | VRAM: {m_used_f:.1f}GB / {m_tot_f:.1f}GB")
                        except ValueError:
                            gpus.append(f"{name} | 負荷: {util}% | VRAM: {m_used}MB / {m_tot}MB")
                if gpus:
                    metrics["gpu"] = "\n".join(gpus)
        except Exception as e:
            logger.warning(f"nvidia-smi execution error: {e}")
            metrics["gpu"] = f"GPU取得エラー: {e}"

    return metrics


def launch_chrome_browser(url: str = "http://localhost:5000") -> Tuple[bool, str]:
    """Launch Google Chrome on the host environment."""
    validation = validate_browser_url(url)
    if not validation.ok:
        return False, validation.message

    chrome_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        shutil.which("chrome"),
        shutil.which("google-chrome"),
    ]
    target_exe = None
    for p in chrome_paths:
        if p and os.path.exists(p):
            target_exe = p
            break

    if not target_exe:
        return False, "Google Chrome の実行ファイルが見つかりませんでした。"

    try:
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        proc = subprocess.Popen(
            [target_exe, validation.value or ""],
            creationflags=creation_flags,
            close_fds=True,
        )
        return True, f"Google Chrome を起動しました (PID: {proc.pid}, URL: {validation.value})"
    except Exception as e:
        logger.error(f"Failed to launch Chrome: {e}", exc_info=True)
        return False, f"Chrome 起動失敗: {e}"


def get_recent_conversations(limit: int = 10) -> List[Dict[str, Any]]:
    """Scan CLI and App brain folders to find past conversation sessions with their initial prompt and timestamp."""
    sessions: List[Dict[str, Any]] = []
    seen_ids: Set[str] = set()

    for brain_root in (CLI_BRAIN_DIR, APP_BRAIN_DIR):
        if not brain_root.exists():
            continue
        try:
            candidates = list(brain_root.iterdir())
        except OSError as exc:
            logger.warning("Cannot read brain directory %s: %s", brain_root, exc)
            continue
        for item in candidates:
            if not item.is_dir() or item.name in seen_ids or item.name.startswith("."):
                continue

            transcript_file = item / ".system_generated" / "logs" / "transcript.jsonl"
            if not transcript_file.exists():
                continue

            seen_ids.add(item.name)
            mtime = transcript_file.stat().st_mtime
            first_prompt = "No prompt recorded"

            try:
                with open(transcript_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line_str = line.strip()
                        if not line_str:
                            continue
                        entry = json.loads(line_str)
                        if entry.get("type") == "USER_INPUT":
                            raw_content = entry.get("content", "")
                            match = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw_content, re.DOTALL)
                            if match:
                                first_prompt = match.group(1).strip()
                            else:
                                first_prompt = raw_content[:120].strip()
                            first_prompt = " ".join(first_prompt.split())
                            break
            except Exception as exc:
                logger.debug("Failed to read transcript %s: %s", transcript_file, exc)

            sessions.append(
                {
                    "id": item.name,
                    "short_id": item.name[:8],
                    "mtime": mtime,
                    "date_str": datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M"),
                    "prompt": first_prompt[:70] + ("..." if len(first_prompt) > 70 else ""),
                }
            )

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions[:limit]


def fetch_agy_models(executable: Optional[str] = None) -> Dict[str, str]:
    """Query the live model catalogue from the Antigravity CLI."""
    exe = executable or resolve_agy_executable()
    if not exe:
        raise FileNotFoundError("Antigravity CLI が見つかりません (AGY_CLI_PATH を確認してください)。")
    completed = subprocess.run(
        [exe, "models"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"`{exe} models` が終了コード {completed.returncode} で終了しました。")
    return parse_agy_models(completed.stdout)


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------


def build_status_embed(
    metrics: Dict[str, Any],
    active_count: int,
    channel_active: int,
    model: str,
    conv_label: str,
) -> discord.Embed:
    """Build the system status embed shared by /status and !status."""
    embed = discord.Embed(
        title="🖥️ システム稼働状況 (Host System Status)",
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="⚙️ CPU 使用率", value=f"`{metrics['cpu']}`", inline=True)
    embed.add_field(name="🧠 メモリ (RAM)", value=f"`{metrics['memory']}`", inline=True)
    embed.add_field(name="⚡ GPU 稼働状況", value=f"```{metrics['gpu']}```", inline=False)
    embed.add_field(
        name="🤖 Antigravity 稼働状況",
        value=(
            f"• 実行中タスク数: **{active_count}** 件 (このチャンネル: **{channel_active}** 件)\n"
            f"• アクティブモデル: **`{model}`**\n"
            f"• 会話コンテキスト: **`{conv_label}`**\n"
            "• 作業ディレクトリ: `simulation/v3`"
        ),
        inline=False,
    )
    return embed


def build_chats_embed(sessions: Sequence[Dict[str, Any]], current_conv: Optional[str]) -> discord.Embed:
    """Build the recent conversation embed shared by /chats and !chats."""
    current_label = current_conv[:8] if current_conv else "None (New)"
    embed = discord.Embed(
        title="💬 Recent Conversations (Chat List)",
        description=(
            f"Current active session: **`{current_label}`**\n"
            "Select from dropdown or use `/resume <id>` to switch."
        ),
        color=discord.Color.teal(),
    )
    for session in sessions:
        is_active = session["id"] == current_conv or session["short_id"] == current_conv
        prefix = "👉 **[ACTIVE]** " if is_active else "• "
        embed.add_field(
            name=f"{prefix}`{session['short_id']}` ({session['date_str']})",
            value=session["prompt"] or "(No prompt)",
            inline=False,
        )
    return embed


def build_models_embed(current_model: str, models: Optional[Dict[str, str]] = None) -> discord.Embed:
    """Build the model catalogue embed shared by /models and !models."""
    catalogue = models if models is not None else AVAILABLE_MODELS
    embed = discord.Embed(
        title="🤖 Available Antigravity Models",
        description=(
            f"Current active model in this channel: **`{current_model}`**\n"
            "Workspace: `simulation/v3`"
        ),
        color=discord.Color.blue(),
    )
    for model_id, name in catalogue.items():
        prefix = "👉 " if model_id == current_model else "• "
        embed.add_field(name=f"{prefix}{name}", value=f"`{model_id}`", inline=False)
    return embed


def build_help_embed() -> discord.Embed:
    """Build the help embed shared by /help and !help."""
    embed = discord.Embed(
        title="📖 Antigravity Remote Bridge コマンド一覧",
        description=(
            "ワークスペースは `simulation/v3` に固定されています。\n"
            "DM、許可チャンネル、または-bot メンションで自然に会話できます。"
        ),
        color=discord.Color.purple(),
    )
    embed.add_field(
        name="💬 会話",
        value=(
            "- `!chat <prompt>` / `/chat <prompt>`: エージェントへプロンプト送信\n"
            "- `!chats` / `/chats`: 最近のセッション一覧・切替\n"
            "- `!resume <id>` / `/resume <id>`: セッションIDから再開\n"
            "- `!new` / `/new`: 会話コンテキストをリセット\n"
            "- `!abort` / `/abort`: 実行中タスクを停止"
        ),
        inline=False,
    )
    embed.add_field(
        name="🤖 モデル / 🖥️ 監視",
        value=(
            "- `!model [name]` / `/model [name]`: モデルの切替・表示\n"
            "- `!models` / `/models`: モデル一覧\n"
            "- `!models refresh` / `/models refresh`: CLI からモデル一覧を再取得\n"
            "- `!status` / `/status`: CPU・メモリ・GPU・タスク状況\n"
            "- `!chrome [url]` / `/chrome [url]`: ローカル Chrome を起動"
        ),
        inline=False,
    )
    embed.set_footer(
        text=f"同時実行上限: {MAX_TASKS_PER_CHANNEL}件/チャンネル, {MAX_TASKS_GLOBAL}件/全体"
    )
    return embed


# ---------------------------------------------------------------------------
# Discord UI components
# ---------------------------------------------------------------------------


def _interaction_values(interaction: discord.Interaction) -> List[str]:
    """Extract the selected values of a component interaction in a type safe way."""
    data = cast(Dict[str, Any], interaction.data or {})
    values = data.get("values") or []
    if isinstance(values, (list, tuple)):
        return [str(value) for value in values]
    return []


class ModelSelect(discord.ui.Select[discord.ui.View]):
    """Select component reporting the chosen model id back to the callback."""

    def __init__(
        self,
        current_model: str,
        on_select: Callable[[discord.Interaction, str], Awaitable[None]],
    ):
        options = [
            discord.SelectOption(
                label=display_name[:100],
                value=model_id,
                description=f"ID: {model_id}"[:100],
                default=(model_id == current_model),
            )
            for model_id, display_name in AVAILABLE_MODELS.items()
        ]
        super().__init__(
            placeholder="Select an Antigravity Model...",
            options=options,
            custom_id="model_select",
        )
        self.on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        values = _interaction_values(interaction)
        if not values:
            await interaction.response.send_message("⚠️ モデルが選択されませんでした。", ephemeral=True)
            return
        await self.on_select(interaction, values[0])


class ChatSelect(discord.ui.Select[discord.ui.View]):
    """Select component reporting the chosen session id back to the callback."""

    def __init__(
        self,
        sessions: Sequence[Dict[str, Any]],
        current_conv: Optional[str],
        on_select: Callable[[discord.Interaction, str], Awaitable[None]],
    ):
        options = []
        for session in sessions[:15]:
            is_active = (session["id"] == current_conv or session["short_id"] == current_conv)
            label = f"{session['date_str']} - {session['prompt']}"[:95]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=session["id"],
                    description=f"Session ID: {session['short_id']}",
                    default=is_active,
                )
            )

        super().__init__(
            placeholder="Select a conversation to resume...",
            options=options,
            custom_id="chat_select",
        )
        self.on_select = on_select

    async def callback(self, interaction: discord.Interaction) -> None:
        values = _interaction_values(interaction)
        if not values:
            await interaction.response.send_message("⚠️ セッションが選択されませんでした。", ephemeral=True)
            return
        await self.on_select(interaction, values[0])


class ModelSelectView(discord.ui.View):
    """Dropdown View for selecting models interactively."""

    def __init__(
        self,
        current_model: str,
        on_select_callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ):
        super().__init__(timeout=60)
        self.current_model = current_model
        self.on_select_callback = on_select_callback
        self.add_item(ModelSelect(current_model, on_select_callback))


class ChatSelectView(discord.ui.View):
    """Dropdown View for resuming past conversations interactively."""

    def __init__(
        self,
        sessions: List[Dict[str, Any]],
        current_conv: Optional[str],
        on_select_callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ):
        super().__init__(timeout=60)
        self.on_select_callback = on_select_callback
        if sessions:
            self.add_item(ChatSelect(sessions, current_conv, on_select_callback))


class PromptReplyModal(discord.ui.Modal, title="💬 返信・プロンプト送信 (セッション継続)"):
    """Modal to enter prompt and continue the conversation session."""

    prompt_input: discord.ui.TextInput[discord.ui.View] = discord.ui.TextInput(
        label="プロンプト / 返信内容",
        style=discord.TextStyle.paragraph,
        placeholder="続きの指示や質問を入力してください...",
        required=True,
        max_length=2000,
    )

    def __init__(self, target_conv_id: Optional[str] = None):
        super().__init__()
        self.target_conv_id = target_conv_id
        if target_conv_id:
            self.title = f"💬 返信 ({target_conv_id[:8]} セッション継続)"

    async def on_submit(self, interaction: discord.Interaction):
        if not client.is_authorized(interaction.user.id, interaction.channel_id):
            await interaction.response.send_message("❌ Unauthorized: You do not have permission.", ephemeral=True)
            return

        if self.target_conv_id:
            client.set_conversation(interaction.channel_id, self.target_conv_id)

        user_prompt = self.prompt_input.value.strip()
        await interaction.response.defer()
        client.spawn_background(
            execute_agy_prompt(
                interaction.channel,
                user_prompt,
                interaction.user,
                interaction=interaction,
            )
        )


class SessionReplyButton(discord.ui.Button):
    """Button opening the reply modal, bound to a specific conversation session."""

    def __init__(
        self,
        conv_id: Optional[str],
        on_click: Callable[[discord.Interaction, Optional[str]], Awaitable[None]],
    ):
        # Custom ID encodes conv_id if present to survive restarts
        btn_custom_id = f"btn_reply_session:{conv_id}" if conv_id else "btn_reply_session:default"
        super().__init__(
            label="💬 返信する (このセッションで継続)",
            style=discord.ButtonStyle.primary,
            custom_id=btn_custom_id,
        )
        self.conv_id = conv_id
        self.on_click = on_click

    async def callback(self, interaction: discord.Interaction) -> None:
        target_conv = self.conv_id
        if not target_conv:
            data = cast(Dict[str, Any], interaction.data or {})
            custom_id = str(data.get("custom_id") or "")
            if custom_id.startswith("btn_reply_session:") and custom_id != "btn_reply_session:default":
                target_conv = custom_id.split("btn_reply_session:", 1)[1]
        if not target_conv:
            target_conv = client.get_conversation(interaction.channel_id)
        await self.on_click(interaction, target_conv)


class SessionReplyView(discord.ui.View):
    """Interactive Button View attached to bot responses allowing instant replies in the same session."""

    def __init__(self, conv_id: Optional[str] = None):
        super().__init__(timeout=None)
        self.conv_id = conv_id
        self.reply_button = SessionReplyButton(conv_id, self.handle_reply_click)
        self.add_item(self.reply_button)

    async def handle_reply_click(self, interaction: discord.Interaction, target_conv: Optional[str]) -> None:
        if not client.is_authorized(interaction.user.id, interaction.channel_id):
            await interaction.response.send_message("❌ Unauthorized: You do not have permission.", ephemeral=True)
            return

        modal = PromptReplyModal(target_conv_id=target_conv)
        await interaction.response.send_modal(modal)


class ResponseTarget:
    """Uniform send interface for slash-command interactions and plain messages."""

    def __init__(
        self,
        interaction: Optional[discord.Interaction] = None,
        message: Optional[discord.Message] = None,
    ):
        if interaction is None and message is None:
            raise ValueError("ResponseTarget requires either an interaction or a message.")
        self.interaction = interaction
        self.message = message
        self._deferred = False

    @property
    def channel(self) -> Any:
        if self.interaction is not None:
            return self.interaction.channel
        assert self.message is not None
        return self.message.channel

    @property
    def user(self) -> Any:
        if self.interaction is not None:
            return self.interaction.user
        assert self.message is not None
        return self.message.author

    @property
    def channel_id(self) -> Optional[int]:
        return getattr(self.channel, "id", None)

    @property
    def deferred(self) -> bool:
        return self._deferred

    async def defer(self) -> None:
        """Defer the interaction so long running work does not expire the token."""
        if self.interaction is not None and not self._deferred:
            await self.interaction.response.defer()
            self._deferred = True

    async def send(
        self,
        content: Optional[str] = None,
        *,
        embed: Optional[discord.Embed] = None,
        view: Optional[discord.ui.View] = None,
        ephemeral: bool = False,
    ) -> Any:
        """Send a message through the underlying interaction or message."""
        kwargs: Dict[str, Any] = {}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        if view is not None:
            kwargs["view"] = view

        if self.interaction is not None:
            if self._deferred:
                if ephemeral:
                    # Discord only supports ephemeral replies through the initial
                    # interaction response, not through deferred followups.
                    logger.debug("Ephemeral flag ignored for a deferred followup.")
                return await self.interaction.followup.send(**kwargs)
            kwargs["ephemeral"] = ephemeral
            return await self.interaction.response.send_message(**kwargs)
        assert self.message is not None
        return await self.message.reply(**kwargs)


# ---------------------------------------------------------------------------
# Bot client
# ---------------------------------------------------------------------------


class DiscordBridgeClient(discord.Client):
    """Discord client holding per-channel state, task registry and concurrency limits."""

    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

        # State tracking: channel_id -> current_model
        self.channel_models: Dict[int, str] = {}
        # channel_id -> conversation_id (for multi-turn memory)
        self.channel_conversations: Dict[int, Optional[str]] = {}
        # task_id (str) -> asyncio.subprocess.Process
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
        # channel_id -> Set[str] of task_ids
        self.channel_tasks: Dict[int, Set[str]] = {}
        # task_id -> aborted by the user
        self.aborted_tasks: Set[str] = set()
        # Strong references to background tasks so they are never garbage collected
        self.background_tasks: Set[asyncio.Task] = set()

    async def setup_hook(self):
        # Register persistent SessionReplyView for dynamic custom_ids
        self.add_view(SessionReplyView(None))
        if PERSISTENT_VIEW_SESSIONS > 0:
            try:
                sessions = await asyncio.to_thread(get_recent_conversations, PERSISTENT_VIEW_SESSIONS)
            except Exception as exc:
                logger.warning("Could not enumerate sessions for persistent views: %s", exc)
                sessions = []
            for session in sessions:
                try:
                    self.add_view(SessionReplyView(conv_id=session["id"]))
                except Exception as exc:
                    logger.warning("Could not register persistent view for %s: %s", session["id"], exc)
        await self.tree.sync()
        logger.info("Application slash commands and persistent views synced.")

    def is_authorized(self, user_id: int, channel_id: Optional[int]) -> bool:
        """Check if user and channel are allowed to issue commands."""
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            return False
        if ALLOWED_CHANNEL_IDS:
            if channel_id is None or channel_id not in ALLOWED_CHANNEL_IDS:
                return False
        return True

    @property
    def bot_user_id(self) -> int:
        """Snowflake of the connected bot user (0 when the client is not ready yet)."""
        user = self.user
        return user.id if user is not None else 0

    def get_model(self, channel_id: Optional[int]) -> str:
        """Active model of a channel (falls back to DEFAULT_MODEL)."""
        if channel_id is None:
            return DEFAULT_MODEL
        return self.channel_models.get(channel_id, DEFAULT_MODEL)

    def set_model(self, channel_id: Optional[int], model: str) -> None:
        """Bind a model to a channel."""
        if channel_id is None:
            return
        self.channel_models[channel_id] = model

    def get_conversation(self, channel_id: Optional[int]) -> Optional[str]:
        """Conversation session bound to a channel (None when a new session is expected)."""
        if channel_id is None:
            return None
        return self.channel_conversations.get(channel_id)

    def set_conversation(self, channel_id: Optional[int], conversation_id: Optional[str]) -> None:
        """Bind (or clear with None) the conversation session of a channel."""
        if channel_id is None:
            return
        self.channel_conversations[channel_id] = conversation_id

    def channel_task_count(self, channel_id: Optional[int]) -> int:
        """Number of running tasks of a channel."""
        if channel_id is None:
            return 0
        return len(self.channel_tasks.get(channel_id, set()))

    def register_task(self, channel_id: int, task_id: str, proc: asyncio.subprocess.Process):
        self.active_processes[task_id] = proc
        if channel_id not in self.channel_tasks:
            self.channel_tasks[channel_id] = set()
        self.channel_tasks[channel_id].add(task_id)

    def unregister_task(self, channel_id: int, task_id: str):
        self.active_processes.pop(task_id, None)
        self.aborted_tasks.discard(task_id)
        if channel_id in self.channel_tasks:
            self.channel_tasks[channel_id].discard(task_id)
            if not self.channel_tasks[channel_id]:
                self.channel_tasks.pop(channel_id, None)

    def check_capacity(self, channel_id: int) -> Resolution:
        """Verify that a new task fits into the configured concurrency limits."""
        global_active = len(self.active_processes)
        if global_active >= MAX_TASKS_GLOBAL:
            return Resolution(
                None,
                f"全体の同時実行数上限 ({MAX_TASKS_GLOBAL}件) に達しています。"
                "完了を待ってから再実行してください。",
                False,
            )
        channel_active = len(self.channel_tasks.get(channel_id, set()))
        if channel_active >= MAX_TASKS_PER_CHANNEL:
            return Resolution(
                None,
                f"このチャンネルの同時実行数上限 ({MAX_TASKS_PER_CHANNEL}件) に達しています。",
                False,
            )
        return Resolution("ok")

    def abort_tasks(self, channel_id: Optional[int] = None, task_id: Optional[str] = None) -> int:
        """Abort tasks either by specific task_id or for an entire channel."""
        aborted = 0
        if task_id and task_id in self.active_processes:
            p = self.active_processes[task_id]
            if p.returncode is None:
                try:
                    terminate_process_tree(p)
                    aborted += 1
                except Exception:
                    pass
                self.aborted_tasks.add(task_id)
            return aborted

        target_tasks = (
            self.channel_tasks.get(channel_id, set()).copy()
            if channel_id
            else set(self.active_processes.keys())
        )
        for tid in target_tasks:
            proc = self.active_processes.get(tid)
            if proc and proc.returncode is None:
                try:
                    terminate_process_tree(proc)
                    aborted += 1
                except Exception:
                    pass
                self.aborted_tasks.add(tid)
        return aborted

    def spawn_background(self, coro: Any) -> asyncio.Task:
        """Run a coroutine as a tracked background task (never garbage collected)."""
        task = asyncio.create_task(coro)
        self.background_tasks.add(task)
        task.add_done_callback(self._background_done)
        return task

    def _background_done(self, task: asyncio.Task) -> None:
        self.background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("Background task failed: %s", exc, exc_info=exc)

    async def close(self) -> None:
        """Terminate every running child process before closing the connection."""
        for task_id in list(self.active_processes.keys()):
            self.abort_tasks(task_id=task_id)
        for task in list(self.background_tasks):
            task.cancel()
        await super().close()


client = DiscordBridgeClient()


def check_auth(target: ResponseTarget) -> bool:
    """Authorization gate shared by slash commands and prefix commands."""
    return client.is_authorized(target.user.id, target.channel_id)


# ---------------------------------------------------------------------------
# Shared command implementations
# ---------------------------------------------------------------------------


async def impl_status(target: ResponseTarget) -> None:
    """Show host metrics and bridge status."""
    metrics = await asyncio.to_thread(get_system_metrics)
    active_count = len(client.active_processes)
    channel_active = client.channel_task_count(target.channel_id)
    model = client.get_model(target.channel_id)
    conv_id = client.get_conversation(target.channel_id)
    conv_label = conv_id[:8] if conv_id else "新規セッション"
    await target.send(embed=build_status_embed(metrics, active_count, channel_active, model, conv_label))


async def impl_chrome(target: ResponseTarget, url: Optional[str]) -> None:
    """Open a URL in the host browser."""
    validation = validate_browser_url(url or "http://localhost:5000")
    if not validation.ok:
        await target.send(f"❌ URLを検証できません: {validation.message}", ephemeral=True)
        return
    success, message = await asyncio.to_thread(launch_chrome_browser, validation.value or "")
    if success:
        await target.send(f"🌐 {message}")
    else:
        await target.send(f"❌ Chrome 起動失敗: {message}", ephemeral=True)


async def impl_models(target: ResponseTarget, refresh: bool = False) -> None:
    """List the model catalogue, optionally refreshing it from the live CLI."""
    if refresh:
        try:
            catalogue = await asyncio.to_thread(fetch_agy_models)
        except Exception as exc:
            logger.warning("Model refresh failed: %s", exc)
            await target.send(f"❌ モデル一覧の取得に失敗しました: {exc}", ephemeral=True)
            return
        if not catalogue:
            await target.send("❌ モデル一覧が空です。CLIの出力形式を確認してください。", ephemeral=True)
            return
        AVAILABLE_MODELS.clear()
        AVAILABLE_MODELS.update(catalogue)
        logger.info("Model catalogue refreshed: %s", ", ".join(AVAILABLE_MODELS))

    current = client.get_model(target.channel_id)
    embed = build_models_embed(current)
    view = ModelSelectView(current, on_model_selected)
    await target.send(embed=embed, view=view)


async def impl_model(target: ResponseTarget, model_id: Optional[str]) -> None:
    """Show or switch the active model of the channel."""
    if not model_id:
        current = client.get_model(target.channel_id)
        view = ModelSelectView(current, on_model_selected)
        await target.send(f"Current active model: `{current}`\nSelect a new model below:", view=view)
        return

    resolution = resolve_model_id(model_id)
    if not resolution.ok or not resolution.value:
        await target.send(f"❌ モデルを解決できません: {resolution.message}", ephemeral=True)
        return

    client.set_model(target.channel_id, resolution.value)
    display_name = AVAILABLE_MODELS.get(resolution.value, resolution.value)
    await target.send(f"✅ Active model updated to: {display_name} (`{resolution.value}`)")


async def impl_chats(target: ResponseTarget) -> None:
    """List recent conversations and allow switching between them."""
    sessions = await asyncio.to_thread(get_recent_conversations, 10)
    current_conv = client.get_conversation(target.channel_id)
    if not sessions:
        await target.send("ℹ️ No previous conversation history found in workspace.")
        return
    embed = build_chats_embed(sessions, current_conv)
    view = ChatSelectView(sessions, current_conv, on_chat_selected)
    await target.send(embed=embed, view=view)


async def impl_resume(target: ResponseTarget, conversation_id: str) -> None:
    """Resume a previous conversation by (possibly abbreviated) session id."""
    sessions = await asyncio.to_thread(get_recent_conversations, 30)
    resolution = resolve_conversation_id(conversation_id, sessions)
    if not resolution.ok or not resolution.value:
        await target.send(f"❌ セッションを解決できません: {resolution.message}", ephemeral=True)
        return

    client.set_conversation(target.channel_id, resolution.value)
    prefix = f"{resolution.message}\n" if resolution.message else ""
    await target.send(
        f"{prefix}✅ Resumed conversation `{resolution.value[:8]}` (`{resolution.value}`). "
        "Next messages will continue this session."
    )


async def impl_new(target: ResponseTarget) -> None:
    """Reset the conversation context of the channel."""
    client.set_conversation(target.channel_id, None)
    await target.send("🔄 Conversation reset. Next message will start a fresh session in `simulation/v3`.")


async def impl_abort(target: ResponseTarget) -> None:
    """Stop every running task of the channel."""
    aborted = client.abort_tasks(channel_id=target.channel_id)
    if aborted > 0:
        await target.send(f"🛑 {aborted} 件のアクティブタスクを停止しました。")
    else:
        await target.send("ℹ️ このチャンネルで実行中のタスクはありません。")


async def impl_help(target: ResponseTarget) -> None:
    """Show the command reference."""
    await target.send(embed=build_help_embed())


# ==========================================
# Slash Commands
# ==========================================


@client.tree.command(name="chat", description="Send a prompt directly to Antigravity")
@app_commands.describe(prompt="The instruction or question for Antigravity")
async def cmd_chat(interaction: discord.Interaction, prompt: str):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized: You do not have permission to run commands.", ephemeral=True)
        return

    # Defer immediately to avoid Discord's 3-second interaction expiration
    await target.defer()
    client.spawn_background(
        execute_agy_prompt(interaction.channel, prompt, interaction.user, interaction=interaction)
    )


@client.tree.command(name="chats", description="List recent conversation sessions and resume them")
async def cmd_chats(interaction: discord.Interaction):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return

    # Defer immediately to prevent 404 Unknown interaction on disk scan
    await target.defer()
    await impl_chats(target)


@client.tree.command(name="resume", description="Resume a conversation by session ID")
@app_commands.describe(conversation_id="Session ID or 8-character short ID")
async def cmd_resume(interaction: discord.Interaction, conversation_id: str):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return

    await target.defer()
    await impl_resume(target, conversation_id)


@client.tree.command(name="models", description="List all available Antigravity models")
@app_commands.describe(refresh="Refresh the catalogue from the live agy models CLI")
async def cmd_models(interaction: discord.Interaction, refresh: bool = False):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized: You do not have permission to run commands.", ephemeral=True)
        return

    if refresh:
        await target.defer()
    await impl_models(target, refresh=refresh)


@client.tree.command(name="model", description="Set or view active model for this channel")
@app_commands.describe(model_id="Model ID (e.g. gemini-3.8-flash-low, claude-sonnet-4-6)")
async def cmd_model(interaction: discord.Interaction, model_id: Optional[str] = None):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized: You do not have permission to run commands.", ephemeral=True)
        return
    await impl_model(target, model_id)


@client.tree.command(name="new", description="Start a fresh conversation (reset conversation context)")
async def cmd_new(interaction: discord.Interaction):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return
    await impl_new(target)


@client.tree.command(name="status", description="Show system performance metrics (CPU, RAM, GPU) and active tasks")
async def cmd_status(interaction: discord.Interaction):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return

    # nvidia-smi probing may exceed the 3 second interaction deadline
    await target.defer()
    await impl_status(target)


@client.tree.command(name="chrome", description="Launch Google Chrome on the host environment")
@app_commands.describe(url="URL to open in Chrome (default: http://localhost:5000)")
async def cmd_chrome(interaction: discord.Interaction, url: Optional[str] = "http://localhost:5000"):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized: You do not have permission to run commands.", ephemeral=True)
        return
    await impl_chrome(target, url)


@client.tree.command(name="abort", description="Abort currently running agent tasks in this channel")
async def cmd_abort(interaction: discord.Interaction):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return
    await impl_abort(target)


@client.tree.command(name="help", description="Show the command reference")
async def cmd_help(interaction: discord.Interaction):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return
    await impl_help(target)


async def on_chat_selected(interaction: discord.Interaction, selected_conv: str):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return
    client.set_conversation(interaction.channel_id, selected_conv)
    await target.send(
        f"✅ Conversation resumed: `{selected_conv[:8]}` (`{selected_conv}`). "
        "Next messages will continue this context."
    )


async def on_model_selected(interaction: discord.Interaction, selected_model: str):
    target = ResponseTarget(interaction=interaction)
    if not check_auth(target):
        await target.send("❌ Unauthorized.", ephemeral=True)
        return
    client.set_model(interaction.channel_id, selected_model)
    display_name = AVAILABLE_MODELS.get(selected_model, selected_model)
    await target.send(f"✅ Model switched to: {display_name} (`{selected_model}`)")


# ==========================================
# Execution Engine (Live Progress & Streaming)
# ==========================================


async def _drain_stderr(stream: Any) -> str:
    """Consume stderr concurrently so the child process never blocks on a full pipe."""
    buffer: List[bytes] = []
    total = 0
    try:
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            buffer.append(chunk)
            total += len(chunk)
            if total > 256 * 1024:
                # Keep only the tail of the diagnostics to bound memory usage.
                del buffer[0]
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("stderr drain failed: %s", exc)
    return b"".join(buffer).decode("utf-8", errors="replace")


async def execute_agy_prompt(
    channel: Any,
    prompt: str,
    user: Optional[discord.abc.User] = None,
    reply_to_msg: Optional[discord.Message] = None,
    interaction: Optional[discord.Interaction] = None,
):
    """
    Runs agy CLI strictly in simulation/v3 workspace in parallel,
    streams live progress (thinking, subagents, tools), and posts reply.
    Supports concurrent executions across and within channels without blocking.
    """
    channel_id = channel.id

    validation = validate_prompt(prompt)
    if not validation.ok or not validation.value:
        await channel.send(f"❌ プロンプトを検証できません: {validation.message}")
        return
    prompt = validation.value

    capacity = client.check_capacity(channel_id)
    if not capacity.ok:
        await channel.send(f"❌ タスクを開始できません: {capacity.message}")
        return

    executable = resolve_agy_executable()
    if not executable:
        await channel.send(
            "Antigravity CLI (`agy`) が見つかりません。AGY_CLI_PATH を設定するか、PATH に agy を追加してください。"
        )
        return

    task_id = new_task_id(channel_id)
    active_in_channel = len(client.channel_tasks.get(channel_id, set()))

    model = client.get_model(channel_id)
    conv_id = client.get_conversation(channel_id)

    # Command construction: locked to WORKSPACE_DIR
    cmd = [
        executable,
        "-p",
        prompt,
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--print-timeout",
        f"{TASK_TIMEOUT_SECONDS}s",
        "--dangerously-skip-permissions",
    ]
    if conv_id:
        cmd.extend(["--conversation", conv_id])

    logger.info(f"Running Task {task_id} in {WORKSPACE_DIR} (Model: {model}, Conv: {conv_id}): {prompt[:60]}...")

    status_embed = discord.Embed(
        title="🚀 Antigravity Agent Running",
        description=(
            f"**Task ID**: `{task_id[-8:]}` | **並列タスク**: {active_in_channel + 1}件目\n"
            f"**Model**: `{model}`\n"
            "**Workspace**: `simulation/v3`\n"
            f"**Prompt**: {prompt[:120]}{'...' if len(prompt) > 120 else ''}"
        ),
        color=discord.Color.gold(),
    )
    status_embed.add_field(name="Current Status", value="🧠 Initializing & Reasoning...", inline=False)
    status_embed.set_footer(text="React with 🛑 or type !abort to cancel")

    # If triggered by deferred slash command, send via followup so Discord's "thinking..." spinner clears!
    if interaction:
        status_msg = await interaction.followup.send(embed=status_embed, wait=True)
    elif reply_to_msg is not None:
        status_msg = await channel.send(embed=status_embed, reference=reply_to_msg)
    else:
        status_msg = await channel.send(embed=status_embed)

    try:
        await status_msg.add_reaction("🛑")
    except Exception:
        pass

    proc = None
    stderr_task: Optional[asyncio.Task] = None
    accumulated_text = ""
    timed_out = False
    was_aborted = False
    stderr_text = ""

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc_kwargs: Dict[str, Any] = {}
        if os.name == "posix":
            # New session group so the whole process tree can be terminated at once.
            proc_kwargs["start_new_session"] = True
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(WORKSPACE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            **proc_kwargs,
        )
        client.register_task(channel_id, task_id, proc)
        stderr_task = asyncio.create_task(_drain_stderr(proc.stderr))

        current_status_line = "🧠 Thinking / Analyzing codebase..."
        recent_activity_log: List[str] = []
        last_edit_time = time.time()
        started_at = time.time()

        async def update_live_embed(force: bool = False):
            nonlocal last_edit_time
            now = time.time()
            if force or (now - last_edit_time >= LIVE_EDIT_INTERVAL_SECONDS):
                try:
                    status_embed.set_field_at(
                        0,
                        name="Current Status",
                        value=current_status_line[:250],
                        inline=False,
                    )
                    if recent_activity_log:
                        log_display = "\n".join(recent_activity_log[-3:])
                        if len(status_embed.fields) > 1:
                            status_embed.set_field_at(1, name="Recent Activity", value=log_display[:500], inline=False)
                        else:
                            status_embed.add_field(name="Recent Activity", value=log_display[:500], inline=False)

                    status_embed.set_footer(
                        text=f"Elapsed: {format_duration(now - started_at)} | "
                        "React with 🛑 or type !abort to cancel"
                    )
                    await status_msg.edit(embed=status_embed)
                    last_edit_time = now
                except Exception as exc:
                    logger.debug("Live embed update failed: %s", exc)

        deadline = time.monotonic() + TASK_TIMEOUT_SECONDS
        stdout = proc.stdout
        if stdout is None:  # pragma: no cover - defensive
            raise RuntimeError("agy stdout pipe was not created.")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            try:
                line = await asyncio.wait_for(stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                timed_out = True
                break
            if not line:
                break
            line_str = line.decode("utf-8", errors="replace").strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
                event = data.get("event")

                if event == "init":
                    new_conv_id = data.get("conversation_id")
                    if new_conv_id:
                        client.set_conversation(channel_id, new_conv_id)

                elif event == "step_update":
                    step = data.get("step_update", {})
                    step_type = step.get("step_type")
                    state = step.get("state")
                    tool_name = step.get("tool_name")

                    # Subagent events
                    if step_type == "subagent" or tool_name == "invoke_subagent":
                        sub_info = step.get("subagent_info", {})
                        subagents = sub_info.get("subagents", [])
                        if subagents:
                            sub_types = ", ".join(s.get("type_name", "subagent") for s in subagents)
                            current_status_line = f"👥 Subagent running: {sub_types}"
                            recent_activity_log.append(f"Subagent `{sub_types}` active")
                        else:
                            current_status_line = "👥 Managing subagents..."
                        await update_live_embed()

                    # Tool execution events
                    elif step_type == "tool":
                        info = step.get("tool_info", {})
                        params = info.get("parameters", {})
                        if tool_name == "run_command":
                            cmd_str = params.get("CommandLine", "")[:60]
                            current_status_line = f"⚙️ Running command: `{cmd_str}`"
                        elif tool_name in ("view_file", "replace_file_content", "write_to_file"):
                            target_file = params.get("TargetFile") or params.get("AbsolutePath", "")
                            base_name = Path(target_file).name if target_file else "file"
                            current_status_line = f"📝 File op ({tool_name}): `{base_name}`"
                        elif tool_name == "invoke_subagent":
                            current_status_line = "👥 Spawning subagents..."
                        else:
                            current_status_line = f"⚙️ Tool: `{tool_name}`"

                        if state == "DONE":
                            recent_activity_log.append(f"DONE {current_status_line}")
                        await update_live_embed()

                    # Agent response streaming / thinking
                    elif step_type == "agent_response":
                        delta = step.get("text_delta", "")
                        accumulated_text += delta
                        usage = step.get("usage", {})
                        thinking_tokens = usage.get("thinking_tokens", 0)
                        if thinking_tokens > 0:
                            current_status_line = f"🧠 Thinking ({thinking_tokens} tokens reasoning)..."
                        else:
                            current_status_line = "✍️ Generating response..."
                        await update_live_embed()

                elif event == "result":
                    res = data.get("result", {})
                    resp_text = res.get("response")
                    if resp_text:
                        accumulated_text = resp_text

            except json.JSONDecodeError:
                accumulated_text += line_str + "\n"

        if timed_out:
            logger.warning("Task %s exceeded the %ds timeout. Terminating.", task_id, TASK_TIMEOUT_SECONDS)
            terminate_process_tree(proc)

        try:
            await asyncio.wait_for(proc.wait(), timeout=15)
        except asyncio.TimeoutError:  # pragma: no cover - defensive
            logger.warning("Task %s did not exit within the grace period.", task_id)
            terminate_process_tree(proc)

        was_aborted = task_id in client.aborted_tasks
        if stderr_task is not None:
            try:
                stderr_text = await asyncio.wait_for(stderr_task, timeout=5)
            except asyncio.TimeoutError:  # pragma: no cover - defensive
                stderr_task.cancel()

        # The CLI enforces --print-timeout on its own and reports the event on stderr.
        cli_timed_out = "print timeout" in stderr_text.lower()

        if timed_out or cli_timed_out:
            partial = accumulated_text.strip()
            message = (
                f"❌ Execution timed out after {format_duration(TASK_TIMEOUT_SECONDS)}. "
                "The agent was terminated to protect the host."
            )
            if partial:
                message += f"\n\n{partial}"
            elif stderr_text:
                message += f"\n```\n{stderr_text[:1500]}\n```"
            accumulated_text = message
        elif was_aborted:
            accumulated_text = "🛑 Task was aborted by the user."
        elif proc.returncode != 0 and not accumulated_text.strip():
            accumulated_text = (
                f"❌ Process exited with code {proc.returncode}:\n```\n{stderr_text[:1800]}\n```"
            )

    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            terminate_process_tree(proc)
        if stderr_task is not None:
            stderr_task.cancel()
        try:
            await channel.send("🛑 Task was cancelled.")
        except Exception:
            pass
        raise
    except Exception as e:
        error_id = make_error_id()
        logger.error("Error running agy (error_id=%s): %s", error_id, e, exc_info=True)
        accumulated_text = (
            "❌ 内部エラーが発生しました。詳細_LOGを確認してください。\n"
            f"**Error ID**: `{error_id}`"
        )
    finally:
        client.unregister_task(channel_id, task_id)
        if stderr_task is not None and not stderr_task.done():
            stderr_task.cancel()

    # Clean up status message
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Post final output
    if not accumulated_text.strip():
        accumulated_text = "(Task completed. No text response was returned.)"

    current_conv_id = client.get_conversation(channel_id)
    reply_view = SessionReplyView(conv_id=current_conv_id)

    async def deliver(content: str, **kwargs: Any) -> Any:
        if reply_to_msg is not None:
            return await channel.send(content, reference=reply_to_msg, **kwargs)
        return await channel.send(content, **kwargs)

    # Discord 2000 character limit handling
    if len(accumulated_text) > DELIVERY_ATTACH_THRESHOLD:
        file_data = io.BytesIO(accumulated_text.encode("utf-8"))
        discord_file = discord.File(file_data, filename="response.md")
        preview = accumulated_text[:1200] + "\n\n... (Full response attached below as response.md)"
        await deliver(preview, file=discord_file, view=reply_view)
    else:
        chunks = split_text_for_discord(accumulated_text)
        for idx, chunk in enumerate(chunks):
            # Attach the interactive reply button to the final chunk
            if idx == len(chunks) - 1:
                await deliver(chunk, view=reply_view)
            else:
                await deliver(chunk)


# ==========================================
# Text Message & Prefix Commands Handler
# ==========================================

PREFIX_COMMANDS = (
    "chat",
    "chats",
    "history",
    "resume",
    "model",
    "m",
    "models",
    "new",
    "abort",
    "help",
    "status",
    "chrome",
)


@client.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot:
        return

    content = message.content.strip()
    target = ResponseTarget(message=message)

    # Prefix commands (!chat, !chats, !resume, !model, !models, !new, !abort, !help)
    if content.startswith(("!", "/")):
        parts = content[1:].split(maxsplit=1)
        cmd_name = parts[0].lower() if parts else ""
        cmd_arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd_name in PREFIX_COMMANDS:
            if not check_auth(target):
                await target.send("❌ Unauthorized: You do not have permission to control Antigravity.")
                return

            if cmd_name == "status":
                await impl_status(target)
                return

            elif cmd_name == "chrome":
                await impl_chrome(target, cmd_arg or "http://localhost:5000")
                return

            elif cmd_name == "chat":
                if not cmd_arg:
                    await target.send("⚠️ Please provide a prompt. Usage: `!chat <prompt>`")
                    return
                # Non-blocking parallel execution
                client.spawn_background(
                    execute_agy_prompt(message.channel, cmd_arg, message.author, reply_to_msg=message)
                )
                return

            elif cmd_name in ("chats", "history"):
                await impl_chats(target)
                return

            elif cmd_name == "resume":
                if not cmd_arg:
                    await target.send("⚠️ Please specify a session ID. Example: `!resume 8678b84b`")
                    return
                await impl_resume(target, cmd_arg)
                return

            elif cmd_name in ("model", "m"):
                await impl_model(target, cmd_arg or None)
                return

            elif cmd_name == "models":
                await impl_models(target, refresh=cmd_arg.lower() == "refresh")
                return

            elif cmd_name == "new":
                await impl_new(target)
                return

            elif cmd_name == "abort":
                await impl_abort(target)
                return

            elif cmd_name == "help":
                await impl_help(target)
                return

    # Natural conversation check:
    # 1. Direct message (DM) -> Always conversational without mention
    # 2. Dedicated channel configured in ALLOWED_CHANNEL_IDS -> Conversational without mention
    # 3. Mentioned by user -> Conversational
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_dedicated_channel = len(ALLOWED_CHANNEL_IDS) > 0 and message.channel.id in ALLOWED_CHANNEL_IDS
    is_mentioned = client.user in message.mentions

    if not (is_dm or is_dedicated_channel or is_mentioned):
        return

    if not check_auth(target):
        await target.send("❌ Unauthorized: You do not have permission to control Antigravity.")
        return

    # Clean prompt
    prompt = message.content.replace(f"<@{client.bot_user_id}>", "").strip()
    if not prompt:
        current_model = client.get_model(message.channel.id)
        current_conv = client.get_conversation(message.channel.id)
        conv_label = current_conv[:8] if current_conv else "New Session"
        await target.send(
            "👋 Antigravity Remote Bridge is ready in `simulation/v3`.\n"
            f"Active Model: `{current_model}` | Session: `{conv_label}`\n"
            "Send me any task, or type `!help` / `!status` / `!chrome` / `!chats` to manage sessions."
        )
        return

    # Non-blocking parallel execution
    client.spawn_background(
        execute_agy_prompt(message.channel, prompt, message.author, reply_to_msg=message)
    )


@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot:
        return
    if str(reaction.emoji) == "🛑":
        channel_id = reaction.message.channel.id
        if client.is_authorized(user.id, channel_id):
            aborted = client.abort_tasks(channel_id=channel_id)
            if aborted > 0:
                await reaction.message.channel.send(
                    f"{aborted} 件のタスクが {user.mention} により停止されました。"
                )


@client.event
async def on_error(event: str, *args: Any, **kwargs: Any) -> None:
    """Log unhandled client errors instead of letting them vanish into stderr."""
    error_id = make_error_id()
    logger.error("Unhandled client error in '%s' (error_id=%s)", event, error_id, exc_info=True)


@client.event
async def on_ready():
    metrics = await asyncio.to_thread(get_system_metrics)
    logger.info(f"Discord Bridge Bot logged in as {client.user} (ID: {client.bot_user_id})")
    logger.info(f"Strict Workspace Directory: {WORKSPACE_DIR}")
    logger.info(f"Default Model: {DEFAULT_MODEL}")
    logger.info(f"System CPU: {metrics['cpu']} | Memory: {metrics['memory']}")
    logger.info(f"System GPU:\n{metrics['gpu']}")
    if DEFAULT_MODEL not in AVAILABLE_MODELS:
        logger.warning(
            "DEFAULT_MODEL '%s' is not part of the known model catalogue. "
            "Run `!models refresh` to update the catalogue.",
            DEFAULT_MODEL,
        )
    executable = resolve_agy_executable()
    if executable:
        logger.info(f"Antigravity CLI: {executable}")
    else:
        logger.error("Antigravity CLI ('%s') was not found in PATH. Agent tasks will fail.", AGY_CLI)
    if ALLOWED_USER_IDS:
        logger.info(f"Authorized User IDs: {ALLOWED_USER_IDS}")
    else:
        logger.warning("No ALLOWED_USER_IDS set. Anyone in invited channels can control Antigravity!")

    # Optionally notify startup message with system metrics to configured channels
    embed = discord.Embed(
        title="Antigravity Discord Bridge 起動完了",
        description="Antigravity Remote Bridge が起動しました。\n並列チャット処理・Chrome起動・システム監視が有効です。",
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="CPU 使用率", value=f"`{metrics['cpu']}`", inline=True)
    embed.add_field(name="メモリ (RAM)", value=f"`{metrics['memory']}`", inline=True)
    embed.add_field(name="GPU 稼働状況", value=f"```{metrics['gpu']}```", inline=False)
    embed.add_field(
        name="設定情報",
        value=(
            f"• モデル: `{DEFAULT_MODEL}`\n"
            "• ワークスペース: `simulation/v3`\n"
            "• コマンド一覧: `!help` / `/status` / `/chrome` / `/chat`\n"
            f"• 同時実行上限: {MAX_TASKS_PER_CHANNEL}件/チャンネル, {MAX_TASKS_GLOBAL}件/全体"
        ),
        inline=False,
    )

    # Post to allowed channels or first available text channel
    target_channels: List[discord.TextChannel] = []
    if ALLOWED_CHANNEL_IDS:
        for cid in ALLOWED_CHANNEL_IDS:
            ch = client.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                target_channels.append(ch)

    if not target_channels:
        # Send to first available text channel if bot has write permissions
        for guild in client.guilds:
            for ch in guild.text_channels:
                if ch.permissions_for(guild.me).send_messages:
                    target_channels.append(ch)
                    break
            if target_channels:
                break

    for ch in target_channels:
        try:
            await ch.send(embed=embed)
        except Exception as e:
            logger.warning(f"Could not send startup message to channel {ch.id}: {e}")


def main():
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("\n" + "=" * 60)
        print("ERROR: DISCORD_BOT_TOKEN is not set in .env file!")
        print("Please edit .env and configure DISCORD_BOT_TOKEN before running.")
        print("=" * 60 + "\n")
        sys.exit(1)

    if not resolve_agy_executable():
        print("\n" + "=" * 60)
        print(f"WARNING: Antigravity CLI ('{AGY_CLI}') was not found in PATH.")
        print("Agent commands will fail until AGY_CLI_PATH points to a valid executable.")
        print("=" * 60 + "\n")

    print("Starting Discord Remote Bridge for Antigravity (Workspace: simulation/v3)...")
    client.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
