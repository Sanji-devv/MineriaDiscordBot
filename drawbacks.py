import json
import random
from pathlib import Path
from typing import Dict, List, Any, Optional
import discord
from discord import app_commands
from discord.ext import commands
from log_handler import logger

class Drawbacks(commands.Cog, name="Drawbacks"):

    EXCLUDED_DRAWBACKS = frozenset({"Family Ties"})

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drawbacks: List[Dict[str, Any]] = []
        # Keep each user's three most recently drawn drawbacks out of their
        # next draw. This mirrors the trait cooldown and resets on restart.
        self.recent_drawbacks: Dict[int, List[str]] = {}
        self._load_drawbacks()

    def _load_drawbacks(self) -> None:
        try:
            file_path = Path(__file__).parent / "data" / "drawbacks.json"
            if not file_path.exists():
                file_path = Path(__file__).parent / "datas" / "drawbacks.json"
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.drawbacks = data.get("drawbacks", [])
                    logger.info(f"Loaded {len(self.drawbacks)} drawbacks from database.")
            else:
                logger.warning("drawbacks.json not found in datas or data directory.")
        except Exception as exc:
            logger.error(f"Error loading drawbacks database: {exc}", exc_info=True)

    def _generate_drawback_embed(self, user_id: int) -> Optional[discord.Embed]:
        if not self.drawbacks:
            return None

        pool = [d for d in self.drawbacks if d.get("name") not in self.EXCLUDED_DRAWBACKS]
        recent_names = set(self.recent_drawbacks.get(user_id, []))
        available_pool = [d for d in pool if d.get("name") not in recent_names]
        if not available_pool:
            available_pool = pool
        if not available_pool:
            return None
        selected = random.choice(available_pool)
        name = selected.get("name", "Unknown Drawback")
        url = selected.get("url")

        recent = self.recent_drawbacks.setdefault(user_id, [])
        recent.append(name)
        self.recent_drawbacks[user_id] = recent[-3:]

        desc_text = f"**[{name}]({url})**" if url else f"**{name}**"

        embed = discord.Embed(
            title="Random Character Drawback",
            description=desc_text,
            color=discord.Color.dark_red()
        )

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Character Drawbacks", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Character Drawbacks")

        return embed

    @commands.command(name="drawback", aliases=["db"])
    async def drawback(self, ctx: commands.Context) -> None:
        embed = self._generate_drawback_embed(ctx.author.id)
        if not embed:
            await ctx.send("The drawback database is currently empty or unavailable.")
            return

        await ctx.send(embed=embed)

    @app_commands.command(name="drawback", description="Draw a random character drawback from the campaign rulebook")
    async def slash_drawback(self, interaction: discord.Interaction) -> None:
        embed = self._generate_drawback_embed(interaction.user.id)
        if not embed:
            await interaction.response.send_message("The drawback database is currently empty or unavailable.", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed)

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Drawbacks(bot))
