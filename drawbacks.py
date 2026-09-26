"""
Mineria Discord Bot - Character Drawbacks Module
================================================
Provides random character drawbacks from the campaign rulebook to add flavor,
challenges, and narrative depth during character creation.
"""

import json
import random
from pathlib import Path
from typing import Dict, List, Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from log_handler import logger


class Drawbacks(commands.Cog, name="Drawbacks"):
    """Cog for drawing random drawbacks from the Mineria rulebook."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drawbacks: List[Dict[str, Any]] = []
        self._load_drawbacks()

    def _load_drawbacks(self) -> None:
        """Loads drawbacks list from datas/drawbacks.json into memory."""
        try:
            file_path = Path(__file__).parent / "datas" / "drawbacks.json"
            if file_path.exists():
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.drawbacks = data.get("drawbacks", [])
                    logger.info(f"Loaded {len(self.drawbacks)} drawbacks from database.")
            else:
                logger.warning("drawbacks.json not found in datas directory.")
        except Exception as exc:
            logger.error(f"Error loading drawbacks database: {exc}", exc_info=True)

    def _generate_drawback_embed(self) -> Optional[discord.Embed]:
        """Picks a random drawback and packages it into a styled Discord embed."""
        if not self.drawbacks:
            return None

        # Select a random drawback entry from the cached list
        selected = random.choice(self.drawbacks)
        name = selected.get("name", "Unknown Drawback")
        url = selected.get("url")

        # Format markdown hyperlink if wiki URL exists
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
        """Draws a random character drawback from the database."""
        embed = self._generate_drawback_embed()
        if not embed:
            await ctx.send("The drawback database is currently empty or unavailable.")
            return

        await ctx.send(embed=embed)

    @app_commands.command(name="drawback", description="Draw a random character drawback from the campaign rulebook")
    async def slash_drawback(self, interaction: discord.Interaction) -> None:
        """Slash command variant for drawing a random drawback."""
        embed = self._generate_drawback_embed()
        if not embed:
            await interaction.response.send_message("The drawback database is currently empty or unavailable.", ephemeral=True)
            return

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point for loading the Drawbacks Cog."""
    await bot.add_cog(Drawbacks(bot))
