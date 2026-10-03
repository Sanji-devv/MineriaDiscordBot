import logging
from pathlib import Path
from discord.ext import commands

def setup_logging() -> logging.Logger:
    # Ensure logs directory exists
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger_instance = logging.getLogger("MineriaBot")
    logger_instance.setLevel(logging.INFO)

    # Prevent attaching duplicate handlers on cog reload
    if not logger_instance.handlers:
        # File handler: Detailed timestamps and full log output
        file_handler = logging.FileHandler(log_dir / "mineria.log", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger_instance.addHandler(file_handler)

        # Console handler: Streamlined timestamps for cleaner terminal viewing
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
        )
        logger_instance.addHandler(console_handler)

    return logger_instance


# Export global logger instance for project-wide usage
logger = setup_logging()


def format_ctx(ctx: commands.Context) -> str:
    # 1. Determine origin (Guild channel or Direct Message)
    if ctx.guild:
        channel_name = getattr(ctx.channel, "name", str(getattr(ctx.channel, "id", "unknown")))
        origin = f"[{ctx.guild.name}/#{channel_name}]"
    else:
        origin = "[DM]"

    # 2. Extract and sanitize message content (truncate long messages and collapse newlines)
    content = ctx.message.content.replace("\n", " ").strip() if ctx.message else ""
    if len(content) > 80:
        content = content[:77] + "..."

    # 3. Extract invoking author name
    author_name = ctx.author.name if ctx.author else "Unknown"

    return f"{origin} {author_name}: {content}"


class LogHandler(commands.Cog, name="LogHandler"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_completion(self, ctx: commands.Context) -> None:
        logger.info(format_ctx(ctx))

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        context_prefix = format_ctx(ctx)

        # Categorize common non-critical command warnings
        if isinstance(error, commands.CommandNotFound):
            logger.warning(f"{context_prefix} -> Unknown command")
        elif isinstance(error, commands.MissingRequiredArgument):
            logger.warning(f"{context_prefix} -> Missing parameter: '{error.param.name}'")
        elif isinstance(error, commands.BadArgument):
            logger.warning(f"{context_prefix} -> Bad argument: {error}")
        elif isinstance(error, commands.CommandOnCooldown):
            logger.warning(f"{context_prefix} -> Command on cooldown ({error.retry_after:.1f}s)")
        elif isinstance(error, commands.MissingPermissions):
            logger.warning(f"{context_prefix} -> User lacks required permissions")
        elif isinstance(error, commands.BotMissingPermissions):
            logger.warning(f"{context_prefix} -> Bot lacks required permissions")
        elif isinstance(error, commands.NotOwner):
            logger.warning(f"{context_prefix} -> Access denied (owner only)")
        elif isinstance(error, commands.CheckFailure) or error.__class__.__name__ == "CommandDisabledError":
            logger.warning(f"{context_prefix} -> Command check failed: {error}")
        else:
            # Log full unexpected errors
            original_error = getattr(error, "original", error)
            logger.error(f"{context_prefix} -> Execution Error: {original_error}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LogHandler(bot))