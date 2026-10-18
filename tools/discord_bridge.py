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
- Process cancellation / abort (!abort, /abort, or 🛑 reaction)
- User / Channel access control
"""

import asyncio
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import discord
from discord import app_commands
from dotenv import load_dotenv

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False
    psutil = None

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

ALLOWED_USER_IDS: Set[int] = {
    int(u.strip()) for u in ALLOWED_USERS_RAW.split(",") if u.strip().isdigit()
}
ALLOWED_CHANNEL_IDS: Set[int] = {
    int(c.strip()) for c in ALLOWED_CHANNELS_RAW.split(",") if c.strip().isdigit()
}

# Strictly lock workspace to simulation/v3
WORKSPACE_DIR = Path(__file__).resolve().parent.parent

# Brain directories to discover conversation sessions
CLI_BRAIN_DIR = Path(os.environ.get("USERPROFILE", "")) / ".gemini" / "antigravity-cli" / "brain"
APP_BRAIN_DIR = Path(os.environ.get("USERPROFILE", "")) / ".gemini" / "antigravity" / "brain"

# Available Models in AGY
AVAILABLE_MODELS: Dict[str, str] = {
    "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low - High Speed)",
    "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
    "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}


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

    # Query GPU via nvidia-smi if available
    smi = shutil.which("nvidia-smi")
    if smi:
        try:
            res = subprocess.run(
                [smi, "--query-gpu=name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
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
                            gpus.append(f"{name} | 使用率: {util}% | VRAM: {m_used_f:.1f}GB / {m_tot_f:.1f}GB")
                        except ValueError:
                            gpus.append(f"{name} | 使用率: {util}% | VRAM: {m_used}MB / {m_tot}MB")
                if gpus:
                    metrics["gpu"] = "\n".join(gpus)
        except Exception as e:
            logger.warning(f"nvidia-smi execution error: {e}")
            metrics["gpu"] = f"GPU取得エラー: {e}"

    return metrics


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
            except Exception:
                pass

            sessions.append({
                "id": item.name,
                "short_id": item.name[:8],
                "mtime": mtime,
                "date_str": datetime.fromtimestamp(mtime).strftime("%m/%d %H:%M"),
                "prompt": first_prompt[:70] + ("..." if len(first_prompt) > 70 else ""),
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
            is_active = (s["id"] == current_conv or s["short_id"] == current_conv)
            label = f"{s['date_str']} - {s['prompt']}"[:95]
            options.append(
                discord.SelectOption(
                    label=label,
                    value=s["id"],
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
        # task_id (str) -> asyncio.subprocess.Process
        self.active_processes: Dict[str, asyncio.subprocess.Process] = {}
        # channel_id -> Set[str] of task_ids
        self.channel_tasks: Dict[int, Set[str]] = {}

    async def setup_hook(self):
        await self.tree.sync()
        logger.info("Application slash commands synced.")

    def is_authorized(self, user_id: int, channel_id: int) -> bool:
        """Check if user and channel are allowed to issue commands."""
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            return False
        if ALLOWED_CHANNEL_IDS and channel_id not in ALLOWED_CHANNEL_IDS:
            return False
        return True

    def get_model(self, channel_id: int) -> str:
        return self.channel_models.get(channel_id, DEFAULT_MODEL)

    def set_model(self, channel_id: int, model: str):
        self.channel_models[channel_id] = model

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

    def abort_tasks(self, channel_id: Optional[int] = None, task_id: Optional[str] = None) -> int:
        """Abort tasks either by specific task_id or for an entire channel."""
        aborted = 0
        if task_id and task_id in self.active_processes:
            p = self.active_processes[task_id]
            if p.returncode is None:
                try:
                    p.kill()
                    aborted += 1
                except Exception:
                    pass
            return aborted

        target_tasks = self.channel_tasks.get(channel_id, set()).copy() if channel_id else set(self.active_processes.keys())
        for tid in target_tasks:
            p = self.active_processes.get(tid)
            if p and p.returncode is None:
                try:
                    p.kill()
                    aborted += 1
                except Exception:
                    pass
        return aborted


client = DiscordBridgeClient()


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
    logger.info(f"System CPU: {metrics['cpu']} | Memory: {metrics['memory']}")
    logger.info(f"System GPU:\n{metrics['gpu']}")
    if ALLOWED_USER_IDS:
        logger.info(f"Authorized User IDs: {ALLOWED_USER_IDS}")
    else:
        logger.warning("No ALLOWED_USER_IDS set. Anyone in invited channels can control Antigravity!")

    # Optionally notify startup message with system metrics to configured channels
    embed = discord.Embed(
        title="🚀 Antigravity Discord Bridge 起動完了",
        description=f"Antigravity Remote Bridge が起動しました。\n並列チャット処理・Chrome起動・システム監視が有効です。",
        color=discord.Color.green(),
        timestamp=datetime.now(),
    )
    embed.add_field(name="⚙️ CPU 使用率", value=f"`{metrics['cpu']}`", inline=True)
    embed.add_field(name="🧠 メモリ (RAM)", value=f"`{metrics['memory']}`", inline=True)
    embed.add_field(name="⚡ GPU 稼働状況", value=f"```{metrics['gpu']}```", inline=False)
    embed.add_field(
        name="ℹ️ 設定情報",
        value=f"• モデル: `{DEFAULT_MODEL}`\n• ワークスペース: `simulation/v3`\n• コマンド一覧: `!help` / `/status` / `/chrome` / `/chat`",
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


@client.tree.command(name="chats", description="List recent conversation sessions and resume them")
async def cmd_chats(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    # Defer immediately to prevent 404 Unknown interaction on disk scan
    await interaction.response.defer()

    # Offload disk search to thread to avoid blocking asyncio event loop
    sessions = await asyncio.to_thread(get_recent_conversations, 10)
    current_conv = client.channel_conversations.get(interaction.channel_id)

    if not sessions:
        await interaction.followup.send("ℹ️ No previous conversation history found in workspace.", ephemeral=True)
        return

    embed = discord.Embed(
        title="💬 Recent Conversations (Chat List)",
        description=f"Current active session: **`{current_conv[:8] if current_conv else 'None (New)'}`**\nSelect from dropdown or use `/resume <id>` to switch.",
        color=discord.Color.teal(),
    )
    for s in sessions:
        prefix = "👉 **[ACTIVE]** " if (s["id"] == current_conv or s["short_id"] == current_conv) else "• "
        embed.add_field(
            name=f"{prefix}`{s['short_id']}` ({s['date_str']})",
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
    sessions = await asyncio.to_thread(get_recent_conversations, 30)
    matched_full_id = cid
    for s in sessions:
        if s["id"].startswith(cid):
            matched_full_id = s["id"]
            break

    client.channel_conversations[interaction.channel_id] = matched_full_id
    await interaction.followup.send(
        f"✅ Resumed conversation **`{matched_full_id[:8]}`** (`{matched_full_id}`)."
    )


@client.tree.command(name="models", description="List all available Antigravity models")
async def cmd_models(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized: You do not have permission to run commands.", ephemeral=True)
        return

    current = client.get_model(interaction.channel_id)
    embed = discord.Embed(
        title="🤖 Available Antigravity Models",
        description=f"Current active model in this channel: **`{current}`**",
        color=discord.Color.blue(),
    )
    for model_id, name in AVAILABLE_MODELS.items():
        prefix = "👉 " if model_id == current else "• "
        embed.add_field(name=f"{prefix}{name}", value=f"`{model_id}`", inline=False)

    view = ModelSelectView(current, on_model_selected)
    await interaction.response.send_message(embed=embed, view=view)


async def on_model_selected(interaction: discord.Interaction, selected_model: str):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return
    client.set_model(interaction.channel_id, selected_model)
    display_name = AVAILABLE_MODELS.get(selected_model, selected_model)
    await interaction.response.send_message(
        f"✅ Model switched to: **{display_name}** (`{selected_model}`)"
    )


@client.tree.command(name="model", description="Set or view active model for this channel")
@app_commands.describe(model_id="Model ID (e.g. gemini-3.8-flash-low, claude-sonnet-4-6)")
async def cmd_model(interaction: discord.Interaction, model_id: Optional[str] = None):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    if not model_id:
        current = client.get_model(interaction.channel_id)
        view = ModelSelectView(current, on_model_selected)
        await interaction.response.send_message(
            f"Current active model: **`{current}`**\nSelect a new model below:",
            view=view,
        )
        return

    target_model = None
    for mid in AVAILABLE_MODELS:
        if model_id.lower() in mid.lower():
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
        f"✅ Active model updated to: **{display_name}** (`{target_model}`)"
    )


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
        name="🤖 Antigravity 稼働状況",
        value=(
            f"• 実行中タスク数: **{active_count}** 件 (このチャンネル: **{channel_active}** 件)\n"
            f"• アクティブモデル: **`{model}`**\n"
            f"• 会話コンテキスト: **`{conv_label}`**\n"
            f"• 作業ディレクトリ: `simulation/v3`"
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

async def execute_agy_prompt(
    channel: discord.TextChannel | discord.DMChannel,
    prompt: str,
    user: discord.User,
    reply_to_msg: Optional[discord.Message] = None,
    interaction: Optional[discord.Interaction] = None,
):
    """
    Runs agy CLI strictly in simulation/v3 workspace in parallel,
    streams live progress (thinking, subagents, tools), and posts reply.
    Supports concurrent executions across and within channels without blocking.
    """
    channel_id = channel.id
    task_id = f"{channel_id}_{int(time.time() * 1000)}"
    active_in_channel = len(client.channel_tasks.get(channel_id, set()))

    model = client.get_model(channel_id)
    conv_id = client.channel_conversations.get(channel_id)

    # Command construction: locked to WORKSPACE_DIR
    cmd = [
        "agy",
        "-p", prompt,
        "--model", model,
        "--output-format", "stream-json",
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
    except Exception:
        pass

    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(WORKSPACE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        client.register_task(channel_id, task_id, proc)

        accumulated_text = ""
        current_status_line = "🧠 Thinking / Analyzing codebase..."
        recent_activity_log: List[str] = []
        last_edit_time = time.time()

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
                except Exception:
                    pass

        while True:
            line = await proc.stdout.readline()
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
                        client.channel_conversations[channel_id] = new_conv_id

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
                            current_status_line = f"👥 Subagent running: **{sub_types}**"
                            recent_activity_log.append(f"👥 Subagent `{sub_types}` active")
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
                            recent_activity_log.append(f"✅ {current_status_line}")
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

        stdout, stderr = await proc.communicate()
        if proc.returncode != 0 and not accumulated_text.strip():
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            accumulated_text = f"❌ Process exited with code {proc.returncode}:\n```\n{err_msg[:1800]}\n```"

    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.kill()
        await channel.send("🛑 Task was cancelled.")
        return
    except Exception as e:
        logger.error(f"Error running agy: {e}", exc_info=True)
        accumulated_text = f"❌ Internal Exception: {e}"
    finally:
        client.unregister_task(channel_id, task_id)

    # Clean up status message
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Post final output
    if not accumulated_text.strip():
        accumulated_text = "(Task completed. No text response was returned.)"

    # Discord 2000 character limit handling
    if len(accumulated_text) <= 1900:
        await channel.send(accumulated_text)
    else:
        if len(accumulated_text) < 4000:
            chunks = [accumulated_text[i:i + 1900] for i in range(0, len(accumulated_text), 1900)]
            for chunk in chunks:
                await channel.send(chunk)
        else:
            file_data = io.BytesIO(accumulated_text.encode("utf-8"))
            discord_file = discord.File(file_data, filename="response.md")
            preview = accumulated_text[:1200] + "\n\n... (Full response attached below as response.md)"
            await channel.send(content=preview, file=discord_file)


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

        valid_cmds = ("chat", "chats", "history", "resume", "model", "m", "models", "new", "abort", "help", "status", "chrome")
        if cmd_name in valid_cmds:
            if not client.is_authorized(message.author.id, message.channel.id):
                await message.reply("❌ Unauthorized: You do not have permission to control Antigravity.")
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
                    name="🤖 Antigravity 稼働状況",
                    value=(
                        f"• 実行中タスク数: **{active_count}** 件 (このチャンネル: **{channel_active}** 件)\n"
                        f"• アクティブモデル: **`{model}`**\n"
                        f"• 会話コンテキスト: **`{conv_label}`**\n"
                        f"• 作業ディレクトリ: `simulation/v3`"
                    ),
                    inline=False,
                )
                await message.reply(embed=embed)
                return

            elif cmd_name == "chrome":
                target_url = cmd_arg.strip() if cmd_arg else "http://localhost:5000"
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
                # Non-blocking parallel execution
                asyncio.create_task(execute_agy_prompt(message.channel, cmd_arg, message.author, reply_to_msg=message))
                return

            elif cmd_name in ("chats", "history"):
                sessions = get_recent_conversations(10)
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
                    prefix = "👉 **[ACTIVE]** " if (s["id"] == current_conv or s["short_id"] == current_conv) else "• "
                    embed.add_field(
                        name=f"{prefix}`{s['short_id']}` ({s['date_str']})",
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
                sessions = get_recent_conversations(30)
                matched_id = cid
                for s in sessions:
                    if s["id"].startswith(cid):
                        matched_id = s["id"]
                        break
                client.channel_conversations[message.channel.id] = matched_id
                await message.reply(f"✅ Resumed conversation **`{matched_id[:8]}`** (`{matched_id}`). Next messages will continue this session.")
                return

            elif cmd_name in ("model", "m"):
                if not cmd_arg:
                    current = client.get_model(message.channel.id)
                    view = ModelSelectView(current, on_model_selected)
                    await message.reply(f"Current active model: **`{current}`**\nSelect a new model below:", view=view)
                    return
                target_model = None
                for mid in AVAILABLE_MODELS:
                    if cmd_arg.lower() in mid.lower():
                        target_model = mid
                        break
                if not target_model:
                    await message.reply(f"❌ Unknown model: `{cmd_arg}`\nAvailable: {', '.join(f'`{m}`' for m in AVAILABLE_MODELS.keys())}")
                    return
                client.set_model(message.channel.id, target_model)
                await message.reply(f"✅ Active model updated to: **{AVAILABLE_MODELS[target_model]}** (`{target_model}`)")
                return

            elif cmd_name == "models":
                current = client.get_model(message.channel.id)
                embed = discord.Embed(
                    title="🤖 Available Antigravity Models",
                    description=f"Current active model in this channel: **`{current}`**\nWorkspace: `simulation/v3`",
                    color=discord.Color.blue(),
                )
                for mid, name in AVAILABLE_MODELS.items():
                    prefix = "👉 " if mid == current else "• "
                    embed.add_field(name=f"{prefix}{name}", value=f"`{mid}`", inline=False)
                await message.reply(embed=embed)
                return

            elif cmd_name == "new":
                client.channel_conversations[message.channel.id] = None
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
                    "**Antigravity Remote Bridge Commands (`simulation/v3`):**\n"
                    "• `!chat <prompt>`: Send prompt to agent (並列実行対応)\n"
                    "• `!status` (or `/status`): CPU・メモリ・GPU・タスク稼働状況の表示\n"
                    "• `!chrome [url]` (or `/chrome`): ローカル環境で Google Chrome を起動\n"
                    "• `!chats` (or `!history`): View recent conversation list & switch session\n"
                    "• `!resume <id>`: Resume previous session by ID (e.g. `!resume 8678b84b`)\n"
                    "• `!model` or `!m [name]`: Change or view model\n"
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
            f"👋 Antigravity Remote Bridge is ready in `simulation/v3`.\n"
            f"Active Model: **`{current_model}`** | Session: **`{conv_label}`**\n"
            f"Send me any task, or type `!help` / `!status` / `!chrome` / `!chats` to manage sessions."
        )
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

    print("Starting Discord Remote Bridge for Antigravity (Workspace: simulation/v3)...")
    client.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
