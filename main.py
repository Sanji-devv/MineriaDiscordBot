import os
import sys
import gc
from pathlib import Path
from typing import List
import discord
from discord.ext import commands
from dotenv import load_dotenv
from log_handler import logger

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(Path(__file__).parent / ".env")

TOKEN = os.getenv("DISCORD_TOKEN_TEST")
PREFIXES: List[str] = ["!mineria ", "!m ", "!"]

class MineriaBot(commands.Bot):

    def __init__(self, command_prefix: List[str]):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True

        super().__init__(
            command_prefix=command_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            max_messages=50,
            member_cache_flags=discord.MemberCacheFlags.from_intents(intents)
        )

    async def setup_hook(self) -> None:
        extensions: List[str] = [
            "dice",
            "help",
            "log_handler",
            "links",
            "traits",
            "drawbacks",
            "documents",
            "utility",
            "error_handler",
            "admin",
            "character",
        ]

        loaded_extensions: List[str] = []

        for ext in extensions:
            try:
                await self.load_extension(ext)
                loaded_extensions.append(ext)
            except Exception as exc:
                logger.error(f"Error loading extension '{ext}': {exc}")

        if loaded_extensions:
            logger.info(f"Loaded {len(loaded_extensions)} extensions: {', '.join(loaded_extensions)}")

        # Only sync globally if application_id is established
        if self.application_id:
            try:
                synced = await self.tree.sync()
                logger.info(f"Synchronized {len(synced)} application (slash) commands globally.")
            except Exception as exc:
                logger.error(f"Error syncing application commands: {exc}")

        gc.collect()

    async def close(self) -> None:
        # Gracefully unload all cogs/extensions so persistent tasks and network sessions shut down
        for ext in list(self.extensions.keys()):
            try:
                await self.unload_extension(ext)
            except Exception as exc:
                logger.debug(f"Error unloading extension '{ext}' on shutdown: {exc}")
        await super().close()

    async def on_ready(self) -> None:
        # If application_id was set upon login, perform synchronization if not already done
        if not self.application_id and self.user:
            try:
                synced = await self.tree.sync()
                logger.info(f"Synchronized {len(synced)} application commands globally in on_ready.")
            except Exception as exc:
                logger.error(f"Error syncing application commands in on_ready: {exc}")

        if self.user:
            logger.info(f"Logged in as {self.user.name} (ID: {self.user.id}) | Active in {len(self.guilds)} servers.")

if __name__ == "__main__":
    if not TOKEN:
        logger.error("Error: Bot token not found in environment.")
        sys.exit(1)

    try:
        bot = MineriaBot(PREFIXES)
        bot.run(TOKEN)
    except Exception as err:
        logger.error(f"Error during bot execution: {err}")