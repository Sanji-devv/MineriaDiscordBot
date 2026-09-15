import discord
from discord import app_commands
from discord.ext import commands, tasks
from log_handler import logger

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.presence_task.start()

    def cog_unload(self):
        self.presence_task.cancel()

    # --- PRESENCE TASK ---

    @tasks.loop(minutes=5)
    async def presence_task(self):
        """
        Ensures the bot remains online and displays specific activity text.
        """
        try:
            # "Game" activity type shows as "Playing !m and !roll"
            activity = discord.Game(name="!m and !roll")
            await self.bot.change_presence(status=discord.Status.online, activity=activity)
        except Exception as e:
            logger.warning(f"Failed to update presence: {e}")

    @presence_task.before_loop
    async def before_presence_task(self):
        await self.bot.wait_until_ready()

    @commands.command(name="sync", hidden=True)
    @commands.is_owner()
    async def sync_tree(self, ctx):
        """
        Syncs the slash command tree to Discord. 
        Useful if commands don't show up in the profile.
        """
        msg = await ctx.send("🔄 Syncing commands...")
        try:
            synced = await self.bot.tree.sync()
            await msg.edit(content=f"✅ Synced **{len(synced)}** commands globally.")
        except Exception as e:
            await msg.edit(content=f"❌ Sync failed: {e}")

    @app_commands.command(name="sync", description="Sync slash commands globally (Owner only)")
    async def slash_sync(self, interaction: discord.Interaction):
        if not await self.bot.is_owner(interaction.user):
            return await interaction.response.send_message("❌ This command is restricted to the bot owner.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f"✅ Synced **{len(synced)}** application commands globally.")
        except Exception as e:
            await interaction.followup.send(f"❌ Sync failed: {e}")

async def setup(bot):
    await bot.add_cog(Admin(bot))

