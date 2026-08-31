import logging
import discord
from discord.ext import commands
from pathlib import Path

def setup_logging():
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger = logging.getLogger("MineriaBot")
    logger.setLevel(logging.INFO)
    
    if not logger.handlers:
        file_handler = logging.FileHandler(log_dir / "mineria.log", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%H:%M:%S'))
        logger.addHandler(console_handler)
    
    return logger

logger = setup_logging()

def format_ctx(ctx: commands.Context) -> str:
    """Format execution context into a concise summary with server/channel and user."""
    if ctx.guild:
        channel_name = getattr(ctx.channel, "name", str(getattr(ctx.channel, "id", "unknown")))
        origin = f"[{ctx.guild.name}/#{channel_name}]"
    else:
        origin = "[DM]"
    
    content = ctx.message.content.replace("\n", " ").strip() if ctx.message else ""
    if len(content) > 80:
        content = content[:77] + "..."
    
    author_name = ctx.author.name if ctx.author else "Unknown"
    return f"{origin} {author_name}: {content}"

class LogHandler(commands.Cog, name="LogHandler"):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        """Log concise command execution summary."""
        logger.info(format_ctx(ctx))

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """Log concise command error summary."""
        prefix = format_ctx(ctx)
        
        if isinstance(error, commands.CommandNotFound):
            logger.warning(f"{prefix} -> Unknown command")
        elif isinstance(error, commands.MissingRequiredArgument):
            logger.warning(f"{prefix} -> Missing arg '{error.param.name}'")
        elif isinstance(error, commands.BadArgument):
            logger.warning(f"{prefix} -> Bad argument: {error}")
        elif isinstance(error, commands.CommandOnCooldown):
            logger.warning(f"{prefix} -> Cooldown ({error.retry_after:.1f}s)")
        elif isinstance(error, commands.MissingPermissions):
            logger.warning(f"{prefix} -> Missing permissions")
        elif isinstance(error, commands.BotMissingPermissions):
            logger.warning(f"{prefix} -> Bot missing permissions")
        elif isinstance(error, commands.NotOwner):
            logger.warning(f"{prefix} -> Not owner")
        else:
            orig = getattr(error, "original", error)
            logger.error(f"{prefix} -> Error: {orig}")

async def setup(bot):
    await bot.add_cog(LogHandler(bot))