import random
import re
import discord
from discord.ext import commands

VALID_CHAR_PATTERN = re.compile(r'^[0-9dk\+\-]+$')
DICE_PATTERN = re.compile(r'([+-]?)(?:(\d*)d(\d+)(?:k(\d+))?|(\d+)|k(\d+))')

class Dice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def parse_expr(self, expr: str):
        expr = expr.lower().replace(" ", "")
        
        # Handle single number (e.g. "20" -> 1d20)
        if expr.isdigit(): 
            sides = int(expr)
            if sides < 1: return None
            return [{"type": "dice", "num": 1, "sides": sides, "keep": None, "sign": "+", "mult": 1}]
            
        if not VALID_CHAR_PATTERN.fullmatch(expr):
            return "INVALID_FORMAT"
            
        matches = list(DICE_PATTERN.finditer(expr))
        
        # validation - matches must consume entire string
        if sum(len(m.group(0)) for m in matches) != len(expr):
            return "INVALID_FORMAT"
            
        terms = []
        for m in matches:
            sign_str = m.group(1)
            num_str = m.group(2)
            sides_str = m.group(3)
            keep_str = m.group(4)
            int_str = m.group(5)
            k_only_str = m.group(6)
            
            sign = -1 if sign_str == '-' else 1
            display_sign = '-' if sign == -1 else '+'
            
            if int_str:
                terms.append({"type": "int", "val": sign * int(int_str), "sign": display_sign})
            elif k_only_str:
                if not terms or terms[-1]["type"] != "dice":
                    return "INVALID_FORMAT"
                
                k_val = int(k_only_str)
                if k_val == 0:
                    return "K_ZERO"
                if k_val > terms[-1]["num"]:
                    k_val = terms[-1]["num"]
                terms[-1]["keep"] = k_val
            else:
                num = int(num_str) if num_str else 1
                sides = int(sides_str)
                keep = int(keep_str) if keep_str else None
                
                if sides < 1 or num < 1: return "ZERO_SIDES"
                if keep is not None and keep == 0: return "K_ZERO"
                
                if num > 100: num = 100
                if keep and keep > num: keep = num
                
                terms.append({
                    "type": "dice",
                    "num": num,
                    "sides": sides,
                    "keep": keep,
                    "sign": display_sign,
                    "mult": sign
                })
        return terms if terms else "INVALID_FORMAT"

    @commands.hybrid_command(name="roll", description="Roll dice (e.g. 1d20+5, 1d8+3d8).")
    async def roll(self, ctx: commands.Context, *, expression: str = None):
        if expression is None:
            embed = discord.Embed(title="🎲 Dice Command Help", color=discord.Color.blue())
            embed.description = "You can use the roll command in the following ways:"
            embed.add_field(name="Basic Roll", value="`!roll 20`\n`!roll 1d10`", inline=True)
            embed.add_field(name="Advanced & Math", value="`!roll 1d20+5`\n`!roll 1d8 + 3d8 - 2`", inline=True)
            embed.add_field(name="Keep Highest", value="`!roll 4d6k3` (Roll 4, keep the best 3)", inline=False)
            embed.add_field(name="Multiple Rolls", value="`!roll d20, d6+2` (Separate with commas)", inline=False)
            await ctx.send(embed=embed)
            return

        # Allow multiple rolls separated by comma (e.g., "d20, d6") - capped at 20 rolls per command
        raw_exprs = [e.strip() for e in expression.split(",") if e.strip()][:20]

        results = []
        for expr in raw_exprs:
            if not expr: continue
            
            terms = self.parse_expr(expr)
            if terms == "INVALID_FORMAT":
                results.append(f"❌ `{expr}`: Invalid dice format!")
                continue
            elif terms == "ZERO_SIDES":
                results.append(f"❌ `{expr}`: Dice sides or count cannot be 0!")
                continue
            elif terms == "K_ZERO":
                results.append(f"❌ `{expr}`: Keep value (k) cannot be 0!")
                continue
            elif not terms:
                results.append(f"❌ `{expr}`: Invalid dice format!")
                continue
            
            total = 0
            parts_str = []
            clean_expr_parts = []
            
            has_keep = False
            keep_info = ""
            
            for i, term in enumerate(terms):
                sign_str = term["sign"]
                
                # For the first term, don't show '+' if it's positive
                display_sign = f"{sign_str} " if i > 0 or sign_str == '-' else ""
                clean_display_sign = f"{sign_str}" if i > 0 or sign_str == '-' else ""
                
                if term["type"] == "int":
                    val = term["val"]
                    total += val
                    clean_expr_parts.append(f"{clean_display_sign}{abs(val)}")
                    parts_str.append(f"{display_sign}{abs(val)}".strip())
                else:
                    num = term["num"]
                    sides = term["sides"]
                    keep = term["keep"]
                    mult = term["mult"]
                    
                    # Roll
                    rolls = [random.randint(1, sides) for _ in range(num)]
                    if keep:
                        has_keep = True
                        keep_info = f" (Top {keep})"
                        sorted_rolls = sorted(rolls, reverse=True)
                        kept_part = sorted_rolls[:keep]
                        dropped_part = sorted_rolls[keep:]
                        
                        term_total = sum(kept_part) * mult
                        total += term_total
                        
                        kept_formatted = [f"**{r}**" for r in kept_part]
                        dropped_formatted = [f"~~{r}~~" for r in dropped_part]
                        full_list_str = ", ".join(kept_formatted + dropped_formatted)
                        
                        parts_str.append(f"{display_sign}[{full_list_str}]".strip())
                        clean_expr_parts.append(f"{clean_display_sign}{num}d{sides}k{keep}")
                    else:
                        term_total = sum(rolls) * mult
                        total += term_total
                        
                        if num > 1:
                            rolls_str = f"({ ' + '.join(map(str, rolls)) })"
                        else:
                            rolls_str = str(rolls[0])
                            
                        parts_str.append(f"{display_sign}{rolls_str}".strip())
                        clean_expr_parts.append(f"{clean_display_sign}{num}d{sides}")
 
            clean_exp = "".join(clean_expr_parts)
            final_str = " ".join(parts_str)
            
            # Simplified output for single basic roll
            if len(terms) == 1 and terms[0]["type"] == "dice" and terms[0]["num"] == 1 and not has_keep:
                results.append(f"🎲 `{clean_exp}` -> **{total}**")
            else:
                results.append(f"🎲 `{clean_exp}` -> {final_str} = **{total}**{keep_info}")
        
        if not results:
             return await ctx.send("❌ Usage: `!roll d6`, `!roll 2d20` or `!roll 1d8 + 3d8`")
        
        desc_text = "\n".join(results)
        if len(desc_text) > 4000:
            desc_text = desc_text[:3900] + "\n*... (output truncated)*"

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        embed = discord.Embed(
            title=f"🎲 {ctx.author.display_name} rolled the dice:",
            description=desc_text,
            color=discord.Color.gold()
        )
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Dice System", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Dice System")

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Dice(bot))