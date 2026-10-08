import traceback
import discord
from discord import app_commands
from discord.ext import commands
from log_handler import logger
from admin import CommandDisabledError

class ErrorHandler(commands.Cog, name="ErrorHandler"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._prev_tree_error = getattr(self.bot.tree, "on_error", None)
        self.bot.tree.on_error = self.on_app_command_error

    def cog_unload(self) -> None:
        if getattr(self.bot.tree, "on_error", None) == self.on_app_command_error:
            if self._prev_tree_error:
                self.bot.tree.on_error = self._prev_tree_error

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        try:
            if isinstance(error, app_commands.CommandOnCooldown):
                msg = f"Command Cooldown: Please wait {error.retry_after:.1f}s before retrying this command."
            elif isinstance(error, app_commands.MissingPermissions):
                msg = "Access Denied: You do not possess the required Discord permissions to invoke this command."
            elif isinstance(error, app_commands.BotMissingPermissions):
                missing = ", ".join(f"`{perm}`" for perm in error.missing_permissions)
                msg = f"Bot Permissions Missing: The bot requires the following permissions in this channel: {missing}"
            elif isinstance(error, app_commands.CheckFailure) or error.__class__.__name__ == "CommandDisabledError":
                if interaction.response.is_done():
                    return
                msg = getattr(error, "message", "This command is temporarily disabled by admin.")
            else:
                original = getattr(error, "original", error)
                cmd_name = interaction.command.name if interaction.command else "slash_command"
                logger.error(f"Execution error on /{cmd_name} invoked by {interaction.user}: {original}", exc_info=original)
                msg = f"An error occurred while executing `/{cmd_name}`. The issue has been logged."

            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception as dispatch_exc:
            logger.error(f"Failed to deliver application command error notice to user: {dispatch_exc}")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return

        try:
            if isinstance(error, commands.MissingRequiredArgument):
                await ctx.send(
                    f"Invalid Command Usage: Missing required parameter: `{error.param.name}`.\n"
                    f"For usage help, use: `!help {ctx.command}`"
                )

            elif isinstance(error, commands.BadArgument):
                await ctx.send(
                    f"Invalid Parameter: The provided argument is invalid.\n"
                    f"For usage help, use: `!help {ctx.command}`"
                )

            elif isinstance(error, commands.CommandOnCooldown):
                await ctx.send(f"Command Cooldown: Please wait {error.retry_after:.1f}s before retrying this command.")

            elif isinstance(error, commands.MissingPermissions):
                await ctx.send("Access Denied: You do not possess the required Discord permissions to invoke this command.")

            elif isinstance(error, commands.BotMissingPermissions):
                missing = ", ".join(f"`{perm}`" for perm in error.missing_permissions)
                await ctx.send(f"Bot Permissions Missing: The bot requires the following permissions in this channel: {missing}")

            elif (
                isinstance(error, CommandDisabledError)
                or error.__class__.__name__ == "CommandDisabledError"
                or isinstance(getattr(error, "original", None), CommandDisabledError)
                or getattr(error, "original", None).__class__.__name__ == "CommandDisabledError"
            ):
                cmd_err = (
                    error
                    if (isinstance(error, CommandDisabledError) or error.__class__.__name__ == "CommandDisabledError")
                    else getattr(error, "original", error)
                )
                notice = getattr(cmd_err, "message", None) or str(cmd_err).strip() or "This command is temporarily disabled by admin."
                await ctx.send(notice)

            elif isinstance(error, commands.NotOwner):
                await ctx.send("Access Denied: This command is strictly restricted to the bot owner.")

            elif isinstance(error, commands.CheckFailure):
                logger.warning(f"Access check failed for command '{ctx.command}' invoked by {ctx.author}: {error}")
                return

            else:
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
