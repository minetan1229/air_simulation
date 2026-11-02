"""
Discord Remote Bridge for Antigravity (AGY) and Kilo Code (KILO)
Allows full remote control of the local agent stack from Discord.

Features:
- Workspace strictly locked to simulation/v3 directory
- Two agent backends: Antigravity (primary) and Kilo Code (automatic fallback)
- Real-time live status updates (Thinking progress, Tool executions, Subagent spawns & completion)
- Interactive multi-turn chat (DM or dedicated channel without repeated mentions)
- Chat List & History inspection (!chats / /chats) with resumption (!resume / /resume)
- Direct chat command (!chat <prompt> / /chat <prompt>)
- Dynamic model selection via slash commands (/model) or prefix (!model, !m)
- Remaining rate limit / quota display (!quota / /quota)
- Monitor agent surfacing health, API latency, errors and rate limits (!monitor / /monitor)
- Automatic continuation on HTTP 429 with exponential backoff
- Automatic fallback from Antigravity to Kilo Code (free model) when the primary fails
- Process cancellation / abort (!abort, /abort, or 🛑 reaction)
- User / Channel access control
"""

import asyncio
import io
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import discord
from discord import app_commands
from dotenv import load_dotenv

try:
    from tools.bridge_backends import (
        ANTIGRAVITY_BACKEND,
        ANTIGRAVITY_MODELS,
        BACKEND_MODELS,
        KILO_BACKEND,
        KILO_DEFAULT_MODEL,
        KILO_MODELS,
        WORKSPACE_DIR,
        build_default_backends,
        describe_model,
        detect_backend,
        model_catalog,
    )
    from tools.bridge_monitor import MonitorAgent, MonitorAlert, get_system_metrics as _collect_system_metrics
    from tools.bridge_quota import QuotaSnapshot, QuotaTracker
    from tools.bridge_router import BridgeOutcome, BridgeRequest, BridgeRouter
except ImportError:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tools.bridge_backends import (
        ANTIGRAVITY_BACKEND,
        ANTIGRAVITY_MODELS,
        BACKEND_MODELS,
        KILO_BACKEND,
        KILO_DEFAULT_MODEL,
        KILO_MODELS,
        WORKSPACE_DIR,
        build_default_backends,
        describe_model,
        detect_backend,
        model_catalog,
    )
    from tools.bridge_monitor import MonitorAgent, MonitorAlert, get_system_metrics as _collect_system_metrics
    from tools.bridge_quota import QuotaSnapshot, QuotaTracker
    from tools.bridge_router import BridgeOutcome, BridgeRequest, BridgeRouter


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("DiscordBridge")

# Load environment variables
load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()
ALLOWED_USERS_RAW = os.getenv("ALLOWED_USER_IDS", "").strip()
ALLOWED_CHANNELS_RAW = os.getenv("ALLOWED_CHANNEL_IDS", "").strip()
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gemini-3.8-flash-low").strip()
DEFAULT_BACKEND = os.getenv("DEFAULT_BACKEND", ANTIGRAVITY_BACKEND).strip()
FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", KILO_DEFAULT_MODEL).strip()

# Router / resilience configuration.
FALLBACK_ENABLED = os.getenv("BRIDGE_FALLBACK_ENABLED", "1") == "1"
RATE_LIMIT_MAX_RETRIES = int(os.getenv("BRIDGE_RATE_LIMIT_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("BRIDGE_BACKOFF_BASE", "2.0"))
BACKOFF_CAP_SECONDS = float(os.getenv("BRIDGE_BACKOFF_CAP", "60.0"))
TASK_TIMEOUT_SECONDS = float(os.getenv("BRIDGE_TASK_TIMEOUT", "1800"))
MONITOR_INTERVAL_SECONDS = float(os.getenv("BRIDGE_MONITOR_INTERVAL", "60"))
MONITOR_ERROR_THRESHOLD = int(os.getenv("BRIDGE_MONITOR_ERROR_THRESHOLD", "3"))
QUOTA_CACHE_TTL_SECONDS = float(os.getenv("BRIDGE_QUOTA_TTL", "120"))

ALLOWED_USER_IDS: Set[int] = {
    int(u.strip()) for u in ALLOWED_USERS_RAW.split(",") if u.strip().isdigit()
}
ALLOWED_CHANNEL_IDS: Set[int] = {
    int(c.strip()) for c in ALLOWED_CHANNELS_RAW.split(",") if c.strip().isdigit()
}

# ---------------------------------------------------------------------------
# Safety guardrails (P1-12)
# ---------------------------------------------------------------------------
# Empty allowlist now means DENY ALL (safe default).  Never treat an empty
# ALLOWED_USER_IDS as "allow everyone".
# Per-user command rate limit (token bucket over a fixed window).
RATE_LIMIT_MAX_COMMANDS = int(os.getenv("DISCORD_RATE_LIMIT_MAX", "10"))
RATE_LIMIT_WINDOW_SECONDS = float(os.getenv("DISCORD_RATE_LIMIT_WINDOW", "60.0"))
_user_command_times: Dict[int, List[float]] = {}

# Maximum accepted prompt length (characters).
MAX_PROMPT_LEN = int(os.getenv("DISCORD_MAX_PROMPT_LEN", "4000"))

# Chrome URL blocklist: non-http/https schemes and private/loopback/link-local /
# cloud-metadata addresses are refused.  The bare "localhost" hostname (the
# documented default dev URL) is allowed; IP-based loopback/private addresses are not.
_CHROME_BLOCKLIST_HOSTS = {
    "metadata.google.internal",
    "metadata.azure.com",
    "metadata.aws.internal",
    "169.254.169.254",
}

# Single-instance control: bind a TCP loopback port so a second bridge process
# cannot start while one is running.
_INSTANCE_LOCK_PORT = int(os.getenv("DISCORD_BRIDGE_LOCK_PORT", "49372"))
_INSTANCE_LOCK_SOCK: Optional[socket.socket] = None

# --dangerously-skip-permissions is never honoured by the bridge; authorization
# is always enforced.  Kept for documentation only; disabled by default.
ENABLE_DANGEROUS_SKIP_PERMISSIONS = bool(os.getenv("DISCORD_DANGEROUS_SKIP_PERMISSIONS", "0") == "1")


def acquire_instance_lock() -> bool:
    """Return True if this process is the single bridge instance (port bound)."""
    global _INSTANCE_LOCK_SOCK
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", _INSTANCE_LOCK_PORT))
        sock.listen(1)
        _INSTANCE_LOCK_SOCK = sock
        return True
    except OSError as exc:
        # Intentional guard: the lock is not held when the bind fails, so the
        # caller must simply report "another instance is running".  The bind
        # error is recorded (class + message) instead of being swallowed, and
        # the half-open socket is closed on a best-effort basis -- a failure
        # there must not hide the bind failure.
        logger.debug(
            "Instance lock bind failed on port %s (%s: %s)",
            _INSTANCE_LOCK_PORT,
            type(exc).__name__,
            exc,
        )
        try:
            sock.close()
        except OSError as close_exc:
            logger.debug(
                "Could not close the unbound instance lock socket (%s: %s)",
                type(close_exc).__name__,
                close_exc,
            )
        return False


def release_instance_lock() -> None:
    global _INSTANCE_LOCK_SOCK
    if _INSTANCE_LOCK_SOCK is not None:
        try:
            _INSTANCE_LOCK_SOCK.close()
        except OSError as exc:
            # Intentional best-effort cleanup: the lock socket is dropped
            # either way, and a failing close must not stop the shutdown.
            logger.debug(
                "Could not close the instance lock socket (%s: %s)",
                type(exc).__name__,
                exc,
            )
        _INSTANCE_LOCK_SOCK = None


def rate_limit_check(user_id: int) -> bool:
    """Token-bucket rate limit per user.  Returns True if the command is allowed."""
    now = time.time()
    window = RATE_LIMIT_WINDOW_SECONDS
    times = [t for t in _user_command_times.get(user_id, []) if now - t < window]
    if len(times) >= RATE_LIMIT_MAX_COMMANDS:
        _user_command_times[user_id] = times
        return False
    times.append(now)
    _user_command_times[user_id] = times
    return True


def is_chrome_url_allowed(url: str) -> bool:
    """Validate a URL passed to !chrome / /chrome: http/https only, blocklist applied."""
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError as exc:
        # Intentional guard: an unparsable URL is refused (fail closed).  The
        # parse error is recorded so a malformed input is visible in the log.
        logger.debug("Could not parse the URL %r (%s: %s)", url, type(exc).__name__, exc)
        return False
    if parsed.scheme.lower() not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in _CHROME_BLOCKLIST_HOSTS:
        return False
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Non-IP hostname (public domain): allowed.
        return True
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
    ):
        return False
    return True


def make_error_id() -> str:
    return uuid.uuid4().hex[:12]

# Strictly lock workspace to simulation/v3 (defined in tools.bridge_backends)
assert WORKSPACE_DIR == Path(__file__).resolve().parent.parent

# Brain directories to discover conversation sessions
CLI_BRAIN_DIR = Path(os.environ.get("USERPROFILE", "")) / ".gemini" / "antigravity-cli" / "brain"
APP_BRAIN_DIR = Path(os.environ.get("USERPROFILE", "")) / ".gemini" / "antigravity" / "brain"

# Every selectable model: Antigravity first, then the Kilo Code catalogue.
AVAILABLE_MODELS: Dict[str, str] = model_catalog()


def get_system_metrics() -> Dict[str, Any]:
    """Gather CPU, RAM, and GPU status (implemented in tools.bridge_monitor)."""
    return _collect_system_metrics()


def launch_chrome_browser(url: str = "http://localhost:5000") -> Tuple[bool, str]:
    """Launch Google Chrome on the host environment."""
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
            [target_exe, url],
            creationflags=creation_flags,
            close_fds=True,
        )
        return True, f"Google Chrome を起動しました (PID: {proc.pid}, URL: {url})"
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
        for item in brain_root.iterdir():
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
            except (OSError, ValueError) as exc:
                # Intentional best-effort scan: one unreadable transcript must
                # not abort the whole session list.  OSError covers the file
                # access, ValueError covers a malformed JSON line and a
                # UnicodeDecodeError (a ValueError subclass).  The reason is
                # recorded instead of being swallowed.
                logger.debug(
                    "Could not read the transcript %s (%s: %s)",
                    transcript_file,
                    type(exc).__name__,
                    exc,
                )

            sessions.append({
                "id": item.name,
                "short_id": item.name[:8],
                "mtime": mtime,
                "date_str": datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M"),
                "prompt": first_prompt[:70] + ("..." if len(first_prompt) > 70 else ""),
                "backend": ANTIGRAVITY_BACKEND,
            })

    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions[:limit]


def get_recent_kilo_sessions(limit: int = 10) -> List[Dict[str, Any]]:
    """Collect the recent Kilo Code sessions of this workspace."""
    backend = client.backends.get(KILO_BACKEND)
    if backend is None:
        return []
    raw_sessions = backend.list_sessions(limit=limit)
    sessions: List[Dict[str, Any]] = []
    for item in raw_sessions:
        session_id = str(item.get("id") or "")
        if not session_id:
            continue
        updated = item.get("updated") or item.get("created") or 0
        try:
            mtime = float(updated) / 1000.0
        except (TypeError, ValueError):
            mtime = 0.0
        title = str(item.get("title") or "(No prompt)")
        sessions.append({
            "id": session_id,
            "short_id": session_id[:8],
            "mtime": mtime,
            "date_str": datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M") if mtime else "-",
            "prompt": title[:70] + ("..." if len(title) > 70 else ""),
            "backend": KILO_BACKEND,
        })
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    return sessions[:limit]


class ModelSelectView(discord.ui.View):
    """Dropdown View for selecting models interactively."""
    def __init__(self, current_model: str, on_select_callback):
        super().__init__(timeout=60)
        self.current_model = current_model
        self.on_select_callback = on_select_callback

        options = [
            discord.SelectOption(
                label=display_name[:100],
                value=model_id,
                description=f"ID: {model_id}"[:100],
                default=(model_id == current_model),
            )
            for model_id, display_name in AVAILABLE_MODELS.items()
        ]

        select = discord.ui.Select(
            placeholder="Select an Antigravity Model...",
            options=options,
            custom_id="model_select",
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_model = interaction.data["values"][0]
        await self.on_select_callback(interaction, selected_model)


class ChatSelectView(discord.ui.View):
    """Dropdown View for resuming past conversations interactively."""
    def __init__(self, sessions: List[Dict[str, Any]], current_conv: Optional[str], on_select_callback):
        super().__init__(timeout=60)
        self.on_select_callback = on_select_callback

        options = []
        for s in sessions[:15]:
            stored = encode_session(s.get("backend", ANTIGRAVITY_BACKEND), s["id"])
            is_active = (stored == current_conv or s["short_id"] == current_conv)
            label = f"{s['date_str']} - {s['prompt']}"[:95]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=stored,
                    description=f"Session ID: {s['short_id']}",
                    default=is_active,
                )
            )

        if options:
            select = discord.ui.Select(
                placeholder="Select a conversation to resume...",
                options=options,
                custom_id="chat_select",
            )
            select.callback = self.select_callback
            self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_conv = interaction.data["values"][0]
        await self.on_select_callback(interaction, selected_conv)


class PromptReplyModal(discord.ui.Modal, title="💬 返信・プロンプト送信 (セッション継続)"):
    """Modal to enter prompt and continue the conversation session."""
    prompt_input = discord.ui.TextInput(
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
            client.channel_conversations[interaction.channel_id] = self.target_conv_id

        user_prompt = self.prompt_input.value.strip()
        if len(user_prompt) > MAX_PROMPT_LEN:
            await interaction.response.send_message(
                f"⚠️ プロンプトが長すぎます（{MAX_PROMPT_LEN} 文字まで）。", ephemeral=True
            )
            return
        await interaction.response.defer()
        asyncio.create_task(
            execute_agy_prompt(
                interaction.channel,
                user_prompt,
                interaction.user,
                interaction=interaction,
            )
        )


class SessionReplyView(discord.ui.View):
    """Interactive Button View attached to bot responses allowing instant replies in the same session."""
    def __init__(self, conv_id: Optional[str] = None):
        super().__init__(timeout=None)
        self.conv_id = conv_id

        # Custom ID encodes conv_id if present to survive restarts
        btn_custom_id = f"btn_reply_session:{conv_id}" if conv_id else "btn_reply_session:default"
        self.reply_button = discord.ui.Button(
            label="💬 返信する (このセッションで継続)",
            style=discord.ButtonStyle.primary,
            custom_id=btn_custom_id,
            emoji="💬",
        )
        self.reply_button.callback = self.on_reply_clicked
        self.add_item(self.reply_button)

    async def on_reply_clicked(self, interaction: discord.Interaction):
        if not client.is_authorized(interaction.user.id, interaction.channel_id):
            await interaction.response.send_message("❌ Unauthorized: You do not have permission.", ephemeral=True)
            return

        # Extract target_conv from instance attribute, custom_id, or channel state
        target_conv = self.conv_id
        if not target_conv and interaction.data and "custom_id" in interaction.data:
            c_id = interaction.data["custom_id"]
            if c_id.startswith("btn_reply_session:") and c_id != "btn_reply_session:default":
                target_conv = c_id.split("btn_reply_session:", 1)[1]

        if not target_conv:
            target_conv = client.channel_conversations.get(interaction.channel_id)

        modal = PromptReplyModal(target_conv_id=target_conv)
        await interaction.response.send_modal(modal)


class DiscordBridgeClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

        # State tracking: channel_id -> current_model
        self.channel_models: Dict[int, str] = {}
        # channel_id -> conversation_id (for multi-turn memory)
        self.channel_conversations: Dict[int, Optional[str]] = {}
        # channel_id -> backend that owns the stored conversation
        self.channel_session_backends: Dict[int, str] = {}
        # channel_id -> active backend (antigravity | kilo)
        self.channel_backends: Dict[int, str] = {}
        # task_id (str) -> asyncio.subprocess.Process
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
        # channel_id -> Set[str] of task_ids
        self.channel_tasks: Dict[int, Set[str]] = {}

        # Agent stack: backends, monitor agent, quota tracker and the router.
        self.backends = build_default_backends()
        self.quota_tracker = QuotaTracker(ttl=QUOTA_CACHE_TTL_SECONDS)
        self.monitor = MonitorAgent(
            interval=MONITOR_INTERVAL_SECONDS,
            error_threshold=MONITOR_ERROR_THRESHOLD,
            on_alert=self._on_monitor_alert,
        )
        self.router = BridgeRouter(
            backends=self.backends,
            monitor=self.monitor,
            fallback_enabled=FALLBACK_ENABLED,
            max_rate_limit_retries=RATE_LIMIT_MAX_RETRIES,
            backoff_base=BACKOFF_BASE_SECONDS,
            backoff_cap=BACKOFF_CAP_SECONDS,
            task_timeout=TASK_TIMEOUT_SECONDS,
            quota_tracker=self.quota_tracker,
        )
        # Channels that receive monitor alerts and fallback notices.
        self.alert_channels: Set[int] = set(ALLOWED_CHANNEL_IDS)

    async def setup_hook(self):
        # Register persistent SessionReplyView for dynamic custom_ids
        self.add_view(SessionReplyView(None))
        await self.tree.sync()
        logger.info("Application slash commands and persistent views synced.")

    def is_authorized(self, user_id: int, channel_id: int) -> bool:
        """Check if user and channel are allowed to issue commands.

        Safe default: an empty ALLOWED_USER_IDS means NO user is authorized.
        """
        if not ALLOWED_USER_IDS:
            return False
        if user_id not in ALLOWED_USER_IDS:
            return False
        if ALLOWED_CHANNEL_IDS and channel_id not in ALLOWED_CHANNEL_IDS:
            return False
        return True

    def get_model(self, channel_id: int) -> str:
        return self.channel_models.get(channel_id, DEFAULT_MODEL)

    def set_model(self, channel_id: int, model: str):
        """Store the active model and derive the owning backend from its id."""
        self.channel_models[channel_id] = model
        self.channel_backends[channel_id] = detect_backend(model)

    def get_backend(self, channel_id: int) -> str:
        """Return the backend that serves the requests of this channel."""
        return self.channel_backends.get(channel_id, DEFAULT_BACKEND)

    def set_backend(self, channel_id: int, backend: str):
        """Switch the backend of a channel, aligning the active model with it."""
        backend = backend.strip().lower()
        if backend not in BACKEND_MODELS:
            raise ValueError(f"未知のバックエンド: {backend}")
        self.channel_backends[channel_id] = backend
        current = self.channel_models.get(channel_id)
        if backend == KILO_BACKEND:
            if not current or detect_backend(current) != KILO_BACKEND:
                self.channel_models[channel_id] = KILO_DEFAULT_MODEL
        else:
            if current and detect_backend(current) != ANTIGRAVITY_BACKEND:
                self.channel_models[channel_id] = DEFAULT_MODEL

    def register_task(self, channel_id: int, task_id: str, proc: asyncio.subprocess.Process):
        self.active_processes[task_id] = proc
        if channel_id not in self.channel_tasks:
            self.channel_tasks[channel_id] = set()
        self.channel_tasks[channel_id].add(task_id)

    def unregister_task(self, channel_id: int, task_id: str):
        self.active_processes.pop(task_id, None)
        if channel_id in self.channel_tasks:
            self.channel_tasks[channel_id].discard(task_id)
            if not self.channel_tasks[channel_id]:
                self.channel_tasks.pop(channel_id, None)

    def _kill_task(self, task_id: str, proc: asyncio.subprocess.Process) -> bool:
        """Kill one running task and report whether the process was killed.

        Intentional guard: a task that cannot be killed (it exited in the
        meantime, or the platform has no kill implementation) must not abort
        the whole ``!abort`` command, so the remaining tasks are still
        aborted.  The exception types are narrow: a process that has already
        gone raises ``ProcessLookupError`` (an ``OSError``) and Windows raises
        ``NotImplementedError`` for an unsupported signal.  The cause is
        recorded instead of being swallowed.
        """
        try:
            proc.kill()
            return True
        except ProcessLookupError as exc:
            logger.info(
                "Task %s had already exited when it was aborted (%s: %s)",
                task_id,
                type(exc).__name__,
                exc,
            )
        except (OSError, NotImplementedError) as exc:
            logger.warning(
                "Could not abort the task %s (%s: %s)",
                task_id,
                type(exc).__name__,
                exc,
            )
        return False

    def abort_tasks(self, channel_id: Optional[int] = None, task_id: Optional[str] = None) -> int:
        """Abort tasks either by specific task_id or for an entire channel."""
        aborted = 0
        if task_id and task_id in self.active_processes:
            p = self.active_processes[task_id]
            if p.returncode is None and self._kill_task(task_id, p):
                aborted += 1
            return aborted

        target_tasks = self.channel_tasks.get(channel_id, set()).copy() if channel_id else set(self.active_processes.keys())
        for tid in target_tasks:
            p = self.active_processes.get(tid)
            if p and p.returncode is None and self._kill_task(tid, p):
                aborted += 1
        return aborted

    async def _on_monitor_alert(self, alert: MonitorAlert) -> None:
        """Deliver a monitor alert to Discord and to the main agent."""
        await deliver_monitor_alert(alert)


client = DiscordBridgeClient()


def encode_session(backend: str, session_id: Optional[str]) -> Optional[str]:
    """Store a session id together with the backend that owns it."""
    if not session_id:
        return None
    if backend == KILO_BACKEND:
        return f"{KILO_BACKEND}:{session_id}"
    return session_id


def decode_session(value: Optional[str]) -> Tuple[str, Optional[str]]:
    """Split a stored session value into ``(backend, session_id)``."""
    if not value:
        return ANTIGRAVITY_BACKEND, None
    if ":" in value:
        backend, _, session_id = value.partition(":")
        if backend in BACKEND_MODELS:
            return backend, session_id or None
    return ANTIGRAVITY_BACKEND, value


def notify_main_agent(title: str, summary: str, status_type: str = "warning") -> bool:
    """Send a webhook notification to the main agent (Discord webhook channel)."""
    try:
        from tools.notify_discord import send_notification
    except ImportError:  # pragma: no cover - direct script execution
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from tools.notify_discord import send_notification

    try:
        return send_notification(status_type, title, summary, auto_report=False)
    except Exception as exc:  # pragma: no cover - notification must not break the flow
        logger.warning(f"Could not notify the main agent: {exc}")
        return False


async def deliver_monitor_alert(alert: MonitorAlert) -> None:
    """Post a monitor alert into Discord and notify the main agent."""
    color = {
        "info": discord.Color.blue(),
        "warning": discord.Color.orange(),
        "critical": discord.Color.red(),
    }.get(alert.level, discord.Color.blue())

    embed = discord.Embed(
        title=f"🩺 監視エージェント: {alert.kind}",
        description="\n".join(alert.to_embed_lines()),
        color=color,
        timestamp=datetime.now(),
    )
    if alert.details:
        embed.add_field(
            name="詳細",
            value="\n".join(f"`{key}`: {value}" for key, value in alert.details.items())[:1000],
            inline=False,
        )

    targets: List[discord.TextChannel] = []
    for cid in client.alert_channels:
        channel = client.get_channel(cid)
        if isinstance(channel, discord.TextChannel):
            targets.append(channel)

    for channel in targets:
        try:
            await channel.send(embed=embed)
        except Exception as exc:
            logger.warning(f"Could not deliver the monitor alert to {channel.id}: {exc}")

    notify_main_agent(
        f"監視エージェントアラート: {alert.kind}",
        alert.message,
        status_type="warning" if alert.level == "warning" else "error",
    )


def build_quota_embed(
    quota_snapshot: Optional[QuotaSnapshot],
    kilo_model: str,
    estimated_requests: Optional[float] = None,
    title: str = "📊 レート制限 / クォータ残量",
) -> discord.Embed:
    """Build the embed that shows the remaining rate limit of the backends."""
    embed = discord.Embed(title=title, color=discord.Color.teal(), timestamp=datetime.now())
    if quota_snapshot is None:
        embed.description = "ℹ️ クォータ情報をまだ取得していません (`!quota` で取得できます)。"
        return embed

    embed.description = "\n".join(quota_snapshot.summary_lines())
    if estimated_requests is not None:
        embed.add_field(
            name="🎯 推定利用可能回数",
            value=f"**約 {estimated_requests:.0f} 回**（実測した消費傾きからの推定）",
            inline=True,
        )
    if quota_snapshot.credits_remaining is not None:
        embed.add_field(
            name="💳 残りクレジット",
            value=f"**{quota_snapshot.credits_remaining}**",
            inline=True,
        )
    embed.add_field(name="🤖 Kilo Code モデル", value=f"`{kilo_model}`（無料）", inline=True)
    if quota_snapshot.error:
        embed.add_field(name="⚠️ 取得エラー", value=quota_snapshot.error[:1000], inline=False)
    return embed


def check_auth(interaction: discord.Interaction) -> bool:
    if not client.is_authorized(interaction.user.id, interaction.channel_id):
        return False
    return True


@client.event
async def on_ready():
    metrics = await asyncio.to_thread(get_system_metrics)
    logger.info(f"Discord Bridge Bot logged in as {client.user} (ID: {client.user.id})")
    logger.info(f"Strict Workspace Directory: {WORKSPACE_DIR}")
    logger.info(f"Default Model: {DEFAULT_MODEL}")
    logger.info(f"Backends: {client.router.backend_status()}")
    logger.info(f"System CPU: {metrics['cpu']} | Memory: {metrics['memory']}")
    logger.info(f"System GPU:\n{metrics['gpu']}")
    if ALLOWED_USER_IDS:
        logger.info(f"Authorized User IDs: {ALLOWED_USER_IDS}")
    else:
        logger.warning("ALLOWED_USER_IDS is empty: NO user is authorized (safe default deny-all).")

    # Start the monitor agent (health / API latency / errors / rate limits).
    client.monitor.start(client.backends)

    # Optionally notify startup message with system metrics to configured channels
    embed = discord.Embed(
        title="🚀 Agent Discord Bridge 起動完了",
        description=(
            "Antigravity (主) + Kilo Code (フォールバック) の Discord リモートブリッジです。\n"
            "並列チャット処理・Chrome起動・システム監視・レート制限表示が有効です。"
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="⚙️ CPU 使用率", value=f"`{metrics['cpu']}`", inline=True)
    embed.add_field(name="🧠 メモリ (RAM)", value=f"`{metrics['memory']}`", inline=True)
    embed.add_field(name="⚡ GPU 稼働状況", value=f"```{metrics['gpu']}```", inline=False)
    backend_status = client.router.backend_status()
    embed.add_field(
        name="🤖 エージェントバックエンド",
        value=(
            f"• Antigravity: `{'✅ 利用可能' if backend_status.get(ANTIGRAVITY_BACKEND) else '❌ 未検出'}`\n"
            f"• Kilo Code: `{'✅ 利用可能' if backend_status.get(KILO_BACKEND) else '❌ 未検出'}`\n"
            f"• フォールバック先モデル: `{FALLBACK_MODEL}`\n"
            f"• 429 自動継続: 最大 **{RATE_LIMIT_MAX_RETRIES}** 回 (指数バックオフ)"
        ),
        inline=False,
    )
    embed.add_field(
        name="ℹ️ 設定情報",
        value=(
            f"• 既定モデル: `{DEFAULT_MODEL}`\n"
            f"• ワークスペース: `simulation/v3`\n"
            f"• コマンド一覧: `!help` / `/status` / `/quota` / `/monitor` / `/backend` / `/chrome` / `/chat`"
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


# ==========================================
# Slash Commands
# ==========================================

@client.tree.command(name="chat", description="Send a prompt directly to Antigravity")
@app_commands.describe(prompt="The instruction or question for Antigravity")
async def cmd_chat(interaction: discord.Interaction, prompt: str):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized: You do not have permission to run commands.", ephemeral=True)
        return

    # Defer immediately to avoid Discord's 3-second interaction expiration
    await interaction.response.defer()
    # Pass interaction so execute_agy_prompt can resolve interaction followup
    await execute_agy_prompt(interaction.channel, prompt, interaction.user, interaction=interaction)


@client.tree.command(name="chats", description="List recent conversation sessions (Antigravity + Kilo Code) and resume them")
async def cmd_chats(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    # Defer immediately to prevent 404 Unknown interaction on disk scan
    await interaction.response.defer()

    # Offload disk search to thread to avoid blocking asyncio event loop
    agy_sessions = await asyncio.to_thread(get_recent_conversations, 10)
    kilo_sessions = await asyncio.to_thread(get_recent_kilo_sessions, 10)
    sessions = agy_sessions + kilo_sessions
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    current_conv = client.channel_conversations.get(interaction.channel_id)

    if not sessions:
        await interaction.followup.send("ℹ️ No previous conversation history found in workspace.", ephemeral=True)
        return

    embed = discord.Embed(
        title="💬 Recent Conversations (Chat List)",
        description=(
            f"Current active session: **`{current_conv[:8] if current_conv else 'None (New)'}`**\n"
            "Select from dropdown or use `/resume <id>` to switch."
        ),
        color=discord.Color.teal(),
    )
    for s in sessions:
        stored = encode_session(s.get("backend", ANTIGRAVITY_BACKEND), s["id"])
        is_active = stored == current_conv or s["short_id"] == current_conv
        prefix = "👉 **[ACTIVE]** " if is_active else "• "
        backend_tag = "🟦" if s.get("backend") == KILO_BACKEND else "🟧"
        embed.add_field(
            name=f"{prefix}{backend_tag} `{s['short_id']}` ({s['date_str']})",
            value=s["prompt"] or "(No prompt)",
            inline=False,
        )

    view = ChatSelectView(sessions, current_conv, on_chat_selected)
    await interaction.followup.send(embed=embed, view=view)


async def on_chat_selected(interaction: discord.Interaction, selected_conv: str):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    client.channel_conversations[interaction.channel_id] = selected_conv
    await interaction.response.send_message(
        f"✅ Conversation resumed: **`{selected_conv[:8]}`** (`{selected_conv}`). Next messages will continue this context."
    )


@client.tree.command(name="resume", description="Resume a conversation by session ID")
@app_commands.describe(conversation_id="Session ID or 8-character short ID")
async def cmd_resume(interaction: discord.Interaction, conversation_id: str):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    await interaction.response.defer()

    cid = conversation_id.strip()
    # Resolve short ID if given asynchronously
    agy_sessions = await asyncio.to_thread(get_recent_conversations, 30)
    kilo_sessions = await asyncio.to_thread(get_recent_kilo_sessions, 30)
    matched_full_id = cid
    matched_backend = ANTIGRAVITY_BACKEND
    for s in agy_sessions + kilo_sessions:
        if s["id"].startswith(cid):
            matched_full_id = s["id"]
            matched_backend = s.get("backend", ANTIGRAVITY_BACKEND)
            break

    client.channel_conversations[interaction.channel_id] = encode_session(matched_backend, matched_full_id)
    client.channel_session_backends[interaction.channel_id] = matched_backend
    await interaction.followup.send(
        f"✅ Resumed conversation **`{matched_full_id[:8]}`** (`{matched_full_id}`) on `{matched_backend}`."
    )


@client.tree.command(name="models", description="List all available models (Antigravity + Kilo Code)")
async def cmd_models(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized: You do not have permission to run commands.", ephemeral=True)
        return

    current = client.get_model(interaction.channel_id)
    backend = client.get_backend(interaction.channel_id)
    embed = discord.Embed(
        title="🤖 利用可能モデル (Antigravity + Kilo Code)",
        description=(
            f"現在のバックエンド: **`{backend}`** / アクティブモデル: **`{current}`**\n"
            "Kilo Code の既定は**無料モデル** (`kilo/kilo-auto/free`) です。"
        ),
        color=discord.Color.blue(),
    )
    for backend_name, catalog in BACKEND_MODELS.items():
        lines = []
        for model_id, name in catalog.items():
            prefix = "👉 " if model_id == current else "• "
            free_mark = " 🆓" if model_id.endswith(":free") or "free" in model_id.split("/")[-1] else ""
            lines.append(f"{prefix}{name}{free_mark} — `{model_id}`")
        embed.add_field(
            name=f"{'🟧' if backend_name == ANTIGRAVITY_BACKEND else '🟦'} {backend_name}",
            value="\n".join(lines)[:1000],
            inline=False,
        )

    view = ModelSelectView(current, on_model_selected)
    await interaction.response.send_message(embed=embed, view=view)


async def on_model_selected(interaction: discord.Interaction, selected_model: str):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    client.set_model(interaction.channel_id, selected_model)
    display_name = AVAILABLE_MODELS.get(selected_model, selected_model)
    await interaction.response.send_message(
        f"✅ Model switched to: **{display_name}** (`{selected_model}`) — backend: `{client.get_backend(interaction.channel_id)}`"
    )


@client.tree.command(name="model", description="Set or view active model for this channel")
@app_commands.describe(model_id="Model ID (gemini-3.8-flash-low, claude-sonnet-4-6, kilo/kilo-auto/free)")
async def cmd_model(interaction: discord.Interaction, model_id: Optional[str] = None):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    if not model_id:
        current = client.get_model(interaction.channel_id)
        view = ModelSelectView(current, on_model_selected)
        await interaction.response.send_message(
            f"Current active model: **`{current}`** (backend: `{client.get_backend(interaction.channel_id)}`)\n"
            "Select a new model below:",
            view=view,
        )
        return

    target_model = None
    lowered = model_id.lower().strip()
    if lowered in AVAILABLE_MODELS:
        target_model = lowered
    else:
        for mid in AVAILABLE_MODELS:
            if lowered in mid.lower():
                target_model = mid
                break

    if not target_model:
        await interaction.response.send_message(
            f"❌ Unknown model: `{model_id}`\nAvailable: {', '.join(f'`{m}`' for m in AVAILABLE_MODELS.keys())}",
            ephemeral=True,
        )
        return

    client.set_model(interaction.channel_id, target_model)
    display_name = AVAILABLE_MODELS.get(target_model, target_model)
    await interaction.response.send_message(
        f"✅ Active model updated to: **{display_name}** (`{target_model}`) — backend: `{client.get_backend(interaction.channel_id)}`"
    )


@client.tree.command(name="backend", description="Switch the agent backend of this channel")
@app_commands.describe(backend="antigravity (主) or kilo (無料フォールバック先)")
async def cmd_backend(interaction: discord.Interaction, backend: Optional[str] = None):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    if not backend:
        current_backend = client.get_backend(interaction.channel_id)
        status = client.router.backend_status()
        await interaction.response.send_message(
            f"現在のバックエンド: **`{current_backend}`** / モデル: **`{client.get_model(interaction.channel_id)}`**\n"
            f"• `antigravity`: {'✅ 利用可能' if status.get(ANTIGRAVITY_BACKEND) else '❌ 未検出'}\n"
            f"• `kilo`: {'✅ 利用可能' if status.get(KILO_BACKEND) else '❌ 未検出'}\n"
            "使用例: `/backend kilo`",
        )
        return

    key = backend.strip().lower()
    if key not in BACKEND_MODELS:
        await interaction.response.send_message(
            f"❌ Unknown backend: `{backend}` (antigravity / kilo が選択できます)", ephemeral=True
        )
        return

    client.set_backend(interaction.channel_id, key)
    await interaction.response.send_message(
        f"✅ バックエンドを **`{key}`** に切り替えました (モデル: `{client.get_model(interaction.channel_id)}`)"
    )


@client.tree.command(name="quota", description="Show the remaining rate limit / quota of the agent backends")
async def cmd_quota(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    await interaction.response.defer()
    snapshot = await client.quota_tracker.get(ANTIGRAVITY_BACKEND, WORKSPACE_DIR)
    client.monitor.record_quota(snapshot)
    estimated = client.quota_tracker.estimated_remaining_requests(ANTIGRAVITY_BACKEND)
    embed = build_quota_embed(snapshot, client.get_model(interaction.channel_id), estimated)
    await interaction.followup.send(embed=embed)


@client.tree.command(name="monitor", description="Show the monitor agent status (health, API, errors, rate limits)")
async def cmd_monitor(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    status = client.monitor.status()
    embed = discord.Embed(
        title="🩺 監視エージェント (Monitor Agent)",
        color=discord.Color.teal(),
        timestamp=datetime.now(),
    )
    embed.description = "\n".join(client.monitor.status_lines())
    backend_status = client.router.backend_status()
    embed.add_field(
        name="🤖 バックエンド可用性",
        value="\n".join(f"`{name}`: {'✅' if ok else '❌'}" for name, ok in backend_status.items()) or "—",
        inline=False,
    )
    if status["recent_alerts"]:
        embed.add_field(
            name="🔔 最新アラート",
            value="\n".join(
                f"• [{alert['level']}] {alert['kind']}: {alert['message']}" for alert in status["recent_alerts"]
            )[:1000],
            inline=False,
        )
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="new", description="Start a fresh conversation (reset conversation context)")
async def cmd_new(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    client.channel_conversations[interaction.channel_id] = None
    await interaction.response.send_message("🔄 Conversation reset. Next message will start a new session in `simulation/v3`.")


@client.tree.command(name="status", description="Show system performance metrics (CPU, RAM, GPU) and active tasks")
async def cmd_status(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    metrics = await asyncio.to_thread(get_system_metrics)
    active_count = len(client.active_processes)
    channel_active = len(client.channel_tasks.get(interaction.channel_id, set()))
    model = client.get_model(interaction.channel_id)
    conv_id = client.channel_conversations.get(interaction.channel_id)
    conv_label = conv_id[:8] if conv_id else "新規セッション"

    embed = discord.Embed(
        title="🖥️ システム稼働状況 (Host System Status)",
        color=discord.Color.blue(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="⚙️ CPU 使用率", value=f"`{metrics['cpu']}`", inline=True)
    embed.add_field(name="🧠 メモリ (RAM)", value=f"`{metrics['memory']}`", inline=True)
    embed.add_field(name="⚡ GPU 稼働状況", value=f"```{metrics['gpu']}```", inline=False)
    embed.add_field(
        name="🤖 エージェント稼働状況",
        value=(
            f"• 実行中タスク数: **{active_count}** 件 (このチャンネル: **{channel_active}** 件)\n"
            f"• バックエンド: **`{client.get_backend(interaction.channel_id)}`**\n"
            f"• アクティブモデル: **`{model}`**\n"
            f"• 会話コンテキスト: **`{conv_label}`**\n"
            f"• 作業ディレクトリ: `simulation/v3`"
        ),
        inline=False,
    )
    quota_snapshot = client.quota_tracker.cached(ANTIGRAVITY_BACKEND)
    if quota_snapshot is not None:
        estimated = client.quota_tracker.estimated_remaining_requests(ANTIGRAVITY_BACKEND)
        quota_text = "\n".join(quota_snapshot.summary_lines())
        if estimated is not None:
            quota_text += f"\n🎯 推定利用可能回数: **約 {estimated:.0f} 回**"
        embed.add_field(name="📊 残りレート制限 / クォータ", value=quota_text[:1000], inline=False)
    monitor_status = client.monitor.status()
    embed.add_field(
        name="🩺 監視エージェント",
        value=(
            f"• 成功 **{monitor_status['success_count']}** / エラー **{monitor_status['error_count']}**\n"
            f"• レート制限 **{monitor_status['rate_limit_count']}** / フォールバック **{monitor_status['fallback_count']}**\n"
            f"• 監視中: **{'はい' if monitor_status['running'] else 'いいえ'}**"
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed)


@client.tree.command(name="chrome", description="Launch Google Chrome on the host environment")
@app_commands.describe(url="URL to open in Chrome (default: http://localhost:5000)")
async def cmd_chrome(interaction: discord.Interaction, url: Optional[str] = "http://localhost:5000"):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    target_url = url.strip() if url else "http://localhost:5000"
    if not is_chrome_url_allowed(target_url):
        await interaction.response.send_message(
            "❌ URL がブロックリストに抵触するか、http/https ではありません。", ephemeral=True
        )
        return
    success, message = await asyncio.to_thread(launch_chrome_browser, target_url)
    if success:
        await interaction.response.send_message(f"🌐 {message}")
    else:
        await interaction.response.send_message(f"❌ {message}", ephemeral=True)


@client.tree.command(name="abort", description="Abort currently running agent tasks in this channel")
async def cmd_abort(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    aborted = client.abort_tasks(channel_id=interaction.channel_id)
    if aborted > 0:
        await interaction.response.send_message(f"🛑 {aborted} 件のアクティブタスクを停止しました。")
    else:
        await interaction.response.send_message("ℹ️ このチャンネルで実行中のタスクはありません。")


# ==========================================
# Execution Engine (Live Progress & Streaming)
# ==========================================

async def execute_agent_prompt(
    channel: discord.TextChannel | discord.DMChannel,
    prompt: str,
    user: discord.User,
    reply_to_msg: Optional[discord.Message] = None,
    interaction: Optional[discord.Interaction] = None,
    backend: Optional[str] = None,
    model: Optional[str] = None,
):
    """
    Runs the selected agent backend strictly in the simulation/v3 workspace in
    parallel, streams the live progress and posts the reply.

    The router takes care of the HTTP 429 backoff with the automatic
    ``continue`` and of the automatic fallback from Antigravity to the free
    Kilo Code model.
    """
    channel_id = channel.id
    task_id = f"{channel_id}_{int(time.time() * 1000)}"
    active_in_channel = len(client.channel_tasks.get(channel_id, set()))

    selected_backend = backend or client.get_backend(channel_id)
    selected_model = model or client.get_model(channel_id)
    stored_session = client.channel_conversations.get(channel_id)
    session_backend, session_id = decode_session(stored_session)
    if session_backend != selected_backend:
        session_id = None

    logger.info(
        f"Running Task {task_id} in {WORKSPACE_DIR} "
        f"(Backend: {selected_backend}, Model: {selected_model}, Session: {session_id}): {prompt[:60]}..."
    )

    status_embed = discord.Embed(
        title=f"🚀 {selected_backend} Agent Running",
        description=(
            f"**Task ID**: `{task_id[-8:]}` | **並列タスク**: {active_in_channel + 1}件目\n"
            f"**Backend**: `{selected_backend}`\n"
            f"**Model**: `{selected_model}`\n"
            f"**Workspace**: `simulation/v3`\n"
            f"**Prompt**: {prompt[:120]}{'...' if len(prompt) > 120 else ''}"
        ),
        color=discord.Color.gold(),
    )
    status_embed.add_field(name="Current Status", value="🧠 Initializing & Reasoning...", inline=False)
    status_embed.set_footer(text="React with 🛑 or type !abort to cancel")

    # If triggered by deferred slash command, send via followup so Discord's "thinking..." spinner clears!
    if interaction:
        status_msg = await interaction.followup.send(embed=status_embed, wait=True)
    else:
        status_msg = await channel.send(embed=status_embed)
    try:
        await status_msg.add_reaction("🛑")
    except Exception as exc:
        # Intentional guard: the abort reaction is a convenience, not a
        # requirement (the ``!abort`` command aborts the task as well), so a
        # failing reaction must not abort the whole agent run.  The type stays
        # broad on purpose -- a Discord REST call can fail with an HTTP
        # error, a transport error or a timeout, and letting any of them
        # propagate would change the behaviour of the run.  The cause is
        # recorded instead of being swallowed.
        logger.debug(
            "Could not add the abort reaction to the status message (%s: %s)",
            type(exc).__name__,
            exc,
        )

    current_status_line = "🧠 Thinking / Analyzing codebase..."
    recent_activity_log: List[str] = []
    last_edit_time = time.time()
    used_backend = selected_backend

    async def update_live_embed(force: bool = False):
        nonlocal last_edit_time
        now = time.time()
        if force or (now - last_edit_time >= 1.5):
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

                await status_msg.edit(embed=status_embed)
                last_edit_time = now
            except Exception as exc:
                # Intentional guard: the live status embed is a progress
                # indicator, not a result.  A failing edit (rate limit, a
                # deleted message, a transport error) must not break the agent
                # run, so the type stays broad on purpose and the cause is
                # recorded instead of being swallowed.
                logger.debug(
                    "Could not refresh the live status embed of task %s (%s: %s)",
                    task_id,
                    type(exc).__name__,
                    exc,
                )

    async def handle_event(event) -> None:
        nonlocal current_status_line, used_backend
        if event.kind == "status":
            current_status_line = event.text[:250]
            await update_live_embed()
        elif event.kind == "fallback":
            used_backend = KILO_BACKEND
            current_status_line = event.text[:250]
            recent_activity_log.append(event.text[:120])
            await update_live_embed(force=True)
        elif event.kind == "session":
            if event.session_id:
                client.channel_conversations[channel_id] = encode_session(used_backend, event.session_id)
                client.channel_session_backends[channel_id] = used_backend
        elif event.kind == "text":
            current_status_line = "✍️ Generating response..."
            await update_live_embed()
        elif event.kind == "error":
            recent_activity_log.append(f"⚠️ {event.text[:100]}")
            await update_live_embed()

    def _on_process(proc) -> None:
        """Register (or release) the running process so !abort can stop it."""
        if proc is None:
            client.unregister_task(channel_id, task_id)
        else:
            client.register_task(channel_id, task_id, proc)

    request = BridgeRequest(
        prompt=prompt,
        backend=selected_backend,
        model=selected_model,
        session_id=session_id,
        continue_session=False,
        timeout=TASK_TIMEOUT_SECONDS,
        on_process=_on_process,
    )

    outcome: Optional[BridgeOutcome] = None
    accumulated_text = ""
    try:
        outcome = await client.router.execute(request, on_event=handle_event)
        accumulated_text = outcome.text
    except asyncio.CancelledError:
        client.abort_tasks(task_id=task_id)
        await channel.send("🛑 Task was cancelled.")
        return
    except Exception as exc:
        err_id = make_error_id()
        logger.error(f"Error running the agent [error_id={err_id}]: {exc}", exc_info=True)
        accumulated_text = f"❌ Internal error (error_id={err_id}). 詳細はサーバーログを参照してください。"

    if outcome is not None:
        if outcome.session_id:
            client.channel_conversations[channel_id] = encode_session(outcome.backend, outcome.session_id)
            client.channel_session_backends[channel_id] = outcome.backend
        if not outcome.ok and not accumulated_text.strip():
            accumulated_text = (
                f"❌ `{outcome.backend}` が処理を完了できませんでした: {outcome.error or outcome.status}"
            )
        if outcome.fallback_used:
            fallback_note = (
                f"🔀 **Kilo Code へフォールバック**しました "
                f"(Antigravity: {outcome.fallback_reason}) — モデル: `{outcome.model}`"
            )
            recent_activity_log.append(fallback_note[:120])
            try:
                await channel.send(fallback_note)
            except Exception as exc:
                # Intentional guard: the fallback notice is an additional
                # message, not the result itself.  A failing send must not
                # discard the answer, so the type stays broad on purpose and
                # the cause is recorded instead of being swallowed.
                logger.warning(
                    "Could not post the fallback notice to channel %s (%s: %s)",
                    channel_id,
                    type(exc).__name__,
                    exc,
                )

    # Clean up status message
    try:
        await status_msg.delete()
    except Exception as exc:
        # Intentional best-effort cleanup: the status message is a progress
        # embed and may already be gone.  Deleting it must never prevent the
        # final answer from being posted, so the type stays broad on purpose
        # and the cause is recorded instead of being swallowed.
        logger.debug(
            "Could not delete the status message of task %s (%s: %s)",
            task_id,
            type(exc).__name__,
            exc,
        )

    # Post final output
    if not accumulated_text.strip():
        accumulated_text = "(Task completed. No text response was returned.)"

    current_conv_id = client.channel_conversations.get(channel_id)
    reply_view = SessionReplyView(conv_id=current_conv_id)

    # Discord 2000 character limit handling
    if len(accumulated_text) <= 1900:
        await channel.send(accumulated_text, view=reply_view)
    else:
        if len(accumulated_text) < 4000:
            chunks = [accumulated_text[i:i + 1900] for i in range(0, len(accumulated_text), 1900)]
            for idx, chunk in enumerate(chunks):
                # Attach the interactive reply button to the final chunk
                if idx == len(chunks) - 1:
                    await channel.send(chunk, view=reply_view)
                else:
                    await channel.send(chunk)
        else:
            file_data = io.BytesIO(accumulated_text.encode("utf-8"))
            discord_file = discord.File(file_data, filename="response.md")
            preview = accumulated_text[:1200] + "\n\n... (Full response attached below as response.md)"
            await channel.send(content=preview, file=discord_file, view=reply_view)


async def execute_agy_prompt(
    channel: discord.TextChannel | discord.DMChannel,
    prompt: str,
    user: discord.User,
    reply_to_msg: Optional[discord.Message] = None,
    interaction: Optional[discord.Interaction] = None,
):
    """Backwards compatible alias of :func:`execute_agent_prompt`."""
    await execute_agent_prompt(
        channel,
        prompt,
        user,
        reply_to_msg=reply_to_msg,
        interaction=interaction,
    )



# ==========================================
# Text Message & Prefix Commands Handler
# ==========================================

@client.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot:
        return

    content = message.content.strip()

    # Prefix commands (!chat, !chats, !resume, !model, !models, !new, !abort, !help)
    if content.startswith(("!", "/")):
        parts = content[1:].split(maxsplit=1)
        cmd_name = parts[0].lower() if parts else ""
        cmd_arg = parts[1].strip() if len(parts) > 1 else ""

        valid_cmds = (
            "chat", "chats", "history", "resume", "model", "m", "models", "new", "abort",
            "help", "status", "chrome", "quota", "monitor", "backend",
        )
        if cmd_name in valid_cmds:
            if not client.is_authorized(message.author.id, message.channel.id):
                await message.reply("❌ Unauthorized: You do not have permission to control Antigravity.")
                return
            if not rate_limit_check(message.author.id):
                await message.reply("⚠️ レート制限に達しました。しばらく経ってから再度お試しください。")
                return

            if cmd_name == "status":
                metrics = await asyncio.to_thread(get_system_metrics)
                active_count = len(client.active_processes)
                channel_active = len(client.channel_tasks.get(message.channel.id, set()))
                model = client.get_model(message.channel.id)
                conv_id = client.channel_conversations.get(message.channel.id)
                conv_label = conv_id[:8] if conv_id else "新規セッション"

                embed = discord.Embed(
                    title="🖥️ システム稼働状況 (Host System Status)",
                    color=discord.Color.blue(),
                    timestamp=datetime.now(),
                )
                embed.add_field(name="⚙️ CPU 使用率", value=f"`{metrics['cpu']}`", inline=True)
                embed.add_field(name="🧠 メモリ (RAM)", value=f"`{metrics['memory']}`", inline=True)
                embed.add_field(name="⚡ GPU 稼働状況", value=f"```{metrics['gpu']}```", inline=False)
                embed.add_field(
                    name="🤖 エージェント稼働状況",
                    value=(
                        f"• 実行中タスク数: **{active_count}** 件 (このチャンネル: **{channel_active}** 件)\n"
                        f"• バックエンド: **`{client.get_backend(message.channel.id)}`**\n"
                        f"• アクティブモデル: **`{model}`**\n"
                        f"• 会話コンテキスト: **`{conv_label}`**\n"
                        f"• 作業ディレクトリ: `simulation/v3`"
                    ),
                    inline=False,
                )
                quota_snapshot = client.quota_tracker.cached(ANTIGRAVITY_BACKEND)
                if quota_snapshot is not None:
                    estimated = client.quota_tracker.estimated_remaining_requests(ANTIGRAVITY_BACKEND)
                    quota_text = "\n".join(quota_snapshot.summary_lines())
                    if estimated is not None:
                        quota_text += f"\n🎯 推定利用可能回数: **約 {estimated:.0f} 回**"
                    embed.add_field(name="📊 残りレート制限 / クォータ", value=quota_text[:1000], inline=False)
                monitor_status = client.monitor.status()
                embed.add_field(
                    name="🩺 監視エージェント",
                    value=(
                        f"• 成功 **{monitor_status['success_count']}** / エラー **{monitor_status['error_count']}**\n"
                        f"• レート制限 **{monitor_status['rate_limit_count']}** / フォールバック **{monitor_status['fallback_count']}**"
                    ),
                    inline=False,
                )
                await message.reply(embed=embed)
                return

            elif cmd_name == "chrome":
                target_url = cmd_arg.strip() if cmd_arg else "http://localhost:5000"
                if not is_chrome_url_allowed(target_url):
                    await message.reply(
                        "❌ URL がブロックリストに抵触するか、http/https ではありません。"
                    )
                    return
                success, msg = await asyncio.to_thread(launch_chrome_browser, target_url)
                if success:
                    await message.reply(f"🌐 {msg}")
                else:
                    await message.reply(f"❌ {msg}")
                return

            elif cmd_name == "chat":
                if not cmd_arg:
                    await message.reply("⚠️ Please provide a prompt. Usage: `!chat <prompt>`")
                    return
                if len(cmd_arg) > MAX_PROMPT_LEN:
                    await message.reply(f"⚠️ プロンプトが長すぎます（{MAX_PROMPT_LEN} 文字まで）。")
                    return
                # Non-blocking parallel execution
                asyncio.create_task(execute_agy_prompt(message.channel, cmd_arg, message.author, reply_to_msg=message))
                return

            elif cmd_name in ("chats", "history"):
                agy_sessions = get_recent_conversations(10)
                kilo_sessions = get_recent_kilo_sessions(10)
                sessions = agy_sessions + kilo_sessions
                sessions.sort(key=lambda s: s["mtime"], reverse=True)
                current_conv = client.channel_conversations.get(message.channel.id)
                if not sessions:
                    await message.reply("ℹ️ No previous conversation history found in workspace.")
                    return
                embed = discord.Embed(
                    title="💬 Recent Conversations (Chat List)",
                    description=f"Current active session: **`{current_conv[:8] if current_conv else 'None (New)'}`**\nSelect below or type `!resume <id>` to switch.",
                    color=discord.Color.teal(),
                )
                for s in sessions:
                    stored = encode_session(s.get("backend", ANTIGRAVITY_BACKEND), s["id"])
                    is_active = (stored == current_conv or s["short_id"] == current_conv)
                    prefix = "👉 **[ACTIVE]** " if is_active else "• "
                    backend_tag = "🟦" if s.get("backend") == KILO_BACKEND else "🟧"
                    embed.add_field(
                        name=f"{prefix}{backend_tag} `{s['short_id']}` ({s['date_str']})",
                        value=s["prompt"] or "(No prompt)",
                        inline=False,
                    )
                view = ChatSelectView(sessions, current_conv, on_chat_selected)
                await message.reply(embed=embed, view=view)
                return

            elif cmd_name == "resume":
                if not cmd_arg:
                    await message.reply("⚠️ Please specify a session ID. Example: `!resume 8678b84b`")
                    return
                cid = cmd_arg.strip()
                agy_sessions = get_recent_conversations(30)
                kilo_sessions = get_recent_kilo_sessions(30)
                matched_id = cid
                matched_backend = ANTIGRAVITY_BACKEND
                for s in agy_sessions + kilo_sessions:
                    if s["id"].startswith(cid):
                        matched_id = s["id"]
                        matched_backend = s.get("backend", ANTIGRAVITY_BACKEND)
                        break
                client.channel_conversations[message.channel.id] = encode_session(matched_backend, matched_id)
                client.channel_session_backends[message.channel.id] = matched_backend
                await message.reply(f"✅ Resumed conversation **`{matched_id[:8]}`** (`{matched_id}`) on `{matched_backend}`. Next messages will continue this session.")
                return

            elif cmd_name in ("model", "m"):
                if not cmd_arg:
                    current = client.get_model(message.channel.id)
                    view = ModelSelectView(current, on_model_selected)
                    await message.reply(
                        f"Current active model: **`{current}`** (backend: `{client.get_backend(message.channel.id)}`)\n"
                        "Select a new model below:",
                        view=view,
                    )
                    return
                lowered = cmd_arg.lower().strip()
                target_model = lowered if lowered in AVAILABLE_MODELS else None
                if target_model is None:
                    for mid in AVAILABLE_MODELS:
                        if lowered in mid.lower():
                            target_model = mid
                            break
                if not target_model:
                    await message.reply(f"❌ Unknown model: `{cmd_arg}`\nAvailable: {', '.join(f'`{m}`' for m in AVAILABLE_MODELS.keys())}")
                    return
                client.set_model(message.channel.id, target_model)
                await message.reply(
                    f"✅ Active model updated to: **{AVAILABLE_MODELS[target_model]}** (`{target_model}`) "
                    f"— backend: `{client.get_backend(message.channel.id)}`"
                )
                return

            elif cmd_name == "models":
                current = client.get_model(message.channel.id)
                embed = discord.Embed(
                    title="🤖 利用可能モデル (Antigravity + Kilo Code)",
                    description=(
                        f"現在のバックエンド: **`{client.get_backend(message.channel.id)}`** / "
                        f"アクティブモデル: **`{current}`**\nWorkspace: `simulation/v3`\n"
                        "Kilo Code の既定は**無料モデル** (`kilo/kilo-auto/free`) です。"
                    ),
                    color=discord.Color.blue(),
                )
                for backend_name, catalog in BACKEND_MODELS.items():
                    lines = []
                    for mid, name in catalog.items():
                        prefix = "👉 " if mid == current else "• "
                        free_mark = " 🆓" if "free" in mid.split("/")[-1] else ""
                        lines.append(f"{prefix}{name}{free_mark} — `{mid}`")
                    embed.add_field(
                        name=f"{'🟧' if backend_name == ANTIGRAVITY_BACKEND else '🟦'} {backend_name}",
                        value="\n".join(lines)[:1000],
                        inline=False,
                    )
                await message.reply(embed=embed)
                return

            elif cmd_name == "backend":
                if not cmd_arg:
                    status = client.router.backend_status()
                    await message.reply(
                        f"現在のバックエンド: **`{client.get_backend(message.channel.id)}`** / "
                        f"モデル: **`{client.get_model(message.channel.id)}`**\n"
                        f"• `antigravity`: {'✅ 利用可能' if status.get(ANTIGRAVITY_BACKEND) else '❌ 未検出'}\n"
                        f"• `kilo`: {'✅ 利用可能' if status.get(KILO_BACKEND) else '❌ 未検出'}\n"
                        "使用例: `!backend kilo`"
                    )
                    return
                key = cmd_arg.strip().lower()
                if key not in BACKEND_MODELS:
                    await message.reply(f"❌ Unknown backend: `{cmd_arg}` (antigravity / kilo が選択できます)")
                    return
                client.set_backend(message.channel.id, key)
                await message.reply(
                    f"✅ バックエンドを **`{key}`** に切り替えました (モデル: `{client.get_model(message.channel.id)}`)"
                )
                return

            elif cmd_name == "quota":
                snapshot = await client.quota_tracker.get(ANTIGRAVITY_BACKEND, WORKSPACE_DIR)
                client.monitor.record_quota(snapshot)
                estimated = client.quota_tracker.estimated_remaining_requests(ANTIGRAVITY_BACKEND)
                embed = build_quota_embed(snapshot, client.get_model(message.channel.id), estimated)
                await message.reply(embed=embed)
                return

            elif cmd_name == "monitor":
                status = client.monitor.status()
                embed = discord.Embed(
                    title="🩺 監視エージェント (Monitor Agent)",
                    color=discord.Color.teal(),
                    timestamp=datetime.now(),
                )
                embed.description = "\n".join(client.monitor.status_lines())
                backend_status = client.router.backend_status()
                embed.add_field(
                    name="🤖 バックエンド可用性",
                    value="\n".join(f"`{name}`: {'✅' if ok else '❌'}" for name, ok in backend_status.items()) or "—",
                    inline=False,
                )
                if status["recent_alerts"]:
                    embed.add_field(
                        name="🔔 最新アラート",
                        value="\n".join(
                            f"• [{alert['level']}] {alert['kind']}: {alert['message']}"
                            for alert in status["recent_alerts"]
                        )[:1000],
                        inline=False,
                    )
                await message.reply(embed=embed)
                return

            elif cmd_name == "new":
                client.channel_conversations[message.channel.id] = None
                client.channel_session_backends.pop(message.channel.id, None)
                await message.reply("🔄 Conversation reset. Next message will start a fresh session in `simulation/v3`.")
                return

            elif cmd_name == "abort":
                aborted = client.abort_tasks(channel_id=message.channel.id)
                if aborted > 0:
                    await message.reply(f"🛑 {aborted} 件のアクティブタスクを停止しました。")
                else:
                    await message.reply("ℹ️ このチャンネルで実行中のタスクはありません。")
                return

            elif cmd_name == "help":
                await message.reply(
                    "**Agent Remote Bridge Commands (`simulation/v3`):**\n"
                    "• `!chat <prompt>`: Send prompt to agent (並列実行・429自動継続・自動フォールバック対応)\n"
                    "• `!status` (or `/status`): CPU・メモリ・GPU・タスク・クォータ・監視状況の表示\n"
                    "• `!quota` (or `/quota`): 残りのレート制限（利用可能回数・クォータ残量）の表示\n"
                    "• `!monitor` (or `/monitor`): 監視エージェントの稼働状況（API応答・エラー・レート制限）\n"
                    "• `!backend [antigravity|kilo]` (or `/backend`): 実行バックエンドの切替\n"
                    "• `!chrome [url]` (or `/chrome`): ローカル環境で Google Chrome を起動\n"
                    "• `!chats` (or `!history`): View recent conversation list & switch session\n"
                    "• `!resume <id>`: Resume previous session by ID (e.g. `!resume 8678b84b`)\n"
                    "• `!model` or `!m [name]`: Change or view model (Kilo Code は `kilo/kilo-auto/free`)\n"
                    "• `!models`: List all available models\n"
                    "• `!new`: Reset conversation context (start fresh)\n"
                    "• `!abort`: Terminate active tasks in this channel\n"
                    "• Natural Chat: DM or dedicated channel chat or `@Bot <prompt>`"
                )
                return

    # Natural conversation check:
    # 1. Direct message (DM) -> Always conversational without mention
    # 2. Dedicated channel configured in ALLOWED_CHANNEL_IDS -> Conversational without mention
    # 3. Mentioned by user -> Conversational
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_dedicated_channel = (len(ALLOWED_CHANNEL_IDS) > 0 and message.channel.id in ALLOWED_CHANNEL_IDS)
    is_mentioned = client.user in message.mentions

    if not (is_dm or is_dedicated_channel or is_mentioned):
        return

    if not client.is_authorized(message.author.id, message.channel.id):
        await message.reply("❌ Unauthorized: You do not have permission to control Antigravity.")
        return

    # Clean prompt
    prompt = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not prompt:
        current_model = client.get_model(message.channel.id)
        current_conv = client.channel_conversations.get(message.channel.id)
        conv_label = current_conv[:8] if current_conv else "New Session"
        await message.reply(
            f"👋 Agent Remote Bridge is ready in `simulation/v3`.\n"
            f"Backend: **`{client.get_backend(message.channel.id)}`** | "
            f"Active Model: **`{current_model}`** | Session: **`{conv_label}`**\n"
            f"Send me any task, or type `!help` / `!status` / `!quota` / `!monitor` / `!chats` to manage sessions."
        )
        return

    if len(prompt) > MAX_PROMPT_LEN:
        await message.reply(f"⚠️ プロンプトが長すぎます（{MAX_PROMPT_LEN} 文字まで）。")
        return

    # Non-blocking parallel execution
    asyncio.create_task(execute_agy_prompt(message.channel, prompt, message.author, reply_to_msg=message))


@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot:
        return
    if str(reaction.emoji) == "🛑":
        channel_id = reaction.message.channel.id
        if client.is_authorized(user.id, channel_id):
            aborted = client.abort_tasks(channel_id=channel_id)
            if aborted > 0:
                await reaction.message.channel.send(f"🛑 {aborted} 件のタスクが {user.mention} により停止されました。")


def main():
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("\n" + "=" * 60)
        print("ERROR: DISCORD_BOT_TOKEN is not set in .env file!")
        print("Please edit .env and configure DISCORD_BOT_TOKEN before running.")
        print("=" * 60 + "\n")
        sys.exit(1)

    if not acquire_instance_lock():
        print(
            f"ERROR: Another Discord Bridge instance is already holding the "
            f"instance lock on port {_INSTANCE_LOCK_PORT}. Refusing to start.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        print("Starting Discord Remote Bridge for Antigravity + Kilo Code (Workspace: simulation/v3)...")
        client.run(DISCORD_BOT_TOKEN)
    finally:
        release_instance_lock()


if __name__ == "__main__":
    main()
