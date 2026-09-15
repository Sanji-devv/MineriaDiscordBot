import os
import asyncio
import discord
from discord.ext import commands
from pathlib import Path
from dotenv import load_dotenv
from log_handler import logger

load_dotenv(Path(__file__).parent / ".env")

# Token and Prefix Logic
TOKEN = os.getenv("DISCORD_TOKEN_TEST")
PREFIXES = ["!mineria ", "!m ", "!"]

if TOKEN:
    logger.info("Using Production Token")
else:
    logger.warning("No DISCORD_TOKEN found in environment variables")

class MineriaBot(commands.AutoShardedBot):
    def __init__(self, command_prefix):
        intents = discord.Intents.default()
        intents.message_content = intents.members = True
        super().__init__(
            command_prefix=command_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True
        )

    async def setup_hook(self):
        extensions = ["dice", "help", "log_handler", "links", "traits", "drawbacks", "documents", "utility", "error_handler", "admin", "character", "new_character"]
        loaded = []

        for ext in extensions:
            try:
                await self.load_extension(ext)
                loaded.append(ext)
            except Exception as e:
                logger.critical(f"Failed to load extension {ext}: {e}")
                
        if loaded:
            logger.info(f"Loaded {len(loaded)} extensions: {', '.join(loaded)}")

        # Automatically sync application commands globally
        try:
            synced = await self.tree.sync()
            logger.info(f"Synced {len(synced)} application commands globally.")
        except Exception as e:
            logger.warning(f"Application command sync skipped or failed during startup: {e}")

    async def on_ready(self):

        logger.info(f"{self.user.name} is online! servers: {len(self.guilds)}")

if __name__ == "__main__":
    if not TOKEN:
        logger.critical("ERROR: DISCORD_TOKEN not found in .env file.")
    else:
        import time
        retry_delay = 60
        max_retry_delay = 300
        
        while True:
            try:
                bot = MineriaBot(PREFIXES)
                bot.run(TOKEN)
                logger.info("Bot execution finished cleanly.")
                break
            except discord.errors.HTTPException as e:
                if e.status == 429:
                    logger.warning(
                        f"Rate limited by Discord/Cloudflare (429 Too Many Requests). "
                        f"Retrying in {retry_delay} seconds..."
                    )
                    time.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
                else:
                    logger.critical(f"HTTP Exception during startup: {e}")
                    time.sleep(30)
            except discord.errors.LoginFailure as e:
                logger.critical(f"Login failed (invalid token?): {e}")
                logger.info("Sleeping for 120 seconds before retrying...")
                time.sleep(120)
            except Exception as e:
                logger.critical(f"Unexpected error during startup: {e}", exc_info=True)
                time.sleep(30)