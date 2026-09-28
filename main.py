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

# Bot Token
TOKEN = os.getenv("DISCORD_TOKEN")

# Supported command prefixes for traditional text-based commands
PREFIXES: List[str] = ["!mineria ", "!m ", "!"]

if TOKEN:
    token_source = "DISCORD_TOKEN_TEST" if os.getenv("DISCORD_TOKEN_TEST") else "DISCORD_TOKEN"
    logger.info(f"Bot token detected from environment ({token_source}).")
else:
    logger.warning("No valid bot token found in .env file (DISCORD_TOKEN / DISCORD_TOKEN_TEST).")


# ---------------------------------------------------------------------------
# Bot Subclass Definition
# ---------------------------------------------------------------------------
class MineriaBot(commands.Bot):
    """
    Custom Bot class for Mineria RPG.
    Configures minimal caching and memory optimizations for cloud-hosted environments.
    """

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
        """
        Asynchronous initialization hook called before the bot connects to Discord gateway.
        Loads all active Cogs and syncs global application slash commands.
        """
        # List of all modular Cogs to load
        extensions: List[str] = [
            "dice",          # Dice rolling engine (!roll, /roll)
            "help",          # Interactive help terminal (!help, /help)
            "log_handler",   # Execution logging and error telemetry
            "links",         # Official wiki and campaign resource links (!wiki)
            "traits",        # Character trait drawing & reroll system (!trait)
            "drawbacks",     # Random drawback generator (!drawback)
            "documents",     # Campaign document & tactical battlemap browser (!doc, !map)
            "utility",       # XP progression, roster analytics, GM stats (!kia, !mia, !d, !gm, !best)
            "error_handler", # Global command error dispatcher and user notices
            "admin",         # Command guard permissions, access control, and admin suite (!cmd, !all)
            "character"      # Unified character creation, distribution, and sheet management (!char)
        ]

        loaded_extensions: List[str] = []

        # Load each extension individually to isolate and report failures cleanly
        for ext in extensions:
            try:
                await self.load_extension(ext)
                loaded_extensions.append(ext)
            except Exception as exc:
                logger.critical(f"Failed to load extension '{ext}': {exc}", exc_info=True)

        if loaded_extensions:
            logger.info(f"Successfully loaded {len(loaded_extensions)} extensions: {', '.join(loaded_extensions)}")

        # Automatically synchronize slash application commands with Discord
        try:
            synced = await self.tree.sync()
            logger.info(f"Synchronized {len(synced)} application (slash) commands globally.")
        except Exception as exc:
            logger.warning(f"Application command synchronization failed during startup: {exc}")

        # Trigger garbage collection to reclaim startup loading and compilation memory
        gc.collect()

    async def on_ready(self) -> None:
        """Invoked when the bot successfully establishes connection and caches guilds."""
        if self.user:
            logger.info(f"Logged in as {self.user.name} (ID: {self.user.id}) | Active in {len(self.guilds)} servers.")


# ---------------------------------------------------------------------------
# Application Entry Point & Resilient Reconnection Loop
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not TOKEN:
        logger.critical("Startup aborted: Neither DISCORD_TOKEN nor DISCORD_TOKEN_TEST is set in .env.")
    else:
        retry_delay = 60
        max_retry_delay = 300

        # Resilient loop to survive transient network outages and Discord rate limits
        while True:
            try:
                bot = MineriaBot(PREFIXES)
                bot.run(TOKEN)
                logger.info("Bot execution finished cleanly.")
                break
            except discord.errors.HTTPException as err:
                # Handle Cloudflare / Discord gateway rate limiting (HTTP 429)
                if err.status == 429:
                    logger.warning(
                        f"Rate limited by Discord/Cloudflare (429 Too Many Requests). "
                        f"Retrying connection in {retry_delay} seconds..."
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)  # Exponential backoff
                else:
                    logger.critical(f"HTTP Exception encountered during startup: {err}")
                    time.sleep(30)
            except discord.errors.LoginFailure as err:
                # Handle authentication failures (e.g., token reset or invalid credential)
                logger.critical(f"Login failure detected (invalid token?): {err}")
                logger.info("Waiting 120 seconds before retrying login...")
                time.sleep(120)
            except Exception as err:
                logger.critical(f"Unexpected error encountered during runtime: {err}", exc_info=True)
                time.sleep(30)