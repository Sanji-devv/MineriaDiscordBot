import re
import copy
import time
import json
import random
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union
import discord
from discord import app_commands
from discord.ext import commands
import aiofiles
from log_handler import logger
from admin import DEVELOPER_ID, is_user_authorized

# =============================================================================
# SECTION 1: CORE CONCURRENCY, ATOMIC STORAGE & INTERACTION ADAPTER
# =============================================================================

DATA_DIR = Path(__file__).parent / "datas"
_JSON_CACHE: Dict[str, Tuple[float, Any]] = {}
_STATIC_CACHE: Dict[str, Any] = {}


class AsyncReentrantLock:
    """An asyncio reentrant lock allowing nested acquisitions within the same async task."""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._owner: Optional[asyncio.Task] = None
        self._count = 0

    async def acquire(self) -> None:
        me = asyncio.current_task()
        if self._owner == me:
            self._count += 1
            return
        await self._lock.acquire()
        self._owner = me
        self._count = 1

    async def release(self) -> None:
        me = asyncio.current_task()
        if self._owner != me:
            raise RuntimeError("Cannot release an un-acquired lock.")
        self._count -= 1
        if self._count == 0:
            self._owner = None
            self._lock.release()

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.release()


_FILE_LOCKS: Dict[str, AsyncReentrantLock] = {}


def get_file_lock(filename: str) -> AsyncReentrantLock:
    """Returns or creates a shared AsyncReentrantLock for the target JSON filename."""
    if filename not in _FILE_LOCKS:
        _FILE_LOCKS[filename] = AsyncReentrantLock()
    return _FILE_LOCKS[filename]


async def load_json(filename: str, force_reload: bool = False) -> Union[Dict, List, Any]:
    """
    Loads JSON data with concurrency-safe locking and in-memory caching.
    Uses static caching for immutable game rulebooks (races.json, classes.json).
    """
    # Fast-path for read-only static rule definitions
    if filename in ("races.json", "classes.json") and not force_reload and filename in _STATIC_CACHE:
        return _STATIC_CACHE[filename]

    lock = get_file_lock(filename)
    async with lock:
        if not force_reload and filename in _JSON_CACHE:
            return copy.deepcopy(_JSON_CACHE[filename][1])

        path = DATA_DIR / filename
        if not path.exists():
            return {}

        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content)
            _JSON_CACHE[filename] = (time.time(), copy.deepcopy(data))
            if filename in ("races.json", "classes.json"):
                _STATIC_CACHE[filename] = data
            return data
        except (json.JSONDecodeError, IOError) as exc:
            logger.error(f"Failed to load JSON file '{filename}': {exc}")
            raise


async def save_json(filename: str, data: Any) -> None:
    """
    Atomically writes data to a temporary file before renaming to target path.
    Prevents corrupt files during abrupt server restarts or process termination.
    """
    lock = get_file_lock(filename)
    async with lock:
        path = DATA_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        _JSON_CACHE[filename] = (time.time(), copy.deepcopy(data))

        # Write to temporary file first
        tmp_path = DATA_DIR / f"{filename}.tmp"
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, indent=4))

        # Atomic replacement
        tmp_path.replace(path)


def roll_stat_detailed(num_dice: int) -> Tuple[List[int], List[int]]:
    """
    Rolls N d6 dice, sorts in descending order, and keeps the highest 3 dice.
    Returns:
        (all_rolls, top_3_kept_rolls)
    """
    all_rolls = [random.randint(1, 6) for _ in range(num_dice)]
    kept_rolls = sorted(all_rolls, reverse=True)[:3]
    return all_rolls, kept_rolls


def get_recommendations(stats: Dict[str, int], classes: List[dict]) -> List[dict]:
    """
    Calculates class suitability scores based on rolled physical and mental attributes.
    Weights primary stats at 100% and secondary stats at 50%.
    """
    recommendations = []
    for cls in classes:
        primaries = cls.get("primary_stats", [])
        secondaries = cls.get("secondary_stats", [])

        p_vals = [stats.get(s, 10) for s in primaries] if primaries else []
        s_vals = [stats.get(s, 10) for s in secondaries] if secondaries else []

        p_score = (sum(p_vals) / len(p_vals)) if p_vals else 0.0
        s_score = (sum(s_vals) / len(s_vals) * 0.5) if s_vals else 0.0
        total_score = p_score + s_score
        variance = random.uniform(0.95, 1.05)  # Subtle variance to break exact ties naturally

        recommendations.append({
            "name": cls.get("name", "Unknown"),
            "score": total_score * variance
        })

    return sorted(recommendations, key=lambda x: x["score"], reverse=True)[:5]


class InteractionContextAdapter:
    """
    Adapts a discord.Interaction into a commands.Context compatible interface.
    Allows handler functions to support both prefix commands and slash interactions seamlessly.
    """

    def __init__(self, interaction: discord.Interaction, bot: Optional[commands.Bot] = None):
        self.interaction = interaction
        self.bot = bot or interaction.client
        self.author = interaction.user
        self.user = interaction.user
        self.guild = interaction.guild
        self.channel = interaction.channel
        self.message = getattr(interaction, "message", None)

    async def defer(self, ephemeral: bool = False) -> None:
        if not self.interaction.response.is_done():
            await self.interaction.response.defer(ephemeral=ephemeral)

    async def send(self, *args, **kwargs) -> Optional[discord.Message]:
        delete_after = kwargs.get("delete_after")
        if self.interaction.response.is_done():
            followup_kwargs = kwargs.copy()
            if "delete_after" in followup_kwargs:
                del followup_kwargs["delete_after"]
            if delete_after:
                followup_kwargs["wait"] = True
            msg = await self.interaction.followup.send(*args, **followup_kwargs)
            if delete_after and msg:
                async def _delayed_delete():
                    try:
                        await asyncio.sleep(delete_after)
                        await msg.delete()
                    except Exception:
                        pass
                asyncio.create_task(_delayed_delete())
            return msg
        else:
            await self.interaction.response.send_message(*args, **kwargs)
            try:
                return await self.interaction.original_response()
            except Exception:
                return None

    def typing(self):
        import contextlib

        @contextlib.asynccontextmanager
        async def _typing_manager():
            if not self.interaction.response.is_done():
                try:
                    await self.interaction.response.defer()
                except Exception:
                    pass
            if self.channel and hasattr(self.channel, "typing"):
                async with self.channel.typing():
                    yield
            else:
                yield

        return _typing_manager()


# =============================================================================
# SECTION 2: CHARACTER CREATION SESSION ENGINE
# =============================================================================

class BonusSelectView(discord.ui.View):
    """Interactive Discord UI View providing buttons to allocate flexible racial stat bonuses (+2 Any)."""

    def __init__(self, cog: Any, ctx: Any, creation: Dict[str, Any], roll_history: str, bonus_val: int):
        super().__init__(timeout=86400)  # Active for up to 24 hours
        self.cog = cog
        self.ctx = ctx
        self.creation = creation
        self.roll_history = roll_history
        self.bonus_val = bonus_val
        self.message: Optional[discord.Message] = None

        stats = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
        for stat in stats:
            btn = discord.ui.Button(label=f"+{bonus_val} {stat}", style=discord.ButtonStyle.secondary, custom_id=stat)
            btn.callback = self.make_callback(stat)
            self.add_item(btn)

    async def on_timeout(self) -> None:
        """Disables all bonus selection buttons when the session expires."""
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    def make_callback(self, stat: str):
        async def callback(interaction: discord.Interaction) -> None:
            # Prevent other users from clicking the author's bonus buttons
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message("This character creation session belongs to another player.", ephemeral=True)
                return

            if self.cog.active_creations.get(interaction.user.id) is not self.creation:
                await interaction.response.send_message("This character creation session is no longer active.", ephemeral=True)
                return

            if stat not in self.creation["stats"]:
                self.creation["stats"][stat] = 0
            self.creation["stats"][stat] += self.bonus_val

            # Highlight selected stat button and disable the rest
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    child.disabled = True
                    if child.custom_id == stat:
                        child.style = discord.ButtonStyle.success

            base_history = self.creation.get("stat_history", self.roll_history)
            new_history = base_history + f"\n**Flexible Bonus**: Applied **+{self.bonus_val} {stat}**"
            self.creation["stat_history"] = new_history

            racial_mods = parse_racial_modifiers(self.creation["race_data"]).copy()
            racial_mods["ANY"] = 0
            embed = generate_stat_embed(self.cog.bot, self.ctx, self.creation, new_history, racial_mods)

            try:
                await interaction.response.edit_message(embed=embed, view=self)
            except Exception as exc:
                logger.warning(f"Failed to update bonus selection embed: {exc}")
            self.stop()

        return callback


def parse_racial_modifiers(race_data: dict) -> Dict[str, int]:
    """
    Extracts fixed attribute bonuses and flexible modifiers from race definition.
    Example: Human gives flexible +2 Any; Dwarf gives +2 CON, +2 WIS, -2 CHA.
    """
    mods = {s: 0 for s in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]}

    # Structured modifiers format
    if "modifiers" in race_data:
        for k, v in race_data["modifiers"].items():
            if k in mods:
                mods[k] = v
        if race_data.get("flexible_stat", 0) > 0:
            mods["ANY"] = race_data["flexible_stat"]
        return mods

    # Unstructured text format parsing
    stat_map = {
        "Strength": "STR", "Dexterity": "DEX", "Constitution": "CON",
        "Intelligence": "INT", "Wisdom": "WIS", "Charisma": "CHA"
    }
    regex = r"([\+\-]\d+)\s*(\w+)"
    for key in ["Ability Score Plus", "Ability Score Minus"]:
        text = race_data.get(key, "")
        if text and text not in ["None", ""]:
            if "to one ability score" in text.lower():
                mods["ANY"] = 2
            for val, name in re.findall(regex, text):
                for full_name, short_code in stat_map.items():
                    if full_name.lower() in name.lower() or short_code.lower() == name.lower():
                        mods[short_code] += int(val)
                        break
    return mods


def generate_stat_embed(
    bot: commands.Bot, ctx: Any, creation: Dict[str, Any], rolls_text: str, racial_mods: Dict[str, int]
) -> discord.Embed:
    """Builds a rich overview embed of rolled attributes, modifiers, and racial traits."""
    author_name = getattr(ctx.author, "display_name", ctx.author.name)
    embed = discord.Embed(
        title="Stat Roll Results",
        description=f"Rolled by **{author_name}**\n\u200b\n" + "─" * 35,
        color=discord.Color.gold()
    )

    details_val = rolls_text
    if len(details_val) > 1020:
        details_val = details_val[:1015] + "..."
    embed.add_field(name="Details", value=details_val, inline=False)

    final_stats = creation["stats"]

    def fmt_stat_dr(label: str, key: str) -> str:
        val = final_stats.get(key, 10)
        mod = (val - 10) // 2
        sign = "+" if mod >= 0 else ""
        return f"**{label}**: {val} (`{sign}{mod}`)"

    col1_list = [fmt_stat_dr("STR", "STR"), fmt_stat_dr("DEX", "DEX"), fmt_stat_dr("CON", "CON")]
    col2_list = [fmt_stat_dr("INT", "INT"), fmt_stat_dr("WIS", "WIS"), fmt_stat_dr("CHA", "CHA")]

    embed.add_field(name="Physical", value="\n".join(col1_list), inline=True)
    embed.add_field(name="Mental", value="\n".join(col2_list), inline=True)

    plus = creation["race_data"].get("Ability Score Plus", "None")
    if "modifiers" in creation["race_data"]:
        mods = creation["race_data"]["modifiers"]
        mod_strs = [f"{('+' if v > 0 else '')}{v} {k}" for k, v in mods.items()]
        plus = ", ".join(mod_strs)

    adj_text = f"**Race**: {creation['race_name']}\n**Mods**: {plus}"
    if racial_mods.get("ANY"):
        adj_text += f"\n\n**Flexible Bonus Available!**\nClick a button below to apply +{racial_mods['ANY']}!"

    embed.add_field(name="Traits", value=adj_text, inline=False)

    avatar_url = bot.user.display_avatar.url if (bot.user and bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text="Use !char save <name> to finalize your character.", icon_url=avatar_url)
    else:
        embed.set_footer(text="Use !char save <name> to finalize your character.")
    return embed


async def handle_create(cog: Any, ctx: Any, race_name: Optional[str] = None) -> None:
    """Initiates character creation session for a chosen race (Admin Only)."""
    if ctx.author.id != DEVELOPER_ID and not await is_user_authorized(cog.bot, ctx.author):
        await ctx.send("This command is restricted to administrators.")
        return

    if not race_name:
        await ctx.send("Usage: `!char create <race>` (e.g. `!char create Human` or `!char create Half-Elf`)")
        return

    race_name = race_name.strip()
    races = await load_json("races.json")

    norm_input = race_name.lower().replace("-", " ").replace("_", " ").strip()
    target_race = next((r for r in races if r.lower() == race_name.lower()), None)
    if not target_race:
        target_race = next((r for r in races if r.lower().replace("-", " ").replace("_", " ").strip() == norm_input), None)

    if not target_race:
        await ctx.send(f"Race **{race_name}** not found in the campaign database.")
        return

    race_data = races[target_race]
    # Calculate dice points budget: 41 - Race Points (minimum 18)
    raw_dice_points = 41 - race_data.get("Race Points", 10)
    dice_points = max(18, raw_dice_points)

    cog.active_creations[ctx.author.id] = {
        "race_name": target_race,
        "race_data": race_data,
        "dice_points": dice_points,
        "stats": {}
    }

    author_name = getattr(ctx.author, "display_name", ctx.author.name)
    embed = discord.Embed(
        title=f"{target_race} Creation Session Started",
        description=f"**{author_name}**, your journey begins.",
        color=discord.Color.gold()
    )
    embed.add_field(name="Dice Points", value=f"**{dice_points}** points available.", inline=True)

    # Balanced distribution example
    avg = dice_points // 6
    rem = dice_points % 6
    ex_parts = [f"{k} {avg + rem if i == 5 else avg}" for i, k in enumerate(["STR", "DEX", "CON", "INT", "WIS", "CHA"])]
    example_cmd = " ".join(ex_parts)

    embed.add_field(name="Next Step", value=f"Distribute using `!char dr`.\nExample: `!char dr {example_cmd}`", inline=False)

    if ctx.author.display_avatar:
        embed.set_thumbnail(url=ctx.author.display_avatar.url)

    avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text="Mineria RPG • Creation Mode", icon_url=avatar_url)
    else:
        embed.set_footer(text="Mineria RPG • Creation Mode")

    await ctx.send(embed=embed)


async def handle_distribute(cog: Any, ctx: Any, *args: str) -> None:
    """Distributes allocated dice points across attributes and rolls stats."""
    user_id = ctx.author.id
    if user_id not in cog.active_creations:
        await ctx.send(embed=discord.Embed(
            title="No Character Creation Active",
            description="Use `!char create <race>` first to start character creation.",
            color=discord.Color.red()
        ))
        return

    creation = cog.active_creations[user_id]
    dice_points = creation["dice_points"]

    if not args:
        avg = dice_points // 6
        rem = dice_points % 6
        ex_parts = [f"{k} {avg + rem if i == 5 else avg}" for i, k in enumerate(["STR", "DEX", "CON", "INT", "WIS", "CHA"])]
        example_cmd = " ".join(ex_parts)

        embed = discord.Embed(
            title="Distribute Attribute Dice",
            description=f"You have **{dice_points}** dice points to distribute among 6 stats.\nMinimum **3**, Maximum **18** dice per stat.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Usage", value="`!char dr <STR> <val> <DEX> <val> ...` or `!char dr 6 6 6 6 6 10`", inline=False)
        embed.add_field(name="Example (Balanced)", value=f"`!char dr {example_cmd}`", inline=False)
        embed.set_footer(text="Tip: You can also pass 6 raw numbers: !char dr 6 6 6 6 6 10")
        await ctx.send(embed=embed)
        return

    flat_args: List[str] = []
    for a in args:
        for part in str(a).replace(",", " ").split():
            if part:
                flat_args.append(part)

    keys = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
    stats_to_set: Dict[str, int] = {}

    # Handle 6 sequential numbers shortcut (e.g. "6 6 6 6 6 10" -> STR:6, DEX:6, etc.)
    if len(flat_args) == 6 and all(a.isdigit() for a in flat_args):
        stats_to_set = dict(zip(keys, [int(a) for a in flat_args], strict=True))
    # Handle key-value pairs (e.g. "STR 10 DEX 6 ...")
    elif len(flat_args) == 12:
        for i in range(0, 12, 2):
            a1, a2 = flat_args[i], flat_args[i + 1]
            if a1.upper() in keys and a2.isdigit():
                stats_to_set[a1.upper()] = int(a2)
            elif a2.upper() in keys and a1.isdigit():
                stats_to_set[a2.upper()] = int(a1)
            else:
                await ctx.send(f"Invalid stat pair: **{a1} {a2}**.")
                return
        if len(stats_to_set) < 6:
            await ctx.send("Please provide values for all 6 unique attributes.")
            return
    else:
        embed = discord.Embed(title="Invalid Distribution Format", color=discord.Color.red())
        embed.description = f"Please provide exactly 6 numbers or 6 key-value pairs.\nTarget Total: **{dice_points}**"
        await ctx.send(embed=embed)
        return

    current_total = sum(stats_to_set.values())
    if current_total != creation["dice_points"]:
        diff = creation["dice_points"] - current_total
        status = "missing" if diff > 0 else "too many"
        await ctx.send(
            f"**Point Mismatch!**\n"
            f"Target: **{creation['dice_points']}** | Current Total: **{current_total}**\n"
            f"You have {status} **{abs(diff)}** dice."
        )
        return

    if any(v < 3 for v in stats_to_set.values()):
        await ctx.send("Each stat must be allocated at least **3** dice.")
        return
    if any(v > 18 for v in stats_to_set.values()):
        await ctx.send("Each stat can be allocated at most **18** dice.")
        return

    final_stats: Dict[str, int] = {}
    racial_mods = parse_racial_modifiers(creation["race_data"])
    rolls_text = ""

    for stat, num in stats_to_set.items():
        all_rolls, top_rolls = roll_stat_detailed(num)
        sorted_rolls = sorted(all_rolls, reverse=True)
        kept_part = sorted_rolls[:3]
        dropped_part = sorted_rolls[3:]

        base_total = sum(kept_part)
        mod = racial_mods.get(stat, 0)
        final_val = base_total + mod
        final_stats[stat] = final_val

        kept_formatted = [f"**{r}**" for r in kept_part]
        dropped_formatted = [f"~~{r}~~" for r in dropped_part]
        full_list_str = ", ".join(kept_formatted + dropped_formatted)
        mod_str = f" {'+' if mod >= 0 else '-'} {abs(mod)} (Race)" if mod != 0 else ""
        rolls_text += f"**{stat}**: [{full_list_str}] -> **{base_total}**{mod_str} = **{final_val}**\n"

    creation["stats"] = final_stats
    creation["stat_history"] = rolls_text
    embed_stats = generate_stat_embed(cog.bot, ctx, creation, rolls_text, racial_mods)

    # Attach interactive button view if flexible racial bonus exists (+2 Any)
    view = None
    if racial_mods.get("ANY") and racial_mods["ANY"] > 0:
        view = BonusSelectView(cog, ctx, creation, rolls_text, racial_mods["ANY"])

    sent_msg = await ctx.send(embed=embed_stats, view=view)
    if view:
        view.message = sent_msg

    # Class recommendations if enabled in user settings
    settings = await load_json("user_settings.json")
    user_settings = settings.get(str(ctx.author.id), {})

    if user_settings.get("show_recommendations", True):
        classes_data = await load_json("classes.json")
        classes = classes_data.get("classes", []) if isinstance(classes_data, dict) else []
        recommendations = get_recommendations(final_stats, classes)
        if recommendations:
            embed_recs = discord.Embed(
                title="Class Recommendations",
                description="\n".join([f"• **{r['name']}**" for r in recommendations]),
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed_recs)


async def _adjust_creation_stat(cog: Any, ctx: Any, args: Tuple[Any, ...], multiplier: int) -> None:
    """Helper to adjust an attribute stat value (+/-) during active character creation."""
    tokens = [str(a).strip().strip('"\'') for a in args if str(a).strip()]
    keys = ("STR", "DEX", "CON", "INT", "WIS", "CHA")
    action = "add" if multiplier > 0 else "remove"
    title = "Add Stat Bonus" if multiplier > 0 else "Remove Stat Bonus"

    stat = None
    value = None
    if len(tokens) == 2:
        t1, t2 = tokens[0].upper(), tokens[1].upper()
        if t1 in keys and (t2.lstrip("-+").isdigit()):
            stat, value = t1, int(t2)
        elif t2 in keys and (t1.lstrip("-+").isdigit()):
            stat, value = t2, int(t1)

    if not stat or value is None:
        embed = discord.Embed(title=title, color=discord.Color.blue())
        embed.description = f"Manually {action}s a value to a stat during creation."
        embed.add_field(name="Usage", value=f"`!char {action} <STAT> <VALUE>` or `!char {action} <VALUE> <STAT>`")
        embed.add_field(name="Example", value=f"`!char {action} STR 2` or `!char {action} 2 STR`")
        await ctx.send(embed=embed)
        return

    user_id = ctx.author.id
    if user_id not in cog.active_creations:
        await ctx.send("No active character creation session. Use `!char create` first.")
        return

    creation = cog.active_creations[user_id]
    if "stats" not in creation or not creation["stats"]:
        await ctx.send("Roll stats first with `!char dr`.")
        return

    if stat not in keys:
        await ctx.send("Invalid attribute name.")
        return

    creation["stats"][stat] += (value * multiplier)
    await ctx.send(f"**{stat}** updated to **{creation['stats'][stat]}**.")


async def handle_add_stat(cog: Any, ctx: Any, *args: str) -> None:
    """Adds bonus points to an attribute during active creation."""
    await _adjust_creation_stat(cog, ctx, args, multiplier=1)


async def handle_remove_stat(cog: Any, ctx: Any, *args: str) -> None:
    """Removes points from an attribute during active creation."""
    await _adjust_creation_stat(cog, ctx, args, multiplier=-1)


# =============================================================================
# SECTION 3: CHARACTER SAVING & USER PREFERENCES
# =============================================================================

async def handle_save_char(cog: Any, ctx: Any, *, name: Optional[str] = None) -> None:
    """Commits and finalizes active character creation session to characters.json."""
    user_id = ctx.author.id
    if user_id not in cog.active_creations or not cog.active_creations[user_id]["stats"]:
        await ctx.send("No pending character creation to save. Use `!char create` first.")
        return

    if not name:
        embed = discord.Embed(title="Save Character", color=discord.Color.blue())
        embed.description = "Finalizes your character creation and commits it to the database."
        embed.add_field(name="Usage", value="`!char save <Name>`")
        embed.add_field(name="Example", value="`!char save Valeros`")
        await ctx.send(embed=embed)
        return

    name = name.strip().strip('"\'')
    if not name:
        await ctx.send("Invalid character name provided.")
        return

    creation = cog.active_creations[user_id]
    uid = str(user_id)

    async with get_file_lock("characters.json"):
        characters = await load_json("characters.json", force_reload=True)
        if uid not in characters:
            characters[uid] = []

        # Prevent duplicate character names under the same account
        if any(c["name"].lower() == name.lower() for c in characters[uid]):
            await ctx.send(f"You already have a character named **{name}**.")
            return

        created_time = ""
        if hasattr(ctx, "message") and ctx.message:
            created_time = str(ctx.message.created_at)
        elif hasattr(ctx, "interaction") and ctx.interaction:
            created_time = str(ctx.interaction.created_at)
        else:
            created_time = str(datetime.now(timezone.utc))

        new_char = {
            "name": name,
            "race": creation["race_name"],
            "class": "None",
            "stats": creation["stats"],
            "created_at": created_time
        }

        if "stat_history" in creation:
            new_char["stat_history"] = creation["stat_history"]

        characters[uid].append(new_char)
        await save_json("characters.json", characters)

    del cog.active_creations[user_id]

    embed = discord.Embed(
        title="Character Saved",
        description=f"**{name}** ({creation['race_name']}) has been successfully saved to your roster!",
        color=discord.Color.green()
    )
    avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text="Mineria RPG • Saved", icon_url=avatar_url)
    else:
        embed.set_footer(text="Mineria RPG • Saved")

    await ctx.send(embed=embed)


async def handle_rec(cog: Any, ctx: Any) -> None:
    """Displays recommendation toggle options."""
    embed = discord.Embed(title="Recommendation Settings", color=discord.Color.blue())
    embed.description = "Toggle automatic class recommendations during character creation."
    embed.add_field(name="Commands", value="`!rec open` - Enable\n`!rec close` - Disable")
    await ctx.send(embed=embed)


async def handle_rec_open(cog: Any, ctx: Any) -> None:
    """Enables class recommendations for user."""
    uid = str(ctx.author.id)
    async with get_file_lock("user_settings.json"):
        settings = await load_json("user_settings.json", force_reload=True)
        if uid not in settings:
            settings[uid] = {}
        settings[uid]["show_recommendations"] = True
        await save_json("user_settings.json", settings)
    await ctx.send("Class recommendations **Enabled**.")


async def handle_rec_close(cog: Any, ctx: Any) -> None:
    """Disables class recommendations for user."""
    uid = str(ctx.author.id)
    async with get_file_lock("user_settings.json"):
        settings = await load_json("user_settings.json", force_reload=True)
        if uid not in settings:
            settings[uid] = {}
        settings[uid]["show_recommendations"] = False
        await save_json("user_settings.json", settings)
    await ctx.send("Class recommendations **Disabled**.")


# =============================================================================
# SECTION 4: CHARACTER ROSTER MANAGEMENT & MODIFICATIONS
# =============================================================================

async def handle_edit(cog: Any, ctx: Any) -> None:
    """Shows character editing help menu."""
    embed = discord.Embed(title="Edit Character", color=discord.Color.blue())
    embed.description = "Modify an existing character's data."
    embed.add_field(name="Class", value="`!char edit class <Name> <NewClass>`")
    embed.add_field(name="Stats", value="`!char edit stat <Name> <Stat> <NewValue>`")
    await ctx.send(embed=embed)


async def handle_edit_class(cog: Any, ctx: Any, *args: str) -> None:
    """Edits a saved character's class."""
    if not args:
        embed = discord.Embed(title="Edit Class", color=discord.Color.blue())
        embed.description = "Modify a character's assigned class."
        embed.add_field(name="Usage", value="`!char edit class <Name> <NewClass>`")
        embed.add_field(name="Example", value='`!char edit class "Kiros Enuma" Guardian`')
        await ctx.send(embed=embed)
        return

    full_input = " ".join(args).strip()
    uid = str(ctx.author.id)

    async with get_file_lock("characters.json"):
        characters = await load_json("characters.json", force_reload=True)
        if uid not in characters or not characters[uid]:
            await ctx.send("You don't have any saved characters.")
            return

        matched_char = None
        new_class = ""
        user_chars = characters[uid]

        # Direct exact match for 2 arguments (e.g. from slash command or clean parameters)
        if len(args) == 2:
            name_guess = args[0].strip('"\',')
            matched = next((c for c in user_chars if c["name"].lower() == name_guess.lower()), None)
            if matched:
                matched_char = matched
                new_class = args[1].strip()

        if not matched_char:
            # Greedy prefix match for multi-word names
            for c in sorted(user_chars, key=lambda x: len(x["name"]), reverse=True):
                c_name = c["name"]
                if full_input.lower().startswith(c_name.lower()):
                    matched_char = c
                    new_class = full_input[len(c_name):].strip()
                    break

        if not matched_char:
            if len(args) >= 2:
                name_guess = args[0].strip('"\',')
                new_class = " ".join(args[1:]).strip()
                matched_char = next((c for c in user_chars if c["name"].lower() == name_guess.lower()), None)

        if not matched_char or not new_class:
            await ctx.send("Could not match character or missing new class.\nUsage: `!char edit class <Name> <NewClass>`")
            return

        old_class = matched_char.get("class", "None")
        matched_char["class"] = new_class
        await save_json("characters.json", characters)

    embed = discord.Embed(
        title="Class Updated",
        description=f"**{matched_char['name']}**: {old_class} -> **{new_class}**",
        color=discord.Color.green()
    )
    avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text="Mineria RPG • Character Management", icon_url=avatar_url)
    else:
        embed.set_footer(text="Mineria RPG • Character Management")

    await ctx.send(embed=embed)


async def handle_edit_stat(cog: Any, ctx: Any, *args: str) -> None:
    """Modifies a specific stat value for a saved character."""
    if not args:
        embed = discord.Embed(title="Edit Stat", color=discord.Color.blue())
        embed.description = "Modify a character's attribute score directly."
        embed.add_field(name="Usage", value="`!char edit stat <Name> <Stat> <Value>`")
        embed.add_field(name="Example", value='`!char edit stat "Kiros Enuma" STR 18`')
        await ctx.send(embed=embed)
        return

    tokens = list(args)
    valid_stats = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}

    char_name = None
    stat = None
    value = None

    if len(tokens) >= 3 and tokens[-2].upper() in valid_stats and tokens[-1].lstrip("-+").isdigit():
        char_name = " ".join(tokens[:-2]).strip('"\',')
        stat = tokens[-2].upper()
        value = int(tokens[-1])
    elif len(tokens) >= 3 and tokens[-1].upper() in valid_stats and tokens[-2].lstrip("-+").isdigit():
        char_name = " ".join(tokens[:-2]).strip('"\',')
        stat = tokens[-1].upper()
        value = int(tokens[-2])
    else:
        await ctx.send("Invalid format.\nUsage: `!char edit stat <Name> <Stat> <Value>` (e.g. `!char edit stat Kiros STR 18`)")
        return

    uid = str(ctx.author.id)
    async with get_file_lock("characters.json"):
        characters = await load_json("characters.json", force_reload=True)
        if uid not in characters or not characters[uid]:
            await ctx.send("You don't have any saved characters.")
            return

        char_data = next((c for c in characters[uid] if c["name"].lower() == char_name.lower()), None)
        if not char_data:
            await ctx.send(f"Character **{char_name}** not found.")
            return

        old_val = char_data["stats"].get(stat, 0)
        char_data["stats"][stat] = value
        await save_json("characters.json", characters)

    embed = discord.Embed(
        title="Stat Updated",
        description=f"**{char_data['name']}** {stat}: {old_val} -> **{value}**",
        color=discord.Color.green()
    )
    avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text="Mineria RPG • Character Management", icon_url=avatar_url)
    else:
        embed.set_footer(text="Mineria RPG • Character Management")

    await ctx.send(embed=embed)


async def handle_info(cog: Any, ctx: Any, *, name: Optional[str] = None) -> None:
    """Displays detailed character sheet with attributes, class, and modifiers."""
    characters = await load_json("characters.json")
    uid = str(ctx.author.id)
    user_chars = characters.get(uid, [])

    if not user_chars:
        await ctx.send("You don't have any saved characters.")
        return

    char_data = None
    if name is None:
        if len(user_chars) == 1:
            char_data = user_chars[0]
        else:
            char_list = "\n".join([f"• `{c['name']}`" for c in user_chars])
            if len(char_list) > 3000:
                truncated = char_list[:3000]
                lines = truncated.splitlines()
                if len(lines) > 1:
                    lines.pop()
                char_list = "\n".join(lines) + f"\n*... and {len(user_chars) - len(lines)} more.*"
            embed = discord.Embed(
                title="Multiple Characters Found",
                description=f"Use `!char info <name>` to see details.\n\n**Your Characters:**\n{char_list}",
                color=discord.Color.gold()
            )
            await ctx.send(embed=embed)
            return
    else:
        name = name.strip().strip('"\'')
        char_data = next((c for c in user_chars if c["name"].lower() == name.lower()), None)

    if not char_data:
        await ctx.send(f"Character **{name}** not found.")
        return

    char_class = char_data.get("class", "Adventurer")
    if char_class == "None":
        char_class = "Adventurer"

    embed = discord.Embed(
        title=f"{char_data['name']}",
        description=f"**{char_data['race']}** • **{char_class}**",
        color=discord.Color.gold()
    )

    if "stat_history" in char_data:
        history_val = str(char_data["stat_history"])
        if len(history_val) > 1020:
            history_val = history_val[:1015] + "..."
        embed.add_field(name="Stats History", value=history_val, inline=False)

    stats = char_data.get("stats") or {}

    def fmt_stat(label: str, key: str) -> str:
        val = stats.get(key, 10)
        mod = (val - 10) // 2
        sign = "+" if mod >= 0 else ""
        return f"**{label}**: {val} (`{sign}{mod}`)"

    col1 = [fmt_stat("STR", "STR"), fmt_stat("DEX", "DEX"), fmt_stat("CON", "CON")]
    col2 = [fmt_stat("INT", "INT"), fmt_stat("WIS", "WIS"), fmt_stat("CHA", "CHA")]

    embed.add_field(name="Physical", value="\n".join(col1), inline=True)
    embed.add_field(name="Mental", value="\n".join(col2), inline=True)

    feats = char_data.get("feats") or {}
    if feats:
        feat_lines = []
        for slot, feat in feats.items():
            short_slot = str(slot).replace("Level", "Lvl").replace("Bonus Feat", "Bonus")
            feat_lines.append(f"• **{short_slot}**: {feat}")

        feats_text = "\n".join(feat_lines)
        if len(feats_text) > 1000:
            feats_text = feats_text[:990] + "..."
        embed.add_field(name="Known Feats", value=feats_text, inline=False)

    created_at_raw = str(char_data.get("created_at") or "")
    created_at = created_at_raw.split(" ")[0] if created_at_raw else "N/A"
    footer_text = f"Mineria RPG • Created: {created_at}"

    avatar_url = ctx.bot.user.display_avatar.url if (ctx.bot.user and ctx.bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text=footer_text, icon_url=avatar_url)
    else:
        embed.set_footer(text=footer_text)

    if ctx.author.display_avatar:
        embed.set_thumbnail(url=ctx.author.display_avatar.url)

    await ctx.send(embed=embed)


async def handle_list_chars(cog: Any, ctx: Any) -> None:
    """Lists all saved characters belonging to the user."""
    characters = await load_json("characters.json")
    user_chars = characters.get(str(ctx.author.id), [])

    if not user_chars:
        await ctx.send("You don't have any saved characters.")
        return

    embed = discord.Embed(title="Your Characters", color=discord.Color.gold())
    names = "\n".join([f"• **{c['name']}** ({c['race']} {c.get('class', 'None')})" for c in user_chars])
    if len(names) > 4000:
        truncated = names[:3900]
        lines = truncated.splitlines()
        if len(lines) > 1 and not names[len(truncated):].startswith("\n"):
            lines.pop()
        names = "\n".join(lines) + f"\n\n*... and {len(user_chars) - len(lines)} more characters.*"

    embed.description = names
    avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text="Mineria RPG • Roster", icon_url=avatar_url)
    else:
        embed.set_footer(text="Mineria RPG • Roster")

    if ctx.author.display_avatar:
        embed.set_thumbnail(url=ctx.author.display_avatar.url)

    await ctx.send(embed=embed)


async def handle_rename(cog: Any, ctx: Any, *args: str) -> None:
    """Renames an existing character."""
    if not args:
        embed = discord.Embed(title="Rename Character", color=discord.Color.blue())
        embed.description = "Change the name of one of your characters."
        embed.add_field(name="Usage", value='`!char rename <OldName> <NewName>`\n`!char rename "Old Name" "New Name"`')
        embed.add_field(name="Example", value='`!char rename "Kiros Enuma" "Kiros Prime"`')
        await ctx.send(embed=embed)
        return

    uid = str(ctx.author.id)
    async with get_file_lock("characters.json"):
        characters = await load_json("characters.json", force_reload=True)
        if uid not in characters or not characters[uid]:
            await ctx.send("No characters found.")
            return

        full_input = " ".join(args).strip()
        user_chars = characters[uid]
        old_char = None
        new_name = None

        # Handle arrow syntax (e.g. "Old Name -> New Name")
        if "->" in full_input:
            parts = full_input.split("->", 1)
            old_guess = parts[0].strip().strip('"\'')
            new_name = parts[1].strip().strip('"\'')
            old_char = next((c for c in user_chars if c["name"].lower() == old_guess.lower()), None)
        else:
            for c in sorted(user_chars, key=lambda x: len(x["name"]), reverse=True):
                c_name = c["name"]
                if full_input.lower().startswith(c_name.lower()):
                    old_char = c
                    new_name = full_input[len(c_name):].strip().strip('"\'')
                    break

            if not old_char and len(args) >= 2:
                old_guess = args[0].strip('"\'')
                new_name = " ".join(args[1:]).strip().strip('"\'')
                old_char = next((c for c in user_chars if c["name"].lower() == old_guess.lower()), None)

        if not old_char or not new_name:
            await ctx.send('Usage: `!char rename <OldName> <NewName>` (e.g. `!char rename "Old Name" "New Name"`)')
            return

        old_name = old_char["name"]
        if any(c["name"].lower() == new_name.lower() and c is not old_char for c in user_chars):
            await ctx.send(f"You already have a character named **{new_name}**.")
            return

        old_char["name"] = new_name
        await save_json("characters.json", characters)

    embed = discord.Embed(
        title="Character Renamed",
        description=f"Character **{old_name}** renamed to **{new_name}**.",
        color=discord.Color.orange()
    )
    avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
    if avatar_url:
        embed.set_footer(text="Mineria RPG • Character Management", icon_url=avatar_url)
    else:
        embed.set_footer(text="Mineria RPG • Character Management")

    await ctx.send(embed=embed)


async def handle_delete_char(cog: Any, ctx: Any, *, name: Optional[str] = None) -> None:
    """Permanently deletes a character from the user's roster."""
    if not name:
        embed = discord.Embed(title="Delete Character", color=discord.Color.red())
        embed.description = "Permanently delete a character from your roster."
        embed.add_field(name="Usage", value="`!char delete <Name>`")
        await ctx.send(embed=embed)
        return

    name = name.strip().strip('"\'')
    if not name:
        await ctx.send("Invalid character name.")
        return

    uid = str(ctx.author.id)
    async with get_file_lock("characters.json"):
        characters = await load_json("characters.json", force_reload=True)
        if uid not in characters or not characters[uid]:
            await ctx.send("You don't have any characters to delete.")
            return

        original_count = len(characters[uid])
        characters[uid] = [c for c in characters[uid] if c["name"].lower() != name.lower()]

        if len(characters[uid]) == original_count:
            await ctx.send(f"Character **{name}** not found in your roster.")
            return

        await save_json("characters.json", characters)

    embed = discord.Embed(
        title="Character Deleted",
        description=f"Character **{name}** has been permanently removed.",
        color=discord.Color.red()
    )
    await ctx.send(embed=embed)


# =============================================================================
# SECTION 5: DISCORD COG & SLASH APPLICATION COMMAND REGISTRATION
# =============================================================================

class CharacterCog(commands.Cog, name="Character"):
    """Consolidated Character Creation, Management, and Stat Engine."""

    char_group = app_commands.Group(name="char", description="Character creation and management commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_creations: Dict[int, Dict[str, Any]] = {}

    def parse_racial_modifiers(self, race_data: dict) -> Dict[str, int]:
        return parse_racial_modifiers(race_data)

    def generate_stat_embed(self, ctx: Any, creation: Dict[str, Any], rolls_text: str, racial_mods: Dict[str, int]) -> discord.Embed:
        return generate_stat_embed(self.bot, ctx, creation, rolls_text, racial_mods)

    # ─────────────────────────────────────────────
    # Prefix Commands: !char
    # ─────────────────────────────────────────────

    @commands.group(name="char", invoke_without_command=True)
    async def char(self, ctx: commands.Context) -> None:
        """Root command for Character Management."""
        embed = discord.Embed(title="Character Commands", color=discord.Color.gold())
        embed.description = (
            "**Creation & Recovery**\n"
            "`!char create <race>` - Start creation (Admin Only)\n"
            "`!char dr <stats>` - Distribute dice\n"
            "`!char add/remove <stat> <val>` - Tweak stats\n"
            "`!char save <name>` - Finalize character\n"
            "`!char kia <name>` - Calculate starting XP for fallen character\n"
            "`!char mia <name>` - Calculate starting XP for missing character\n\n"
            "**Management**\n"
            "`!char info [name]` - View character\n"
            "`!char list` - List your characters\n"
            "`!char rename <old> <new>` - Rename\n"
            "`!char delete <name>` - Delete\n\n"
            "**Editing**\n"
            "`!char edit class <name> <class>`\n"
            "`!char edit stat <name> <stat> <val>`"
        )
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Character System", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Character System")
        await ctx.send(embed=embed)

    @char.command(name="create")
    async def create(self, ctx: commands.Context, *, race_name: Optional[str] = None) -> None:
        await handle_create(self, ctx, race_name)

    @char.command(name="dr")
    async def distribute(self, ctx: commands.Context, *args: str) -> None:
        await handle_distribute(self, ctx, *args)

    @char.command(name="add")
    async def add_stat(self, ctx: commands.Context, *args: str) -> None:
        await handle_add_stat(self, ctx, *args)

    @char.command(name="remove")
    async def remove_stat(self, ctx: commands.Context, *args: str) -> None:
        await handle_remove_stat(self, ctx, *args)

    @char.command(name="save")
    async def save_char(self, ctx: commands.Context, *, name: Optional[str] = None) -> None:
        await handle_save_char(self, ctx, name=name)

    @char.command(name="kia")
    async def kia(self, ctx: commands.Context, *, char_name: str) -> None:
        """Calculates dead character's starting XP (50% task XP + fixed XP)."""
        cog = self.bot.get_cog("Utility")
        if cog:
            await cog.fetch_and_calculate_xp(ctx, char_name, 0.5, "KIA XP", discord.Color.dark_red())
        else:
            await ctx.send("Sheet utility is unavailable.")

    @char.command(name="mia")
    async def mia(self, ctx: commands.Context, *, char_name: str) -> None:
        """Calculates missing character's starting XP (90% task XP + fixed XP)."""
        cog = self.bot.get_cog("Utility")
        if cog:
            await cog.fetch_and_calculate_xp(ctx, char_name, 0.9, "MIA XP", discord.Color.gold())
        else:
            await ctx.send("Sheet utility is unavailable.")

    @commands.group(name="rec", invoke_without_command=True)
    async def rec(self, ctx: commands.Context) -> None:
        await handle_rec(self, ctx)

    @rec.command(name="open")
    async def rec_open(self, ctx: commands.Context) -> None:
        await handle_rec_open(self, ctx)

    @rec.command(name="close")
    async def rec_close(self, ctx: commands.Context) -> None:
        await handle_rec_close(self, ctx)

    @char.group(name="edit", invoke_without_command=True)
    async def edit(self, ctx: commands.Context) -> None:
        await handle_edit(self, ctx)

    @edit.command(name="class")
    async def edit_class(self, ctx: commands.Context, *args: str) -> None:
        await handle_edit_class(self, ctx, *args)

    @edit.command(name="stat")
    async def edit_stat(self, ctx: commands.Context, *args: str) -> None:
        await handle_edit_stat(self, ctx, *args)

    @char.command(name="info")
    async def info(self, ctx: commands.Context, *, name: Optional[str] = None) -> None:
        await handle_info(self, ctx, name=name)

    @char.command(name="list")
    async def list_chars(self, ctx: commands.Context) -> None:
        await handle_list_chars(self, ctx)

    @char.command(name="rename")
    async def rename(self, ctx: commands.Context, *args: str) -> None:
        await handle_rename(self, ctx, *args)

    @char.command(name="delete")
    async def delete_char(self, ctx: commands.Context, *, name: Optional[str] = None) -> None:
        await handle_delete_char(self, ctx, name=name)

    # ─────────────────────────────────────────────
    # Slash Commands & Autocomplete: /char
    # ─────────────────────────────────────────────

    async def _user_characters_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        try:
            characters = await load_json("characters.json")
            uid = str(interaction.user.id)
            user_chars = characters.get(uid, [])
            choices = []
            curr_lower = current.lower().strip()
            for c in user_chars:
                cname = c.get("name", "")
                if not curr_lower or curr_lower in cname.lower():
                    race = c.get("race", "")
                    char_class = c.get("class", "Adventurer")
                    label = f"{cname} ({race} {char_class})".strip()
                    if len(label) > 100:
                        label = label[:97] + "..."
                    choices.append(app_commands.Choice(name=label, value=cname))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception as exc:
            logger.error(f"Error in user character autocomplete: {exc}")
            return []

    async def _classes_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        try:
            classes_data = await load_json("classes.json")
            class_list = (
                classes_data.get("classes", [])
                if isinstance(classes_data, dict)
                else (classes_data if isinstance(classes_data, list) else [])
            )
            choices = []
            curr_lower = current.lower().strip()
            for item in class_list:
                cname = item.get("name") if isinstance(item, dict) else str(item)
                if not cname:
                    continue
                if not curr_lower or curr_lower in cname.lower():
                    choices.append(app_commands.Choice(name=cname[:100], value=cname[:100]))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception as exc:
            logger.error(f"Error in classes autocomplete: {exc}")
            return []

    @char_group.command(name="create", description="Start creating a new character for a race (Admin Only)")
    @app_commands.describe(race="The race of your character (e.g. Human, Elf, Dwarf)")
    async def slash_create(self, interaction: discord.Interaction, race: str) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_create(self, adapter, race_name=race)

    @slash_create.autocomplete("race")
    async def slash_create_race_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        try:
            races = await load_json("races.json")
            choices = []
            curr_lower = current.lower().strip()
            for rname, rdata in races.items():
                if not curr_lower or curr_lower in rname.lower():
                    rp = rdata.get("Race Points", 10)
                    mods = rdata.get("modifiers", {})
                    flex = rdata.get("flexible_stat", 0)
                    parts = []
                    for k, v in mods.items():
                        sign = "+" if v > 0 else ""
                        parts.append(f"{sign}{v} {k}")
                    if flex > 0:
                        parts.append(f"+{flex} Any")
                    m_str = ", ".join(parts) if parts else "No Mod"
                    label = f"{rname} (RP {rp} | {m_str})"
                    if len(label) > 100:
                        label = label[:97] + "..."
                    choices.append(app_commands.Choice(name=label, value=rname))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception as exc:
            logger.error(f"Error in race autocomplete: {exc}")
            return []

    @char_group.command(name="info", description="View detailed character sheet and statistics")
    @app_commands.describe(name="Name of your saved character")
    async def slash_info(self, interaction: discord.Interaction, name: Optional[str] = None) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_info(self, adapter, name=name)

    @slash_info.autocomplete("name")
    async def slash_info_name_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @char_group.command(name="list", description="List all characters you have created")
    async def slash_list(self, interaction: discord.Interaction) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_list_chars(self, adapter)

    @char_group.command(name="edit_class", description="Change a saved character's class")
    @app_commands.describe(name="Name of your character", new_class="The new class to assign")
    async def slash_edit_class(self, interaction: discord.Interaction, name: str, new_class: str) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_edit_class(self, adapter, name, new_class)

    @slash_edit_class.autocomplete("name")
    async def slash_edit_class_name_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @slash_edit_class.autocomplete("new_class")
    async def slash_edit_class_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._classes_autocomplete(interaction, current)

    @char_group.command(name="edit_stat", description="Modify a specific stat for a saved character")
    @app_commands.describe(name="Name of your character", stat="The attribute to modify", new_value="The new score value")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Strength (STR)", value="STR"),
        app_commands.Choice(name="Dexterity (DEX)", value="DEX"),
        app_commands.Choice(name="Constitution (CON)", value="CON"),
        app_commands.Choice(name="Intelligence (INT)", value="INT"),
        app_commands.Choice(name="Wisdom (WIS)", value="WIS"),
        app_commands.Choice(name="Charisma (CHA)", value="CHA"),
    ])
    async def slash_edit_stat(self, interaction: discord.Interaction, name: str, stat: str, new_value: int) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_edit_stat(self, adapter, name, stat, str(new_value))

    @slash_edit_stat.autocomplete("name")
    async def slash_edit_stat_name_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @char_group.command(name="distribute", description="Distribute dice points to attributes (e.g. 6 6 6 6 6 10)")
    @app_commands.describe(distribution="Dice distribution values (e.g. '6 6 6 6 6 10' or 'STR 10 DEX 6 ...')")
    async def slash_distribute(self, interaction: discord.Interaction, distribution: str) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        args = distribution.split()
        await handle_distribute(self, adapter, *args)

    @char_group.command(name="add", description="Add bonus points to a stat during character creation")
    @app_commands.describe(stat="Attribute name", value="Bonus value to add")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Strength (STR)", value="STR"),
        app_commands.Choice(name="Dexterity (DEX)", value="DEX"),
        app_commands.Choice(name="Constitution (CON)", value="CON"),
        app_commands.Choice(name="Intelligence (INT)", value="INT"),
        app_commands.Choice(name="Wisdom (WIS)", value="WIS"),
        app_commands.Choice(name="Charisma (CHA)", value="CHA"),
    ])
    async def slash_add(self, interaction: discord.Interaction, stat: str, value: int) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_add_stat(self, adapter, stat, str(value))

    @char_group.command(name="remove", description="Remove points from a stat during character creation")
    @app_commands.describe(stat="Attribute name", value="Points value to remove")
    @app_commands.choices(stat=[
        app_commands.Choice(name="Strength (STR)", value="STR"),
        app_commands.Choice(name="Dexterity (DEX)", value="DEX"),
        app_commands.Choice(name="Constitution (CON)", value="CON"),
        app_commands.Choice(name="Intelligence (INT)", value="INT"),
        app_commands.Choice(name="Wisdom (WIS)", value="WIS"),
        app_commands.Choice(name="Charisma (CHA)", value="CHA"),
    ])
    async def slash_remove(self, interaction: discord.Interaction, stat: str, value: int) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_remove_stat(self, adapter, stat, str(value))

    @char_group.command(name="save", description="Finalize and save your created character")
    @app_commands.describe(name="Name for your character")
    async def slash_save(self, interaction: discord.Interaction, name: str) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_save_char(self, adapter, name=name)

    @char_group.command(name="rename", description="Rename an existing character")
    @app_commands.describe(current_name="Current name of your character", new_name="New name for your character")
    async def slash_rename(self, interaction: discord.Interaction, current_name: str, new_name: str) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_rename(self, adapter, current_name, "->", new_name)

    @slash_rename.autocomplete("current_name")
    async def slash_rename_name_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @char_group.command(name="delete", description="Delete a saved character permanently")
    @app_commands.describe(name="Name of your character to delete")
    async def slash_delete(self, interaction: discord.Interaction, name: str) -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_delete_char(self, adapter, name=name)

    @slash_delete.autocomplete("name")
    async def slash_delete_name_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @char_group.command(name="kia", description="Calculate fallen character's starting XP")
    @app_commands.describe(char_name="Name of the fallen character")
    async def slash_char_kia(self, interaction: discord.Interaction, char_name: str) -> None:
        cog = self.bot.get_cog("Utility")
        if cog:
            await interaction.response.defer()
            adapter = InteractionContextAdapter(interaction, self.bot)
            await cog.fetch_and_calculate_xp(adapter, char_name, 0.5, "KIA XP", discord.Color.dark_red())
        else:
            await interaction.response.send_message("Sheet utility is unavailable.", ephemeral=True)

    @slash_char_kia.autocomplete("char_name")
    async def slash_char_kia_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        cog = self.bot.get_cog("Utility")
        if cog:
            return await cog._kia_character_autocomplete(interaction, current)
        return []

    @char_group.command(name="mia", description="Calculate missing character's starting XP")
    @app_commands.describe(char_name="Name of the missing character")
    async def slash_char_mia(self, interaction: discord.Interaction, char_name: str) -> None:
        cog = self.bot.get_cog("Utility")
        if cog:
            await interaction.response.defer()
            adapter = InteractionContextAdapter(interaction, self.bot)
            await cog.fetch_and_calculate_xp(adapter, char_name, 0.9, "MIA XP", discord.Color.gold())
        else:
            await interaction.response.send_message("Sheet utility is unavailable.", ephemeral=True)

    @slash_char_mia.autocomplete("char_name")
    async def slash_char_mia_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        cog = self.bot.get_cog("Utility")
        if cog:
            return await cog._kia_character_autocomplete(interaction, current)
        return []

    @app_commands.command(name="rec", description="Toggle automatic class recommendations during character creation")
    @app_commands.describe(mode="Enable (open) or disable (close) recommendations")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Enable Recommendations", value="open"),
        app_commands.Choice(name="Disable Recommendations", value="close")
    ])
    async def slash_rec(self, interaction: discord.Interaction, mode: str = "toggle") -> None:
        adapter = InteractionContextAdapter(interaction, self.bot)
        if mode == "open":
            await handle_rec_open(self, adapter)
        elif mode == "close":
            await handle_rec_close(self, adapter)
        else:
            await handle_rec(self, adapter)


async def setup(bot: commands.Bot) -> None:
    """Extension entry point for loading the Character Cog."""
    await bot.add_cog(CharacterCog(bot))