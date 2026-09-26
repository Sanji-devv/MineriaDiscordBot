"""
Mineria Dice Engine
===================
High-performance, feature-rich dice roller tailored for Pathfinder 1e TTRPG gameplay.

Capabilities:
- Shorthand single-number rolls (e.g. "20" -> 1d20)
- Standard dice notation (e.g. "1d20", "2d6", "3d8+4")
- Drop-lowest / Keep-highest notation (e.g. "4d6k3" or "4d6kh3")
- Multi-pool arithmetic (e.g. "1d8 + 2d6 - 2")
- Comma-separated multi-rolls (e.g. "d20, d6+2, 4d6k3")
- Visual Markdown formatting: kept dice bolded (**6**), dropped dice struck through (~~1~~)
"""

import random
import re
import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Dict, Any, Optional, Tuple, Union
from log_handler import logger


# =============================================================================
# DATA STRUCTURES & TYPE MODELS
# =============================================================================

class DiceTerm:
    """Represents a dice pool term, such as 4d6k3 or -1d8."""
    def __init__(self, count: int, sides: int, keep_highest: Optional[int] = None, sign: int = 1):
        self.count = count                  # Number of dice to roll (e.g. 4 in 4d6)
        self.sides = sides                  # Number of sides per die (e.g. 6 in 4d6)
        self.keep_highest = keep_highest    # Highest N dice to keep (e.g. 3 in 4d6k3)
        self.sign = sign                    # +1 for positive, -1 for negative

    def roll(self) -> Tuple[int, List[int], List[int], List[int]]:
        """
        Executes the dice roll and applies keep/drop logic.
        
        Returns:
            Tuple of:
            - subtotal: int (sum of kept dice * sign)
            - all_rolls: List[int] (all rolled dice in original order)
            - kept_rolls: List[int] (the dice counted toward the total)
            - dropped_rolls: List[int] (the dice discarded)
        """
        raw_rolls = [random.randint(1, self.sides) for _ in range(self.count)]

        if self.keep_highest is not None and self.keep_highest < self.count:
            # Sort descending to find the top N dice
            sorted_desc = sorted(raw_rolls, reverse=True)
            kept_rolls = sorted_desc[:self.keep_highest]
            dropped_rolls = sorted_desc[self.keep_highest:]
        else:
            kept_rolls = raw_rolls
            dropped_rolls = []

        subtotal = sum(kept_rolls) * self.sign
        return subtotal, raw_rolls, kept_rolls, dropped_rolls


class ModifierTerm:
    """Represents a static flat integer bonus or penalty (e.g. +5 or -2)."""
    def __init__(self, value: int, sign: int = 1):
        self.value = abs(value) * sign  # Signed integer value
        self.sign = sign


# =============================================================================
# DICE PARSER & TOKENIZER
# =============================================================================

VALID_CHAR_PATTERN = re.compile(r'^[0-9dkh\+\-]+$', re.IGNORECASE)
TOKEN_PATTERN = re.compile(r'([+-]?)(?:(\d*)d(\d+)(?:k(?:h)?(\d+))?|(\d+)|k(?:h)?(\d+))', re.IGNORECASE)


def parse_dice_expression(expression: str) -> Union[List[Union[DiceTerm, ModifierTerm]], str]:
    """
    Parses a single dice expression string into a structured list of terms.
    
    Returns:
        List[Union[DiceTerm, ModifierTerm]] if parsing succeeded.
        str error code ("INVALID_FORMAT", "ZERO_SIDES", "ZERO_KEEP", etc.) on failure.
    """
    expr = expression.lower().replace(" ", "")
    if not expr:
        return "INVALID_FORMAT"

    # Handle single number shortcut (e.g. "20" -> 1d20, "6" -> 1d6)
    if expr.isdigit():
        sides = int(expr)
        if sides < 1:
            return "ZERO_SIDES"
        return [DiceTerm(count=1, sides=sides, keep_highest=None, sign=1)]

    # Validate character set to prevent unexpected regex behavior
    if not VALID_CHAR_PATTERN.fullmatch(expr):
        return "INVALID_FORMAT"

    # Match tokens across the expression
    matches = list(TOKEN_PATTERN.finditer(expr))
    
    # Ensure matches consume the entire input string without leftover characters
    matched_length = sum(len(m.group(0)) for m in matches)
    if matched_length != len(expr):
        return "INVALID_FORMAT"

    terms: List[Union[DiceTerm, ModifierTerm]] = []

    for m in matches:
        sign_str = m.group(1)
        count_str = m.group(2)
        sides_str = m.group(3)
        keep_str = m.group(4)
        modifier_str = m.group(5)
        k_only_str = m.group(6)

        # Determine mathematical sign
        sign = -1 if sign_str == '-' else 1

        # Case 1: Flat integer modifier (e.g. "+5", "-3")
        if modifier_str is not None:
            terms.append(ModifierTerm(value=int(modifier_str), sign=sign))

        # Case 2: Post-fixed keep modifier attached to previous dice pool (e.g. "...k3")
        elif k_only_str is not None:
            if not terms or not isinstance(terms[-1], DiceTerm):
                return "INVALID_FORMAT"
            k_val = int(k_only_str)
            if k_val <= 0:
                return "ZERO_KEEP"
            # Cap keep count to total rolled count
            terms[-1].keep_highest = min(k_val, terms[-1].count)

        # Case 3: Standard or keep-highest dice pool (e.g. "1d20", "4d6k3", "d8")
        else:
            # Handle shorthand dice count (e.g. "d20" implies 1 die)
            count = int(count_str) if count_str else 1
            sides = int(sides_str)
            keep = int(keep_str) if keep_str else None

            # Validate non-zero constraints
            if sides < 1 or count < 1:
                return "ZERO_SIDES"
            if keep is not None and keep <= 0:
                return "ZERO_KEEP"

            # Safety caps to protect bot performance against catastrophic resource spikes
            if count > 100:
                count = 100
            if keep is not None and keep > count:
                keep = count

            terms.append(DiceTerm(count=count, sides=sides, keep_highest=keep, sign=sign))

    return terms if terms else "INVALID_FORMAT"


# =============================================================================
# EVALUATION & FORMATTING
# =============================================================================

def evaluate_dice_terms(terms: List[Union[DiceTerm, ModifierTerm]]) -> Tuple[int, str, str]:
    """
    Evaluates parsed terms by executing rolls, tracking totals, and building Markdown output.

    Returns:
        Tuple of:
        - total: int
        - breakdown_str: str (e.g. "[**6**, **5**, **4**, ~~1~~] + 4")
        - clean_expression: str (e.g. "4d6k3+4")
    """
    total = 0
    breakdown_parts: List[str] = []
    expression_parts: List[str] = []

    for i, term in enumerate(terms):
        sign_char = "+" if term.sign > 0 else "-"
        prefix_space = f"{sign_char} " if i > 0 or term.sign < 0 else ""
        expr_sign = f"{sign_char}" if i > 0 or term.sign < 0 else ""

        # Flat integer modifier
        if isinstance(term, ModifierTerm):
            total += term.value
            abs_val = abs(term.value)
            breakdown_parts.append(f"{prefix_space}{abs_val}".strip())
            expression_parts.append(f"{expr_sign}{abs_val}")

        # Dice pool term
        elif isinstance(term, DiceTerm):
            subtotal, raw_rolls, kept_rolls, dropped_rolls = term.roll()
            total += subtotal

            # Build expression string (e.g. "4d6k3")
            keep_tag = f"k{term.keep_highest}" if term.keep_highest else ""
            expression_parts.append(f"{expr_sign}{term.count}d{term.sides}{keep_tag}")

            # Format roll breakdown with bold/strikethrough styling
            if term.keep_highest is not None and dropped_rolls:
                # Mark kept dice as bold, dropped dice as strikethrough
                kept_str = [f"**{r}**" for r in kept_rolls]
                dropped_str = [f"~~{r}~~" for r in dropped_rolls]
                full_pool_str = ", ".join(kept_str + dropped_str)
                breakdown_parts.append(f"{prefix_space}[{full_pool_str}]".strip())
            elif term.count > 1:
                # Multiple dice without drop
                rolls_joined = " + ".join(str(r) for r in raw_rolls)
                breakdown_parts.append(f"{prefix_space}({rolls_joined})".strip())
            else:
                # Single die
                breakdown_parts.append(f"{prefix_space}{raw_rolls[0]}".strip())

    clean_expression = "".join(expression_parts)
    breakdown_str = " ".join(breakdown_parts)
    return total, breakdown_str, clean_expression


# =============================================================================
# COG & COMMAND HANDLERS
# =============================================================================

class Dice(commands.Cog):
    """Pathfinder 1e Polyhedral Dice Roller Engine."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build_help_embed(self) -> discord.Embed:
        """Constructs an aesthetic help guide for dice syntax."""
        embed = discord.Embed(
            title="Dice Roller Help",
            description="Roll standard Pathfinder 1e polyhedral dice with math modifiers and keep-highest logic.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Basic Rolls", value="`!roll 20` *(1d20)*\n`!roll 1d10`\n`!roll d6`", inline=True)
        embed.add_field(name="Math & Modifiers", value="`!roll 1d20+5`\n`!roll 2d6 + 1d8 - 2`", inline=True)
        embed.add_field(name="Keep Highest (k)", value="`!roll 4d6k3` *(Roll 4, keep best 3)*\n`!roll 2d20k1` *(Advantage)*", inline=False)
        embed.add_field(name="Multi-Roll Sequences", value="`!roll d20, d6+2, 4d6k3` *(Separate with commas)*", inline=False)
        embed.set_footer(text="Mineria RPG • Dice System")
        return embed

    async def _execute_rolls(self, ctx_or_interaction: Any, expression: Optional[str]):
        """Shared execution engine for both prefix and slash dice commands."""
        # If no expression provided, send syntax help embed
        if not expression or not expression.strip():
            help_embed = self._build_help_embed()
            if isinstance(ctx_or_interaction, discord.Interaction):
                return await ctx_or_interaction.response.send_message(embed=help_embed)
            return await ctx_or_interaction.send(embed=help_embed)

        # Split comma-separated expressions (capped at 20 rolls per request)
        raw_expressions = [e.strip() for e in expression.split(",") if e.strip()][:20]
        if not raw_expressions:
            msg = "Please specify a dice expression. Example: `!roll 1d20+5`"
            if isinstance(ctx_or_interaction, discord.Interaction):
                return await ctx_or_interaction.response.send_message(msg, ephemeral=True)
            return await ctx_or_interaction.send(msg)

        results: List[str] = []

        for expr in raw_expressions:
            terms = parse_dice_expression(expr)

            # Error dispatching
            if terms == "INVALID_FORMAT":
                results.append(f"`{expr}`: Invalid dice format.")
                continue
            elif terms == "ZERO_SIDES":
                results.append(f"`{expr}`: Dice sides or count must be 1 or higher.")
                continue
            elif terms == "ZERO_KEEP":
                results.append(f"`{expr}`: Keep count (k) must be 1 or higher.")
                continue
            elif not isinstance(terms, list):
                results.append(f"`{expr}`: Unrecognized expression.")
                continue

            # Evaluate terms and format output
            total, breakdown, clean_expr = evaluate_dice_terms(terms)

            # Format simple single-die output versus multi-die breakdown
            if len(terms) == 1 and isinstance(terms[0], DiceTerm) and terms[0].count == 1:
                results.append(f"`{clean_expr}` -> **{total}**")
            else:
                results.append(f"`{clean_expr}` -> {breakdown} = **{total}**")

        # Assemble embed description
        description_text = "\n".join(results)
        if len(description_text) > 4000:
            description_text = description_text[:3900] + "\n*... (output truncated)*"

        author = ctx_or_interaction.user if isinstance(ctx_or_interaction, discord.Interaction) else ctx_or_interaction.author
        avatar_url = author.display_avatar.url if author.display_avatar else None

        embed = discord.Embed(
            title=f"{author.display_name} rolled:",
            description=description_text,
            color=discord.Color.gold()
        )
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Dice System", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Dice System")

        if isinstance(ctx_or_interaction, discord.Interaction):
            await ctx_or_interaction.response.send_message(embed=embed)
        else:
            await ctx_or_interaction.send(embed=embed)

    # ─────────────────────────────────────────────────────────────────────────
    # PREFIX COMMANDS: !roll / !r
    # ─────────────────────────────────────────────────────────────────────────

    @commands.command(name="roll", aliases=["r"], description="Roll polyhedral dice (e.g. 1d20+5, 4d6k3, d6).")
    async def prefix_roll(self, ctx: commands.Context, *, expression: Optional[str] = None):
        """Rolls dice based on expression (e.g. !roll 1d20+5, !r 4d6k3)."""
        await self._execute_rolls(ctx, expression)

    # ─────────────────────────────────────────────────────────────────────────
    # APPLICATION COMMANDS: /roll
    # ─────────────────────────────────────────────────────────────────────────

    @app_commands.command(name="roll", description="Roll polyhedral dice (e.g. 1d20+5, 4d6k3, d6)")
    @app_commands.describe(expression="Dice expression to roll (e.g. 1d20+5, 4d6k3, d20, d6)")
    async def slash_roll(self, interaction: discord.Interaction, expression: Optional[str] = None):
        """Slash command for rolling dice."""
        await self._execute_rolls(interaction, expression)


async def setup(bot: commands.Bot):
    await bot.add_cog(Dice(bot))