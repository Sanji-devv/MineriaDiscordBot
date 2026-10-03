import traceback
import discord
from discord.ext import commands
from log_handler import logger
from admin import CommandDisabledError

class ErrorHandler(commands.Cog, name="ErrorHandler"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # 1. Ignore unknown commands silently to prevent chat spam
        if isinstance(error, commands.CommandNotFound):
            return

        try:
            # 2. Handle missing required parameters (e.g., !char dr without stats)
            if isinstance(error, commands.MissingRequiredArgument):
                await ctx.send(
                    f"Invalid Command Usage: Missing required parameter: `{error.param.name}`.\n"
                    f"For usage help, use: `!help {ctx.command}`"
                )

            # 3. Handle bad argument conversions (e.g., passing text where a number is expected)
            elif isinstance(error, commands.BadArgument):
                await ctx.send(
                    f"Invalid Parameter: The provided argument is invalid.\n"
                    f"For usage help, use: `!help {ctx.command}`"
                )

            # 4. Handle command cooldown limits
            elif isinstance(error, commands.CommandOnCooldown):
                await ctx.send(f"Command Cooldown: Please wait {error.retry_after:.1f}s before retrying this command.")

            # 5. Handle user permission deficiencies
            elif isinstance(error, commands.MissingPermissions):
                await ctx.send("Access Denied: You do not possess the required Discord permissions to invoke this command.")

            # 6. Handle bot permission deficiencies (e.g., missing Send Messages or Embed Links)
            elif isinstance(error, commands.BotMissingPermissions):
                missing = ", ".join(f"`{perm}`" for perm in error.missing_permissions)
                await ctx.send(f"Bot Permissions Missing: The bot requires the following permissions in this channel: {missing}")

            # 7. Handle Command Guard restrictions (commands disabled by developer/admin)
            elif (
                isinstance(error, CommandDisabledError)
                or isinstance(getattr(error, "original", None), CommandDisabledError)
                or getattr(error, "original", None).__class__.__name__ == "CommandDisabledError"
            ):
                cmd_err = error if isinstance(error, CommandDisabledError) else getattr(error, "original", error)
                notice = getattr(cmd_err, "message", None) or str(cmd_err).strip() or "This command is temporarily disabled by admin."
                await ctx.send(notice)

            # 8. Handle owner-only command restrictions
            elif isinstance(error, commands.NotOwner):
                await ctx.send("Access Denied: This command is strictly restricted to the bot owner.")

            # 9. Handle generic check failures (logged as warning; user feedback handled by specific checks)
            elif isinstance(error, commands.CheckFailure):
                logger.warning(f"Access check failed for command '{ctx.command}' invoked by {ctx.author}: {error}")
                return

            # 10. Handle all unexpected runtime errors
            else:
                # Log full traceback to console and log file for debugging
                traceback.print_exception(type(error), error, error.__traceback__)
                original = getattr(error, "original", error)
                logger.error(f"Execution error on command '{ctx.command}' invoked by {ctx.author}: {original}", exc_info=original)

                cmd_name = ctx.command.qualified_name if ctx.command else "command"
                embed = discord.Embed(
                    title="Command Error / Invalid Usage",
                    description=(
                        f"An error occurred while executing `!{cmd_name}` or invalid parameters were provided.\n\n"
                        f"Help & Usage:\n"
                        f"- For command details: `!help {cmd_name}`\n"
                        f"- For trait categories and guide: `!trait`"
                    ),
                    color=discord.Color.red()
                )
                embed.set_footer(text="The error has been logged for developer investigation.")
                await ctx.send(embed=embed)

        except Exception as dispatch_exc:
            logger.error(f"Failed to deliver command error notice to Discord channel: {dispatch_exc}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ErrorHandler(bot))
