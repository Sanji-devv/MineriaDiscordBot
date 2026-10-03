import re
import json
import time
import random
import asyncio
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from log_handler import logger
from character import InteractionContextAdapter, load_json

# ---------------------------------------------------------------------------
# Pre-compiled Regular Expressions for Race Matching & Normalization
# ---------------------------------------------------------------------------
# Strip whitespace, underscores, and hyphens for canonical string comparison
NORM_RE = re.compile(r"[\s_\-]+")

# Extract text within parentheses (e.g. "Elf (High)" -> "High")
PAREN_RE = re.compile(r"\((.*?)\)")

# Split multiple race variants within descriptions (e.g. "Werebat or Werebat-kin")
SPLIT_RE = re.compile(r"[,/|;]|\bor\b")

# Normalize 'half-' prefix variations into compound words (e.g. 'half-elf' -> 'halfelf')
HALF_RE = re.compile(r"half[\s_\-]+")

# Match alphanumeric tokens for exact word matching
WORDS_RE = re.compile(r"[a-zA-Z0-9]+")

# Strip '-kin' suffix for creature ancestry matching (e.g. 'werebear-kin' -> 'werebear')
KIN_RE = re.compile(r"-kin\b")

# Category Aliases for user-friendly input mapping (e.g. 'planar' -> 'plane', 'planes' -> 'plane', 'crafting' -> 'craft')
CATEGORY_ALIASES: Dict[str, str] = {
    "planar": "plane",
    "planes": "plane",
    "plane": "plane",
    "dimension": "plane",
    "dimensions": "plane",
    "crafting": "craft",
    "crafter": "craft",
    "wild": "nature",
    "wilderness": "nature",
    "underworld": "underworld",
    "crime": "underworld",
    "criminal": "underworld",
    "thief": "underworld",
    "thieves": "underworld",
    "blackmarket": "underworld",
    "scholar": "scholar",
    "academic": "scholar",
    "academy": "scholar",
    "knowledge": "scholar",
    "lore": "scholar",
    "occult": "occult",
    "occults": "occult",
    "eldritch": "occult",
    "tactics": "tactic",
    "tactic": "tactic",
    "tactical": "tactic",
    "strategy": "tactic",
    "urban": "urban",
    "city": "urban",
    "cities": "urban",
    "metropolis": "urban",
    "town": "urban",
}


async def _safe_delete(ctx: Any) -> None:
    msg = getattr(ctx, "message", None)
    if msg:
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException, AttributeError):
            pass


class Traits(commands.Cog, name="Traits"):

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.traits: List[Dict[str, Any]] = []
        self.traits_by_cat: Dict[str, List[Dict[str, Any]]] = {}
        self.race_traits: List[Dict[str, Any]] = []
        # In-memory session cache for reroll commands: user_id -> roll_session_dict
        self.last_rolls: Dict[int, Dict[str, Any]] = {}
        # Synchronous initial load as immediate fallback
        self._load_traits_sync()

    async def cog_load(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._load_traits_sync)
        except Exception as exc:
            logger.error(f"Error loading traits database in thread executor: {exc}")

    def _load_traits_sync(self, force: bool = False) -> None:
        if self.traits and not force:
            return
        file_path = Path(__file__).parent / "datas" / "traits.json"
        if not file_path.exists():
            file_path = Path(__file__).parent / "data" / "traits.json"
        if not file_path.exists():
            logger.warning("datas/traits.json or data/traits.json not found.")
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.traits = data.get("traits", [])

                # Index traits by lowercase category for O(1) retrieval
                by_cat: Dict[str, List[Dict[str, Any]]] = {}
                race_list: List[Dict[str, Any]] = []

                for trait in self.traits:
                    category = trait.get("category", "").strip().lower()
                    if category and category not in ("none", "disabled", "inactive"):
                        if category not in by_cat:
                            by_cat[category] = []
                        by_cat[category].append(trait)

                        # Dual-index singular/plural aliases for instant O(1) retrieval
                        if category in ("plane", "planes"):
                            other = "planes" if category == "plane" else "plane"
                            if other not in by_cat:
                                by_cat[other] = []
                            by_cat[other].append(trait)
                        elif category in ("craft", "crafting"):
                            other = "crafting" if category == "craft" else "craft"
                            if other not in by_cat:
                                by_cat[other] = []
                            by_cat[other].append(trait)
                        elif category in ("tactic", "tactics"):
                            other = "tactics" if category == "tactic" else "tactic"
                            if other not in by_cat:
                                by_cat[other] = []
                            by_cat[other].append(trait)

                    # Maintain a separate index for race-specific traits
                    if category == "race" or "/race-traits/" in trait.get("url", "") or any(f"({r})" in trait.get("name", "").lower() for r in ("human", "gnome", "halfling", "elf", "dwarf", "orc", "half-elf", "half-orc", "aasimar")):
                        race_list.append(trait)

                self.traits_by_cat = by_cat
                self.race_traits = race_list
                logger.info(f"Loaded {len(self.traits)} traits across {len(self.traits_by_cat)} categories.")
        except Exception as exc:
            logger.error(f"Failed to parse traits.json: {exc}", exc_info=True)

    # =========================================================================
    # SECTION 1: RACE COMPATIBILITY & TRAIT SELECTION ALGORITHMS
    # =========================================================================

    async def _is_admin(self, user: Any) -> bool:
        if not user:
            return False
        # Server administrators have full access
        if getattr(user, "guild_permissions", None) and user.guild_permissions.administrator:
            return True
        # Developer & allowed_users check from admin module
        try:
            from admin import is_user_authorized
            if await is_user_authorized(self.bot, user):
                return True
        except Exception as exc:
            logger.debug(f"Error checking admin authorization: {exc}")
        return False

    async def _send_all_category_traits(self, ctx: Any, cat_query: str) -> None:
        raw_cat = cat_query.strip().lower()
        cat_key = CATEGORY_ALIASES.get(raw_cat, raw_cat)

        # Handle race-specific filter: e.g. "race(elf)" or "race"
        if cat_key.startswith("race(") and cat_key.endswith(")"):
            race_name = cat_key[5:-1].strip()
            pool = [t for t in self.race_traits if self._is_race_match(t.get("name", ""), race_name)]
            display_cat = f"Race ({race_name.capitalize()})"
        elif cat_key == "race":
            pool = self.race_traits
            display_cat = "Race"
        else:
            pool = self.traits_by_cat.get(cat_key, [])
            display_cat = cat_key.capitalize()

        if not pool:
            all_cats = sorted(list(set([
                t.get("category", "").strip().capitalize()
                for t in self.traits
                if t.get("category") and t.get("category").strip().lower() not in ("none", "disabled", "inactive")
            ])))
            await ctx.send(
                f"No traits found in category '{cat_query}'.\n"
                f"Available Categories: {', '.join(all_cats)}"
            )
            return

        sorted_pool = sorted(pool, key=lambda x: x.get("name", "").lower())
        total_traits = len(sorted_pool)
        PAGE_SIZE = 25
        total_pages = (total_traits + PAGE_SIZE - 1) // PAGE_SIZE

        author_user = getattr(ctx, "author", getattr(ctx, "user", None))
        avatar_url = author_user.display_avatar.url if (author_user and getattr(author_user, "display_avatar", None)) else None

        embeds: List[discord.Embed] = []
        for page_idx in range(total_pages):
            chunk = sorted_pool[page_idx * PAGE_SIZE : (page_idx + 1) * PAGE_SIZE]
            lines = []
            for idx, t in enumerate(chunk, start=page_idx * PAGE_SIZE + 1):
                tname = t.get("name", "Unknown")
                turl = t.get("url", "")
                if turl:
                    lines.append(f"`{idx:02d}.` [{tname}]({turl})")
                else:
                    lines.append(f"`{idx:02d}.` **{tname}**")

            embed = discord.Embed(
                title=f"All Traits in Category: {display_cat.capitalize()}",
                description=(
                    f"Category: {display_cat} | Total: {total_traits} traits\n\n"
                    + "\n".join(lines)
                ),
                color=discord.Color.from_rgb(114, 137, 218)
            )
            footer_text = f"Page {page_idx + 1}/{total_pages} | Mineria RPG | Admin"
            if avatar_url:
                embed.set_footer(text=footer_text, icon_url=avatar_url)
            else:
                embed.set_footer(text=footer_text)

            embeds.append(embed)

        for i in range(0, len(embeds), 5):
            chunk_embeds = embeds[i : i + 5]
            if len(chunk_embeds) == 1:
                await ctx.send(embed=chunk_embeds[0])
            else:
                await ctx.send(embeds=chunk_embeds)

    @staticmethod
    def _is_race_match(trait_name: str, race_query: str) -> bool:
        if not race_query or not trait_name:
            return False

        q_norm = NORM_RE.sub("", race_query.lower())
        if not q_norm:
            return False

        # Extract parenthetical clauses, or test whole name if none exist
        parentheticals = PAREN_RE.findall(trait_name)
        if not parentheticals:
            parentheticals = [trait_name]

        for p in parentheticals:
            p_lower = p.lower()
            p_norm = NORM_RE.sub("", p_lower)

            # 1. Exact match against normalized parenthetical
            if q_norm == p_norm:
                return True

            # 2. Split multi-race segments by comma, slash, or 'or'
            segments = SPLIT_RE.split(p_lower)
            for seg in segments:
                seg_norm = NORM_RE.sub("", seg.strip())
                if q_norm == seg_norm:
                    return True

                # Prevent 'elf' from matching 'half-elf' by treating 'half-xxx' as compound 'halfxxx'
                seg_compound = HALF_RE.sub("half", seg)
                seg_words = WORDS_RE.findall(seg_compound)
                if q_norm in seg_words:
                    return True

                # Handle -kin suffixes (e.g., 'werebear' matches 'werebear-kin')
                seg_no_kin = KIN_RE.sub("", seg.strip())
                if q_norm == NORM_RE.sub("", seg_no_kin):
                    return True

        return False

    def _select_trait(
        self, t_type: str, val: Optional[str], race: Optional[str], exclude_names: Set[str]
    ) -> Optional[Dict[str, Any]]:
        if t_type == "category" and val:
            cat_key = CATEGORY_ALIASES.get(val.lower(), val.lower())
            category_traits = self.traits_by_cat.get(cat_key, [])
            if race:
                # Filter category traits by race requirement if specified
                pool = [
                    t for t in category_traits
                    if (t.get("req_race", "Any").lower() == "any" or self._is_race_match(t.get("req_race", ""), race))
                    and t.get("name") not in exclude_names
                ]
            else:
                pool = [t for t in category_traits if t.get("name") not in exclude_names]

            if pool:
                return random.choice(pool)

        elif t_type == "race":
            race_specific_pool = []
            if val:
                # Find traits specifically targeting the requested race
                race_specific_pool = [
                    tr for tr in self.race_traits
                    if self._is_race_match(tr.get("name", ""), val)
                    and tr.get("name") not in exclude_names
                ]

            # Fallback to general race trait pool if no specific race match
            race_fallback_pool = [
                tr for tr in self.race_traits
                if tr.get("name") not in exclude_names
            ]
            pool = race_specific_pool if race_specific_pool else race_fallback_pool
            if pool:
                return random.choice(pool)

        return None

    def _build_trait_embed(
        self, results: List[Optional[Dict[str, Any]]], race: Optional[str], errors: Optional[List[str]] = None
    ) -> discord.Embed:
        race_desc = f" for race **{race.capitalize()}**" if race else ""
        embed = discord.Embed(
            title="Random Traits",
            description=f"Traits{race_desc} drawn from your selected categories:",
            color=discord.Color.dark_blue()
        )

        level_labels = ["Member", "Senior", "Expert"]
        for idx, selected in enumerate(results):
            if not selected:
                continue

            category = selected.get("category", "Unknown")
            name = selected.get("name", "Unknown")
            url = selected.get("url", "")

            prefix = level_labels[idx] if idx < len(level_labels) else f"Level {idx + 1}"

            if category.lower() == "race":
                field_title = f"{prefix} Race Trait: {name}"
            else:
                field_title = f"{prefix} {category} Trait: {name}"

            val_text = f"**[Wiki Page]({url})**" if url else f"**{name}**"
            embed.add_field(name=field_title, value=val_text, inline=False)

        footer_text = "Mineria RPG | Traits"
        if errors:
            footer_text += f" | Not found: {', '.join(errors)}"

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text=footer_text, icon_url=avatar_url)
        else:
            embed.set_footer(text=footer_text)

        return embed

    def build_trait_help_embed(self, user: Optional[Any] = None) -> discord.Embed:
        embed = discord.Embed(
            title="Mineria Trait System Guide",
            description=(
                "Characters draw **3 traits** across distinct categories during character creation.\n"
                "Traits grant unique background bonuses, combat options, and roleplay flavor."
            ),
            color=discord.Color.from_rgb(114, 137, 218)
        )

        cat_meta = [
            ("Combat", "combat", "Physical combat, initiative bonuses & attack maneuvers"),
            ("Magic", "magic", "Spellcasting modifiers, concentration & metamagic"),
            ("Faith", "faith", "Sacred devotion, spiritual conviction & divine favor"),
            ("Social", "social", "Persuasion, bluff, diplomacy & interpersonal influence"),
            ("Race", "race", "Heritage traits (e.g. `race(human)`, `race(elf)`, `race(dwarf)`)"),
            ("Tactic", "tactic", "Battlefield coordination, teamwork & defensive tactics"),
            ("Craft", "craft", "Alchemy, blacksmithing & artisan production"),
            ("Underworld", "underworld", "Crime, stealth, thievery & streetwise subterfuge"),
            ("Scholar", "scholar", "Academic research, archives, lore & languages"),
            ("Nature", "nature", "Wilderness survival, fauna/flora & biome adaptation"),
            ("Regional", "regional", "Homeland origins, terrain & regional folklore"),
            ("Religion", "religion", "Deity patronage, sacred tenets & dogma"),
            ("Plane", "plane", "Planar ancestry, Great Beyond & elemental adaptability"),
            ("Campaign", "campaign", "Adventure milestones & campaign storyline hooks"),
            ("Equipment", "equipment", "Specialized arms, ancestral armor & heirloom gear"),
            ("Family", "family", "Noble bloodlines, household traditions & legacies"),
            ("Mount", "mount", "Mounted combat, loyal steeds & beast bonding"),
            ("Occult", "occult", "Spiritual entities, mediums, eldritch lore & curses"),
            ("Urban", "urban", "Metropolitan life, city streets & civic influence"),
        ]

        cat_lines: List[str] = []
        known_keys = set()
        for name, key, desc in cat_meta:
            known_keys.add(key)
            if key == "race":
                count = len(self.race_traits)
            else:
                count = len(self.traits_by_cat.get(key, []))
            count_str = f" `({count})`" if count > 0 else ""
            cat_lines.append(f"• **{name}**{count_str} — *{desc}*")

        # Dynamically include any other categories found in traits.json
        ignored_keys = {"none", "disabled", "inactive"} | set(CATEGORY_ALIASES.keys())
        for key in sorted(list(self.traits_by_cat.keys())):
            canonical = CATEGORY_ALIASES.get(key, key)
            if key not in known_keys and canonical not in known_keys and key not in ignored_keys:
                count = len(self.traits_by_cat[key])
                count_str = f" `({count})`" if count > 0 else ""
                cat_lines.append(f"• **{key.capitalize()}**{count_str} — *Custom category*")
                known_keys.add(key)
                known_keys.add(canonical)

        # Split categories cleanly into two balanced fields
        mid = (len(cat_lines) + 1) // 2
        part1 = cat_lines[:mid]
        part2 = cat_lines[mid:]

        embed.add_field(
            name="AVAILABLE TRAIT CATEGORIES (1/2)",
            value="\n".join(part1),
            inline=False
        )
        embed.add_field(
            name="AVAILABLE TRAIT CATEGORIES (2/2)",
            value="\n".join(part2),
            inline=False
        )

        embed.add_field(
            name="COMMAND USAGE & EXAMPLES",
            value=(
                "• `!trait <cat1> <cat2> <cat3>` -> Draw 3 categories (e.g. `!trait combat social magic`)\n"
                "• `!trait race(<race>) <c1> <c2>` -> Include race trait (e.g. `!trait race(elf) combat faith`)\n"
                "• `!trait random 3` -> Draw from 3 distinct random categories\n"
                "• `!trait <category> all` -> (Admin) List all traits in category (e.g. `!trait plane all`)"
            ),
            inline=False
        )

        embed.add_field(
            name="REROLL (24-HOUR WINDOW)",
            value=(
                "• `!trait reroll <1/2/3>` -> Rerolls specific trait slot (e.g. `!trait reroll 1 2`)\n"
                "• `!trait reroll <category>` -> Rerolls only that category (e.g. `!trait reroll combat`)\n"
                "• `!trait reroll all` -> Rerolls all 3 drawn traits"
            ),
            inline=False
        )

        avatar_url = user.display_avatar.url if (user and getattr(user, "display_avatar", None)) else None
        user_name = getattr(user, "display_name", getattr(user, "name", "Player")) if user else "Player"
        footer_text = f"Requested by: {user_name} | Mineria RPG • Trait System"
        if avatar_url:
            embed.set_footer(text=footer_text, icon_url=avatar_url)
        else:
            embed.set_footer(text=footer_text)

        return embed

    # =========================================================================
    # SECTION 2: COMMAND PARSING & EXECUTION (!trait, !trait reroll)
    # =========================================================================

    @commands.group(name="trait", aliases=["traits", "t"], invoke_without_command=True)
    async def trait(self, ctx: commands.Context, *args: str) -> None:
        await self._execute_trait_roll(ctx, *args)

    async def _execute_trait_roll(self, ctx: Any, *args: str) -> None:
        if not self.traits:
            await ctx.send("Trait database is empty or could not be loaded.")
            return

        # 1. Clean and normalize input tokens (split commas and spaces)
        cleaned_tokens: List[str] = []
        for a in args:
            for part in a.replace(",", " ").split():
                if part:
                    cleaned_tokens.append(part)

        # Show trait help embed if no arguments or explicit help query
        if not cleaned_tokens or (len(cleaned_tokens) == 1 and cleaned_tokens[0].lower() in ("help", "h", "list", "categories")):
            user = getattr(ctx, "author", getattr(ctx, "user", None))
            embed = self.build_trait_help_embed(user)
            await ctx.send(embed=embed)
            return

        # Check for admin '!trait <category> all' or '!trait all <category>' command
        tokens_lower = [t.lower() for t in cleaned_tokens]
        if "all" in tokens_lower:
            user = getattr(ctx, "author", getattr(ctx, "user", None))
            if not await self._is_admin(user):
                await ctx.send("Access Denied: This command is restricted to administrators.")
                return

            cat_tokens = [t for t in tokens_lower if t != "all"]
            if not cat_tokens:
                await ctx.send("Please specify the category to list.\nUsage: `!trait <category> all` or `!trait all <category>` (e.g. `!trait plane all`)")
                return

            await self._send_all_category_traits(ctx, cat_tokens[0])
            return

        # 2. Merge parenthetical race tokens that may contain spaces: e.g. "race(half", "elf)" -> "race(half elf)"
        merged_args: List[str] = []
        in_race_bracket = False
        temp_race_tokens: List[str] = []
        i = 0

        while i < len(cleaned_tokens):
            tok = cleaned_tokens[i]
            tok_lower = tok.lower()

            if not in_race_bracket:
                # Handle single-token race: "race(human)"
                if tok_lower.startswith("race(") and tok_lower.endswith(")"):
                    merged_args.append(tok)
                # Handle start of multi-token race: "race(half"
                elif tok_lower.startswith("race("):
                    in_race_bracket = True
                    temp_race_tokens.append(tok)
                # Handle separated race syntax: "race" "(half elf)"
                elif tok_lower == "race" and i + 1 < len(cleaned_tokens) and cleaned_tokens[i + 1].startswith("("):
                    in_race_bracket = True
                    temp_race_tokens.append("race" + cleaned_tokens[i + 1])
                    i += 1
                    if temp_race_tokens[0].endswith(")"):
                        merged_args.append(temp_race_tokens[0])
                        temp_race_tokens = []
                        in_race_bracket = False
                else:
                    merged_args.append(tok)
            else:
                temp_race_tokens.append(tok)
                if tok.endswith(")"):
                    merged_args.append(" ".join(temp_race_tokens))
                    temp_race_tokens = []
                    in_race_bracket = False
            i += 1

        if temp_race_tokens:
            merged_args.append(" ".join(temp_race_tokens))

        # 3. Pre-scan for race parameter (e.g. "race(human)" -> race="human")
        race: Optional[str] = None
        for arg in merged_args:
            arg_lower = arg.lower().strip()
            if arg_lower.startswith("race(") and arg_lower.endswith(")"):
                race = arg_lower[5:-1].strip()
                break

        # 4. Parse arguments into selection sequence
        selection_order: List[Tuple[str, Optional[str]]] = []
        i = 0
        while i < len(merged_args):
            arg = merged_args[i]
            arg_lower = arg.lower().strip()

            # Handle race category shortcut
            if arg_lower.startswith("race(") and arg_lower.endswith(")"):
                r_name = arg_lower[5:-1].strip()
                selection_order.append(("race", r_name))
            # Handle 'random' keyword with count: e.g. "random 3" -> 3 distinct random categories
            elif arg_lower == "random":
                count = 1
                if i + 1 < len(merged_args) and merged_args[i + 1].isdigit():
                    count = int(merged_args[i + 1])
                    i += 1
                for _ in range(count):
                    selection_order.append(("random", None))
            elif arg_lower == "race":
                selection_order.append(("race", race))
            else:
                cat_target = CATEGORY_ALIASES.get(arg_lower, arg_lower)
                selection_order.append(("category", cat_target))
            i += 1

        # Catalog all available categories for user hints
        all_cats = sorted(list(set([
            t.get("category", "").strip()
            for t in self.traits
            if t.get("category") and t.get("category").strip().lower() not in ("none", "disabled", "inactive")
        ])))
        display_cats = [cat if cat != "Race" else "Race(human)" for cat in all_cats]
        cat_list = ", ".join(display_cats)

        # Validate minimum requirement of 3 distinct categories
        if not args:
            hint_message = (
                f"Invalid Command Usage: You must specify at least 3 distinct categories.\n"
                f"Usage: `!trait combat social magic` or `!trait race(human) combat faith`\n"
                f"Available Categories: `{cat_list}`\n"
                f"Use `!trait` to view all categories and descriptions."
            )
            await ctx.send(hint_message)
            return

        wants_race_trait = any(t == "race" for t, _ in selection_order) or (race is not None)
        if wants_race_trait and not race:
            hint_message = (
                f"Invalid Command Usage: When selecting a Race trait, you must specify the race: `race(<race_name>)`\n"
                f"Usage: `!trait race(human) combat social` or `!trait race(elf) magic faith`\n"
                f"Available Categories: `{cat_list}`"
            )
            await ctx.send(hint_message)
            return

        unique_selections = set()
        for t, val in selection_order:
            if t == "category":
                unique_selections.add(val)
            elif t == "race":
                unique_selections.add("race")
            elif t == "random":
                unique_selections.add(f"random_{len(unique_selections)}")

        if len(unique_selections) < 3:
            hint_message = (
                f"Invalid Command Usage: You must specify at least 3 distinct categories.\n"
                f"Usage: `!trait combat social magic` or `!trait race(human) combat faith`\n"
                f"Available Categories: `{cat_list}`\n"
                f"Use `!trait` to view all categories and descriptions."
            )
            await ctx.send(hint_message)
            return

        # 5. Resolve 'random' placeholders to concrete categories
        resolved_order: List[Tuple[str, Optional[str]]] = []
        used_categories: List[str] = []
        errors: List[str] = []

        for t, val in selection_order:
            if t == "category" and val:
                resolved_order.append((t, val))
                used_categories.append(val)
            elif t == "race":
                resolved_order.append((t, val))
            elif t == "random":
                available = [c.lower() for c in all_cats if c.lower() != "race" and c.lower() not in used_categories]
                if available:
                    picked = random.choice(available)
                    resolved_order.append(("category", picked))
                    used_categories.append(picked)
                else:
                    errors.append("random")

        # 6. Draw traits ensuring non-duplicate trait names
        results: List[Optional[Dict[str, Any]]] = []
        for t, val in resolved_order:
            exclude_names = {tr.get("name") for tr in results if tr and tr.get("name")}
            selected = self._select_trait(t, val, race, exclude_names)
            if selected:
                results.append(selected)
            else:
                results.append(None)
                if t == "category" and val:
                    errors.append(val)
                elif t == "race":
                    errors.append(f"race({race})" if race else "race")

        if not any(results):
            await ctx.send(
                f"Invalid Category: No matching traits found for `{', '.join(errors)}`.\n"
                f"Available Categories: `{cat_list}`\n"
                f"Use `!trait` to list all categories."
            )
            return

        # 7. Render embed and store session in cache for reroll commands
        embed = self._build_trait_embed(results, race, errors)
        sent_message = await ctx.send(embed=embed)

        user = getattr(ctx, "author", getattr(ctx, "user", None))
        user_id = user.id if user else 0
        if user_id:
            self.last_rolls[user_id] = {
                "message": sent_message,
                "race": race,
                "resolved_order": resolved_order,
                "results": results,
                "errors": errors,
                "time": time.time()
            }

    @trait.command(name="help", aliases=["h", "list", "categories"])
    async def trait_help(self, ctx: commands.Context) -> None:
        embed = self.build_trait_help_embed(ctx.author)
        await ctx.send(embed=embed)

    @trait.command(name="all")
    async def trait_all(self, ctx: commands.Context, *, category: Optional[str] = None) -> None:
        user = ctx.author
        if not await self._is_admin(user):
            await ctx.send("Access Denied: This command is restricted to administrators.")
            return

        if not category:
            await ctx.send("Please specify the category to list.\nUsage: `!trait all <category>` or `!trait <category> all` (e.g. `!trait all plane`)")
            return

        await self._send_all_category_traits(ctx, category.strip())

    @trait.command(name="reroll", aliases=["rr"])
    async def reroll(self, ctx: commands.Context, *args: str) -> None:
        user_id = ctx.author.id
        now = time.time()

        # Clean expired sessions (older than 24 hours)
        expired_keys = [k for k, v in self.last_rolls.items() if now - v.get("time", 0) > 86400]
        for k in expired_keys:
            del self.last_rolls[k]

        if user_id not in self.last_rolls:
            await _safe_delete(ctx)
            await ctx.send(
                f"{ctx.author.mention}, no active trait roll found (rolls expire after 24h). "
                f"Please start with `!trait <categories>`.",
                delete_after=10
            )
            return

        roll_data = self.last_rolls[user_id]
        message = roll_data["message"]
        race = roll_data["race"]
        resolved_order = roll_data["resolved_order"]
        results = roll_data["results"].copy()
        errors = roll_data["errors"].copy()

        # Clean input arguments
        cleaned_args: List[str] = []
        for a in args:
            for part in a.replace(",", " ").split():
                if part:
                    cleaned_args.append(part)

        if not cleaned_args:
            await _safe_delete(ctx)
            await ctx.send(
                f"{ctx.author.mention}, please specify targets: numbers (`1 2`), "
                f"categories (`combat social`), or `all`.",
                delete_after=10
            )
            return

        # Handle 'all' reroll shortcut
        indices_to_reroll: List[int] = []
        invalid_targets: List[str] = []

        if any(arg.lower().strip() == "all" for arg in cleaned_args):
            indices_to_reroll = list(range(len(resolved_order)))
        else:
            for arg in cleaned_args:
                arg_lower = arg.lower().strip()
                # Handle numeric 1-based index (e.g. "1" -> index 0)
                if arg_lower.isdigit():
                    idx = int(arg_lower) - 1
                    if 0 <= idx < len(resolved_order):
                        if idx not in indices_to_reroll:
                            indices_to_reroll.append(idx)
                    else:
                        invalid_targets.append(arg)
                else:
                    # Handle category name targeting (e.g. "combat")
                    found = False
                    norm_arg = CATEGORY_ALIASES.get(arg_lower, arg_lower)
                    for idx, (t_type, val) in enumerate(resolved_order):
                        val_norm = CATEGORY_ALIASES.get(val.lower(), val.lower()) if val else ""
                        if t_type == "category" and val_norm == norm_arg:
                            if idx not in indices_to_reroll:
                                indices_to_reroll.append(idx)
                            found = True
                        elif t_type == "race" and arg_lower == "race":
                            if idx not in indices_to_reroll:
                                indices_to_reroll.append(idx)
                            found = True
                    if not found:
                        invalid_targets.append(arg)

        if invalid_targets:
            await _safe_delete(ctx)
            await ctx.send(
                f"{ctx.author.mention}, invalid reroll targets: `{', '.join(invalid_targets)}`.",
                delete_after=10
            )
            return

        # Perform reroll excluding other selected traits and current trait
        rerolled_any = False
        for idx in indices_to_reroll:
            t_type, val = resolved_order[idx]
            other_names = {results[i].get("name") for i in range(len(results)) if i != idx and results[i] and results[i].get("name")}
            current_name = results[idx].get("name") if results[idx] else None

            # First attempt: Exclude both other traits AND current trait to guarantee a distinct result
            exclude_names = other_names | ({current_name} if current_name else set())
            new_trait = self._select_trait(t_type, val, race, exclude_names)

            # Fallback: If no other trait exists in pool, permit re-drawing
            if not new_trait:
                new_trait = self._select_trait(t_type, val, race, other_names)

            if new_trait:
                results[idx] = new_trait
                rerolled_any = True

        if not rerolled_any:
            await _safe_delete(ctx)
            await ctx.send(
                f"{ctx.author.mention}, no other suitable traits available in pool to reroll.",
                delete_after=10
            )
            return

        # Update original message embed in-place
        embed = self._build_trait_embed(results, race, errors)
        original_footer = embed.footer.text if embed.footer else "Mineria RPG • Traits"

        reroll_labels = []
        if any(arg.lower().strip() == "all" for arg in cleaned_args):
            reroll_labels.append("All")
        else:
            for idx in sorted(indices_to_reroll):
                t_type, val = resolved_order[idx]
                reroll_labels.append(f"#{idx + 1} ({val.capitalize() if val else 'Race'})")

        reroll_label = "Rerolled " + " & ".join(reroll_labels)
        embed.set_footer(text=f"{original_footer} | {reroll_label}")

        try:
            if message and hasattr(message, "edit"):
                await message.edit(embed=embed)
            else:
                await ctx.send(embed=embed)
        except (discord.NotFound, discord.HTTPException, AttributeError):
            await _safe_delete(ctx)
            await ctx.send("Original trait message could not be edited. Please roll again with `!trait`.", delete_after=10)
            return

        # Update cached roll session
        self.last_rolls[user_id]["results"] = results
        self.last_rolls[user_id]["time"] = time.time()

        await _safe_delete(ctx)

    # =========================================================================
    # SECTION 3: SLASH APPLICATION COMMANDS & AUTOCOMPLETE
    # =========================================================================

    async def _trait_categories_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        standard = [
            "Combat", "Magic", "Faith", "Social", "Race", "Campaign", "Equipment", "Regional", "Religion", "Family", "Mount", "Plane", "Craft", "Nature", "Underworld", "Scholar", "Occult", "Tactic", "Urban"
        ]
        curr_lower = current.lower().strip()
        choices = []

        for cat in standard:
            aliases = [k for k, v in CATEGORY_ALIASES.items() if v == cat.lower()]
            if not curr_lower or curr_lower in cat.lower() or any(curr_lower in a for a in aliases):
                choices.append(app_commands.Choice(name=cat, value=cat.lower()))

        standard_lowers = {s.lower() for s in standard}
        for cat in sorted(self.traits_by_cat.keys()):
            canonical = CATEGORY_ALIASES.get(cat, cat)
            if canonical not in standard_lowers and cat not in standard_lowers:
                cap_cat = cat.capitalize()
                if not curr_lower or curr_lower in cat.lower():
                    choices.append(app_commands.Choice(name=cap_cat, value=cat.lower()))
                    if len(choices) >= 25:
                        break

        return choices[:25]

    async def _trait_race_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        try:
            races = await load_json("races.json")
            curr_lower = current.lower().strip()
            choices = []
            for rname in races.keys():
                if not curr_lower or curr_lower in rname.lower():
                    choices.append(app_commands.Choice(name=rname[:100], value=rname[:100]))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception:
            return []

    @app_commands.command(name="trait", description="Roll character traits for specified categories")
    @app_commands.describe(
        category1="First trait category (e.g. Combat, Magic, Social)",
        category2="Second trait category",
        category3="Third trait category",
        race="Specify race if rolling a Race trait (e.g. Human, Elf)"
    )
    async def slash_trait(
        self,
        interaction: discord.Interaction,
        category1: str,
        category2: Optional[str] = None,
        category3: Optional[str] = None,
        race: Optional[str] = None
    ) -> None:
        args = [category1]
        if category2:
            args.append(category2)
        if category3:
            args.append(category3)
        if race:
            args.append(f"race({race})")

        adapter = InteractionContextAdapter(interaction, self.bot)
        await self._execute_trait_roll(adapter, *args)

    @slash_trait.autocomplete("category1")
    async def slash_trait_cat1_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._trait_categories_autocomplete(interaction, current)

    @slash_trait.autocomplete("category2")
    async def slash_trait_cat2_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._trait_categories_autocomplete(interaction, current)

    @slash_trait.autocomplete("category3")
    async def slash_trait_cat3_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._trait_categories_autocomplete(interaction, current)

    @slash_trait.autocomplete("race")
    async def slash_trait_race_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._trait_race_autocomplete(interaction, current)

    @app_commands.command(name="trait_all", description="[Admin] List all traits in the specified category")
    @app_commands.describe(category="Trait category to list (e.g. Plane, Craft, Combat)")
    async def slash_trait_all(self, interaction: discord.Interaction, category: str) -> None:
        if not await self._is_admin(interaction.user):
            await interaction.response.send_message("Access Denied: This command is restricted to administrators.", ephemeral=True)
            return

        adapter = InteractionContextAdapter(interaction, self.bot)
        await self._send_all_category_traits(adapter, category)

    @slash_trait_all.autocomplete("category")
    async def slash_trait_all_cat_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._trait_categories_autocomplete(interaction, current)

    @app_commands.command(name="traits", description="List all available trait categories and system guide")
    async def slash_traits(self, interaction: discord.Interaction) -> None:
        embed = self.build_trait_help_embed(interaction.user)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Traits(bot))