import os
import io
import gc
import re
import csv
import time
import difflib
import asyncio
from pathlib import Path
from typing import Tuple, List, Dict, Any, Optional
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from dotenv import load_dotenv
from log_handler import logger
from admin import DEVELOPER_ID, is_user_authorized
from character import InteractionContextAdapter, load_json

# =============================================================================
# SECTION 1: CONSTANTS, NUMBER FORMATTING & XP PROGRESSION TABLES
# =============================================================================

load_dotenv(Path(__file__).parent / ".env")
XP_SHEET_URL = os.getenv("XP_SHEET_URL")
REPORTS_SHEET_URL = os.getenv("REPORTS_SHEET_URL")

# Automatically derive Mission Reports URL (gid=0) from XP_SHEET_URL if omitted
if not REPORTS_SHEET_URL and XP_SHEET_URL:
    if "gid=" in XP_SHEET_URL:
        REPORTS_SHEET_URL = re.sub(r"gid=\d+", "gid=0", XP_SHEET_URL)
    else:
        REPORTS_SHEET_URL = f"{XP_SHEET_URL}&gid=0" if "?" in XP_SHEET_URL else f"{XP_SHEET_URL}?export?format=csv&gid=0"

# Keywords designating inactive, retired, or deceased characters
INACTIVE_KEYWORDS = (
    "inactive", "inaktif", "in-aktif",
    "dead", "ölü", "olu",
    "left", "leave", "ayrıldı", "ayrildi",
    "pasif", "ex", "emekli"
)

# Server-approved ranked positions permitted to be paired with a Clerk character
QUALIFIED_RANKS = ("kıdemli", "kidemli", "uzman", "gezgin", "senior", "expert", "wanderer")

# Campaign XP progression milestones from Level 1 to 20
XP_TABLE: Dict[int, int] = {
    1: 0,
    2: 1300,
    3: 3300,
    4: 6000,
    5: 10000,
    6: 15000,
    7: 23000,
    8: 34000,
    9: 50000,
    10: 71000,
    11: 105000,
    12: 145000,
    13: 210000,
    14: 295000,
    15: 425000,
    16: 600000,
    17: 850000,
    18: 1200000,
    19: 1700000,
    20: 2400000
}
XP_LEVELS: List[Tuple[int, int]] = sorted(XP_TABLE.items(), key=lambda x: x[0])

# Turkish character mapping table for case-folding and phonetic normalization
TR_MAP = str.maketrans({
    "İ": "i", "I": "i", "ı": "i",
    "Ş": "s", "ş": "s",
    "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u",
    "Ö": "o", "ö": "o",
    "Ç": "c", "ç": "c"
})


def normalize_name(s: str) -> str:
    """
    Normalizes Turkish characters, collapses whitespace, and converts to lowercase.
    Example: 'İlker ŞAHİN' -> 'ilker sahin'
    """
    if not s:
        return ""
    return " ".join(s.translate(TR_MAP).lower().split())


def parse_xp_value(val: Any) -> float:
    """
    Safely parses numeric XP values from Google Sheet cells across international formatting variations.
    Example: '43.935' -> 43935.0, '14,049' -> 14049.0, '1.250,50' -> 1250.5
    """
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)

    val_str = str(val).strip()
    if not val_str:
        return 0.0

    # Strip formatting artefacts, quotes, and non-breaking spaces
    val_str = val_str.replace('"', "").replace("'", "").replace(" ", "").replace("\u00a0", "")

    # Handle mixed punctuation (e.g. 1.250,50 vs 1,250.50)
    if "," in val_str and "." in val_str:
        if val_str.rfind(",") > val_str.rfind("."):
            val_str = val_str.replace(".", "").replace(",", ".")
        else:
            val_str = val_str.replace(",", "")
    elif "." in val_str:
        parts = val_str.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            val_str = val_str.replace(".", "")
    elif "," in val_str:
        parts = val_str.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            val_str = val_str.replace(",", "")
        else:
            val_str = val_str.replace(",", ".")

    try:
        return float(val_str)
    except ValueError:
        return 0.0


def get_level_info(current_xp: float) -> Tuple[int, float, int]:
    """
    Calculates current character level, remaining XP needed for the next milestone, and next level number.
    Returns:
        (current_level, xp_needed_for_next, next_level)
    """
    current_xp = max(0.0, current_xp)
    current_level = 1

    for level, threshold in XP_LEVELS:
        if current_xp >= threshold:
            current_level = level
        else:
            break

    next_level = current_level + 1
    if next_level > 20:
        return 20, 0.0, 20

    xp_needed = XP_TABLE[next_level] - current_xp
    return current_level, xp_needed, next_level


def format_number(val: Any) -> str:
    """Formats numeric values with thousand comma separators (e.g. 57984 -> '57,984')."""
    if val is None:
        return "0"
    val_str = str(val).strip()
    if not val_str:
        return "0"
    try:
        cleaned = val_str.replace(".", "").replace(",", "").strip()
        num = int(cleaned)
        return f"{num:,}"
    except Exception:
        return val_str


# =============================================================================
# SECTION 2: 4-STAGE FUZZY CHARACTER MATCHING ENGINE
# =============================================================================

def find_sheet_character(
    query: str, data: List[Dict[str, Any]]
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Finds a character in sheet data using a robust 4-stage matching strategy:
    1. Exact Match (case-insensitive & TR-normalized)
    2. Substring Match (query inside character name)
    3. Token-Set Match (handles inverted word order, e.g. 'Quickfire Thoma' -> 'Thoma Quickfire')
    4. Fuzzy / Typo Match (difflib SequenceMatcher on whole string & individual words, e.g. 'Thoma Quackfire' -> 'Thoma Quickfire')

    Returns:
        (matched_char, []) if a single high-confidence match is resolved.
        (None, [candidates]) if ambiguous or multiple suggestions exist.
        (None, []) if no match could be found.
    """
    clean_target = normalize_name(query)
    if not clean_target or not data:
        return None, []

    # Stage 1: Exact match
    for char in data:
        if char.get("norm_name") == clean_target:
            return char, []

    # Stage 2: Substring match
    partial_matches = [char for char in data if clean_target in char.get("norm_name", "")]
    if len(partial_matches) == 1:
        return partial_matches[0], []
    elif len(partial_matches) > 1:
        return None, partial_matches

    # Stage 3: Token-set match (handles flipped names e.g. 'Quickfire Thoma')
    q_tokens = set(clean_target.split())
    if q_tokens:
        token_matches = [char for char in data if set(char.get("norm_name", "").split()) == q_tokens]
        if len(token_matches) == 1:
            return token_matches[0], []
        elif len(token_matches) > 1:
            return None, token_matches

    # Stage 4: Fuzzy sequence matching for typos
    scored: List[Tuple[float, Dict[str, Any]]] = []
    q_words = clean_target.split()

    for char in data:
        norm = char.get("norm_name", "")
        if not norm:
            continue
        sim = difflib.SequenceMatcher(None, clean_target, norm).ratio()

        c_words = norm.split()
        if len(q_words) == len(c_words) and len(q_words) > 1:
            token_ratios = [difflib.SequenceMatcher(None, qw, cw).ratio() for qw, cw in zip(q_words, c_words, strict=True)]
            sim = max(sim, sum(token_ratios) / len(token_ratios))
        elif len(q_words) == 1 and len(c_words) > 1:
            best_token_sim = max(difflib.SequenceMatcher(None, q_words[0], cw).ratio() for cw in c_words)
            if best_token_sim >= 0.75:
                sim = max(sim, best_token_sim * 0.9)

        if sim >= 0.65:
            scored.append((sim, char))

    if scored:
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score, best_char = scored[0]
        # Resolve if single high match, clear leader (>8% gap), or >=80% similarity
        if len(scored) == 1 or best_score >= 0.80 or (best_score - scored[1][0] >= 0.08):
            return best_char, []
        else:
            return None, [c for _, c in scored[:5]]

    # Stage 5: Suggestion fallback
    norm_to_char = {c.get("norm_name", ""): c for c in data if c.get("norm_name")}
    close_names = difflib.get_close_matches(clean_target, list(norm_to_char.keys()), n=3, cutoff=0.50)
    if close_names:
        return None, [norm_to_char[n] for n in close_names]

    return None, []


# =============================================================================
# SECTION 3: ONE TIME COMMANDS COG & GOOGLE SHEET INGESTION
# =============================================================================

class OneTimeCommands(commands.Cog, name="Utility"):
    """Cog handling XP table queries, roster compliance, and campaign analytics."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache_data: List[Dict[str, Any]] = []
        self._cache_active: List[Dict[str, Any]] = []
        self._cache_graveyard: List[Dict[str, Any]] = []
        self._cache_players: Dict[str, str] = {}
        self._cache_char_map: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_gm_counts: Dict[str, int] = {}
        self._cache_kia_data: List[Dict[str, Any]] = []
        self._cache_skipped: int = 0
        self._cache_timestamp: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        """Reuses or creates persistent aiohttp ClientSession with 45s total / 15s connect timeout."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=45, connect=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def fetch_xp_data(self, force_refresh: bool = True) -> Tuple[List[Dict[str, Any]], int]:
        """
        Fetches and parses both the Player XP Tracker (Active & Archived tables) and Mission Reports (GM Column AM)
        directly from Google Sheets every time it is requested (no 5-minute caching).
        Returns:
            Tuple: (List of active character entries for dup checking, count of skipped rows)
        """
        now = time.time()

        if not XP_SHEET_URL:
            logger.error("XP_SHEET_URL environment variable is not configured.")
            return self._cache_data, self._cache_skipped

        try:
            session = await self._get_session()
            async with session.get(XP_SHEET_URL) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch XP sheet: HTTP {resp.status}")
                    return self._cache_data, self._cache_skipped
                xp_content = await resp.text()

            report_content = None
            if REPORTS_SHEET_URL:
                try:
                    async with session.get(REPORTS_SHEET_URL) as r_resp:
                        if r_resp.status == 200:
                            report_content = await r_resp.text()
                        else:
                            logger.warning(f"Failed to fetch Reports sheet: HTTP {r_resp.status}")
                except Exception as exc:
                    logger.warning(f"Error fetching Reports sheet: {exc}")

        except (asyncio.TimeoutError, TimeoutError, aiohttp.ClientError) as exc:
            err_type = type(exc).__name__
            err_msg = f"{err_type}: {exc}" if str(exc) else err_type
            logger.warning(f"Network timeout or connection error fetching XP sheet ({err_msg}). Using cached data.")
            return self._cache_data, self._cache_skipped
        except Exception as exc:
            err_type = type(exc).__name__
            err_msg = f"{err_type}: {exc}" if str(exc) else err_type
            logger.error(f"Exception encountered while fetching XP data: {err_msg}", exc_info=True)
            return self._cache_data, self._cache_skipped

        xp_reader = csv.reader(io.StringIO(xp_content))
        xp_rows = list(xp_reader)
        if not xp_rows:
            return self._cache_data, self._cache_skipped

        skipped_count = 0
        parsed_compat = []
        parsed_active = []
        parsed_graveyard = []
        parsed_kia_data = []
        players_dict = {}
        char_map = {}

        for r_idx, row in enumerate(xp_rows[1:], start=2):
            # Left Table: Columns B to N (Active Roster)
            if len(row) > 2:
                char_name = row[1].strip() if len(row) > 1 else ""
                player_name = row[2].strip() if len(row) > 2 else ""

                if char_name and player_name:
                    norm_p = normalize_name(player_name)
                    norm_c = normalize_name(char_name)
                    if norm_p not in players_dict:
                        players_dict[norm_p] = player_name

                    active_entry = {
                        "row": r_idx,
                        "char_name": char_name,
                        "player_name": player_name,
                        "norm_player": norm_p,
                        "norm_char": norm_c,
                        "xp": row[3].strip() if len(row) > 3 else "0",
                        "rank": row[4].strip() if len(row) > 4 else "-",
                        "level": row[5].strip() if len(row) > 5 else "1",
                        "missions": row[6].strip() if len(row) > 6 else "0",
                        "rank_xp": row[7].strip() if len(row) > 7 else "0",
                        "task_xp": row[8].strip() if len(row) > 8 else "0",
                        "report_xp": row[9].strip() if len(row) > 9 else "0",
                        "bailiff_xp": row[10].strip() if len(row) > 10 else "0",
                        "ex_xp": row[11].strip() if len(row) > 11 else "0",
                        "wiki_xp": row[12].strip() if len(row) > 12 else "0",
                        "inactive_xp": row[13].strip() if len(row) > 13 else "0",
                        "type": "Active"
                    }
                    parsed_active.append(active_entry)
                    parsed_compat.append({
                        "char_name": char_name,
                        "player_name": player_name,
                        "xp": active_entry["xp"],
                        "rank": active_entry["rank"],
                    })
                    char_map.setdefault(norm_c, []).append(active_entry)

                    # Compute KIA/MIA Base and Task XP
                    k_full_xp = 0.0
                    for idx in [10, 11, 12]:  # Fixed XP: K, L, M
                        if idx < len(row):
                            k_full_xp += parse_xp_value(row[idx])
                    k_task_xp = 0.0
                    for idx in [8, 9]:  # Task XP: I, J
                        if idx < len(row):
                            k_task_xp += parse_xp_value(row[idx])

                    parsed_kia_data.append({
                        "raw_name": char_name,
                        "norm_name": norm_c,
                        "full_xp": k_full_xp,
                        "task_xp": k_task_xp
                    })
                elif len(row) >= 5 and (not char_name or not player_name):
                    skipped_count += 1

            # Right Table: Columns P to V (Archived / Graveyard / Retired Roster)
            if len(row) > 16:
                char_name = row[15].strip() if len(row) > 15 else ""
                player_name = row[16].strip() if len(row) > 16 else ""

                if char_name and player_name:
                    norm_p = normalize_name(player_name)
                    norm_c = normalize_name(char_name)
                    if norm_p not in players_dict:
                        players_dict[norm_p] = player_name

                    graveyard_entry = {
                        "row": r_idx,
                        "char_name": char_name,
                        "player_name": player_name,
                        "norm_player": norm_p,
                        "norm_char": norm_c,
                        "xp": row[17].strip() if len(row) > 17 else "0",
                        "status": row[18].strip() if len(row) > 18 else "-",
                        "games": row[19].strip() if len(row) > 19 else "0",
                        "missions": row[20].strip() if len(row) > 20 else "0",
                        "reports": row[21].strip() if len(row) > 21 else "0",
                        "type": "Archived"
                    }
                    parsed_graveyard.append(graveyard_entry)
                    char_map.setdefault(norm_c, []).append(graveyard_entry)

        # Parse Mission Reports Column AM (GM Column, idx 38)
        gm_counts = {}
        report_rows = None
        if report_content:
            report_reader = csv.reader(io.StringIO(report_content))
            report_rows = list(report_reader)
            for row in report_rows[1:]:
                if len(row) > 38:
                    gm_val = row[38].strip()
                    if gm_val:
                        norm_gm = normalize_name(gm_val)
                        gm_counts[norm_gm] = gm_counts.get(norm_gm, 0) + 1

        self._cache_data = parsed_compat
        self._cache_active = parsed_active
        self._cache_graveyard = parsed_graveyard
        self._cache_players = players_dict
        self._cache_char_map = char_map
        self._cache_gm_counts = gm_counts
        self._cache_kia_data = parsed_kia_data
        self._cache_skipped = skipped_count
        self._cache_timestamp = now

        # Immediately free large temporary CSV buffers and invoke GC
        del xp_content
        if report_content is not None:
            del report_content
        del xp_rows
        if report_rows is not None:
            del report_rows
        gc.collect()

        return self._cache_data, self._cache_skipped

    # =========================================================================
    # SECTION 4: GM PLAYER TRACKER (!gm, /gm)
    # =========================================================================

    @commands.command(name="gm", aliases=["player", "gmcheck", "pinfo"])
    async def gm_player_command(self, ctx: commands.Context, *, player_query: Optional[str] = None) -> None:
        """
        Queries Player XP Tracker and Mission Reports for player character and GM session data.
        Usage: !gm <player's full name> (e.g.: !gm John Doe)
        """
        if ctx.author.id != DEVELOPER_ID and not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to administrators.")
            return

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None

        if not player_query or not player_query.strip():
            embed = discord.Embed(
                title="GM Player Query Terminal",
                description=(
                    "Queries the Player XP Tracker and Mission Reports to list all active and archived characters and GM sessions for a player.\n\n"
                    "**Usage:** `!gm <player's full name>`\n"
                    "**Example:** `!gm John Doe`\n\n"
                    "**Scanned Data:**\n"
                    "• **Active Characters:** Columns B (Character) & C (Player)\n"
                    "• **Archived Characters:** Columns P (Character) & Q (Player)\n"
                    "• **GM Sessions:** Mission Reports AM (GM) Column"
                ),
                color=discord.Color.gold()
            )
            if avatar_url:
                embed.set_footer(text="Mineria RPG • GM Player Tracker", icon_url=avatar_url)
            else:
                embed.set_footer(text="Mineria RPG • GM Player Tracker")
            await ctx.send(embed=embed)
            return

        raw_query = player_query.strip().strip('"\'')
        query_norm = normalize_name(raw_query)

        msg = await ctx.send("Scanning Player XP Tracker and Mission Reports...")
        await self.fetch_xp_data()

        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

        if not self._cache_players:
            await ctx.send("Failed to fetch XP table data or sheet is empty.")
            return

        # Player matching algorithm
        target_player = None
        match_note = None

        # 1. Exact match on player name
        if query_norm in self._cache_players:
            target_player = self._cache_players[query_norm]

        # 2. Check if query matches character name
        if not target_player and query_norm in self._cache_char_map:
            owners = list({e["player_name"] for e in self._cache_char_map[query_norm]})
            if len(owners) == 1:
                target_player = owners[0]
                matched_char_name = self._cache_char_map[query_norm][0]["char_name"]
                match_note = f"Character Match: **{matched_char_name}** -> **{target_player}**"

        # 3. Partial match on player names
        if not target_player:
            candidates = [p for norm_p, p in self._cache_players.items() if query_norm in norm_p or norm_p in query_norm]
            if len(candidates) == 1:
                target_player = candidates[0]
            elif len(candidates) > 1:
                cand_list = "\n".join([f"• `{p}`" for p in candidates[:15]])
                if len(candidates) > 15:
                    cand_list += f"\n*...and {len(candidates) - 15} more player(s).*"
                embed = discord.Embed(
                    title=f"Multiple Players Found ({len(candidates)})",
                    description=(
                        f"Players matching **'{raw_query}'**:\n\n"
                        f"{cand_list}\n\n"
                        f"Please try again using the player's full name:\n`!gm <player's full name>`"
                    ),
                    color=discord.Color.orange()
                )
                if avatar_url:
                    embed.set_footer(text="Mineria RPG • GM Player Tracker", icon_url=avatar_url)
                await ctx.send(embed=embed)
                return

        # 4. Fuzzy match for character name typos
        if not target_player:
            char_candidates = [norm_c for norm_c in self._cache_char_map if query_norm in norm_c]
            if len(char_candidates) == 1:
                matched_entries = self._cache_char_map[char_candidates[0]]
                owners = list({e["player_name"] for e in matched_entries})
                if len(owners) == 1:
                    target_player = owners[0]
                    match_note = f"Character Match: **{matched_entries[0]['char_name']}** -> **{target_player}**"
            elif not char_candidates:
                scored_c = []
                for norm_c in self._cache_char_map:
                    sim = difflib.SequenceMatcher(None, query_norm, norm_c).ratio()
                    words = norm_c.split()
                    if words:
                        w_sim = max(difflib.SequenceMatcher(None, query_norm, w).ratio() for w in words)
                        sim = max(sim, w_sim * 0.9)
                    if sim >= 0.70:
                        scored_c.append((sim, norm_c))
                if scored_c:
                    scored_c.sort(key=lambda x: x[0], reverse=True)
                    if len(scored_c) == 1 or scored_c[0][0] >= 0.80 or (scored_c[0][0] - scored_c[1][0] >= 0.08):
                        best_norm = scored_c[0][1]
                        matched_entries = self._cache_char_map[best_norm]
                        owners = list({e["player_name"] for e in matched_entries})
                        if len(owners) == 1:
                            target_player = owners[0]
                            match_note = f"Character Match: **{matched_entries[0]['char_name']}** -> **{target_player}**"

        # 5. Not found fallback with suggestions
        if not target_player:
            close_matches = difflib.get_close_matches(query_norm, list(self._cache_players.keys()), n=4, cutoff=0.5)
            embed = discord.Embed(
                title="Player Not Found",
                description=f"No record found for **{raw_query}** on the Player XP Tracker.",
                color=discord.Color.red()
            )
            if close_matches:
                sug_list = "\n".join([f"• `!gm {self._cache_players[c]}`" for c in close_matches])
                embed.add_field(name="Did you mean:", value=sug_list, inline=False)
            if avatar_url:
                embed.set_footer(text="Mineria RPG • GM Player Tracker", icon_url=avatar_url)
            await ctx.send(embed=embed)
            return

        # Build player overview
        target_norm = normalize_name(target_player)
        active_list = [c for c in self._cache_active if c["norm_player"] == target_norm]
        graveyard_list = [c for c in self._cache_graveyard if c["norm_player"] == target_norm]
        total_chars = len(active_list) + len(graveyard_list)

        kia_count = sum(1 for c in graveyard_list if "kia" in c["status"].lower())
        mia_count = sum(1 for c in graveyard_list if "mia" in c["status"].lower())
        inaktif_count = sum(1 for c in graveyard_list if "inaktif" in c["status"].lower() or "inactive" in c["status"].lower())
        other_count = len(graveyard_list) - kia_count - mia_count - inaktif_count

        total_xp = 0
        for c in active_list + graveyard_list:
            try:
                cleaned = str(c["xp"]).replace(".", "").replace(",", "").strip()
                total_xp += int(cleaned)
            except Exception:
                pass

        gm_breakdown = []
        total_gm_count = 0
        for c in active_list + graveyard_list:
            c_norm = c["norm_char"]
            gm_c = self._cache_gm_counts.get(c_norm, 0)
            if gm_c > 0:
                is_active = (c["type"] == "Active")
                status_label = f"Active - {c['rank']}" if is_active else f"Archived - {c['status']}"
                gm_breakdown.append({
                    "char_name": c["char_name"],
                    "count": gm_c,
                    "status_label": status_label
                })
                total_gm_count += gm_c

        gm_breakdown.sort(key=lambda x: x["count"], reverse=True)

        embed = discord.Embed(
            title=f"GM Player Profile: {target_player}",
            color=discord.Color.gold()
        )

        desc_lines = []
        if match_note:
            desc_lines.append(match_note)

        desc_lines.extend([
            f"**Player:** `{target_player}`",
            f"**Total GM Sessions:** **{total_gm_count} Session(s)**",
            f"**Total Characters:** **{total_chars}** ({len(active_list)} Active • {len(graveyard_list)} Archived)",
        ])

        if graveyard_list:
            gy_parts = []
            if kia_count:
                gy_parts.append(f"{kia_count} KIA")
            if mia_count:
                gy_parts.append(f"{mia_count} MIA")
            if inaktif_count:
                gy_parts.append(f"{inaktif_count} Inactive")
            if other_count:
                gy_parts.append(f"{other_count} Other")
            if gy_parts:
                desc_lines.append(f"**Archived Status:** {', '.join(gy_parts)}")

        desc_lines.append("──────────────────────────────")

        if gm_breakdown:
            desc_lines.append(f"**GM Session History ({len(gm_breakdown)})**")
            for g in gm_breakdown:
                desc_lines.append(f"• **{g['char_name']}**: **{g['count']}** GM Session(s) *({g['status_label']})*")

            other_chars_count = total_chars - len(gm_breakdown)
            if other_chars_count > 0:
                desc_lines.append(f"\n*{other_chars_count} other character(s) have no recorded GM sessions.*")
        else:
            desc_lines.append("**GM History:** *No recorded GM sessions found.*")

        embed.description = "\n".join(desc_lines)

        if avatar_url:
            embed.set_footer(text="Mineria RPG • Player XP Tracker", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Player XP Tracker")

        await ctx.send(embed=embed)

    # =========================================================================
    # SECTION 5: BEST CHARACTER MISSION RANKINGS (!best, /best)
    # =========================================================================

    @commands.command(name="best", aliases=["top", "mostmissions", "bestchar"])
    async def best_character_command(self, ctx: commands.Context, *, player_query: Optional[str] = None) -> None:
        """
        Ranks a player's characters by most missions/games played in descending order.
        Usage: !best <player's full name> (e.g.: !best John Doe)
        """
        if ctx.author.id != DEVELOPER_ID and not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to administrators.")
            return

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None

        if not player_query or not player_query.strip():
            embed = discord.Embed(
                title="Most Active Characters Ranking",
                description=(
                    "Lists all active and archived characters of a player ranked by most missions/games played.\n\n"
                    "**Usage:** `!best <player's full name>`\n"
                    "**Example:** `!best John Doe`\n\n"
                    "**Alternative Commands:** `!m best`, `!top`, `!mostmissions`, `!bestchar`"
                ),
                color=discord.Color.gold()
            )
            if avatar_url:
                embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
            else:
                embed.set_footer(text="Mineria RPG • Character Rankings")
            await ctx.send(embed=embed)
            return

        raw_query = player_query.strip().strip('"\'')
        query_norm = normalize_name(raw_query)

        msg = await ctx.send("Scanning Player XP Tracker data...")
        await self.fetch_xp_data()

        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

        if not self._cache_players:
            await ctx.send("Failed to fetch XP table data or sheet is empty.")
            return

        target_player = None
        match_note = None

        if query_norm in self._cache_players:
            target_player = self._cache_players[query_norm]

        if not target_player and query_norm in self._cache_char_map:
            owners = list({e["player_name"] for e in self._cache_char_map[query_norm]})
            if len(owners) == 1:
                target_player = owners[0]
                matched_char_name = self._cache_char_map[query_norm][0]["char_name"]
                match_note = f"Character Match: **{matched_char_name}** -> **{target_player}**"

        if not target_player:
            candidates = [p for norm_p, p in self._cache_players.items() if query_norm in norm_p or norm_p in query_norm]
            if len(candidates) == 1:
                target_player = candidates[0]
            elif len(candidates) > 1:
                cand_list = "\n".join([f"• `{p}`" for p in candidates[:15]])
                if len(candidates) > 15:
                    cand_list += f"\n*...and {len(candidates) - 15} more player(s).*"
                embed = discord.Embed(
                    title=f"Multiple Players Found ({len(candidates)})",
                    description=(
                        f"Players matching **'{raw_query}'**:\n\n"
                        f"{cand_list}\n\n"
                        f"Please try again using the player's full name:\n`!best <player's full name>`"
                    ),
                    color=discord.Color.orange()
                )
                if avatar_url:
                    embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
                await ctx.send(embed=embed)
                return

        if not target_player:
            char_candidates = [norm_c for norm_c in self._cache_char_map if query_norm in norm_c]
            if len(char_candidates) == 1:
                matched_entries = self._cache_char_map[char_candidates[0]]
                owners = list({e["player_name"] for e in matched_entries})
                if len(owners) == 1:
                    target_player = owners[0]
                    match_note = f"Character Match: **{matched_entries[0]['char_name']}** -> **{target_player}**"
            elif not char_candidates:
                scored_c = []
                for norm_c in self._cache_char_map:
                    sim = difflib.SequenceMatcher(None, query_norm, norm_c).ratio()
                    words = norm_c.split()
                    if words:
                        w_sim = max(difflib.SequenceMatcher(None, query_norm, w).ratio() for w in words)
                        sim = max(sim, w_sim * 0.9)
                    if sim >= 0.70:
                        scored_c.append((sim, norm_c))
                if scored_c:
                    scored_c.sort(key=lambda x: x[0], reverse=True)
                    if len(scored_c) == 1 or scored_c[0][0] >= 0.80 or (scored_c[0][0] - scored_c[1][0] >= 0.08):
                        best_norm = scored_c[0][1]
                        matched_entries = self._cache_char_map[best_norm]
                        owners = list({e["player_name"] for e in matched_entries})
                        if len(owners) == 1:
                            target_player = owners[0]
                            match_note = f"Character Match: **{matched_entries[0]['char_name']}** -> **{target_player}**"

        if not target_player:
            close_matches = difflib.get_close_matches(query_norm, list(self._cache_players.keys()), n=4, cutoff=0.5)
            embed = discord.Embed(
                title="Player Not Found",
                description=f"No record found for **{raw_query}** on the Player XP Tracker.",
                color=discord.Color.red()
            )
            if close_matches:
                sug_list = "\n".join([f"• `!best {self._cache_players[c]}`" for c in close_matches])
                embed.add_field(name="Did you mean:", value=sug_list, inline=False)
            if avatar_url:
                embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
            await ctx.send(embed=embed)
            return

        target_norm = normalize_name(target_player)
        active_list = [c for c in self._cache_active if c["norm_player"] == target_norm]
        graveyard_list = [c for c in self._cache_graveyard if c["norm_player"] == target_norm]

        def _safe_int(val: Any) -> int:
            try:
                return int(str(val).replace(".", "").replace(",", "").strip())
            except Exception:
                return 0

        all_ranked_chars = []
        for c in active_list:
            all_ranked_chars.append({
                "char_name": c["char_name"],
                "norm_char": c["norm_char"],
                "type": "Active",
                "status_label": f"Active - {c['rank']}",
                "missions_count": _safe_int(c["missions"]),
                "xp_int": _safe_int(c["xp"]),
            })

        for c in graveyard_list:
            all_ranked_chars.append({
                "char_name": c["char_name"],
                "norm_char": c["norm_char"],
                "type": "Archived",
                "status_label": f"Archived - {c['status']}",
                "missions_count": _safe_int(c["games"]),
                "xp_int": _safe_int(c["xp"]),
            })

        if not all_ranked_chars:
            await ctx.send(f"No characters found for player **{target_player}**.")
            return

        all_ranked_chars.sort(key=lambda x: (x["missions_count"], x["xp_int"]), reverse=True)

        total_missions = sum(c["missions_count"] for c in all_ranked_chars)
        total_xp = sum(c["xp_int"] for c in all_ranked_chars)

        embed = discord.Embed(
            title=f"Most Missions Played: {target_player}",
            color=discord.Color.gold()
        )

        desc_lines = []
        if match_note:
            desc_lines.append(match_note)

        desc_lines.extend([
            f"**Player:** `{target_player}`",
            f"**Total Characters:** **{len(all_ranked_chars)}** ({len(active_list)} Active • {len(graveyard_list)} Archived)",
            f"**Total Missions:** **{total_missions:,}** • **Total XP:** **{total_xp:,} XP**",
            "──────────────────────────────",
        ])

        medals = {1: "[#1]", 2: "[#2]", 3: "[#3]"}
        ranking_lines = []
        for rank, c in enumerate(all_ranked_chars, start=1):
            medal = medals.get(rank, f"`#{rank:02d}`")
            gm_c = self._cache_gm_counts.get(c["norm_char"], 0)
            gm_badge = f" • **{gm_c} GM**" if gm_c > 0 else ""
            line = f"{medal} **{c['char_name']}** — **{c['missions_count']}** Mission(s) *({c['status_label']} • {format_number(c['xp_int'])} XP)*{gm_badge}"
            ranking_lines.append(line)

        desc_lines.append("\n".join(ranking_lines))
        embed.description = "\n".join(desc_lines)

        if avatar_url:
            embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Character Rankings")

        await ctx.send(embed=embed)

    # =========================================================================
    # SECTION 6: DUPLICATE PLAYER & RANK RULE ENFORCEMENT (!d, /d)
    # =========================================================================

    @commands.command(name="d", aliases=["dup", "checkdup"])
    async def duplicate_check_command(self, ctx: commands.Context, *args: str) -> None:
        """
        Scans the Google Sheet for players violating character limit rules:
        Max 1 Ranked (Senior/Expert/Wanderer) + 1 Clerk per player.
        Usage: !d [refresh]
        """
        if ctx.author.id != DEVELOPER_ID and not await is_user_authorized(self.bot, ctx.author):
            await ctx.send("This command is restricted to administrators.")
            return

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        force_refresh = any(arg.lower() in ("refresh", "reload", "r") for arg in args)

        msg = await ctx.send("Fetching XP table data...")
        data, skipped = await self.fetch_xp_data(force_refresh=force_refresh)
        if not data and skipped == 0:
            err_msg = "Error: Failed to fetch XP table data or sheet is empty. Please check logs."
            if msg:
                await msg.edit(content=err_msg)
            else:
                await ctx.send(err_msg)
            return

        active_chars = []
        inactive_count = 0

        for entry in data:
            rank_str = entry.get("rank", "").lower()
            if any(k in rank_str for k in INACTIVE_KEYWORDS):
                inactive_count += 1
            else:
                active_chars.append(entry)

        players: Dict[str, Tuple[str, list]] = {}
        for entry in active_chars:
            raw_p = entry["player_name"].strip()
            norm_p = normalize_name(raw_p)
            if norm_p not in players:
                players[norm_p] = (raw_p, [])
            players[norm_p][1].append(entry)

        violations: Dict[str, Tuple[str, list, str]] = {}

        for norm_p, (display_name, chars) in players.items():
            if len(chars) <= 1:
                continue

            if len(chars) >= 3:
                reason = f"**3+ Character Violation** ({len(chars)} active characters)"
                violations[norm_p] = (display_name, chars, reason)
            elif len(chars) == 2:
                c1_rank = chars[0].get("rank", "").lower()
                c2_rank = chars[1].get("rank", "").lower()

                c1_is_clerk = "clerk" in c1_rank
                c2_is_clerk = "clerk" in c2_rank
                clerk_count = (1 if c1_is_clerk else 0) + (1 if c2_is_clerk else 0)

                def is_qualified_ranked(r_str: str) -> bool:
                    return any(k in r_str for k in QUALIFIED_RANKS)

                if clerk_count == 2:
                    reason = "**2 Clerk Character Violation**"
                    violations[norm_p] = (display_name, chars, reason)
                elif clerk_count == 0:
                    if any(k in c1_rank for k in ["candidate"]) and any(k in c2_rank for k in ["candidate"]):
                        reason = "**2 Candidate Character Violation**"
                    else:
                        reason = "**2 Ranked Character Violation** (Missing Clerk character)"
                    violations[norm_p] = (display_name, chars, reason)
                elif clerk_count == 1:
                    other_rank = c2_rank if c1_is_clerk else c1_rank
                    if is_qualified_ranked(other_rank):
                        # Compliant combination (Senior + Clerk, Expert + Clerk, or Wanderer + Clerk)
                        pass
                    elif any(k in other_rank for k in ["candidate"]):
                        reason = "**Candidate + Clerk Violation** (Only Senior/Expert/Wanderer + Clerk allowed)"
                        violations[norm_p] = (display_name, chars, reason)
                    elif any(k in other_rank for k in ["member"]):
                        reason = "**Member + Clerk Violation** (Only Senior/Expert/Wanderer + Clerk allowed)"
                        violations[norm_p] = (display_name, chars, reason)
                    else:
                        reason = "**Invalid Duo Violation** (Only Senior/Expert/Wanderer + Clerk allowed)"
                        violations[norm_p] = (display_name, chars, reason)

        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

        has_violations = bool(violations)
        embed = discord.Embed(
            title="Duplicate Player Check",
            color=discord.Color.red() if has_violations else discord.Color.green()
        )

        embed.add_field(
            name="Scan Summary",
            value=(
                f"Scanned: **{len(data)}** entries\n"
                f"Active:   **{len(active_chars)}** characters\n"
                f"Inactive: **{inactive_count}** (ignored)\n"
                f"Skipped:  **{skipped}** rows (missing data)"
            ),
            inline=True
        )

        if has_violations:
            embed.add_field(name="Status", value=f"**{len(violations)}** player(s) in violation", inline=True)
        else:
            embed.add_field(name="Status", value="All active players are **compliant**!", inline=True)

        embed.add_field(
            name="Allowed Rule",
            value="Max **1 Ranked** (Senior / Expert / Wanderer) + **1 Clerk** per player",
            inline=False
        )

        if has_violations:
            embed.add_field(name="\u200b", value="─" * 30, inline=False)
            items_added = 0
            for _norm_p, (display_name, chars, reason) in violations.items():
                if items_added >= 15:
                    embed.add_field(
                        name="Other Violations",
                        value=f"*...and {len(violations) - items_added} more player(s) in violation.*",
                        inline=False
                    )
                    break

                char_lines = []
                for c in chars:
                    r_lower = c.get("rank", "").lower()
                    is_clerk = "clerk" in r_lower
                    role_tag = "Clerk" if is_clerk else "Ranked"
                    char_name = c.get("char_name", "Unknown")
                    rank_name = c.get("rank", "Unknown")
                    char_lines.append(f"[{role_tag}] **{char_name}** — *{rank_name}*")

                val_text = f"**Reason:** {reason}\n" + "\n".join(char_lines)
                if len(val_text) > 1000:
                    val_text = val_text[:990] + "\n*...*"

                embed.add_field(name=f"{display_name} ({len(chars)} Characters)", value=val_text, inline=False)
                items_added += 1
        else:
            embed.add_field(name="Result", value="No violations found. The server is compliant.", inline=False)

        if avatar_url:
            embed.set_footer(text="Mineria RPG • Rule Enforcement", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Rule Enforcement")

        await ctx.send(embed=embed)

    # =========================================================================
    # SECTION 7: FALLEN (KIA) & MISSING (MIA) XP SYSTEM (!kia, !mia)
    # =========================================================================

    async def fetch_and_calculate_xp(
        self, ctx: Any, char_name: str, multiplier: float, title: str, color: discord.Color
    ) -> None:
        """
        Calculates starting XP for replacement characters:
        KIA: (Fixed XP) + (50% of Task XP)
        MIA: (Fixed XP) + (90% of Task XP)
        """
        char_name = char_name.strip().strip('"\'')
        if not char_name:
            await ctx.send("Character name required. Example: `!kia Varka`")
            return

        async with ctx.typing():
            try:
                await self.fetch_xp_data()
                data = self._cache_kia_data
                if not data:
                    await ctx.send("Sheet data is empty.")
                    return

                matched_char, candidates = find_sheet_character(char_name, data)

                if not matched_char:
                    if candidates:
                        options = ", ".join(f"**{c['raw_name']}**" for c in candidates[:5])
                        await ctx.send(f"Character **{char_name}** not found. Did you mean: {options}?")
                    else:
                        await ctx.send(f"Character **{char_name}** not found in XP sheet.")
                    return

                full_xp = matched_char["full_xp"]
                task_xp = matched_char["task_xp"]
                display_name = matched_char["raw_name"]

                # Calculate final XP and level milestones
                added_xp = task_xp * multiplier
                final_xp = full_xp + added_xp
                level, xp_needed, next_level = get_level_info(final_xp)
                pct = int(multiplier * 100)

                base_title = title.replace(" Calculation", "").strip()
                embed_title = f"{base_title} • {display_name}"

                embed = discord.Embed(title=embed_title, color=color)

                embed.add_field(name="Base XP", value=f"{full_xp:,.0f} XP", inline=True)
                embed.add_field(
                    name=f"Added XP ({pct}%)",
                    value=f"{added_xp:,.0f} XP\n*(from {task_xp:,.0f} Task XP)*",
                    inline=True
                )
                embed.add_field(name="Total Starting XP", value=f"**{final_xp:,.0f} XP**", inline=False)
                embed.add_field(name="Starting Level", value=f"**Level {level}**", inline=True)

                if level < 20:
                    next_text = f"**{xp_needed:,.0f} XP** remaining for Level {next_level}."
                else:
                    next_text = "Max Level reached."
                embed.add_field(name="Next Level", value=next_text, inline=True)

                footer_text = "Mineria RPG • System"
                if normalize_name(char_name) != matched_char["norm_name"]:
                    footer_text += f" • Matched from '{char_name}'"

                avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
                if avatar_url:
                    embed.set_footer(text=footer_text, icon_url=avatar_url)
                else:
                    embed.set_footer(text=footer_text)

                await ctx.send(embed=embed)

            except asyncio.TimeoutError:
                logger.warning(f"Timeout during {title} command for '{char_name}'")
                await ctx.send("Request timed out.")
            except aiohttp.ClientError as exc:
                err_text = str(exc).strip() or type(exc).__name__
                logger.error(f"ClientError during {title} command: {err_text}", exc_info=True)
                await ctx.send(f"Network error: {err_text}")
            except Exception as exc:
                logger.error(f"Error during {title} command: {exc}", exc_info=True)
                err_text = str(exc).strip() or f"{type(exc).__name__}"
                await ctx.send(f"Error: {err_text}")

    @commands.command(name="kia")
    async def kia_command(self, ctx: commands.Context, *, char_name: str) -> None:
        """
        Calculates dead character's starting XP: (K+L+M) + (0.5 * (I+J)).
        Usage: !kia <character name>
        """
        await self.fetch_and_calculate_xp(ctx, char_name, 0.5, "KIA XP", discord.Color.dark_red())

    @commands.command(name="mia")
    async def mia_command(self, ctx: commands.Context, *, char_name: str) -> None:
        """
        Calculates missing character's starting XP: (K+L+M) + (0.9 * (I+J)).
        Usage: !mia <character name>
        """
        await self.fetch_and_calculate_xp(ctx, char_name, 0.9, "MIA XP", discord.Color.gold())

    # =========================================================================
    # SECTION 8: SLASH APPLICATION COMMANDS & AUTOCOMPLETE
    # =========================================================================

    async def _kia_character_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Provides autocomplete choices from user's saved characters and campaign sheet cache."""
        choices = []
        curr_lower = current.lower().strip()
        seen_names = set()

        # 1. User's saved characters
        try:
            characters = await load_json("characters.json")
            uid = str(interaction.user.id)
            user_chars = characters.get(uid, [])
            for c in user_chars:
                name = c.get("name", "")
                if name and (not curr_lower or curr_lower in name.lower()):
                    seen_names.add(name.lower())
                    label = f"{name} (Saved Character)"
                    if len(label) > 100:
                        label = label[:97] + "..."
                    choices.append(app_commands.Choice(name=label, value=name))
                    if len(choices) >= 25:
                        return choices
        except Exception:
            pass

        # 2. Characters from Google Sheet cache
        if self._cache_kia_data:
            for char in self._cache_kia_data:
                raw_name = char.get("raw_name", "")
                if raw_name and raw_name.lower() not in seen_names:
                    if not curr_lower or curr_lower in char.get("norm_name", "") or curr_lower in raw_name.lower():
                        seen_names.add(raw_name.lower())
                        label = f"{raw_name} (Campaign Sheet)"
                        if len(label) > 100:
                            label = label[:97] + "..."
                        choices.append(app_commands.Choice(name=label, value=raw_name))
                        if len(choices) >= 25:
                            return choices

            # 3. Fuzzy matches for typos
            if curr_lower and len(choices) < 25:
                fuzzy_scored = []
                for char in self._cache_kia_data:
                    raw_name = char.get("raw_name", "")
                    if raw_name and raw_name.lower() not in seen_names:
                        norm = char.get("norm_name", "")
                        sim = difflib.SequenceMatcher(None, curr_lower, norm).ratio()
                        words = norm.split()
                        if words:
                            word_sim = max(difflib.SequenceMatcher(None, curr_lower, w).ratio() for w in words)
                            sim = max(sim, word_sim * 0.9)
                        if sim >= 0.65:
                            fuzzy_scored.append((sim, raw_name))
                fuzzy_scored.sort(key=lambda x: x[0], reverse=True)
                for _, r_name in fuzzy_scored:
                    seen_names.add(r_name.lower())
                    label = f"{r_name} (Campaign Sheet)"
                    if len(label) > 100:
                        label = label[:97] + "..."
                    choices.append(app_commands.Choice(name=label, value=r_name))
                    if len(choices) >= 25:
                        break

        return choices

    @app_commands.command(name="kia", description="Calculate KIA starting XP from Google Sheet or saved character")
    @app_commands.describe(char_name="Name of the fallen character")
    async def slash_kia(self, interaction: discord.Interaction, char_name: str) -> None:
        await interaction.response.defer()
        adapter = InteractionContextAdapter(interaction, self.bot)
        await self.fetch_and_calculate_xp(adapter, char_name, 0.5, "KIA XP", discord.Color.dark_red())

    @slash_kia.autocomplete("char_name")
    async def slash_kia_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._kia_character_autocomplete(interaction, current)

    @app_commands.command(name="mia", description="Calculate MIA starting XP from Google Sheet or saved character")
    @app_commands.describe(char_name="Name of the missing character")
    async def slash_mia(self, interaction: discord.Interaction, char_name: str) -> None:
        await interaction.response.defer()
        adapter = InteractionContextAdapter(interaction, self.bot)
        await self.fetch_and_calculate_xp(adapter, char_name, 0.9, "MIA XP", discord.Color.gold())

    @slash_mia.autocomplete("char_name")
    async def slash_mia_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        return await self._kia_character_autocomplete(interaction, current)


KiaCog = OneTimeCommands
Utility = OneTimeCommands


async def setup(bot: commands.Bot) -> None:
    """Extension entry point for loading the Utility Cog."""
    await bot.add_cog(OneTimeCommands(bot))
