import discord
from discord import app_commands
from discord.ext import commands

class Links(commands.Cog, name="Links"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build_wiki_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Mineria Wiki Links",
            description="Official reference resources and guides for the Mineria RPG Campaign.",
            color=discord.Color.gold()
        )

        # 1. Main Wiki portal
        embed.add_field(
            name="Wiki Homepage",
            value="[Mineria Wiki](https://mineria.fandom.com/tr/wiki/Mineria_Wiki)",
            inline=False
        )

        # 2. Official character sheet template
        embed.add_field(
            name="Character Page Guide",
            value="[Character Sheet Template](https://mineria.fandom.com/tr/wiki/TaslakKarakterKagidi?so=search)",
            inline=False
        )

        # 3. New character creation guidelines
        embed.add_field(
            name="Character Creation",
            value="[Guide: Character Creation](https://mineria.fandom.com/tr/wiki/Karakter_yaratmak)",
            inline=False
        )

        # Set official wiki thumbnail and bot footer
        embed.set_thumbnail(
            url="https://static.wikia.nocookie.net/mineria/images/e/e6/Site-logo.png/revision/latest?cb=20230101000000"
        )

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Wiki Reference", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Wiki Reference")

        return embed

    @commands.command(name="wiki", aliases=["link"])
    async def links(self, ctx: commands.Context) -> None:
        embed = self._build_wiki_embed()
        await ctx.send(embed=embed)

    @app_commands.command(name="wiki", description="Display official Mineria Wiki and reference links")
    async def slash_wiki(self, interaction: discord.Interaction) -> None:
        embed = self._build_wiki_embed()
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Links(bot))