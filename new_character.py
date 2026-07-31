import discord
from discord.ext import commands
import os
import aiohttp
import csv
from io import StringIO
from log_handler import logger

XP_TABLE = {
    1: 0,
    2: 1300,
    3: 3300,
    4: 6000,
    5: 10000,
    6: 15000,
    7: 23000,
    8: 34000,
    9: 50000,
    10: 71000,
    11: 105000,
    12: 145000,
    13: 210000,
    14: 295000,
    15: 425000,
    16: 600000,
    17: 850000,
    18: 1200000,
    19: 1700000,
    20: 2400000
}

class KiaCog(commands.Cog, name="KIA"):
    """
    Commands to calculate KIA, MIA, and current XP from the Google Sheet.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def get_level_info(self, current_xp: float) -> tuple[int, float, int]:
        """
        Returns the current level, XP needed for next level, and the next level.
        """
        current_level = 1
        for level in sorted(XP_TABLE.keys()):
            if current_xp >= XP_TABLE[level]:
                current_level = level
            else:
                break
        
        next_level = current_level + 1
        if next_level > 20:
            return 20, 0, 20  # Max level
            
        xp_needed = XP_TABLE[next_level] - current_xp
        return current_level, xp_needed, next_level

    async def fetch_and_calculate_xp(self, ctx: commands.Context, char_name: str, multiplier: float, title: str, color: discord.Color, is_xp_cmd: bool = False):
        char_name = char_name.strip()
        sheet_url = os.getenv("XP_SHEET_URL")
        if not sheet_url:
            await ctx.send("❌ Error: `XP_SHEET_URL` not found in `.env` file.")
            return

        async with ctx.typing():
            try:
                # Download data
                async with aiohttp.ClientSession() as session:
                    async with session.get(sheet_url) as response:
                        if response.status != 200:
                            await ctx.send("❌ Error: Could not reach Google Sheets. Please check the connection.")
                            return
                        csv_data = await response.text()

                # Parse CSV
                reader = csv.reader(StringIO(csv_data))
                headers = next(reader, None) # Skip headers
                
                char_found = False
                full_xp = 0.0  # Columns K (10), L (11), M (12) -> 100% full value
                task_xp = 0.0  # Columns I (8), J (9) -> Multiplied by 0.5 (KIA) or 0.9 (MIA)

                for row in reader:
                    if len(row) < 12:
                        continue
                        
                    # Column B (index 1) is Name
                    row_name = row[1].strip()
                    if row_name.lower() == char_name.lower():
                        char_found = True
                        
                        # Full XP: K (10), L (11), M (12)
                        for idx in [10, 11, 12]:
                            if idx < len(row) and row[idx].strip():
                                try:
                                    full_xp += float(row[idx].strip().replace(",", ""))
                                except ValueError:
                                    pass
                                    
                        # Task XP: I (8), J (9)
                        for idx in [8, 9]:
                            if idx < len(row) and row[idx].strip():
                                try:
                                    task_xp += float(row[idx].strip().replace(",", ""))
                                except ValueError:
                                    pass

                        break # Character found, exit loop

                if not char_found:
                    await ctx.send(f"❌ Error: Could not find a character named **{char_name}** in the list.")
                    return

                # Create Response Embed
                embed = discord.Embed(
                    title=title,
                    description=f"Data retrieved for **{char_name.title()}**.",
                    color=color
                )
                
                if is_xp_cmd:
                    current_total = full_xp + task_xp
                    level, xp_needed, next_level = self.get_level_info(current_total)
                    embed.add_field(name="Current Total XP", value=f"**{current_total:,.0f} XP**", inline=False)
                    embed.add_field(name="• Fixed XP (K, L, M)", value=f"{full_xp:,.0f} XP", inline=True)
                    embed.add_field(name="• Task XP (I, J)", value=f"{task_xp:,.0f} XP", inline=True)
                    embed.add_field(name="\u200b", value="\u200b", inline=True)
                    
                    embed.add_field(name="🎖️ Current Level", value=f"**Level {level}**", inline=True)
                    if level < 20:
                        embed.add_field(name="📈 Next Level", value=f"**{xp_needed:,.0f} XP** remaining for Level {next_level}.", inline=True)
                    else:
                        embed.add_field(name="📈 Next Level", value="Maximum Level Reached", inline=True)
                        
                    kia_pred = full_xp + (task_xp * 0.5)
                    mia_pred = full_xp + (task_xp * 0.9)
                    
                    k_lvl, _, _ = self.get_level_info(kia_pred)
                    m_lvl, _, _ = self.get_level_info(mia_pred)
                    
                    embed.add_field(name="────────── New Character Predictions ──────────", value="\u200b", inline=False)
                    
                    kia_details = f"**Fixed XP (K+L+M):** {full_xp:,.0f}\n**Added XP:** {(task_xp*0.5):,.0f} *(50% of {task_xp:,.0f})*\n**Total XP:** {kia_pred:,.0f}\n**Starts at:** Level {k_lvl}"
                    mia_details = f"**Fixed XP (K+L+M):** {full_xp:,.0f}\n**Added XP:** {(task_xp*0.9):,.0f} *(90% of {task_xp:,.0f})*\n**Total XP:** {mia_pred:,.0f}\n**Starts at:** Level {m_lvl}"
                    
                    embed.add_field(name="💀 If KIA", value=kia_details, inline=True)
                    embed.add_field(name="🕵️ If MIA", value=mia_details, inline=True)
                else:
                    final_xp = full_xp + (task_xp * multiplier)
                    level, xp_needed, next_level = self.get_level_info(final_xp)
                    pct = int(multiplier * 100)
                    
                    embed.add_field(name="Fixed XP (K, L, M)", value=f"{full_xp:,.0f} XP", inline=True)
                    embed.add_field(name=f"Added XP ({pct}%)", value=f"{(task_xp * multiplier):,.0f} XP\n*(from {task_xp:,.0f} Task XP)*", inline=True)
                    embed.add_field(name="\u200b", value="\u200b", inline=True) # Spacer
                    
                    embed.add_field(name="Total Calculated XP", value=f"**{final_xp:,.0f} XP**", inline=False)
                    
                    embed.add_field(name="🎖️ Starting Level", value=f"**Level {level}**", inline=True)
                    if level < 20:
                        embed.add_field(name="📈 Next Level", value=f"**{xp_needed:,.0f} XP** remaining for Level {next_level}.", inline=True)
                    else:
                        embed.add_field(name="📈 Next Level", value="Maximum Level Reached", inline=True)

                embed.set_footer(text="Mineria RPG • System", icon_url=self.bot.user.display_avatar.url)

                await ctx.send(embed=embed)

            except Exception as e:
                logger.error(f"Error during {title} command: {e}")
                await ctx.send(f"❌ An error occurred: `{str(e)}`")

    @commands.command(name="kia")
    async def kia_command(self, ctx: commands.Context, *, char_name: str):
        """
        Calculates dead character's XP: (K+L+M) + (0.5 * (I+J)).
        """
        await self.fetch_and_calculate_xp(ctx, char_name, 0.5, "💀 KIA XP Calculation", discord.Color.dark_red())

    @commands.command(name="mia")
    async def mia_command(self, ctx: commands.Context, *, char_name: str):
        """
        Calculates missing character's XP: (K+L+M) + (0.9 * (I+J)).
        """
        await self.fetch_and_calculate_xp(ctx, char_name, 0.9, "🕵️ MIA XP Calculation", discord.Color.gold())

    @commands.command(name="xp")
    async def xp_command(self, ctx: commands.Context, *, char_name: str):
        """
        Shows current XP (K+L+M + I+J) and predicts starting XP if KIA/MIA.
        """
        await self.fetch_and_calculate_xp(ctx, char_name, 1.0, "✨ Current XP Status", discord.Color.blue(), is_xp_cmd=True)

async def setup(bot):
    await bot.add_cog(KiaCog(bot))

