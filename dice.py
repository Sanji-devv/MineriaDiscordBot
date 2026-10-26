import random
import re
import discord
from discord import app_commands
from discord.ext import commands
from typing import List, Any, Optional, Tuple, Union

class DiceTerm:
    def __init__(
        self,
        count: int,
        sides: int,
        keep_highest: Optional[int] = None,
        keep_lowest: Optional[int] = None,
        sign: int = 1
    ):
        self.count = count
        self.sides = sides
        self.keep_highest = keep_highest
        self.keep_lowest = keep_lowest
        self.sign = sign

    def roll(self) -> Tuple[int, List[int], List[int], List[int]]:
        raw_rolls = [random.randint(1, self.sides) for _ in range(self.count)]

        if self.keep_highest is not None and self.keep_highest < self.count:
            sorted_desc = sorted(raw_rolls, reverse=True)
            kept_rolls = sorted_desc[:self.keep_highest]
            dropped_rolls = sorted_desc[self.keep_highest:]
        elif self.keep_lowest is not None and self.keep_lowest < self.count:
            sorted_asc = sorted(raw_rolls)
            kept_rolls = sorted_asc[:self.keep_lowest]
            dropped_rolls = sorted_asc[self.keep_lowest:]
        else:
            kept_rolls = raw_rolls
            dropped_rolls = []

        subtotal = sum(kept_rolls) * self.sign
        return subtotal, raw_rolls, kept_rolls, dropped_rolls

class ModifierTerm:
    def __init__(self, value: int, sign: int = 1):
        self.value = abs(value) * sign
        self.sign = sign

VALID_CHAR_PATTERN = re.compile(r'^[0-9dkhl\+\-]+$', re.IGNORECASE)
TOKEN_PATTERN = re.compile(r'([+-]?)(?:(\d*)d(\d+)(?:k([hl])?(\d+))?|(\d+)|k([hl])?(\d+))', re.IGNORECASE)

def parse_dice_expression(expression: str) -> Union[List[Union[DiceTerm, ModifierTerm]], str]:
    expr = expression.lower().replace(" ", "")
    if not expr:
        return "INVALID_FORMAT"

    if expr.isdigit():
        sides = int(expr)
        if sides < 1:
            return "ZERO_SIDES"
        return [DiceTerm(count=1, sides=sides, keep_highest=None, keep_lowest=None, sign=1)]

    if not VALID_CHAR_PATTERN.fullmatch(expr):
        return "INVALID_FORMAT"

    matches = list(TOKEN_PATTERN.finditer(expr))
    
    matched_length = sum(len(m.group(0)) for m in matches)
    if matched_length != len(expr):
        return "INVALID_FORMAT"

    terms: List[Union[DiceTerm, ModifierTerm]] = []

    for m in matches:
        sign_str = m.group(1)
        count_str = m.group(2)
        sides_str = m.group(3)
        keep_mode = (m.group(4) or "").lower()
        keep_str = m.group(5)
        modifier_str = m.group(6)
        k_only_mode = (m.group(7) or "").lower()
        k_only_str = m.group(8)

        sign = -1 if sign_str == '-' else 1

        if modifier_str is not None:
            terms.append(ModifierTerm(value=int(modifier_str), sign=sign))

        elif k_only_str is not None:
            if not terms or not isinstance(terms[-1], DiceTerm):
                return "INVALID_FORMAT"
            k_val = int(k_only_str)
            if k_val <= 0:
                return "ZERO_KEEP"
            k_bound = min(k_val, terms[-1].count)
            if k_only_mode == "l":
                terms[-1].keep_lowest = k_bound
                terms[-1].keep_highest = None
            else:
                terms[-1].keep_highest = k_bound
                terms[-1].keep_lowest = None

        else:
            count = int(count_str) if count_str else 1
            sides = int(sides_str)

            if sides < 1 or count < 1:
                return "ZERO_SIDES"

            count = min(count, 100)

            keep_highest = None
            keep_lowest = None
            if keep_str is not None:
                k_val = int(keep_str)
                if k_val <= 0:
                    return "ZERO_KEEP"
                k_bound = min(k_val, count)
                if keep_mode == "l":
                    keep_lowest = k_bound
                else:
                    keep_highest = k_bound

            terms.append(DiceTerm(count=count, sides=sides, keep_highest=keep_highest, keep_lowest=keep_lowest, sign=sign))

    return terms if terms else "INVALID_FORMAT"

def evaluate_dice_terms(terms: List[Union[DiceTerm, ModifierTerm]]) -> Tuple[int, str, str]:
    total = 0
    breakdown_parts: List[str] = []
    expression_parts: List[str] = []

    for i, term in enumerate(terms):
        sign_char = "+" if term.sign > 0 else "-"
        prefix_space = f"{sign_char} " if i > 0 or term.sign < 0 else ""
        expr_sign = f"{sign_char}" if i > 0 or term.sign < 0 else ""

        if isinstance(term, ModifierTerm):
            total += term.value
            abs_val = abs(term.value)
            breakdown_parts.append(f"{prefix_space}{abs_val}".strip())
            expression_parts.append(f"{expr_sign}{abs_val}")

        elif isinstance(term, DiceTerm):
            subtotal, raw_rolls, kept_rolls, dropped_rolls = term.roll()
            total += subtotal

            if term.keep_lowest is not None:
                keep_tag = f"kl{term.keep_lowest}"
            elif term.keep_highest is not None:
                keep_tag = f"kh{term.keep_highest}"
            else:
                keep_tag = ""
            expression_parts.append(f"{expr_sign}{term.count}d{term.sides}{keep_tag}")

            if (term.keep_highest is not None or term.keep_lowest is not None) and dropped_rolls:
                kept_str = [f"**{r}**" for r in kept_rolls]
                dropped_str = [f"~~{r}~~" for r in dropped_rolls]
                full_pool_str = ", ".join(kept_str + dropped_str)
                breakdown_parts.append(f"{prefix_space}[{full_pool_str}]".strip())
            elif term.count > 1:
                rolls_joined = " + ".join(str(r) for r in raw_rolls)
                breakdown_parts.append(f"{prefix_space}({rolls_joined})".strip())
            else:
                breakdown_parts.append(f"{prefix_space}{raw_rolls[0]}".strip())

    clean_expression = "".join(expression_parts)
    breakdown_str = " ".join(breakdown_parts)
    return total, breakdown_str, clean_expression

class Dice(commands.Cog):

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _build_help_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="Dice Roller Help",
            description="Roll standard Pathfinder 1e polyhedral dice with math modifiers and keep-highest logic.",
            color=discord.Color.gold()
        )
        embed.add_field(name="Basic Rolls", value="`!roll 20` *(1d20)*\n`!roll 1d10`\n`!roll d6`", inline=True)
        embed.add_field(name="Math & Modifiers", value="`!roll 1d20+5`\n`!roll 2d6 + 1d8 - 2`", inline=True)
        embed.add_field(
            name="Keep Highest / Lowest (k)",
            value="`!roll 4d6k3` *(Roll 4, keep best 3)*\n`!roll 2d20kh1` *(Advantage)*\n`!roll 2d20kl1` *(Disadvantage)*",
            inline=False
        )
        embed.add_field(name="Multi-Roll Sequences", value="`!roll d20, d6+2, 4d6k3` *(Separate with commas)*", inline=False)
        embed.set_footer(text="Mineria RPG • Dice System")
        return embed

    async def _execute_rolls(self, ctx_or_interaction: Any, expression: Optional[str]):
        if not expression or not expression.strip():
            help_embed = self._build_help_embed()
            if isinstance(ctx_or_interaction, discord.Interaction):
                return await ctx_or_interaction.response.send_message(embed=help_embed)
            return await ctx_or_interaction.send(embed=help_embed)

        raw_expressions = [e.strip() for e in expression.split(",") if e.strip()][:20]
        if not raw_expressions:
            msg = "Please specify a dice expression. Example: `!roll 1d20+5`"
            if isinstance(ctx_or_interaction, discord.Interaction):
                return await ctx_or_interaction.response.send_message(msg, ephemeral=True)
            return await ctx_or_interaction.send(msg)

        results: List[str] = []

        for expr in raw_expressions:
            terms = parse_dice_expression(expr)

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

            total, breakdown, clean_expr = evaluate_dice_terms(terms)

            if len(terms) == 1 and isinstance(terms[0], DiceTerm) and terms[0].count == 1:
                results.append(f"`{clean_expr}` -> **{total}**")
            else:
                results.append(f"`{clean_expr}` -> {breakdown} = **{total}**")

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

    @commands.command(name="roll", aliases=["r"], description="Roll polyhedral dice (e.g. 1d20+5, 4d6k3, d6).")
    async def prefix_roll(self, ctx: commands.Context, *, expression: Optional[str] = None):
        await self._execute_rolls(ctx, expression)

    @app_commands.command(name="roll", description="Roll polyhedral dice (e.g. 1d20+5, 4d6k3, d6)")
    @app_commands.describe(expression="Dice expression to roll (e.g. 1d20+5, 4d6k3, d20, d6)")
    async def slash_roll(self, interaction: discord.Interaction, expression: Optional[str] = None):
        await self._execute_rolls(interaction, expression)

async def setup(bot: commands.Bot):
    await bot.add_cog(Dice(bot))
