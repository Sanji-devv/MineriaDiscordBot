
import discord
from discord import app_commands
from discord.ext import commands

from admin import is_user_authorized

COLOR_TERMINAL  = discord.Color.from_rgb(67,  97,  238)
COLOR_CHARACTER = discord.Color.from_rgb(230, 126,  34)
COLOR_CORE      = discord.Color.from_rgb(46,  204, 113)
COLOR_DOCS      = discord.Color.from_rgb(26,  188, 156)
COLOR_ADMIN     = discord.Color.from_rgb(231,  76,  60)

# ---------------------------------------------------------------------------
# HOW THE HELP SYSTEM WORKS
# ---------------------------------------------------------------------------
# The bot accepts three prefix styles for every text command:
#   !<command>          -- short prefix          e.g. !roll, !help
#   !m <command>        -- "mineria" shorthand    e.g. !m roll, !m char
#   !mineria <command>  -- full prefix            e.g. !mineria roll
#
# The help command itself has additional aliases: !m, !h, !mineria
#   Typing just "!m" with NO argument -> shows this Command Hub overview
#   Typing "!m <topic>"               -> jumps directly to that topic guide
#
# Slash command equivalent: /help [topic]  (with autocomplete drop-down)
#
# Available topics:
#   char   -- Character creation and roster management
#   core   -- Dice roller and trait/drawback mechanics
#   trait  -- Full trait system deep-dive
#   doc    -- Campaign PDFs and tactical battlemaps
#   admin  -- Administrative controls (authorized users only)
# ---------------------------------------------------------------------------


class HelpCog(commands.Cog, name="Help"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -----------------------------------------------------------------------
    # OVERVIEW HUB EMBED
    # -----------------------------------------------------------------------

    def _build_help_embed(
        self, user: discord.User | discord.Member, is_admin: bool = False
    ) -> discord.Embed:
        embed = discord.Embed(
            title="\U0001f5fa\ufe0f  Mineria Command Hub",
            description=(
                "**Mineria Campaign Assistant** \u2014 Pathfinder 1e interactive command terminal.\n\n"
                "__**How to use helper commands**__\n"
                "```\n"
                "Typing just  !m            Opens this Command Hub overview\n"
                "             !m <topic>    Opens a detailed guide for that topic\n"
                "             !help         Same as !m (direct alias)\n"
                "             !mineria      Same as !m (full-name alias)\n"
                "             /help [topic] Slash command with autocomplete\n"
                "```\n"
                "__**Available topics**__\n"
                "`char` \u2014 Character creation & roster\n"
                "`core` \u2014 Dice roller & drawbacks\n"
                "`trait` \u2014 Trait drawing system\n"
                "`doc` \u2014 PDFs, maps & resources\n"
                "`admin` \u2014 Admin controls *(authorized only)*\n\n"
                "*Example: type* `!m char` *or* `/help char` *to open the Character guide.*"
            ),
            color=COLOR_TERMINAL,
        )

        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(
            name="\u2694\ufe0f  CORE MECHANICS",
            value=(
                "\u2022 `!roll <expr>` \u2014 Polyhedral dice roller *(e.g. `4d6kh3`, `1d20+7`)*\n"
                "\u2022 `!trait [categories]` \u2014 Draw 3 traits, reroll within 24 h\n"
                "\u2022 `!drawback` \u2014 Random character drawback from campaign rules\n"
                "*\u2192 Details: `!m core` or `/help core`*"
            ),
            inline=False,
        )

        embed.add_field(
            name="\ud83d\udcdc  CHARACTER ENGINE",
            value=(
                "\u2022 `!char create <race>` \u2014 Launch creation wizard\n"
                "\u2022 `!char dr <stats>` \u2014 Distribute points across 6 attributes\n"
                "\u2022 `!char list / info / save` \u2014 Character sheets and roster\n"
                "\u2022 `!kia / !mia <name>` \u2014 Calculate starting XP from sheet\n"
                "*\u2192 Details: `!m char` or `/help char`*"
            ),
            inline=False,
        )

        embed.add_field(
            name="\ud83c\udf3f  TRAIT SYSTEM",
            value=(
                "Draw traits by category: Combat, Magic, Faith, Social, Race, Tactic,\n"
                "Craft, Underworld, Scholar, Hunter, Nature, Regional, Religion, Plane,\n"
                "Campaign, Equipment, Family, Nobility, Mount, Animal, Occult, Urban\n"
                "\u2022 `!trait hunter nature animal` \u2014 Draw from chosen categories\n"
                "\u2022 `!trait random 3` \u2014 3 traits from random distinct categories\n"
                "*\u2192 Details: `!m trait` or `/help trait`*"
            ),
            inline=False,
        )

        embed.add_field(
            name="\ud83d\udcda  RESOURCES & PROGRESSION",
            value=(
                "\u2022 `!doc / !map [name]` \u2014 Campaign PDFs & battlemaps\n"
                "\u2022 `!wiki` \u2014 Official wiki links and creation guides\n"
                "\u2022 `!gm <player>` \u2014 Player GM session history\n"
                "*\u2192 Details: `!m doc` or `/help doc`*"
            ),
            inline=False,
        )

        if is_admin:
            embed.add_field(
                name="\ud83d\udee1\ufe0f  ADMINISTRATIVE SUITE",
                value=(
                    "\u2022 `!admin disable <cmd|all>` \u2014 Restrict command globally\n"
                    "\u2022 `!admin enable <cmd|all>` \u2014 Re-enable command globally\n"
                    "\u2022 `!admin list` \u2014 View all currently disabled commands\n"
                    "\u2022 `!admin allow/remove <user>` \u2014 Manage bypass list\n"
                    "*\u2192 Details: `!m admin` or `/help admin`*"
                ),
                inline=False,
            )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(
            text=f"Requested by {user_name}  \u2022  Use !m <topic> to open any guide",
            icon_url=avatar_url,
        )
        return embed

    # -----------------------------------------------------------------------
    # CHARACTER SYSTEM EMBED
    # -----------------------------------------------------------------------

    def _build_char_help_embed(self, user: discord.User | discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title="\ud83d\udcdc  Character System Guide",
            description=(
                "Comprehensive manual for character creation, attribute distribution, and sheet management.\n\n"
                "__**How to reach this page**__\n"
                "\u2022 Text: `!m char`  \u2022  `!help char`  \u2022  `!mineria char`\n"
                "\u2022 Slash: `/help char`  *(with autocomplete drop-down)*"
            ),
            color=COLOR_CHARACTER,
        )

        embed.add_field(
            name="CREATION WORKFLOW",
            value=(
                "**Step 1** \u2014 `!char create <race>` \u2014 Initializes creation with dynamic budget (`41 - Race Points`).\n"
                "**Step 2** \u2014 `!char dr <stats>` \u2014 Distribute points across all 6 stats (`STR DEX CON INT WIS CHA`).\n"
                "\u00a0\u00a0\u2022 Each attribute rolls *N* d6 dice, keeping top 3 and adding racial bonuses.\n"
                "\u00a0\u00a0\u2022 Constraints: Minimum 3 dice, maximum 18 dice per attribute.\n"
                "**Step 3** \u2014 Flexible Bonus \u2014 If race grants flexible bonus (`+2 Any`), click the interactive button.\n"
                "**Step 4** \u2014 `!char add/remove <stat> <val>` \u2014 Manual adjustments for campaign tuning.\n"
                "**Step 5** \u2014 `!char save <name>` \u2014 Commits finalized character to your permanent roster.\n"
                "**Step 6** \u2014 `!char cancel` \u2014 Cancels active session and resets draft."
            ),
            inline=False,
        )

        embed.add_field(
            name="ROSTER MANAGEMENT",
            value=(
                "\u2022 `!char list` \u2014 View all your saved characters.\n"
                "\u2022 `!char info [name]` \u2014 Full sheet, ability modifiers, and roll logs.\n"
                "\u2022 `!char edit class <name> <class>` \u2014 Reassign character's class.\n"
                "\u2022 `!char edit stat <name> <stat> <val>` \u2014 Direct attribute adjustments.\n"
                "\u2022 `!char rename <old> <new>` \u2014 Rename an active character.\n"
                "\u2022 `!char delete <name>` \u2014 Permanently remove a character from your roster."
            ),
            inline=False,
        )

        embed.add_field(
            name="CAMPAIGN PROGRESSION & XP RECOVERY",
            value=(
                "\u2022 `!kia <name>` \u2014 Starting XP for fallen characters *(50% task XP + fixed base)*.\n"
                "\u2022 `!mia <name>` \u2014 Starting XP for missing characters *(90% task XP + fixed base)*.\n"
                "\u2022 `!rec [open/close]` \u2014 Toggles automated class recommendations."
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(
            text=f"Requested by {user_name}  \u2022  Type !m to return to the Command Hub",
            icon_url=avatar_url,
        )
        return embed

    # -----------------------------------------------------------------------
    # CORE MECHANICS EMBED (dice, traits, drawbacks)
    # -----------------------------------------------------------------------

    def _build_core_help_embed(self, user: discord.User | discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title="\ud83c\udfb2  Dice Engine & Core Mechanics Guide",
            description=(
                "Official rules and syntax for polyhedral dice rolling, character traits, and drawbacks.\n\n"
                "__**How to reach this page**__\n"
                "\u2022 Text: `!m core`  \u2022  `!help core`  \u2022  `!m roll`  \u2022  `!m dice`\n"
                "\u2022 Slash: `/help core`  *(with autocomplete drop-down)*"
            ),
            color=COLOR_CORE,
        )

        embed.add_field(
            name="POLYHEDRAL DICE ROLLER",
            value=(
                "\u2022 `!roll 1d20+7` \u2014 Standard D20 check with signed modifier\n"
                "\u2022 `!roll 4d6kh3` \u2014 Roll 4d6, keep highest 3 (standard attribute roll)\n"
                "\u2022 `!roll 2d20kl1` \u2014 Roll with disadvantage (keep lowest 1)\n"
                "\u2022 `!roll 20` \u2014 Fast shortcut for 1d20\n"
                "\u2022 `!roll d20, 2d6, d8+3` \u2014 Batch rolling multiple expressions at once\n"
                "\u00a0\u00a0*Aliases: `!r`, `!m roll`*"
            ),
            inline=False,
        )

        embed.add_field(
            name="TRAIT DRAWING & REROLL MECHANICS",
            value=(
                "\u2022 `!trait` \u2014 View all categories and system rules\n"
                "\u2022 `!trait combat social magic` \u2014 Draw 3 traits from selected categories\n"
                "\u2022 `!trait race(elf) combat faith` \u2014 Draw 1 racial trait + 2 categories\n"
                "\u2022 `!trait random 3` \u2014 Draw 3 traits from random distinct categories\n"
                "\u2022 `!trait reroll <1/2/3>` \u2014 Reroll specific slot within 24 hours\n"
                "\u2022 `!trait reroll <category>` \u2014 Reroll specific category in-place\n"
                "\u2022 `!trait reroll all` \u2014 Reroll all 3 active traits\n"
                "\u00a0\u00a0*For the full trait system deep-dive: `!m trait`*"
            ),
            inline=False,
        )

        embed.add_field(
            name="CHARACTER DRAWBACKS",
            value=(
                "\u2022 `!drawback` \u2014 Draw a random character drawback from campaign rules.\n"
                "\u00a0\u00a0*Alias: `!db`*"
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(
            text=f"Requested by {user_name}  \u2022  Type !m to return to the Command Hub",
            icon_url=avatar_url,
        )
        return embed

    # -----------------------------------------------------------------------
    # DOCUMENTS & MAPS EMBED
    # -----------------------------------------------------------------------

    def _build_doc_help_embed(self, user: discord.User | discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title="\ud83d\udcda  Campaign Documents & Tactical Maps Guide",
            description=(
                "Instant access to server PDF rulebooks, character sheets, and tactical battlemaps.\n\n"
                "__**How to reach this page**__\n"
                "\u2022 Text: `!m doc`  \u2022  `!help doc`  \u2022  `!m map`  \u2022  `!m docs`\n"
                "\u2022 Slash: `/help doc`  *(with autocomplete drop-down)*"
            ),
            color=COLOR_DOCS,
        )

        embed.add_field(
            name="CAMPAIGN RULEBOOKS & PDF DOWNLOADS",
            value=(
                "\u2022 `!doc` or `!doc list` \u2014 Catalog of all campaign PDF documents with file sizes.\n"
                "\u2022 `!doc <name>` \u2014 Download PDF document directly (supports fuzzy search).\n"
                "\u2022 `/doc [name]` \u2014 Slash command with real-time autocomplete suggestions."
            ),
            inline=False,
        )

        embed.add_field(
            name="TACTICAL BATTLEMAPS",
            value=(
                "\u2022 `!map` or `!map list` \u2014 Catalog of all available tactical battlemaps.\n"
                "\u2022 `!map <name>` \u2014 Display tactical battlemap image directly in chat.\n"
                "\u2022 `/map [name]` \u2014 Slash command with real-time autocomplete suggestions."
            ),
            inline=False,
        )

        embed.add_field(
            name="WIKI & EXTERNAL LINKS",
            value=(
                "\u2022 `!wiki` \u2014 Official wiki links, creation guides, and reference pages.\n"
                "\u00a0\u00a0*Alias: `!link`*"
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(
            text=f"Requested by {user_name}  \u2022  Type !m to return to the Command Hub",
            icon_url=avatar_url,
        )
        return embed

    # -----------------------------------------------------------------------
    # ADMIN EMBED
    # -----------------------------------------------------------------------

    def _build_admin_help_embed(self, user: discord.User | discord.Member) -> discord.Embed:
        embed = discord.Embed(
            title="\ud83d\udee1\ufe0f  Administrative Control Terminal",
            description=(
                "Restricted management suite for bot command restrictions and server tools.\n\n"
                "__**How to reach this page**__\n"
                "\u2022 Text: `!m admin`  \u2022  `!help admin`  \u2022  `!help adm`\n"
                "\u2022 Slash: `/help admin`  *(authorized users only)*"
            ),
            color=COLOR_ADMIN,
        )

        embed.add_field(
            name="COMMAND RESTRICTION CONTROLS",
            value=(
                "\u2022 `!admin disable <command|all>` \u2014 Restricts a command (or all) globally.\n"
                "\u2022 `!admin enable <command|all>` \u2014 Re-enables a command (or all) for all members.\n"
                "\u2022 `!admin list` \u2014 Displays all currently disabled commands.\n"
                "\u2022 `!admin allow <user>` \u2014 Grants user command guard bypass authority.\n"
                "\u2022 `!admin remove <user>` \u2014 Removes user from bypass list.\n"
                "\u2022 `!admin users` \u2014 Lists all authorized bypass users."
            ),
            inline=False,
        )

        embed.add_field(
            name="CAMPAIGN TOOLS & MANAGEMENT",
            value=(
                "\u2022 `!trait all <category>` \u2014 Catalog every trait belonging to a category.\n"
                "\u2022 `!gm <player>` \u2014 Player GM session history and total completed sessions."
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(
            text=f"Requested by {user_name}  \u2022  Type !m to return to the Command Hub",
            icon_url=avatar_url,
        )
        return embed

    # -----------------------------------------------------------------------
    # COMMAND DISPATCHERS
    # -----------------------------------------------------------------------

    # Prefix command: !help / !m / !mineria / !h
    # With no argument  -> overview hub
    # With <topic>      -> matching topic embed
    @commands.command(name="help", aliases=["m", "mineria", "h"])
    async def help_command(self, ctx: commands.Context, *, topic: str | None = None) -> None:
        is_admin = await is_user_authorized(self.bot, ctx.author)

        if topic:
            t = topic.lower().strip()
            if t in ("char", "character", "c"):
                embed = self._build_char_help_embed(ctx.author)
            elif t in ("core", "roll", "r", "dice", "drawback", "drawbacks"):
                embed = self._build_core_help_embed(ctx.author)
            elif t in ("trait", "traits", "t"):
                traits_cog = self.bot.get_cog("Traits")
                if traits_cog and hasattr(traits_cog, "build_trait_help_embed"):
                    embed = traits_cog.build_trait_help_embed(ctx.author)
                else:
                    embed = self._build_core_help_embed(ctx.author)
            elif t in ("doc", "docs", "map", "maps"):
                embed = self._build_doc_help_embed(ctx.author)
            elif t in ("admin", "adm"):
                if is_admin:
                    embed = self._build_admin_help_embed(ctx.author)
                else:
                    await ctx.send("This help topic is restricted to authorized administrators.")
                    return
            else:
                embed = self._build_help_embed(ctx.author, is_admin=is_admin)
        else:
            embed = self._build_help_embed(ctx.author, is_admin=is_admin)

        await ctx.send(embed=embed)

    # Slash command: /help [topic]  -- autocomplete drop-down in Discord
    @app_commands.command(name="help", description="Display the Mineria command terminal and system guide")
    @app_commands.describe(topic="Specific help topic: char | core | trait | doc | admin")
    @app_commands.choices(topic=[
        app_commands.Choice(name="Core Commands & Dice (core)",       value="core"),
        app_commands.Choice(name="Character System (char)",           value="char"),
        app_commands.Choice(name="Traits & Drawbacks (trait)",        value="trait"),
        app_commands.Choice(name="Documents & Battlemaps (doc)",      value="doc"),
        app_commands.Choice(name="Admin & Command Controls (admin)",  value="admin"),
    ])
    async def slash_help(self, interaction: discord.Interaction, topic: str | None = None) -> None:
        is_admin = await is_user_authorized(self.bot, interaction.user)

        if topic:
            t = topic.lower().strip()
            if t in ("char", "character", "c"):
                embed = self._build_char_help_embed(interaction.user)
            elif t in ("core", "roll", "r", "dice", "drawback", "drawbacks"):
                embed = self._build_core_help_embed(interaction.user)
            elif t in ("trait", "traits", "t"):
                traits_cog = self.bot.get_cog("Traits")
                if traits_cog and hasattr(traits_cog, "build_trait_help_embed"):
                    embed = traits_cog.build_trait_help_embed(interaction.user)
                else:
                    embed = self._build_core_help_embed(interaction.user)
            elif t in ("doc", "docs", "map", "maps"):
                embed = self._build_doc_help_embed(interaction.user)
            elif t in ("admin", "adm"):
                if is_admin:
                    embed = self._build_admin_help_embed(interaction.user)
                else:
                    await interaction.response.send_message(
                        "This help topic is restricted to authorized administrators.",
                        ephemeral=True,
                    )
                    return
            else:
                embed = self._build_help_embed(interaction.user, is_admin=is_admin)
        else:
            embed = self._build_help_embed(interaction.user, is_admin=is_admin)

        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
