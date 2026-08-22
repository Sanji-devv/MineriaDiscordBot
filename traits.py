import discord
from discord.ext import commands
import json
import random
import time
from pathlib import Path
import re
from typing import Dict, List, Any, Optional
from log_handler import logger

# Pre-compiled regular expressions for race matching
NORM_RE = re.compile(r'[\s_\-]+')
PAREN_RE = re.compile(r'\((.*?)\)')
SPLIT_RE = re.compile(r'[,/|;]|\bor\b')
HALF_RE = re.compile(r'half[\s_\-]+')
WORDS_RE = re.compile(r'[a-zA-Z0-9]+')
KIN_RE = re.compile(r'-kin\b')

class Traits(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.traits: List[Dict[str, Any]] = []
        self.traits_by_cat: Dict[str, List[Dict[str, Any]]] = {}
        self.race_traits: List[Dict[str, Any]] = []
        self.last_rolls: Dict[int, Dict[str, Any]] = {}  # user_id -> dict

    async def cog_load(self):
        """Loads traits database asynchronously via executor."""
        import asyncio
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._load_traits_sync)
        except Exception as e:
            logger.error(f"Error preloading traits: {e}")

    def _load_traits_sync(self):
        file_path = Path(__file__).parent / "datas" / "traits.json"
        if file_path.exists():
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.traits = data.get("traits", [])
                
                # Pre-index traits by lower-case category for fast O(1) retrieval
                by_cat: Dict[str, List[Dict[str, Any]]] = {}
                race_list: List[Dict[str, Any]] = []
                for t in self.traits:
                    cat = t.get("category", "").lower()
                    if cat:
                        if cat not in by_cat:
                            by_cat[cat] = []
                        by_cat[cat].append(t)
                    if cat == "race":
                        race_list.append(t)
                self.traits_by_cat = by_cat
                self.race_traits = race_list

    @staticmethod
    def _is_race_match(trait_name: str, race_query: str) -> bool:
        """Matches a race query (e.g. halfelf, half-elf, elf) against a trait name parenthetical."""
        if not race_query or not trait_name:
            return False
        q_norm = NORM_RE.sub('', race_query.lower())
        if not q_norm:
            return False

        parentheticals = PAREN_RE.findall(trait_name)
        if not parentheticals:
            parentheticals = [trait_name]

        for p in parentheticals:
            p_lower = p.lower()
            p_norm = NORM_RE.sub('', p_lower)

            # 1. Exact match against entire normalized parenthetical
            if q_norm == p_norm:
                return True

            # 2. Split by comma/slash/semicolon/pipe or 'or' (e.g. 'Elf, Desert' or 'Werebat or Werebat-kin')
            segments = SPLIT_RE.split(p_lower)
            for seg in segments:
                seg_norm = NORM_RE.sub('', seg.strip())
                if q_norm == seg_norm:
                    return True

                # Treat 'half-xxx' as compound word 'halfxxx' (prevents 'elf' from matching 'half-elf')
                seg_compound = HALF_RE.sub('half', seg)
                seg_words = WORDS_RE.findall(seg_compound)
                if q_norm in seg_words:
                    return True

                # Handle -kin suffixes (e.g. 'werebear' matching 'werebear-kin')
                seg_no_kin = KIN_RE.sub('', seg.strip())
                if q_norm == NORM_RE.sub('', seg_no_kin):
                    return True

        return False

    def _select_trait(self, t_type, val, race, exclude_names):
        """Helper to select a single random trait based on category or race."""
        if t_type == 'category':
            category_traits = self.traits_by_cat.get(val.lower(), [])
            if race:
                pool = [
                    t for t in category_traits
                    if (t.get("req_race", "Any").lower() == "any" or self._is_race_match(t.get("req_race", ""), race))
                    and t.get("name") not in exclude_names
                ]
            else:
                pool = [
                    t for t in category_traits
                    if t.get("name") not in exclude_names
                ]
            if pool:
                return random.choice(pool)
        elif t_type == 'race':
            race_specific_pool = []
            if val:
                race_specific_pool = [
                    tr for tr in self.race_traits
                    if self._is_race_match(tr.get("name", ""), val)
                    and tr.get("name") not in exclude_names
                ]
            # Priority 2: fallback to any Race trait if no race query or no specific matches
            race_fallback_pool = [
                tr for tr in self.race_traits
                if tr.get("name") not in exclude_names
            ]
            pool = race_specific_pool if race_specific_pool else race_fallback_pool
            if pool:
                return random.choice(pool)
        return None

    def _build_trait_embed(self, results, race, errors=None):
        race_desc = f" for race **{race.capitalize()}**" if race else ""
        embed = discord.Embed(
            title="🎲 Random Traits",
            description=f"Traits{race_desc} from your selected categories:",
            color=discord.Color.dark_blue()
        )

        level_labels = ["Üye", "Kıdemli", "Uzman"]
        for idx, selected in enumerate(results):
            if not selected:
                continue
            cat = selected.get('category', 'Unknown')
            name = selected.get('name', 'Unknown')
            url = selected.get('url', '')
            
            prefix = level_labels[idx] if idx < len(level_labels) else f"Level {idx + 1}"
            
            if cat.lower() == 'race':
                field_name = f"{prefix} Race Trait: {name}"
            else:
                field_name = f"{prefix} {cat} Trait: {name}"
                
            embed.add_field(
                name=field_name,
                value=f"**[Wiki Page]({url})**",
                inline=False
            )

        footer_text = "Mineria RPG • Traits"
        if errors:
            footer_text += f" | Not found: {', '.join(errors)}"

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text=footer_text, icon_url=avatar_url)
        else:
            embed.set_footer(text=footer_text)
        return embed

    @commands.group(name="trait", aliases=["t"], invoke_without_command=True)
    async def trait(self, ctx, *args: str):
        """Displays a random trait for each specified category."""
        traits = self.traits.copy()

        if not traits:
            await ctx.send("❌ Trait list not found.")
            return


        # --- Pre-process args to support race(half elf) with spaces ---
        merged_args = []
        in_race_bracket = False
        temp_race_tokens = []
        for arg in args:
            if not in_race_bracket and arg.lower().startswith("race("):
                if arg.endswith(")"):
                    merged_args.append(arg)
                else:
                    in_race_bracket = True
                    temp_race_tokens.append(arg)
            elif in_race_bracket:
                temp_race_tokens.append(arg)
                if arg.endswith(")"):
                    merged_args.append(" ".join(temp_race_tokens))
                    temp_race_tokens = []
                    in_race_bracket = False
            else:
                merged_args.append(arg)
        if temp_race_tokens:
            merged_args.append(" ".join(temp_race_tokens))

        # --- Parse arguments and build selection order ---
        selection_order = []  # List of tuples: ('category', cat_name) or ('race', race_name)
        
        # Pre-pass to get the race (needed for filtering pools)
        race = None
        for arg in merged_args:
            arg_lower = arg.lower().strip()
            if arg_lower.startswith("race(") and arg_lower.endswith(")"):
                race = arg_lower[5:-1].strip()
                break

        # Second pass: build the selection order
        i = 0
        while i < len(merged_args):
            arg = merged_args[i]
            arg_lower = arg.lower().strip()
            if arg_lower.startswith("race(") and arg_lower.endswith(")"):
                r_name = arg_lower[5:-1].strip()
                selection_order.append(('race', r_name))
            elif arg_lower == "random":
                count = 1
                if i + 1 < len(merged_args) and merged_args[i + 1].isdigit():
                    count = int(merged_args[i + 1])
                    i += 1
                for _ in range(count):
                    selection_order.append(('random', None))
            else:
                if arg_lower == "race":
                    selection_order.append(('race', race))
                else:
                    selection_order.append(('category', arg_lower))
            i += 1

        all_cats = sorted(list(set([t.get("category", "") for t in traits if t.get("category")])))
        # Show Race as Race(human) in category hint
        display_cats = [cat if cat != "Race" else "Race(human)" for cat in all_cats]
        cat_list = ", ".join(display_cats)

        # Determine if user requested a Race category
        wants_race_trait = any(t == 'race' for t, _ in selection_order) or (race is not None)

        if not args:
            hint_message = (
                f"❌ You must specify at least 3 different categories.\n"
                f"**Usage:** `!trait combat social magic` or `!trait race(human) combat family mount`\n"
                f"**Available Categories:** `{cat_list}`"
            )
            await ctx.send(hint_message)
            return

        if wants_race_trait and not race:
            hint_message = (
                f"❌ When selecting a Race trait, you must specify the race.\n"
                f"**Usage:** `!trait race(human) combat social`\n"
                f"**Available Categories:** `{cat_list}`"
            )
            await ctx.send(hint_message)
            return

        # --- Enforce at least 3 different categories ---
        unique_selections = set()
        for t, val in selection_order:
            if t == 'category':
                unique_selections.add(val)
            elif t == 'race':
                unique_selections.add('race')
            elif t == 'random':
                unique_selections.add(f"random_{len(unique_selections)}")
                
        if len(unique_selections) < 3:
            hint_message = (
                f"❌ You must specify at least 3 different categories.\n"
                f"**Usage:** `!trait combat social magic` or `!trait race(human) combat family mount`\n"
                f"**Available Categories:** `{cat_list}`"
            )
            await ctx.send(hint_message)
            return

        errors = []
        results = []

        # Resolve 'random' to concrete categories
        resolved_order = []
        used_categories = []
        for t, val in selection_order:
            if t == 'category':
                resolved_order.append((t, val))
                used_categories.append(val)
            elif t == 'race':
                resolved_order.append((t, val))
            elif t == 'random':
                available = [c.lower() for c in all_cats if c.lower() != "race" and c.lower() not in used_categories]
                if available:
                    picked = random.choice(available)
                    resolved_order.append(('category', picked))
                    used_categories.append(picked)
                else:
                    errors.append("random")

        # Select traits in the exact resolved order
        for t, val in resolved_order:
            exclude_names = {tr.get("name") for tr in results if tr and tr.get("name")}
            selected = self._select_trait(t, val, race, exclude_names)
            if selected:
                results.append(selected)
            else:
                results.append(None)
                if t == 'category':
                    errors.append(val)
                elif t == 'race':
                    errors.append(f"race({race})" if race else "race")

        if not any(results):
            await ctx.send(
                f"❌ No suitable trait found for categories `{', '.join(errors)}` and race `{race}`.\n"
                f"**Available Categories:** `{cat_list}`"
            )
            return

        # --- Build embed ---
        embed = self._build_trait_embed(results, race, errors)

        sent_message = await ctx.send(embed=embed)
        
        # Cache this roll for possible reroll
        self.last_rolls[ctx.author.id] = {
            "message": sent_message,
            "race": race,
            "resolved_order": resolved_order,
            "results": results,
            "errors": errors,
            "time": time.time()
        }

    @trait.command(name="reroll", aliases=["rr"])
    async def reroll(self, ctx, *args: str):
        """Rerolls one or more of your recently rolled traits.
        Usage: !trait reroll 1 2  OR  !trait reroll combat social  OR  !trait reroll all
        """
        user_id = ctx.author.id
        
        # Clean up any expired entries (older than 15 minutes / 900 seconds)
        now = time.time()
        expired_keys = [k for k, v in self.last_rolls.items() if now - v.get("time", 0) > 900]
        for k in expired_keys:
            del self.last_rolls[k]

        if user_id not in self.last_rolls:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
            await ctx.send(
                f"❌ **{ctx.author.mention}**, no active or non-expired trait selection found. "
                f"Please use `!trait <categories>` first (valid for 15 minutes).",
                delete_after=10
            )
            return

        roll_data = self.last_rolls[user_id]
        message = roll_data["message"]
        race = roll_data["race"]
        resolved_order = roll_data["resolved_order"]
        results = roll_data["results"].copy()
        errors = roll_data["errors"].copy()

        if not args:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
            await ctx.send(
                f"❌ **{ctx.author.mention}**, please specify index numbers (1, 2, 3), "
                f"category names, or 'all'. Example: `!trait reroll 1 2` or `!trait reroll combat social`",
                delete_after=10
            )
            return

        indices_to_reroll = []
        invalid_targets = []

        # Parse targets
        if any(arg.lower().strip() == "all" for arg in args):
            indices_to_reroll = list(range(len(resolved_order)))
        else:
            for arg in args:
                arg_lower = arg.lower().strip()
                if arg_lower.isdigit():
                    idx = int(arg_lower) - 1
                    if 0 <= idx < len(resolved_order):
                        if idx not in indices_to_reroll:
                            indices_to_reroll.append(idx)
                    else:
                        invalid_targets.append(arg)
                else:
                    found = False
                    for idx, (t_type, val) in enumerate(resolved_order):
                        if t_type == 'category' and val.lower() == arg_lower:
                            if idx not in indices_to_reroll:
                                indices_to_reroll.append(idx)
                            found = True
                        elif t_type == 'race' and arg_lower == 'race':
                            if idx not in indices_to_reroll:
                                indices_to_reroll.append(idx)
                            found = True
                    if not found:
                        invalid_targets.append(arg)

        if invalid_targets:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
            await ctx.send(
                f"❌ **{ctx.author.mention}**, invalid or not found categories/numbers: "
                f"`{', '.join(invalid_targets)}`.",
                delete_after=10
            )
            return

        # Perform reroll
        rerolled_any = False
        for idx in indices_to_reroll:
            t_type, val = resolved_order[idx]
            exclude_names = {results[i].get("name") for i in range(len(results)) if i != idx and results[i] and results[i].get("name")}
            
            new_trait = self._select_trait(t_type, val, race, exclude_names)
            if new_trait:
                results[idx] = new_trait
                rerolled_any = True

        if not rerolled_any:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
            await ctx.send(
                f"❌ **{ctx.author.mention}**, no other suitable traits available to reroll for specified categories.",
                delete_after=10
            )
            return

        # Rebuild and edit embed
        embed = self._build_trait_embed(results, race, errors)
        
        # Add a note in footer about reroll details
        original_footer = embed.footer.text if embed.footer else "Mineria RPG • Traits"
        
        reroll_labels = []
        if any(arg.lower().strip() == "all" for arg in args):
            reroll_labels.append("All")
        else:
            for idx in sorted(indices_to_reroll):
                t_type, val = resolved_order[idx]
                reroll_labels.append(f"#{idx+1} ({val.capitalize() if val else 'Race'})")
        
        reroll_label = "Rerolled " + " & ".join(reroll_labels)
        embed.set_footer(text=f"{original_footer} | {reroll_label}")

        try:
            await message.edit(embed=embed)
        except discord.NotFound:
            try:
                await ctx.message.delete()
            except discord.Forbidden:
                pass
            await ctx.send("❌ Original trait message not found. Please issue a new `!trait` command.", delete_after=10)
            return

        self.last_rolls[user_id]["results"] = results
        self.last_rolls[user_id]["time"] = time.time()
        
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

async def setup(bot):
    await bot.add_cog(Traits(bot))