from datetime import datetime, timezone
from typing import Optional, Union, List
import discord
from discord import app_commands
from discord.ext import commands
from admin import is_user_authorized

# =============================================================================
# SECTION 1: MODERN CURATED COLOR PALETTE & CONSTANTS
# =============================================================================
# Rich theme colors tailored for Pathfinder / Mineria campaign visual identity
COLOR_TERMINAL = discord.Color.from_rgb(67, 97, 238)    # #4361EE - Arcane Sapphire
COLOR_CHARACTER = discord.Color.from_rgb(230, 126, 34)  # #E67E22 - Warm Dragon Amber
COLOR_CORE = discord.Color.from_rgb(46, 204, 113)       # #2ECC71 - Emerald Sage
COLOR_STATS = discord.Color.from_rgb(52, 152, 219)      # #3498DB - Astral Celestial Blue
COLOR_DOCS = discord.Color.from_rgb(26, 188, 156)       # #1ABC9C - Tactical Cyan Mint
COLOR_ADMIN = discord.Color.from_rgb(231, 76, 60)       # #E74C3C - Crimson Bastion


# =============================================================================
# SECTION 2: INTERACTIVE UI NAVIGATION COMPONENTS
# =============================================================================

class HelpNavSelect(discord.ui.Select):
    def __init__(self, is_admin: bool = False, current_topic: str = "overview"):
        options = [
            discord.SelectOption(
                label="Command Overview",
                value="overview",
                description="Quick summary of all active commands",
                default=(current_topic == "overview")
            ),
            discord.SelectOption(
                label="Character System Guide",
                value="char",
                description="Creation wizard, attribute allocation, and roster management",
                default=(current_topic == "char")
            ),
            discord.SelectOption(
                label="Core Dice & Traits Guide",
                value="core",
                description="Polyhedral dice roller, trait drawing, and drawbacks",
                default=(current_topic == "core")
            ),
            discord.SelectOption(
                label="Stat Distribution Analytics",
                value="stats",
                description="Roll history, distribution metrics, and visual charts",
                default=(current_topic == "stats")
            ),
            discord.SelectOption(
                label="Documents & Tactical Maps",
                value="doc",
                description="PDF rulebook downloads and tactical battlemaps",
                default=(current_topic == "doc")
            ),
        ]
        if is_admin:
            options.append(
                discord.SelectOption(
                    label="Admin Controls",
                    value="admin",
                    description="Command restrictions (!admin enable/disable) and tools",
                    default=(current_topic == "admin")
                )
            )
        super().__init__(
            placeholder="Select a category to navigate details...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "HelpNavView" = self.view
        val = self.values[0]

        for opt in self.options:
            opt.default = (opt.value == val)

        if val == "overview":
            embed = view.cog._build_help_embed(interaction.user, is_admin=view.is_admin)
        elif val == "char":
            embed = view.cog._build_char_help_embed(interaction.user)
        elif val == "core":
            embed = view.cog._build_core_help_embed(interaction.user)
        elif val == "stats":
            embed = view.cog._build_stats_help_embed(interaction.user)
        elif val == "doc":
            embed = view.cog._build_doc_help_embed(interaction.user)
        elif val == "admin":
            if view.is_admin:
                embed = view.cog._build_admin_help_embed(interaction.user)
            else:
                await interaction.response.send_message(
                    "This section is restricted to authorized administrators.",
                    ephemeral=True
                )
                return

        await interaction.response.edit_message(embed=embed, view=view)


class HelpNavView(discord.ui.View):
    def __init__(
        self,
        cog: "HelpCog",
        author: Union[discord.User, discord.Member],
        is_admin: bool = False,
        current_topic: str = "overview",
        timeout: float = 180.0
    ):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.author = author
        self.is_admin = is_admin
        self.message: Optional[discord.Message] = None
        self.select_menu = HelpNavSelect(is_admin=is_admin, current_topic=current_topic)
        self.add_item(self.select_menu)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "Only the command author can navigate this help terminal.",
                ephemeral=True
            )
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass


# =============================================================================
# SECTION 3: HELP COG & EMBED BUILDERS
# =============================================================================

class HelpCog(commands.Cog, name="Help"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build_help_embed(
        self, user: Union[discord.User, discord.Member], is_admin: bool = False
    ) -> discord.Embed:
        embed = discord.Embed(
            title="Mineria Command Hub",
            description=(
                "**Mineria Campaign Assistant**\n"
                "Interactive command terminal for Pathfinder 1e campaign play.\n\n"
                "**Prefix:** `!` • **Aliases:** `!m`, `!mineria` • **Slash:** `/`\n"
                "*Select a category below or type `!m <topic>` for deep guides.*"
            ),
            color=COLOR_TERMINAL,
            timestamp=datetime.now(timezone.utc),
        )

        if user.display_avatar:
            embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(
            name="CORE MECHANICS",
            value=(
                "• `!roll <expr>` — Polyhedral dice roller *(e.g. `4d6kh3`, `1d20+7`)*\n"
                "• `!trait [categories]` — Draw 3 traits or reroll within 24h\n"
                "• `!drawback` — Random character drawback from campaign rules"
            ),
            inline=False,
        )

        embed.add_field(
            name="CHARACTER ENGINE",
            value=(
                "• `!char create <race>` — Launch creation wizard *(Staff)*\n"
                "• `!char dr <stats>` — Distribute points across 6 attributes\n"
                "• `!char list / info / save` — Character sheets and roster\n"
                "• `!kia / !mia <name>` — Calculate starting XP from sheet"
            ),
            inline=False,
        )

        embed.add_field(
            name="ANALYTICS & RESOURCES",
            value=(
                "• `!stats [user]` — Visual 4-panel stat distribution charts\n"
                "• `!doc / !map [name]` — Campaign PDF rulebooks & battlemaps\n"
                "• `!wiki` — Official wiki links and creation guides\n"
                "• `!gm <player>` — Player GM session history and counts"
            ),
            inline=False,
        )

        if is_admin:
            embed.add_field(
                name="ADMINISTRATIVE SUITE",
                value=(
                    "• `!admin disable <cmd>` — Restrict command globally\n"
                    "• `!admin enable <cmd>` — Re-enable command globally\n"
                    "• `!admin list` — View all currently disabled commands\n"
                    "• `!trait all <category>` — Full catalog of traits in category"
                ),
                inline=False,
            )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(
            text=f"Requested by: {user_name} • Mineria OS • Select below for deep guides",
            icon_url=avatar_url,
        )
        return embed

    def _build_char_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        embed = discord.Embed(
            title="Character System Guide",
            description="Comprehensive manual for character creation, attribute distribution, and sheet management.",
            color=COLOR_CHARACTER,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="CREATION WORKFLOW",
            value=(
                "1. `!char create <race>` — Initializes creation with dynamic budget (`41 - Race Points`). *(Admin/Staff)*\n"
                "2. `!char dr <stats>` — Distribute points across all 6 stats (`STR DEX CON INT WIS CHA`).\n"
                "   • Each attribute rolls `N` d6 dice, keeping top 3 and adding racial bonuses.\n"
                "   • Constraints: Minimum 3 dice, maximum 18 dice per attribute.\n"
                "3. Flexible Bonus — If race grants flexible bonus (`+2 Any`), click the interactive button.\n"
                "4. `!char add/remove <stat> <val>` — Manual adjustments for campaign adjustments.\n"
                "5. `!char save <name>` — Commits finalized character to your permanent roster."
            ),
            inline=False,
        )

        embed.add_field(
            name="ROSTER MANAGEMENT",
            value=(
                "• `!char list` — View all your saved characters.\n"
                "• `!char info [name]` — Full sheet, ability modifiers, and roll logs.\n"
                "• `!char edit class <name> <class>` — Reassign character's class.\n"
                "• `!char edit stat <name> <stat> <val>` — Direct attribute adjustments.\n"
                "• `!char rename <old> <new>` — Rename an active character.\n"
                "• `!char delete <name>` — Permanently remove a character from your roster."
            ),
            inline=False,
        )

        embed.add_field(
            name="CAMPAIGN PROGRESSION & XP RECOVERY",
            value=(
                "• `!kia <name>` — Starting XP for fallen characters *(50% task XP + fixed base)*.\n"
                "• `!mia <name>` — Starting XP for missing characters *(90% task XP + fixed base)*.\n"
                "• `!rec [open/close]` — Toggles automated class recommendations."
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS • Character Engine", icon_url=avatar_url)
        return embed

    def _build_core_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        embed = discord.Embed(
            title="Dice Engine & Trait System Guide",
            description="Official rules and syntax for polyhedral dice rolling, character traits, and drawbacks.",
            color=COLOR_CORE,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="POLYHEDRAL DICE ROLLER",
            value=(
                "• `!roll 1d20+7` — Standard D20 check with signed modifier\n"
                "• `!roll 4d6kh3` — Roll 4d6, keep highest 3 (standard attribute roll)\n"
                "• `!roll 2d20kl1` — Roll with disadvantage (keep lowest 1)\n"
                "• `!roll 20` — Fast shortcut for 1d20\n"
                "• `!roll d20, 2d6, d8+3` — Batch rolling multiple expressions at once"
            ),
            inline=False,
        )

        embed.add_field(
            name="TRAIT DRAWING & REROLL MECHANICS",
            value=(
                "• `!trait` — View all categories and system rules\n"
                "• `!trait combat social magic` — Draw 3 traits from selected categories\n"
                "• `!trait race(elf) combat faith` — Draw 1 racial trait + 2 categories\n"
                "• `!trait random 3` — Draw 3 traits from random distinct categories\n"
                "• `!trait reroll <1/2/3>` — Reroll specific slot within 24 hours\n"
                "• `!trait reroll <category>` — Reroll specific category in-place\n"
                "• `!trait reroll all` — Reroll all 3 active traits"
            ),
            inline=False,
        )

        embed.add_field(
            name="CHARACTER DRAWBACKS",
            value="• `!drawback` — Draw a random character drawback from campaign rules.",
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS • Core Mechanics", icon_url=avatar_url)
        return embed

    def _build_stats_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        embed = discord.Embed(
            title="Stat Distribution Analytics Guide",
            description="Data-driven analytics engine tracking all character stat rolls and generating visual charts.",
            color=COLOR_STATS,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="VISUAL DASHBOARDS & MATPLOTLIB CHARTS",
            value=(
                "• `!stats` — Generate 4-panel visual distribution dashboard *(Direct image attachment)*.\n"
                "• `!stats @Player` — Filter dashboard specifically for that player's roll history.\n"
                "• **Chart Panels:**\n"
                "  1. Average Final Stats & Ability Modifiers (color-coded bars).\n"
                "  2. Average Dice Points Allocation per Attribute.\n"
                "  3. Attribute Bracket Distribution (18+ Legendary down to <8 Deficient).\n"
                "  4. Player vs Server Benchmark or Overall Total Stat Density.\n"
                "• **Storage:** All generated plots are saved to `data/graph/`."
            ),
            inline=False,
        )

        embed.add_field(
            name="NUMERICAL METRICS & AUDIT LOGS",
            value=(
                "• `!stats summary [user]` — Textual report with sample counts, averages, and records.\n"
                "• `!stats history [user]` — Recent 8 distribution logs with allocated vs rolled dice.\n"
                "• **Interactive View:** Summary embeds feature a `Generate Visual Graph` button."
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS • Stat Analytics", icon_url=avatar_url)
        return embed

    def _build_doc_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        embed = discord.Embed(
            title="Campaign Documents & Tactical Maps Guide",
            description="Instant access to server PDF rulebooks, character sheets, and tactical battlemaps.",
            color=COLOR_DOCS,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="CAMPAIGN RULEBOOKS & PDF DOWNLOADS",
            value=(
                "• `!doc` or `!doc list` — Catalog of all campaign PDF documents with file sizes.\n"
                "• `!doc <name>` — Download PDF document directly (supports fuzzy search).\n"
                "• Slash command `/doc` supports real-time autocomplete suggestions as you type."
            ),
            inline=False,
        )

        embed.add_field(
            name="TACTICAL BATTLEMAPS",
            value=(
                "• `!map` or `!map list` — Catalog of all available tactical battlemaps.\n"
                "• `!map <name>` — Display tactical battlemap image directly in chat.\n"
                "• Slash command `/map` supports real-time autocomplete suggestions as you type."
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS • Campaign Documents", icon_url=avatar_url)
        return embed

    def _build_admin_help_embed(self, user: Union[discord.User, discord.Member]) -> discord.Embed:
        embed = discord.Embed(
            title="Administrative Control Terminal",
            description="Restricted management suite for bot command restrictions and server tools.",
            color=COLOR_ADMIN,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(
            name="COMMAND RESTRICTION CONTROLS",
            value=(
                "• `!admin disable <command>` — Restricts a command globally across all servers.\n"
                "• `!admin enable <command>` — Re-enables a command for all server members.\n"
                "• `!admin list` — Displays all currently disabled commands."
            ),
            inline=False,
        )

        embed.add_field(
            name="CAMPAIGN TOOLS & MANAGEMENT",
            value=(
                "• `!char create <race>` — Authorize character creation wizard for a player.\n"
                "• `!trait all <category>` — Catalog every trait belonging to a category.\n"
                "• `!gm <player>` — Player GM session history and total completed sessions."
            ),
            inline=False,
        )

        user_name = getattr(user, "display_name", user.name)
        avatar_url = user.display_avatar.url if user.display_avatar else None
        embed.set_footer(text=f"Requested by: {user_name} • Mineria OS • Admin Privileges Verified", icon_url=avatar_url)
        return embed

    # =========================================================================
    # SECTION 4: COMMAND DISPATCHERS (!help, !m, /help)
    # =========================================================================

    @commands.command(name="help", aliases=["m", "mineria", "h"])
    async def help_command(self, ctx: commands.Context, *, topic: Optional[str] = None) -> None:
        is_admin = await is_user_authorized(self.bot, ctx.author)
        current_topic = "overview"

        if topic:
            t = topic.lower().strip()
            if t in ("char", "character", "c"):
                embed = self._build_char_help_embed(ctx.author)
                current_topic = "char"
            elif t in ("roll", "r", "dice", "drawback", "drawbacks"):
                embed = self._build_core_help_embed(ctx.author)
                current_topic = "core"
            elif t in ("trait", "traits", "t"):
                traits_cog = self.bot.get_cog("Traits")
                if traits_cog and hasattr(traits_cog, "build_trait_help_embed"):
                    embed = traits_cog.build_trait_help_embed(ctx.author)
                else:
                    embed = self._build_core_help_embed(ctx.author)
                current_topic = "core"
            elif t in ("doc", "docs", "map", "maps"):
                embed = self._build_doc_help_embed(ctx.author)
                current_topic = "doc"
            elif t in ("stats", "stat", "analytics", "graph"):
                embed = self._build_stats_help_embed(ctx.author)
                current_topic = "stats"
            elif t in ("admin", "adm"):
                if is_admin:
                    embed = self._build_admin_help_embed(ctx.author)
                    current_topic = "admin"
                else:
                    await ctx.send("This help topic is restricted to authorized administrators.")
                    return
            else:
                embed = self._build_help_embed(ctx.author, is_admin=is_admin)
                current_topic = "overview"
        else:
            embed = self._build_help_embed(ctx.author, is_admin=is_admin)

        view = HelpNavView(cog=self, author=ctx.author, is_admin=is_admin, current_topic=current_topic)
        msg = await ctx.send(embed=embed, view=view)
        view.message = msg

    @app_commands.command(name="help", description="Display the Mineria command terminal and system guide")
    @app_commands.describe(topic="Specific help topic (roll, char, trait, stats, doc, admin)")
    @app_commands.choices(topic=[
        app_commands.Choice(name="Core Commands & Dice (roll)", value="roll"),
        app_commands.Choice(name="Character System (char)", value="char"),
        app_commands.Choice(name="Traits & Drawbacks (trait)", value="trait"),
        app_commands.Choice(name="Stat Distribution Analytics (stats)", value="stats"),
        app_commands.Choice(name="Documents & Battlemaps (doc)", value="doc"),
        app_commands.Choice(name="Admin & Command Controls (admin)", value="admin"),
    ])
    async def slash_help(self, interaction: discord.Interaction, topic: Optional[str] = None) -> None:
        is_admin = await is_user_authorized(self.bot, interaction.user)
        current_topic = "overview"

        if topic:
            t = topic.lower().strip()
            if t in ("char", "character", "c"):
                embed = self._build_char_help_embed(interaction.user)
                current_topic = "char"
            elif t in ("roll", "r", "dice", "drawback", "drawbacks"):
                embed = self._build_core_help_embed(interaction.user)
                current_topic = "core"
            elif t in ("trait", "traits", "t"):
                traits_cog = self.bot.get_cog("Traits")
                if traits_cog and hasattr(traits_cog, "build_trait_help_embed"):
                    embed = traits_cog.build_trait_help_embed(interaction.user)
                else:
                    embed = self._build_core_help_embed(interaction.user)
                current_topic = "core"
            elif t in ("doc", "docs", "map", "maps"):
                embed = self._build_doc_help_embed(interaction.user)
                current_topic = "doc"
            elif t in ("stats", "stat", "analytics", "graph"):
                embed = self._build_stats_help_embed(interaction.user)
                current_topic = "stats"
            elif t in ("admin", "adm"):
                if is_admin:
                    embed = self._build_admin_help_embed(interaction.user)
                    current_topic = "admin"
                else:
                    await interaction.response.send_message(
                        "This help topic is restricted to authorized administrators.",
                        ephemeral=True
                    )
                    return
            else:
                embed = self._build_help_embed(interaction.user, is_admin=is_admin)
                current_topic = "overview"
        else:
            embed = self._build_help_embed(interaction.user, is_admin=is_admin)

        view = HelpNavView(cog=self, author=interaction.user, is_admin=is_admin, current_topic=current_topic)
        await interaction.response.send_message(embed=embed, view=view)
        try:
            view.message = await interaction.original_response()
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))