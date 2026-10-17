"""
Discord Remote Bridge for Antigravity (AGY)
Allows full remote control of the local Antigravity agent from Discord.

Features:
- Streaming thought & tool execution status updates in Discord
- Dynamic model selection via slash commands (/model) or select menus
- Multi-turn conversation support (/new, /continue)
- Process cancellation / abort (/abort or 🛑 reaction)
- User / Channel access control
"""

import asyncio
import io
import json
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

import discord
from discord import app_commands
from dotenv import load_dotenv

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

WORKSPACE_DIR = Path(__file__).resolve().parent.parent

# Available Models in AGY
AVAILABLE_MODELS: Dict[str, str] = {
    "gemini-3.8-flash-low": "Gemini 3.8 Flash (Low - High Speed)",
    "gemini-3.8-flash-medium": "Gemini 3.8 Flash (Medium)",
    "gemini-3.8-flash-high": "Gemini 3.8 Flash (High)",
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (Thinking)",
    "claude-opus-4-6-thinking": "Claude Opus 4.6 (Thinking)",
    "gpt-oss-120b-medium": "GPT-OSS 120B (Medium)",
}


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


class DiscordBridgeClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

        # State tracking: channel_id -> current_model
        self.channel_models: Dict[int, str] = {}
        # channel_id -> conversation_id
        self.channel_conversations: Dict[int, Optional[str]] = {}
        # channel_id -> asyncio.subprocess.Process
        self.active_processes: Dict[int, asyncio.subprocess.Process] = {}

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


client = DiscordBridgeClient()


def check_auth(interaction: discord.Interaction) -> bool:
    if not client.is_authorized(interaction.user.id, interaction.channel_id):
        return False
    return True


@client.event
async def on_ready():
    logger.info(f"Discord Bridge Bot logged in as {client.user} (ID: {client.user.id})")
    logger.info(f"Working Directory: {WORKSPACE_DIR}")
    logger.info(f"Default Model: {DEFAULT_MODEL}")
    if ALLOWED_USER_IDS:
        logger.info(f"Authorized User IDs: {ALLOWED_USER_IDS}")
    else:
        logger.warning("No ALLOWED_USER_IDS set. Anyone in invited channels can control Antigravity!")


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

    if model_id not in AVAILABLE_MODELS:
        await interaction.response.send_message(
            f"❌ Unknown model: `{model_id}`\nAvailable: {', '.join(f'`{m}`' for m in AVAILABLE_MODELS.keys())}",
            ephemeral=True,
        )
        return

    client.set_model(interaction.channel_id, model_id)
    display_name = AVAILABLE_MODELS.get(model_id, model_id)
    await interaction.response.send_message(
        f"✅ Active model updated to: **{display_name}** (`{model_id}`)"
    )


@client.tree.command(name="new", description="Start a fresh conversation (reset conversation context)")
async def cmd_new(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    client.channel_conversations[interaction.channel_id] = None
    await interaction.response.send_message("🔄 Conversation reset. Next message will start a new session.")


@client.tree.command(name="abort", description="Abort currently running agent command")
async def cmd_abort(interaction: discord.Interaction):
    if not check_auth(interaction):
        await interaction.response.send_message("❌ Unauthorized.", ephemeral=True)
        return

    proc = client.active_processes.get(interaction.channel_id)
    if proc and proc.returncode is None:
        try:
            proc.kill()
            await interaction.response.send_message("🛑 Active Antigravity task has been terminated.")
        except Exception as e:
            await interaction.response.send_message(f"⚠️ Failed to terminate process: {e}")
    else:
        await interaction.response.send_message("ℹ️ No active task is currently running in this channel.")


async def execute_agy_prompt(
    channel: discord.TextChannel,
    prompt: str,
    user: discord.User,
    reply_to_msg: Optional[discord.Message] = None,
):
    """Runs agy CLI in background, streams progress, and posts response."""
    channel_id = channel.id

    if channel_id in client.active_processes and client.active_processes[channel_id].returncode is None:
        await channel.send("⚠️ Another task is already running in this channel. Use `/abort` to cancel it first.")
        return

    model = client.get_model(channel_id)
    conv_id = client.channel_conversations.get(channel_id)

    # Build command line
    cmd = ["agy", "-p", prompt, "--model", model, "--output-format", "stream-json", "--dangerously-skip-permissions"]
    if conv_id:
        cmd.extend(["--conversation", conv_id])

    logger.info(f"Running command: {' '.join(cmd[:6])}... [Model: {model}]")

    status_embed = discord.Embed(
        title="🚀 Running Antigravity Agent",
        description=f"**Model**: `{model}`\n**Prompt**: {prompt[:150]}{'...' if len(prompt) > 150 else ''}",
        color=discord.Color.gold(),
    )
    status_embed.set_footer(text="React 🛑 to abort")
    status_msg = await channel.send(embed=status_embed)
    await status_msg.add_reaction("🛑")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(WORKSPACE_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        client.active_processes[channel_id] = proc

        accumulated_text = ""
        current_step_info = "Initializing agent..."
        new_conv_id = conv_id
        last_edit_time = asyncio.get_event_loop().time()

        async def update_status_embed(text: str):
            nonlocal last_edit_time
            now = asyncio.get_event_loop().time()
            if now - last_edit_time > 1.5:  # Rate limit edits to prevent 429
                try:
                    status_embed.description = (
                        f"**Model**: `{model}`\n"
                        f"**Status**: {text}\n"
                        f"**Prompt**: {prompt[:100]}..."
                    )
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
                    if step_type == "tool_call":
                        tool_name = step.get("tool_name", "tool")
                        current_step_info = f"⚙️ Running tool: `{tool_name}`"
                        await update_status_embed(current_step_info)
                    elif step_type == "agent_response":
                        delta = step.get("text_delta", "")
                        accumulated_text += delta

                elif event == "result":
                    res = data.get("result", {})
                    resp_text = res.get("response")
                    if resp_text:
                        accumulated_text = resp_text

            except json.JSONDecodeError:
                # Regular text output if not JSON
                accumulated_text += line_str + "\n"

        stdout, stderr = await proc.communicate()
        if proc.returncode != 0 and not accumulated_text:
            err_msg = stderr.decode("utf-8", errors="replace").strip()
            accumulated_text = f"❌ Error (Exit Code {proc.returncode}):\n```\n{err_msg[:1800]}\n```"

    except asyncio.CancelledError:
        if proc and proc.returncode is None:
            proc.kill()
        await channel.send("🛑 Task was cancelled.")
        return
    except Exception as e:
        logger.error(f"Error running agy: {e}", exc_info=True)
        accumulated_text = f"❌ Internal Exception: {e}"
    finally:
        client.active_processes.pop(channel_id, None)

    # Delete or finalize status message
    try:
        await status_msg.delete()
    except Exception:
        pass

    # Post final output
    if not accumulated_text.strip():
        accumulated_text = "(Done. No output generated.)"

    # Discord 2000 character limit handling
    if len(accumulated_text) <= 1900:
        await channel.send(accumulated_text)
    else:
        # Send chunks or file
        if len(accumulated_text) < 4000:
            chunks = [accumulated_text[i:i + 1900] for i in range(0, len(accumulated_text), 1900)]
            for chunk in chunks:
                await channel.send(chunk)
        else:
            # Send preview + full text file
            file_data = io.BytesIO(accumulated_text.encode("utf-8"))
            discord_file = discord.File(file_data, filename="response.md")
            preview = accumulated_text[:1200] + "\n\n... (Full response attached as file)"
            await channel.send(content=preview, file=discord_file)


@client.event
async def on_message(message: discord.Message):
    # Ignore bot messages
    if message.author.bot:
        return

    # Check mention or DM
    is_mentioned = client.user in message.mentions
    is_dm = isinstance(message.channel, discord.DMChannel)

    if not (is_mentioned or is_dm):
        return

    if not client.is_authorized(message.author.id, message.channel.id):
        await message.reply("❌ Unauthorized: You do not have permission to control Antigravity.")
        return

    # Clean prompt (remove bot mention)
    prompt = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not prompt:
        current_model = client.get_model(message.channel.id)
        await message.reply(
            f"👋 Antigravity Remote Bridge is ready.\n"
            f"Active Model: **`{current_model}`**\n"
            f"Send me any task or instruction, or use `/model` to change models."
        )
        return

    async with message.channel.typing():
        await execute_agy_prompt(message.channel, prompt, message.author, reply_to_msg=message)


@client.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    if user.bot:
        return
    if str(reaction.emoji) == "🛑":
        channel_id = reaction.message.channel.id
        proc = client.active_processes.get(channel_id)
        if proc and proc.returncode is None:
            if client.is_authorized(user.id, channel_id):
                proc.kill()
                await reaction.message.channel.send(f"🛑 Task aborted by {user.mention}.")


def main():
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("\n" + "=" * 60)
        print("ERROR: DISCORD_BOT_TOKEN is not set in .env file!")
        print("Please edit .env and configure DISCORD_BOT_TOKEN before running.")
        print("=" * 60 + "\n")
        sys.exit(1)

    print("Starting Discord Remote Bridge for Antigravity...")
    client.run(DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
