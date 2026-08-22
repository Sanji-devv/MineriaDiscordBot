import discord
from discord.ext import commands, tasks
import shutil
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from log_handler import logger

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._last_backup_day = None
        self.presence_task.start()
        self.backup_schedule.start()

    def cog_unload(self):
        self.presence_task.cancel()
        self.backup_schedule.cancel()

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

    # --- BACKUP MAINTENANCE ---

    @tasks.loop(minutes=1)
    async def backup_schedule(self):
        """Checks every minute to trigger scheduled backups at 08:00."""
        now = datetime.now()
        today_key = (now.year, now.month, now.day)
        
        # Schedule: 08:00 AM once per day
        if now.hour == 8 and now.minute == 0 and self._last_backup_day != today_key:
            self._last_backup_day = today_key
            # Daily Backup
            await self.perform_backup("daily", retention=7)
            
            # Weekly Backup (Monday = 0)
            if now.weekday() == 0:
                await self.perform_backup("weekly", retention=5)

    @backup_schedule.before_loop
    async def before_schedule(self):
        await self.bot.wait_until_ready()

    def prune_backups(self, directory: Path, limit: int):
        """Keeps only the regular 'limit' number of backups."""
        try:
            files = sorted(directory.glob("*.zip"), key=lambda f: f.stat().st_mtime)
            
            if len(files) > limit:
                for f in files[:-limit]:
                    f.unlink()
                    logger.info(f"🗑️ Pruned old backup: {f.name} from {directory.name}")
        except Exception as e:
            logger.error(f"⚠️ Failed to prune {directory.name}: {e}")

    @commands.command(name="backup", hidden=True)
    @commands.is_owner()
    async def manual_backup(self, ctx):
        """Triggers an immediate manual backup to the daily folder."""
        await ctx.send("⏳ Starting manual backup...")
        try:
            filename = await self.perform_backup("daily", retention=7)
            if filename:
                await ctx.send(f"✅ Backup created in daily: `{filename}`")
            else:
                await ctx.send("❌ Backup failed. Check logs.")
        except Exception as e:
            await ctx.send(f"❌ Error: {e}")

    async def perform_backup(self, backup_type: str, retention: int = 7):
        """
        Creates a backup in backups/<backup_type>/
        backup_type: 'daily' or 'weekly'
        retention: number of files to keep
        """
        base_dir = Path(__file__).parent
        data_dir = base_dir / "datas"
        if not data_dir.exists():
            data_dir = base_dir / "data"
             
        backup_root = base_dir / "backups"
        target_dir = backup_root / backup_type
        
        if not data_dir.exists():
            logger.warning("⚠️ Data directory not found. Skipping backup.")
            return None

        target_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        
        if backup_type == "daily":
            filename = now.strftime("%A_%d_%m_%Y").lower()
        elif backup_type == "weekly":
            start_date = now - timedelta(days=now.weekday()) # Monday
            end_date = start_date + timedelta(days=6)        # Sunday
            month_name = end_date.strftime("%B").lower()
            filename = f"{start_date.day}-{end_date.day}_{month_name}"
        else:
            timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"mineria_{backup_type}_{timestamp}"

        backup_path = target_dir / filename
        
        try:
            # Run make_archive and pruning in a thread pool to avoid blocking the event loop
            await asyncio.to_thread(shutil.make_archive, str(backup_path), 'zip', str(data_dir))
            
            final_filename = f"{filename}.zip"
            logger.info(f"✅ {backup_type.capitalize()} backup created: {final_filename}")
            
            await asyncio.to_thread(self.prune_backups, target_dir, retention)
            return final_filename
        except Exception as e:
            logger.error(f"❌ {backup_type.capitalize()} backup failed: {e}")
            return None

async def setup(bot):
    await bot.add_cog(Admin(bot))
