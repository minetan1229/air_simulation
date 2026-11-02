"""
Discord Slash Commands Cleaner
Connects to Discord and clears all global and guild-level slash commands for the bot,
then cleanly registers only the current Antigravity commands (or leaves it completely clean).

Usage:
  python tools/clear_discord_commands.py [--all-clean] [--guild-id GUILD_ID] [--dry-run] [--yes]
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


async def run_cleaner(all_clean: bool, target_guild_id: int | None, dry_run: bool, yes_confirm: bool):
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

        # 1. Guild-specific commands across all joined guilds
        guilds = client.guilds
        if target_guild_id:
            guilds = [g for g in guilds if g.id == target_guild_id]

        print(f"Found {len(guilds)} guild(s) to inspect.")
        for guild in guilds:
            try:
                existing_guild_cmds = await tree.fetch_commands(guild=guild)
                print(f"  Guild '{guild.name}' ({guild.id}): {len(existing_guild_cmds)} command(s) found.")
                for cmd in existing_guild_cmds:
                    print(f"    - {'[dry-run] ' if dry_run else ''}Removing guild command: /{cmd.name}")

                if not dry_run and all_clean and yes_confirm:
                    tree.clear_commands(guild=guild)
                    await tree.sync(guild=guild)
                    print(f"  ✅ Cleared and synced guild '{guild.name}'.")
                elif dry_run:
                    print(f"  [dry-run] Guild '{guild.name}' のコマンド削除はシミュレーションのみ（API 書き込みなし）")
                else:
                    print(f"  [スキップ] Guild '{guild.name}' は --all-clean --yes 未指定のため削除しません。")
            except Exception as e:
                print(f"  ⚠️ Error clearing guild '{guild.name}': {e}")

        # 2. Clear Global commands
        try:
            existing_global_cmds = await tree.fetch_commands()
            print(f"\nGlobal commands: {len(existing_global_cmds)} command(s) found.")
            for cmd in existing_global_cmds:
                print(f"  - {'[dry-run] ' if dry_run else ''}Removing global command: /{cmd.name}")

            if not dry_run and all_clean and yes_confirm:
                tree.clear_commands(guild=None)
                await tree.sync()
                print("✅ Global command tree cleared and synced.")
            elif dry_run:
                print("[dry-run] グローバルコマンドの削除はシミュレーションのみ（API 書き込みなし）")
            else:
                print("[スキップ] グローバルコマンドは --all-clean --yes 未指定のため削除しません。")
        except Exception as e:
            print(f"⚠️ Error clearing global commands: {e}")

        if all_clean and yes_confirm and not dry_run:
            print("\nAll slash commands for this bot have been completely removed.")
        elif dry_run:
            print("\n[dry-run] 削除対象の一覧表示のみ完了。実際の削除は行われていません。")
        else:
            print("\n[安全側] --all-clean --yes 未指定のため、コマンドは削除されませんでした。")
            print("次回 tools/discord_bridge.py 起動時、4 コマンド (/model, /models, /new, /abort) がクリーンに登録されます。")

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
        help="全コマンドを実際に削除する（未指定時は削除せず一覧表示のみ）。",
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="Specific Guild (Server) ID to clean. If omitted, cleans all joined guilds and global.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="削除対象を一覧表示するのみで、Discord API への書き込みを行わない。",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="--all-clean と併用して、実際のコマンド削除を実行する確認をスキップする。",
    )
    args = parser.parse_args()

    if args.yes and not args.all_clean:
        print("[注意] --yes は --all-clean と併用時のみ有効です。", file=sys.stderr)

    asyncio.run(run_cleaner(args.all_clean, args.guild_id, args.dry_run, args.yes))


if __name__ == "__main__":
    main()
