import discord
from discord.ext import commands
import json
import random
from pathlib import Path
from log_handler import logger

class Drawbacks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.drawbacks = []
        try:
            file_path = Path(__file__).parent / "datas" / "drawbacks.json"
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.drawbacks = data.get("drawbacks", [])
        except Exception as e:
            logger.error(f"Error loading drawbacks: {e}")

    @commands.command(name="drawback", aliases=["db"])
    async def drawback(self, ctx):
        """Displays a random drawback."""
        if not self.drawbacks:
            await ctx.send("❌ Drawback list could not be loaded.")
            return
            
        drawback = random.choice(self.drawbacks)
        url = drawback.get('url')
        name = drawback.get('name', 'Unknown')
        desc = f"**[{name}]({url})**" if url else f"**{name}**"
        
        embed = discord.Embed(
            title="🎲 Random Drawback",
            description=desc,
            color=discord.Color.dark_red()
        )
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Drawbacks", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Drawbacks")
        
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Drawbacks(bot))

