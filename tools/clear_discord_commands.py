"""
Discord Slash Commands Cleaner
Connects to Discord and clears all global and guild-level slash commands for the bot,
then cleanly registers only the current Antigravity commands (or leaves it completely clean).

Usage:
  python tools/clear_discord_commands.py [--all-clean] [--guild-id GUILD_ID]
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "").strip()


async def run_cleaner(all_clean: bool, target_guild_id: int | None):
    if not DISCORD_BOT_TOKEN or DISCORD_BOT_TOKEN == "your_bot_token_here":
        print("\n[ERROR] DISCORD_BOT_TOKEN is not set in .env file.")
        print("Please configure DISCORD_BOT_TOKEN before running this cleaner.\n")
        sys.exit(1)

    intents = discord.Intents.default()
    client = discord.Client(intents=intents)
    tree = discord.app_commands.CommandTree(client)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user} (ID: {client.user.id})")
        print("Starting slash command cleanup...\n")

        # 1. Clear Guild-specific commands across all joined guilds
        guilds = client.guilds
        if target_guild_id:
            guilds = [g for g in guilds if g.id == target_guild_id]

        print(f"Found {len(guilds)} guild(s) to inspect.")
        for guild in guilds:
            try:
                # Fetch existing guild commands
                existing_guild_cmds = await tree.fetch_commands(guild=guild)
                print(f"  Guild '{guild.name}' ({guild.id}): {len(existing_guild_cmds)} command(s) found.")
                for cmd in existing_guild_cmds:
                    print(f"    - Removing guild command: /{cmd.name}")

                tree.clear_commands(guild=guild)
                await tree.sync(guild=guild)
                print(f"  ✅ Cleared and synced guild '{guild.name}'.")
            except Exception as e:
                print(f"  ⚠️ Error clearing guild '{guild.name}': {e}")

        # 2. Clear Global commands
        try:
            existing_global_cmds = await tree.fetch_commands()
            print(f"\nGlobal commands: {len(existing_global_cmds)} command(s) found.")
            for cmd in existing_global_cmds:
                print(f"  - Removing global command: /{cmd.name}")

            tree.clear_commands(guild=None)
            await tree.sync()
            print("✅ Global command tree cleared and synced.")
        except Exception as e:
            print(f"⚠️ Error clearing global commands: {e}")

        if not all_clean:
            print("\nNote: When you next start tools/discord_bridge.py, only its 4 commands (/model, /models, /new, /abort) will be registered cleanly.")
        else:
            print("\nAll slash commands for this bot have been completely removed.")

        await client.close()

    try:
        await client.start(DISCORD_BOT_TOKEN)
    except discord.errors.LoginFailure:
        print("\n[ERROR] Failed to login: Invalid Bot Token.")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] An unexpected error occurred: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Clear old Discord slash commands.")
    parser.add_argument(
        "--all-clean",
        action="store_true",
        help="Leave the tree completely empty without re-registering.",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="Specific Guild (Server) ID to clean. If omitted, cleans all joined guilds and global.",
    )
    args = parser.parse_args()

    asyncio.run(run_cleaner(args.all_clean, args.guild_id))


if __name__ == "__main__":
    main()
