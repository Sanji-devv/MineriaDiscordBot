"""
Mineria Discord Bot - System Help Terminal
==========================================
Displays a beautifully formatted, categorized overview of all commands across
core gameplay, character creation/management, and utility/administrative tools.
Supports dedicated sub-topic guides (e.g. !help roll, !help char, !help trait, !help doc, !help admin).
"""

from datetime import datetime, timezone
from typing import Optional, Union
import discord
from discord import app_commands
from discord.ext import commands

from admin import is_user_authorized

ACCENT_COLOR = discord.Color.from_rgb(255, 170, 0)


class HelpCog(commands.Cog, name="Help"):
    """Cog handling user guidance, command discovery, and interactive help documentation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build_help_embed(
        self, user: Union[discord.User, discord.Member], is_admin: bool = False
    ) -> discord.Embed:
        """Constructs the centralized help dashboard embed."""
        embed = discord.Embed(
            title="Mineria System Terminal",
            description=(
                "Welcome to the **Mineria Campaign Assistant**.\n"
                "Below are all available commands divided into functional categories.\n\n"
                "**Default Prefix:** `!` • **Aliases:** `!m`, `!h`, `!mineria` • **Slash Commands:** `/` supported\n"
                "Type `!help <topic>` for detailed guides *(e.g., `!help roll`, `!help char`, `!help trait`, `!help doc`)*.\n"
            ),
            color=ACCENT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )

        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)

        # -------------------------------------------------------------------
        # Category 1: Core Commands (Dice, Traits, Drawbacks)
        # -------------------------------------------------------------------
        embed.add_field(
            name="**CORE COMMANDS**",
            value=(
                "> **`!roll <expr>`** -> Polyhedral dice roller. *(e.g., `1d20+7`, `4d6kh3`, `20`, `d20, d6`)*\n"
                "> **`!trait`** -> Lists all available trait categories.\n"
                "> **`!trait <categories>`** -> Draws 3 random traits from chosen or general categories.\n"
                "> **`!trait reroll <1/2/3/cat/all>`** -> Rerolls active traits within 24 hours.\n"
                "> **`!drawback`** -> Suggests a random character drawback from campaign rules."
            ),
            inline=False,
        )

        # -------------------------------------------------------------------
        # Category 2: Character System (Creation, Stats, Management)
        # -------------------------------------------------------------------
        embed.add_field(
            name="**CHARACTER SYSTEM**",
            value=(
                "> **`!char create <race>`** -> Starts character creation session *(Admin Only)*.\n"
                "> **`!char dr <stats>`** -> Distributes rolled attribute points into character stats.\n"
                "> **`!char add/remove <stat> <val>`** -> Manually adjust stats during creation.\n"
                "> **`!char save <name>`** -> Finalizes and commits your character to the roster.\n"
                "> **`!char list`** -> Lists all registered characters belonging to you.\n"
                "> **`!char info [name]`** -> Displays complete character stats, traits, and details.\n"
                "> **`!char edit <class/stat>`** -> Modifies an existing character's class or attribute.\n"
                "> **`!char rename <old> <new>`** -> Renames a character in your roster.\n"
                "> **`!char delete <name>`** -> Permanently deletes a character from your roster."
            ),
            inline=False,
        )

        # -------------------------------------------------------------------
        # Category 3: Utility & Campaign Tools
        # -------------------------------------------------------------------
        embed.add_field(
            name="**UTILITY COMMANDS**",
            value=(
                "> **`!kia <name>`** & **`!mia <name>`** -> Calculates starting XP for new character *(Live sheet data)*.\n"
                "> **`!wiki`** -> Official Mineria Wiki, character template, and creation guides.\n"
                "> **`!doc [name]`** -> Access server PDF forms and downloadable campaign files.\n"
                "> **`!map [name]`** -> Display tactical battlemaps and regional world maps.\n"
                "> **`!rec [open/close]`** -> Toggles automated class recommendations during creation."
            ),
            inline=False,
        )

        # -------------------------------------------------------------------
        # Category 4: Administrative Tools (Shown for authorized staff)
        # -------------------------------------------------------------------
        if is_admin:
            embed.add_field(
                name="**ADMINISTRATIVE COMMANDS**",
                value=(
                    "> **`!trait all <category>`** -> Displays all traits for the specified category.\n"
                    "> **`!cmd <disable/enable/list>`** -> Restricts or re-enables specific commands.\n"
                    "> **`!cmd <allow/disallow> <user>`** -> Manages bypass permissions for restricted commands.\n"
                    "> **`!all <disable/enable/status>`** -> Bulk restricts or re-enables all commands.\n"
                    "> **`!d`** -> Server rank rule duplicate check.\n"
                    "> **`!gm <player>`** -> Player GM history and total session count.\n"
                    "> **`!best <player>`** -> Character ranking by most missions played.\n"
                    "> **`!sync`** -> Synchronizes application slash commands with Discord *(Owner Only)*."
                ),
                inline=False,
            )

        user_name = getattr(user, "display_name", user.name)
        embed.set_footer(
            text=f"Requested by: {user_name} • Mineria OS",
            icon_url=user.display_avatar.url if user.display_avatar else None,
        )

        return embed

    def _build_char_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        """Constructs a detailed help embed for the character creation and management workflow."""
        embed = discord.Embed(
            title="Character System Guide",
            description=(
                "Complete guide to character creation, attribute distribution, and roster management.\n\n"
                "**Creation Workflow:**\n"
                "1. **`!char create <race>`** -> Initiates creation with dynamic point budget (`41 - Race Points`). *(Admin Only)*\n"
                "2. **`!char dr <stats>`** -> Distribute dice across all 6 attributes *(e.g. `!char dr 6 6 6 6 6 10`)*.\n"
                "   • Each stat rolls `N` d6 dice, keeping top 3 highest and adding racial modifiers.\n"
                "   • Allocation constraints: Minimum 3 dice, maximum 18 dice per attribute.\n"
                "3. **Flexible Bonus Selection** -> If race grants flexible bonus (`+2 Any`), click the UI button.\n"
                "4. **`!char add/remove <stat> <val>`** -> Optional fine-tuning of attribute bonuses.\n"
                "5. **`!char save <name>`** -> Commits finalized character to your permanent roster.\n\n"
                "**Roster Management:**\n"
                "• **`!char list`** -> Displays all your saved characters.\n"
                "• **`!char info [name]`** -> Displays full character sheet, attribute modifiers, and roll history.\n"
                "• **`!char edit class <name> <class>`** -> Changes character's assigned class.\n"
                "• **`!char edit stat <name> <stat> <val>`** -> Direct attribute score adjustments.\n"
                "• **`!char rename <old> <new>`** -> Updates character name.\n"
                "• **`!char delete <name>`** -> Permanently deletes character.\n\n"
                "**Campaign Recovery:**\n"
                "• **`!kia <name>`** -> Calculates starting XP for fallen characters *(50% task XP + fixed XP)*.\n"
                "• **`!mia <name>`** -> Calculates starting XP for missing characters *(90% task XP + fixed XP)*."
            ),
            color=ACCENT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        user_name = getattr(user, "display_name", user.name)
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS")
        return embed

    def _build_doc_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        """Constructs a detailed help embed for documents and maps navigation."""
        embed = discord.Embed(
            title="Documents & Tactical Maps Guide",
            description=(
                "Access official campaign PDF rulebooks, form templates, and tactical battlemaps.\n\n"
                "**Document Commands:**\n"
                "> **`!doc`** or **`!doc list`** -> Lists all available campaign PDF documents with file sizes.\n"
                "> **`!doc <name>`** -> Downloads the specified PDF document directly. Supports fuzzy matching.\n\n"
                "**Battlemap Commands:**\n"
                "> **`!map`** or **`!map list`** -> Lists all available tactical battlemaps.\n"
                "> **`!map <name>`** -> Displays the tactical battlemap image. Supports fuzzy matching.\n\n"
                "**Slash Commands:**\n"
                "> **`/doc`** and **`/map`** feature real-time autocomplete suggestions as you type."
            ),
            color=ACCENT_COLOR,
            timestamp=datetime.now(timezone.utc),
        )
        user_name = getattr(user, "display_name", user.name)
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS")
        return embed

    def _build_admin_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        """Constructs a detailed help embed for administrative and command guard tools."""
        embed = discord.Embed(
            title="Administrative Control Terminal",
            description=(
                "System administration, command guard controls, and campaign server checks.\n\n"
                "**Command Guard Management:**\n"
                "> **`!cmd disable <command>`** -> Restricts a command to authorized administrators only.\n"
                "> **`!cmd enable <command>`** -> Re-enables a command for all server members.\n"
                "> **`!cmd list`** -> Lists currently disabled commands and authorized bypass users.\n"
                "> **`!cmd allow <user>`** -> Grants command bypass permission to a user.\n"
                "> **`!cmd disallow <user>`** -> Revokes command bypass permission from a user.\n\n"
                "**Bulk Command Restrictions:**\n"
                "> **`!all disable`** -> Restricts all manageable commands across the bot.\n"
                "> **`!all enable`** -> Removes restrictions from all commands.\n"
                "> **`!all status`** -> Displays full status of all restricted commands.\n\n"
                "**Campaign Administration:**\n"
                "> **`!char create <race>`** -> Starts authorized character creation session for a player.\n"
                "> **`!trait all <category>`** -> Displays full catalog of all traits in a category.\n"
                "> **`!d`** -> Runs duplicate rank rule check against server members.\n"
                "> **`!gm <player>`** -> Displays player GM session history and counts.\n"
                "> **`!best <player>`** -> Displays character ranking by most missions played.\n"
                "> **`!sync`** -> Synchronizes application slash commands with Discord gateway *(Owner Only)*."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        user_name = getattr(user, "display_name", user.name)
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS • Admin Privileges Verified")
        return embed

    @commands.command(name="help", aliases=["m", "mineria", "h"])
    async def help_command(self, ctx: commands.Context, *, topic: Optional[str] = None) -> None:
        """Displays the Mineria command terminal and system guide, or topic help (e.g. !help trait)."""
        is_admin = await is_user_authorized(self.bot, ctx.author)

        if topic:
            t = topic.lower().strip()
            if t in ("trait", "traits", "t"):
                traits_cog = self.bot.get_cog("Traits")
                if traits_cog and hasattr(traits_cog, "build_trait_help_embed"):
                    embed = traits_cog.build_trait_help_embed(ctx.author)
                    await ctx.send(embed=embed)
                    return
            elif t in ("roll", "r", "dice"):
                dice_cog = self.bot.get_cog("Dice")
                if dice_cog and hasattr(dice_cog, "_build_help_embed"):
                    embed = dice_cog._build_help_embed()
                    await ctx.send(embed=embed)
                    return
            elif t in ("char", "character", "c"):
                embed = self._build_char_help_embed(ctx.author)
                await ctx.send(embed=embed)
                return
            elif t in ("doc", "docs", "map", "maps"):
                embed = self._build_doc_help_embed(ctx.author)
                await ctx.send(embed=embed)
                return
            elif t in ("admin", "cmd", "all", "guard"):
                if is_admin:
                    embed = self._build_admin_help_embed(ctx.author)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send("This help topic is restricted to authorized administrators.")
                return

        embed = self._build_help_embed(ctx.author, is_admin=is_admin)
        await ctx.send(embed=embed)

    @app_commands.command(name="help", description="Display the Mineria command terminal and system guide")
    @app_commands.describe(topic="Specific help topic (trait, roll, char, doc, admin)")
    @app_commands.choices(topic=[
        app_commands.Choice(name="Core Commands & Dice (roll)", value="roll"),
        app_commands.Choice(name="Character System (char)", value="char"),
        app_commands.Choice(name="Traits & Drawbacks (trait)", value="trait"),
        app_commands.Choice(name="Documents & Maps (doc)", value="doc"),
        app_commands.Choice(name="Admin & Command Guard (admin)", value="admin"),
    ])
    async def slash_help(self, interaction: discord.Interaction, topic: Optional[str] = None) -> None:
        """Slash command variant for displaying the help dashboard."""
        is_admin = await is_user_authorized(self.bot, interaction.user)

        if topic:
            t = topic.lower().strip()
            if t in ("trait", "traits", "t"):
                traits_cog = self.bot.get_cog("Traits")
                if traits_cog and hasattr(traits_cog, "build_trait_help_embed"):
                    embed = traits_cog.build_trait_help_embed(interaction.user)
                    await interaction.response.send_message(embed=embed)
                    return
            elif t in ("roll", "r", "dice"):
                dice_cog = self.bot.get_cog("Dice")
                if dice_cog and hasattr(dice_cog, "_build_help_embed"):
                    embed = dice_cog._build_help_embed()
                    await interaction.response.send_message(embed=embed)
                    return
            elif t in ("char", "character", "c"):
                embed = self._build_char_help_embed(interaction.user)
                await interaction.response.send_message(embed=embed)
                return
            elif t in ("doc", "docs", "map", "maps"):
                embed = self._build_doc_help_embed(interaction.user)
                await interaction.response.send_message(embed=embed)
                return
            elif t in ("admin", "cmd", "all", "guard"):
                if is_admin:
                    embed = self._build_admin_help_embed(interaction.user)
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message("This help topic is restricted to authorized administrators.", ephemeral=True)
                return

        embed = self._build_help_embed(interaction.user, is_admin=is_admin)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point for loading the HelpCog."""
    await bot.add_cog(HelpCog(bot))