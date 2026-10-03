import os
import sys
import gc
import time
from pathlib import Path
from typing import List
import discord
from discord.ext import commands
from dotenv import load_dotenv
from log_handler import logger

# Ensure console standard streams handle UTF-8 without charmap errors on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Environment & Configuration Setup
# ---------------------------------------------------------------------------
# Load environment variables from the project root .env file
load_dotenv(Path(__file__).parent / ".env")

# Bot Token & Command Prefixes
TOKEN = os.getenv("DISCORD_TOKEN_TEST") or os.getenv("DISCORD_TOKEN")
PREFIXES: List[str] = ["!mineria ", "!m ", "!"]

# ---------------------------------------------------------------------------
# Bot Subclass Definition
# ---------------------------------------------------------------------------
class MineriaBot(commands.Bot):

    def __init__(self, command_prefix: List[str]):
        # Configure required gateway intents (Message Content & Server Members)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=command_prefix,
            intents=intents,
            help_command=None,  # Custom help menu handled by help.py Cog
            case_insensitive=True,
            max_messages=50,    # Cap message cache to minimize RAM consumption
            member_cache_flags=discord.MemberCacheFlags.from_intents(intents)
        )

    async def setup_hook(self) -> None:
        # List of all modular Cogs to load
        extensions: List[str] = [
            "dice",          # Dice rolling engine (!roll, /roll)
            "help",          # Interactive help terminal (!help, /help)
            "log_handler",   # Execution logging and error telemetry
            "links",         # Official wiki and campaign resource links (!wiki)
            "traits",        # Character trait drawing & reroll system (!trait)
            "drawbacks",     # Random drawback generator (!drawback)
            "documents",     # Campaign document & tactical battlemap browser (!doc, !map)
            "utility",       # XP progression and GM stats (!kia, !mia, !gm)
            "error_handler", # Global command error dispatcher and user notices
            "admin",         # Command guard permissions and admin suite (!admin)
            "character",     # Unified character creation, distribution, and sheet management (!char)
            "stat_analytics" # Distribution roll statistics, analytics, and matplotlib charts (!stats)
        ]

        loaded_extensions: List[str] = []

        # Load each extension individually to isolate and report failures cleanly
        for ext in extensions:
            try:
                await self.load_extension(ext)
                loaded_extensions.append(ext)
            except Exception as exc:
                logger.error(f"Error loading extension '{ext}': {exc}")

        if loaded_extensions:
            logger.info(f"Loaded {len(loaded_extensions)} extensions: {', '.join(loaded_extensions)}")

        # Automatically synchronize slash application commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"Synchronized {len(synced)} application (slash) commands globally.")
        except Exception as exc:
            logger.error(f"Error syncing application commands: {exc}")

        # Trigger garbage collection to reclaim startup loading and compilation memory
        gc.collect()

    async def on_ready(self) -> None:
        if self.user:
            logger.info(f"Logged in as {self.user.name} (ID: {self.user.id}) | Active in {len(self.guilds)} servers.")


# ---------------------------------------------------------------------------
# Application Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        logger.error("Error: Bot token not found in environment.")
        sys.exit(1)

    try:
        bot = MineriaBot(PREFIXES)
        bot.run(TOKEN)
    except Exception as err:
        logger.error(f"Error during bot execution: {err}")

