import io
import copy
import time
import uuid
import json
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple, Union

import discord
from discord import app_commands
from discord.ext import commands

import aiofiles
from log_handler import logger

# Configure headless backend for matplotlib before importing pyplot
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# =============================================================================
# SECTION 1: CONSTANTS, DATA PATHS & DIRECTORY CREATION
# =============================================================================

STAT_KEYS: List[str] = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]

STAT_COLORS: Dict[str, str] = {
    "STR": "#E74C3C",  # Vibrant Red
    "DEX": "#2ECC71",  # Emerald Green
    "CON": "#E67E22",  # Fiery Orange
    "INT": "#3498DB",  # Astral Blue
    "WIS": "#9B59B6",  # Mystical Purple
    "CHA": "#F1C40F",  # Radiant Gold
}

# Resolve storage directory supporting both data/ and datas/
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "datas" if (BASE_DIR / "datas").exists() else BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Primary stats database path
STATS_FILE = DATA_DIR / "stats.json"

# Directory dedicated to saved charts and graphical plots
GRAPH_DIR = DATA_DIR / "graph"
GRAPH_DIR.mkdir(parents=True, exist_ok=True)

_FILE_LOCK = asyncio.Lock()
_CACHE: Optional[List[Dict[str, Any]]] = None


# =============================================================================
# SECTION 2: ATOMIC STORAGE ENGINE & MIGRATION
# =============================================================================

async def load_stats(force_reload: bool = False) -> List[Dict[str, Any]]:
    global _CACHE
    if not force_reload and _CACHE is not None:
        return copy.deepcopy(_CACHE)

    async with _FILE_LOCK:
        # Check for legacy dr_stats.json to seamlessly migrate
        legacy_file = DATA_DIR / "dr_stats.json"
        if not STATS_FILE.exists() and legacy_file.exists():
            try:
                legacy_file.rename(STATS_FILE)
            except Exception:
                pass

        if not STATS_FILE.exists():
            _CACHE = []
            return []

        try:
            async with aiofiles.open(STATS_FILE, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)
                if isinstance(data, list):
                    _CACHE = data
                elif isinstance(data, dict) and "rolls" in data:
                    _CACHE = data["rolls"]
                else:
                    _CACHE = []
                return copy.deepcopy(_CACHE)
        except Exception as exc:
            logger.error(f"Error loading stats database: {exc}")
            return []


async def save_stats(records: List[Dict[str, Any]]) -> None:
    global _CACHE
    _CACHE = records

    async with _FILE_LOCK:
        try:
            tmp_path = STATS_FILE.with_suffix(".tmp")
            content = json.dumps({"rolls": records}, ensure_ascii=False, indent=2)
            async with aiofiles.open(tmp_path, "w", encoding="utf-8") as f:
                await f.write(content)
            tmp_path.replace(STATS_FILE)
        except Exception as exc:
            logger.error(f"Error saving stats database: {exc}")


async def record_dr_roll(
    user: Union[discord.User, discord.Member],
    race: str,
    dice_points: int,
    stats_allocated: Dict[str, int],
    raw_rolls: Dict[str, List[int]],
    base_stats: Dict[str, int],
    racial_modifiers: Optional[Dict[str, int]] = None,
    final_stats: Optional[Dict[str, int]] = None,
    modifiers: Optional[Dict[str, int]] = None,
    total_stats: Optional[int] = None,
    total_modifiers: Optional[int] = None,
    racial_mods: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    # Resolve racial modifiers allowing both parameter names
    r_mods = racial_modifiers if racial_modifiers is not None else (racial_mods if racial_mods is not None else {})

    # Resolve final stats fallback
    f_stats = final_stats if final_stats is not None else {}
    if not f_stats and base_stats:
        f_stats = {k: int(base_stats.get(k, 0)) + int(r_mods.get(k, 0)) for k in STAT_KEYS}

    # Calculate modifiers if not provided: (score - 10) // 2
    if modifiers is None:
        modifiers = {k: (int(f_stats.get(k, 10)) - 10) // 2 for k in STAT_KEYS}

    # Calculate total stats if not provided
    if total_stats is None:
        total_stats = sum(int(f_stats.get(k, 0)) for k in STAT_KEYS)

    # Calculate total modifiers if not provided
    if total_modifiers is None:
        total_modifiers = sum(int(modifiers.get(k, 0)) for k in STAT_KEYS)

    # Record metadata and payload
    roll_entry = {
        "id": str(uuid.uuid4()),
        "user_id": int(user.id),
        "username": str(user.name),
        "display_name": str(getattr(user, "display_name", user.name)),
        "race": race,
        "dice_points": dice_points,
        "allocated_dice": {k: int(stats_allocated.get(k, 0)) for k in STAT_KEYS},
        "raw_rolls": {k: list(raw_rolls.get(k, [])) for k in STAT_KEYS},
        "base_stats": {k: int(base_stats.get(k, 0)) for k in STAT_KEYS},
        "racial_modifiers": {k: int(r_mods.get(k, 0)) for k in STAT_KEYS},
        "final_stats": {k: int(f_stats.get(k, 0)) for k in STAT_KEYS},
        "modifiers": {k: int(modifiers.get(k, 0)) for k in STAT_KEYS},
        "total_stats": int(total_stats),
        "total_modifiers": int(total_modifiers),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    records = await load_stats(force_reload=True)
    records.append(roll_entry)
    await save_stats(records)
    logger.info(f"Recorded !char dr roll for {user.name} ({race}, total stats: {total_stats})")
    return roll_entry


# Backwards compatibility alias
load_dr_stats = load_stats
save_dr_stats = save_stats


# =============================================================================
# SECTION 3: ANALYTICS CALCULATION ENGINE
# =============================================================================

def compute_analytics(records: List[Dict[str, Any]], user_id: Optional[int] = None) -> Dict[str, Any]:
    filtered = [r for r in records if r.get("user_id") == user_id] if user_id else records

    if not filtered:
        return {
            "has_data": False,
            "total_rolls": 0,
            "unique_users": 0,
            "overall_avg_stat": 0.0,
            "overall_avg_total": 0.0,
            "overall_avg_mod_total": 0.0,
            "stat_data": {s: {"avg_final": 0.0, "avg_base": 0.0, "avg_alloc": 0.0, "avg_mod": 0.0, "max": 0, "min": 0} for s in STAT_KEYS},
            "brackets": {"18+ (Legendary)": 0, "16-17 (High)": 0, "14-15 (Good)": 0, "12-13 (Decent)": 0, "10-11 (Average)": 0, "<10 (Low)": 0},
            "records": {
                "highest_total": None,
                "lowest_total": None,
                "highest_single": None,
                "highest_alloc": None,
            },
            "recent_rolls": [],
        }

    total_rolls = len(filtered)
    unique_users = len({r.get("user_id") for r in filtered if "user_id" in r})

    stat_data: Dict[str, Dict[str, Any]] = {}
    for s in STAT_KEYS:
        finals = [r["final_stats"].get(s, 0) for r in filtered if "final_stats" in r]
        bases = [r["base_stats"].get(s, 0) for r in filtered if "base_stats" in r]
        allocs = [r["allocated_dice"].get(s, 0) for r in filtered if "allocated_dice" in r]
        mods = [r["modifiers"].get(s, 0) for r in filtered if "modifiers" in r]

        stat_data[s] = {
            "avg_final": float(np.mean(finals)) if finals else 0.0,
            "avg_base": float(np.mean(bases)) if bases else 0.0,
            "avg_alloc": float(np.mean(allocs)) if allocs else 0.0,
            "avg_mod": float(np.mean(mods)) if mods else 0.0,
            "max": int(max(finals)) if finals else 0,
            "min": int(min(finals)) if finals else 0,
        }

    all_totals = [r.get("total_stats", 0) for r in filtered]
    all_mod_totals = [r.get("total_modifiers", 0) for r in filtered]

    brackets = {
        "18+ (Legendary)": 0,
        "16-17 (High)": 0,
        "14-15 (Good)": 0,
        "12-13 (Decent)": 0,
        "10-11 (Average)": 0,
        "<10 (Low)": 0,
    }

    for r in filtered:
        f_stats = r.get("final_stats", {})
        for s in STAT_KEYS:
            val = f_stats.get(s, 0)
            if val >= 18:
                brackets["18+ (Legendary)"] += 1
            elif val >= 16:
                brackets["16-17 (High)"] += 1
            elif val >= 14:
                brackets["14-15 (Good)"] += 1
            elif val >= 12:
                brackets["12-13 (Decent)"] += 1
            elif val >= 10:
                brackets["10-11 (Average)"] += 1
            else:
                brackets["<10 (Low)"] += 1

    highest_total_roll = max(filtered, key=lambda x: x.get("total_stats", 0), default=None)
    lowest_total_roll = min(filtered, key=lambda x: x.get("total_stats", 0), default=None)

    highest_single = None
    for r in filtered:
        for s in STAT_KEYS:
            val = r.get("final_stats", {}).get(s, 0)
            if highest_single is None or val > highest_single["val"]:
                highest_single = {
                    "stat": s,
                    "val": val,
                    "user": r.get("display_name", r.get("username", "Unknown")),
                    "race": r.get("race", "Unknown"),
                }

    highest_alloc = None
    for r in filtered:
        for s in STAT_KEYS:
            d_val = r.get("allocated_dice", {}).get(s, 0)
            if highest_alloc is None or d_val > highest_alloc["dice"]:
                highest_alloc = {
                    "stat": s,
                    "dice": d_val,
                    "user": r.get("display_name", r.get("username", "Unknown")),
                    "race": r.get("race", "Unknown"),
                }

    all_finals_flat = [val for r in filtered for val in r.get("final_stats", {}).values()]

    return {
        "has_data": True,
        "total_rolls": total_rolls,
        "unique_users": unique_users,
        "overall_avg_stat": float(np.mean(all_finals_flat)) if all_finals_flat else 0.0,
        "overall_avg_total": float(np.mean(all_totals)) if all_totals else 0.0,
        "overall_avg_mod_total": float(np.mean(all_mod_totals)) if all_mod_totals else 0.0,
        "stat_data": stat_data,
        "brackets": brackets,
        "records": {
            "highest_total": highest_total_roll,
            "lowest_total": lowest_total_roll,
            "highest_single": highest_single,
            "highest_alloc": highest_alloc,
        },
        "recent_rolls": filtered[-5:][::-1],
    }


# =============================================================================
# SECTION 4: VISUAL GRAPH CHART ENGINE (DATA/GRAPH DISK PERSISTENCE)
# =============================================================================

def generate_analytics_chart(
    all_records: List[Dict[str, Any]],
    user_id: Optional[int] = None,
    user_name: Optional[str] = None
) -> Tuple[io.BytesIO, Path]:
    server_stats = compute_analytics(all_records)
    user_stats = compute_analytics(all_records, user_id=user_id) if user_id else None
    active_stats = user_stats if user_stats and user_stats.get("has_data") else server_stats

    # Configure dark theme styling matching Discord client palette
    plt.style.use("dark_background")
    fig = plt.figure(figsize=(13, 8), dpi=140)
    fig.patch.set_facecolor("#1E1F22")  # Discord background color

    # Grid layout: 2 rows, 2 columns
    gs = fig.add_gridspec(2, 2, hspace=0.35, wspace=0.25, top=0.90, bottom=0.08, left=0.08, right=0.95)
    ax1 = fig.add_subplot(gs[0, 0])
    ax2 = fig.add_subplot(gs[0, 1])
    ax3 = fig.add_subplot(gs[1, 0])
    ax4 = fig.add_subplot(gs[1, 1])

    for ax in (ax1, ax2, ax3, ax4):
        ax.set_facecolor("#2B2D31")  # Discord panel card color
        ax.tick_params(colors="#CCCCCC", labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#3F4147")

    colors = [STAT_COLORS[s] for s in STAT_KEYS]

    # Panel 1: Average Final Attribute Scores & Modifiers
    avg_finals = [active_stats["stat_data"][s]["avg_final"] for s in STAT_KEYS]
    bars1 = ax1.bar(STAT_KEYS, avg_finals, color=colors, edgecolor="#ffffff", linewidth=0.6, alpha=0.9)
    for bar, val in zip(bars1, avg_finals):
        mod = int((val - 10) // 2)
        mod_text = f"+{mod}" if mod >= 0 else str(mod)
        ax1.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.35,
            f"{val:.1f}\n({mod_text})",
            ha="center", va="bottom", color="#FFFFFF", fontweight="bold", fontsize=9
        )

    ax1.axhline(10.0, color="#888888", linestyle=":", linewidth=1, alpha=0.7, label="Base (10)")
    ax1.set_ylim(0, 21)
    ax1.set_title("Average Final Stat Values", color="#F1C40F", fontsize=12, fontweight="bold", pad=10)
    ax1.grid(axis="y", linestyle="--", alpha=0.15, color="#FFFFFF")

    # Panel 2: Average Dice Points Allocation per Attribute
    avg_allocs = [active_stats["stat_data"][s]["avg_alloc"] for s in STAT_KEYS]
    bars2 = ax2.barh(STAT_KEYS[::-1], avg_allocs[::-1], color=colors[::-1], edgecolor="#ffffff", linewidth=0.6, alpha=0.9)
    for bar, val in zip(bars2, avg_allocs[::-1]):
        ax2.text(
            bar.get_width() + 0.15,
            bar.get_y() + bar.get_height() / 2,
            f"{val:.1f} dice",
            ha="left", va="center", color="#FFFFFF", fontweight="bold", fontsize=9
        )
    max_alloc = max(avg_allocs) if avg_allocs else 8
    ax2.set_xlim(0, max(10, max_alloc + 2))
    ax2.set_title("Average Dice Points Allocation", color="#3498DB", fontsize=12, fontweight="bold", pad=10)
    ax2.grid(axis="x", linestyle="--", alpha=0.15, color="#FFFFFF")

    # Panel 3: Stat Score Distribution Across Brackets
    bracket_labels = list(active_stats["brackets"].keys())
    bracket_counts = list(active_stats["brackets"].values())
    total_samples = sum(bracket_counts) or 1
    bracket_percents = [(c / total_samples) * 100 for c in bracket_counts]

    palette_brackets = ["#F1C40F", "#2ECC71", "#3498DB", "#9B59B6", "#E67E22", "#E74C3C"]
    bars3 = ax3.bar([b.split(" ")[0] for b in bracket_labels], bracket_percents, color=palette_brackets, edgecolor="#ffffff", linewidth=0.6, alpha=0.9)
    for bar, pct, cnt in zip(bars3, bracket_percents, bracket_counts):
        ax3.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 0.6,
            f"{pct:.0f}%\n({cnt})",
            ha="center", va="bottom", color="#FFFFFF", fontweight="bold", fontsize=8
        )
    ax3.set_ylim(0, max(bracket_percents + [40]) + 10)
    ax3.set_title("Score Frequency Distribution", color="#2ECC71", fontsize=12, fontweight="bold", pad=10)
    ax3.set_ylabel("% of All Rolled Stats", color="#CCCCCC", fontsize=8)
    ax3.grid(axis="y", linestyle="--", alpha=0.15, color="#FFFFFF")

    # Panel 4: Player vs Server Comparison OR Total Attribute Density
    if user_stats and user_stats.get("has_data") and server_stats.get("has_data"):
        u_vals = [user_stats["stat_data"][s]["avg_final"] for s in STAT_KEYS]
        s_vals = [server_stats["stat_data"][s]["avg_final"] for s in STAT_KEYS]

        x_indices = np.arange(len(STAT_KEYS))
        width = 0.35

        ax4.bar(x_indices - width / 2, u_vals, width, label=user_name or "Player", color="#3498DB", edgecolor="#ffffff", linewidth=0.5, alpha=0.9)
        ax4.bar(x_indices + width / 2, s_vals, width, label="Server Avg", color="#95A5A6", edgecolor="#ffffff", linewidth=0.5, alpha=0.7)

        ax4.set_xticks(x_indices)
        ax4.set_xticklabels(STAT_KEYS)
        ax4.set_ylim(0, 21)
        ax4.set_title(f"{user_name or 'Player'} vs Server Average", color="#E67E22", fontsize=12, fontweight="bold", pad=10)
        ax4.legend(loc="upper right", facecolor="#2B2D31", edgecolor="#3F4147", fontsize=8)
        ax4.grid(axis="y", linestyle="--", alpha=0.15, color="#FFFFFF")
    else:
        all_totals = [r.get("total_stats", 0) for r in all_records if r.get("total_stats")]
        if all_totals:
            n_bins = min(15, max(5, len(set(all_totals))))
            ax4.hist(all_totals, bins=n_bins, color="#9B59B6", edgecolor="#FFFFFF", linewidth=0.6, alpha=0.85)
            mean_tot = float(np.mean(all_totals))
            ax4.axvline(mean_tot, color="#F1C40F", linestyle="--", linewidth=1.5, label=f"Mean: {mean_tot:.1f}")
            ax4.set_title("Total Character Stat Density", color="#9B59B6", fontsize=12, fontweight="bold", pad=10)
            ax4.set_xlabel("Combined Score (6 Stats)", color="#CCCCCC", fontsize=8)
            ax4.legend(loc="upper right", facecolor="#2B2D31", edgecolor="#3F4147", fontsize=8)
            ax4.grid(axis="y", linestyle="--", alpha=0.15, color="#FFFFFF")

    # Master Figure Header
    title_text = f"Mineria RPG • {user_name}'s Distribution Analytics" if user_name else "Mineria RPG • Global Character Distribution Analytics"
    fig.suptitle(title_text, color="#FFFFFF", fontsize=16, fontweight="bold", y=0.97)

    # Ensure data/graph directory exists
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)

    # Save to data/graph
    file_name = f"stats_{user_id}.png" if user_id else "stats_global.png"
    target_disk_path = GRAPH_DIR / file_name
    latest_disk_path = GRAPH_DIR / "stats.png"

    fig.savefig(target_disk_path, format="png", facecolor=fig.get_facecolor(), edgecolor="none")
    if target_disk_path != latest_disk_path:
        fig.savefig(latest_disk_path, format="png", facecolor=fig.get_facecolor(), edgecolor="none")

    # Export to memory buffer for immediate Discord upload
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor(), edgecolor="none")
    buf.seek(0)
    plt.close(fig)

    return buf, target_disk_path


# =============================================================================
# SECTION 5: DISCORD EMBED BUILDER
# =============================================================================

def build_analytics_embed(
    analytics: Dict[str, Any],
    user_name: Optional[str] = None,
    avatar_url: Optional[str] = None
) -> discord.Embed:
    if not analytics.get("has_data"):
        embed = discord.Embed(
            title="No Stat Records Found",
            description=(
                "No `!char dr` rolls have been recorded yet.\n"
                "Once players distribute character stats with `!char dr`, analytics and charts will appear here automatically."
            ),
            color=discord.Color.red()
        )
        return embed

    total_rolls = analytics["total_rolls"]
    unique_users = analytics["unique_users"]
    overall_avg = analytics["overall_avg_stat"]
    overall_tot = analytics["overall_avg_total"]
    overall_mod = analytics["overall_avg_mod_total"]

    title = f"Stat Distribution Analytics ({user_name})" if user_name else "Global Stat Distribution Analytics"
    embed = discord.Embed(
        title=title,
        color=discord.Color.from_rgb(46, 204, 113),
        timestamp=datetime.now(timezone.utc)
    )

    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    embed.add_field(
        name="Summary Metrics",
        value=(
            f"• **Total Rolls:** `{total_rolls}`\n"
            f"• **Players Sampled:** `{unique_users}`\n"
            f"• **Overall Average Stat:** `{overall_avg:.2f}`\n"
            f"• **Avg Total Stat Score:** `{overall_tot:.1f}`\n"
            f"• **Avg Total Modifiers:** `{overall_mod:+.1f}`"
        ),
        inline=True
    )

    bracket_lines = []
    tot_samples = sum(analytics["brackets"].values()) or 1
    for k, v in analytics["brackets"].items():
        pct = (v / tot_samples) * 100
        bracket_lines.append(f"• **{k}:** `{v}` ({pct:.1f}%)")
    embed.add_field(
        name="Score Frequency",
        value="\n".join(bracket_lines[:4]),
        inline=True
    )

    # Detailed Per-Stat breakdown
    stat_rows = []
    for s in STAT_KEYS:
        d = analytics["stat_data"][s]
        avg_f = d["avg_final"]
        avg_al = d["avg_alloc"]
        mod = int((avg_f - 10) // 2)
        mod_str = f"+{mod}" if mod >= 0 else str(mod)
        stat_rows.append(f"`{s}`: **{avg_f:.1f}** ({mod_str}) | Dice: `{avg_al:.1f}d6`")

    embed.add_field(
        name="Average Stat Breakdown",
        value="\n".join(stat_rows),
        inline=False
    )

    # Hall of Records
    records = analytics.get("records", {})
    record_lines = []
    h_tot = records.get("highest_total")
    if h_tot:
        p_name = h_tot.get("display_name", h_tot.get("username", "Unknown"))
        record_lines.append(f"• **Highest Total:** `{h_tot.get('total_stats')}` by **{p_name}** ({h_tot.get('race')})")

    l_tot = records.get("lowest_total")
    if l_tot:
        p_name = l_tot.get("display_name", l_tot.get("username", "Unknown"))
        record_lines.append(f"• **Lowest Total:** `{l_tot.get('total_stats')}` by **{p_name}** ({l_tot.get('race')})")

    h_single = records.get("highest_single")
    if h_single:
        record_lines.append(f"• **Max Single Stat:** `{h_single['stat']} {h_single['val']}` by **{h_single['user']}**")

    h_al = records.get("highest_alloc")
    if h_al:
        record_lines.append(f"• **Most Dice Allocated:** `{h_al['dice']}d6` on `{h_al['stat']}` by **{h_al['user']}**")

    if record_lines:
        embed.add_field(
            name="Campaign Records",
            value="\n".join(record_lines),
            inline=False
        )

    embed.set_footer(text="Type '!stats' to generate graphical charts • Mineria RPG")
    return embed


# =============================================================================
# SECTION 6: INTERACTIVE UI BUTTONS
# =============================================================================

class StatsGraphView(discord.ui.View):
    def __init__(self, target_user: Optional[Union[discord.User, discord.Member]] = None, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.target_user = target_user

    @discord.ui.button(label="Show Visual Graph", style=discord.ButtonStyle.primary)
    async def show_graph_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        records = await load_stats()
        uid = self.target_user.id if self.target_user else None
        uname = getattr(self.target_user, "display_name", self.target_user.name) if self.target_user else None

        analytics = compute_analytics(records, user_id=uid)
        if not analytics.get("has_data"):
            await interaction.followup.send("No stat distribution records found to chart.", ephemeral=True)
            return

        buf, disk_path = await asyncio.to_thread(generate_analytics_chart, records, uid, uname)
        file = discord.File(fp=buf, filename="stats.png")

        embed = discord.Embed(
            title="Character Stat Distribution Analytics",
            description=(
                f"**Total Rolls Analyzed:** `{analytics['total_rolls']}` • "
                f"**Average Stat:** `{analytics['overall_avg_stat']:.1f}`\n"
                f"*Saved to: `data/graph/{disk_path.name}`*"
            ),
            color=discord.Color.from_rgb(52, 152, 219)
        )
        embed.set_image(url="attachment://stats.png")
        if uname:
            embed.set_footer(text=f"Filtered for {uname} • Mineria RPG")
        else:
            embed.set_footer(text="Global Campaign Overview • Mineria RPG")

        button.disabled = True
        button.label = "Graph Generated"
        await interaction.message.edit(view=self)
        await interaction.followup.send(file=file, embed=embed)


# Backwards compatibility alias
DRStatsView = StatsGraphView


# =============================================================================
# SECTION 7: COG COMMANDS DEFINITION (!stats, !drstats)
# =============================================================================

class StatAnalytics(commands.Cog, name="StatAnalytics"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _resolve_target_user(self, ctx: commands.Context, target: Optional[Union[discord.Member, discord.User, str]]) -> Optional[Union[discord.Member, discord.User]]:
        if isinstance(target, (discord.Member, discord.User)):
            return target
        if isinstance(target, str):
            cleaned = target.strip("<@!>").strip()
            if cleaned.isdigit():
                user_obj = ctx.guild.get_member(int(cleaned)) if ctx.guild else None
                if user_obj:
                    return user_obj
            if ctx.guild:
                found = discord.utils.find(
                    lambda m: m.name.lower() == target.lower() or m.display_name.lower() == target.lower(),
                    ctx.guild.members
                )
                if found:
                    return found
        return None

    async def _send_stats_graph(
        self,
        ctx: commands.Context,
        target: Optional[Union[discord.Member, discord.User, str]] = None
    ) -> None:
        target_user = self._resolve_target_user(ctx, target)
        records = await load_stats()
        uid = target_user.id if target_user else None
        uname = getattr(target_user, "display_name", target_user.name) if target_user else None

        analytics = compute_analytics(records, user_id=uid)
        if not analytics.get("has_data"):
            await ctx.send(
                "No stat distribution records found yet.\n"
                "Distribute character stats with `!char dr` to generate analytics and charts."
            )
            return

        try:
            async with ctx.typing():
                buf, disk_path = await asyncio.to_thread(generate_analytics_chart, records, uid, uname)
        except Exception:
            buf, disk_path = await asyncio.to_thread(generate_analytics_chart, records, uid, uname)

        file = discord.File(fp=buf, filename="stats.png")

        embed = discord.Embed(
            title="Character Stat Distribution Analytics",
            description=(
                f"**Total Rolls Analyzed:** `{analytics['total_rolls']}` • "
                f"**Average Stat:** `{analytics['overall_avg_stat']:.1f}` • "
                f"**Average Total:** `{analytics['overall_avg_total']:.1f}`\n"
                f"*Saved to: `data/graph/{disk_path.name}`*"
            ),
            color=discord.Color.from_rgb(52, 152, 219)
        )
        embed.set_image(url="attachment://stats.png")
        if uname:
            embed.set_footer(text=f"Filtered for {uname} • Mineria RPG")
        else:
            embed.set_footer(text="Global Campaign Overview • Mineria RPG")

        await ctx.send(file=file, embed=embed)

    # -------------------------------------------------------------------------
    # PRIMARY PREFIX COMMAND: !stats
    # -------------------------------------------------------------------------

    @commands.group(name="stats", aliases=["stat", "drstats", "statanalytics", "rollstats"], invoke_without_command=True)
    async def stats_group(
        self, ctx: commands.Context,
        target: Optional[Union[discord.Member, discord.User, str]] = None
    ) -> None:
        # User requested: when '!stats' is entered, send graphics directly
        await self._send_stats_graph(ctx, target)

    @stats_group.command(name="graph", aliases=["chart", "plot"])
    async def stats_graph(
        self, ctx: commands.Context,
        target: Optional[Union[discord.Member, discord.User, str]] = None
    ) -> None:
        await self._send_stats_graph(ctx, target)

    @stats_group.command(name="summary", aliases=["overview", "text", "info"])
    async def stats_summary(
        self, ctx: commands.Context,
        target: Optional[Union[discord.Member, discord.User, str]] = None
    ) -> None:
        target_user = self._resolve_target_user(ctx, target)
        records = await load_stats()
        uid = target_user.id if target_user else None
        uname = getattr(target_user, "display_name", target_user.name) if target_user else None
        avatar = target_user.display_avatar.url if (target_user and target_user.display_avatar) else None

        analytics = compute_analytics(records, user_id=uid)
        embed = build_analytics_embed(analytics, user_name=uname, avatar_url=avatar)
        view = StatsGraphView(target_user=target_user) if analytics.get("has_data") else None
        await ctx.send(embed=embed, view=view)

    @stats_group.command(name="history", aliases=["recent", "logs"])
    async def stats_history(
        self, ctx: commands.Context,
        target: Optional[Union[discord.Member, discord.User, str]] = None
    ) -> None:
        target_user = self._resolve_target_user(ctx, target)
        records = await load_stats()
        uid = target_user.id if target_user else None
        uname = getattr(target_user, "display_name", target_user.name) if target_user else None

        filtered = [r for r in records if r.get("user_id") == uid] if uid else records
        if not filtered:
            await ctx.send("No roll history available.")
            return

        embed = discord.Embed(
            title=f"Stat Roll History ({uname})" if uname else "Global Stat Roll History",
            color=discord.Color.gold()
        )

        for r in filtered[-8:][::-1]:
            player = r.get("display_name", r.get("username", "Unknown"))
            race = r.get("race", "Unknown")
            tot = r.get("total_stats", 0)
            alloc_fmt = " ".join(f"{s}:{r['allocated_dice'].get(s, 0)}" for s in STAT_KEYS)
            res_fmt = " ".join(f"{s}:{r['final_stats'].get(s, 0)}" for s in STAT_KEYS)
            embed.add_field(
                name=f"{player} • {race} (Total: {tot})",
                value=f"**Allocated:** `{alloc_fmt}`\n**Result:** `{res_fmt}`",
                inline=False
            )

        await ctx.send(embed=embed)

    # -------------------------------------------------------------------------
    # SLASH COMMANDS: /stats
    # -------------------------------------------------------------------------

    @app_commands.command(name="stats", description="View statistical analytics and graphs of character stat rolls")
    @app_commands.describe(
        action="Choose whether to generate visual graphs or view summary text",
        user="Optional player to filter analytics for"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="Visual Graph Chart", value="graph"),
        app_commands.Choice(name="Overview & Summary", value="summary"),
        app_commands.Choice(name="Recent Roll Logs", value="history")
    ])
    async def slash_stats(
        self, interaction: discord.Interaction,
        action: Optional[str] = "graph",
        user: Optional[discord.User] = None
    ) -> None:
        records = await load_stats()
        uid = user.id if user else None
        uname = getattr(user, "display_name", user.name) if user else None

        if action == "graph":
            await interaction.response.defer()
            analytics = compute_analytics(records, user_id=uid)
            if not analytics.get("has_data"):
                await interaction.followup.send("No stat distribution records found to chart.", ephemeral=True)
                return

            buf, disk_path = await asyncio.to_thread(generate_analytics_chart, records, uid, uname)
            file = discord.File(fp=buf, filename="stats.png")
            embed = discord.Embed(
                title="Character Stat Distribution Analytics",
                description=(
                    f"**Total Rolls Analyzed:** `{analytics['total_rolls']}` • "
                    f"**Average Stat:** `{analytics['overall_avg_stat']:.1f}`\n"
                    f"*Saved to: `data/graph/{disk_path.name}`*"
                ),
                color=discord.Color.from_rgb(52, 152, 219)
            )
            embed.set_image(url="attachment://stats.png")
            await interaction.followup.send(file=file, embed=embed)
        elif action == "history":
            filtered = [r for r in records if r.get("user_id") == uid] if uid else records
            if not filtered:
                await interaction.response.send_message("No roll history available.", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"Stat Roll History ({uname})" if uname else "Global Stat Roll History",
                color=discord.Color.gold()
            )
            for r in filtered[-8:][::-1]:
                player = r.get("display_name", r.get("username", "Unknown"))
                race = r.get("race", "Unknown")
                tot = r.get("total_stats", 0)
                alloc_fmt = " ".join(f"{s}:{r['allocated_dice'].get(s, 0)}" for s in STAT_KEYS)
                res_fmt = " ".join(f"{s}:{r['final_stats'].get(s, 0)}" for s in STAT_KEYS)
                embed.add_field(
                    name=f"{player} • {race} (Total: {tot})",
                    value=f"**Allocated:** `{alloc_fmt}`\n**Result:** `{res_fmt}`",
                    inline=False
                )
            await interaction.response.send_message(embed=embed)
        else:
            analytics = compute_analytics(records, user_id=uid)
            avatar = user.display_avatar.url if (user and user.display_avatar) else None
            embed = build_analytics_embed(analytics, user_name=uname, avatar_url=avatar)
            view = StatsGraphView(target_user=user) if analytics.get("has_data") else None
            await interaction.response.send_message(embed=embed, view=view)


# Backwards compatibility Cog alias
DRAnalytics = StatAnalytics


# =============================================================================
# SECTION 8: SETUP EXTENSION ENTRY POINT
# =============================================================================

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatAnalytics(bot))
