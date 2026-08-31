import discord
from discord.ext import commands, tasks
import csv
import io
import aiohttp
import time
import difflib
import re
from typing import Tuple, List, Dict, Any, Optional
from pathlib import Path
import os
from dotenv import load_dotenv
from log_handler import logger

load_dotenv(Path(__file__).parent / ".env")
XP_SHEET_URL = os.getenv("XP_SHEET_URL")
REPORTS_SHEET_URL = os.getenv("REPORTS_SHEET_URL")

if not REPORTS_SHEET_URL and XP_SHEET_URL:
    if "gid=" in XP_SHEET_URL:
        REPORTS_SHEET_URL = re.sub(r'gid=\d+', 'gid=0', XP_SHEET_URL)
    else:
        REPORTS_SHEET_URL = f"{XP_SHEET_URL}&gid=0" if "?" in XP_SHEET_URL else f"{XP_SHEET_URL}?export?format=csv&gid=0"

INACTIVE_KEYWORDS = (
    "inactive", "inaktif", "in-aktif",
    "dead", "ölü", "olu",
    "left", "leave", "ayrıldı", "ayrildi",
    "pasif", "ex", "emekli"
)

QUALIFIED_RANKS = ("kıdemli", "kidemli", "uzman", "gezgin", "senior", "expert", "wanderer")

TR_MAP = str.maketrans({
    'İ': 'i', 'I': 'i', 'ı': 'i',
    'Ş': 's', 'ş': 's',
    'Ğ': 'g', 'ğ': 'g',
    'Ü': 'u', 'ü': 'u',
    'Ö': 'o', 'ö': 'o',
    'Ç': 'c', 'ç': 'c'
})

def normalize_name(s: str) -> str:
    if not s:
        return ""
    return " ".join(s.translate(TR_MAP).lower().split())

def format_number(val: Any) -> str:
    """Format string/int numbers with thousand separators."""
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


class OneTimeCommands(commands.Cog):
    """XP table queries, GM player inspection, and duplicate player detection with high-performance caching."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache_data: List[Dict[str, Any]] = []
        self._cache_active: List[Dict[str, Any]] = []
        self._cache_graveyard: List[Dict[str, Any]] = []
        self._cache_players: Dict[str, str] = {}
        self._cache_char_map: Dict[str, List[Dict[str, Any]]] = {}
        self._cache_gm_counts: Dict[str, int] = {}  # norm_char -> GM session count
        self._cache_skipped: int = 0
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 300.0  # 5 minutes cache TTL
        self.auto_refresh_xp.start()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Reuse or create persistent aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def cog_unload(self):
        self.auto_refresh_xp.cancel()
        if self._session and not self._session.closed:
            await self._session.close()

    @tasks.loop(minutes=5)
    async def auto_refresh_xp(self):
        """Background task to keep XP and Report sheet caches fresh."""
        if XP_SHEET_URL:
            await self.fetch_xp_data(force_refresh=True)

    @auto_refresh_xp.before_loop
    async def before_auto_refresh(self):
        await self.bot.wait_until_ready()

    async def fetch_xp_data(self, force_refresh: bool = False) -> Tuple[List[Dict[str, Any]], int]:
        """
        Fetches and parses both the Player XP Tracker (Left & Right tables) and Görev Raporları (GM column AM).

        Returns:
            Tuple: (List of active character dicts for dup check, Count of skipped/invalid rows)
        """
        now = time.time()
        if not force_refresh and self._cache_data and (now - self._cache_timestamp < self._cache_ttl):
            return self._cache_data, self._cache_skipped

        if not XP_SHEET_URL:
            logger.error("XP_SHEET_URL environment variable is not set.")
            return self._cache_data, self._cache_skipped
            
        try:
            session = await self._get_session()
            async with session.get(XP_SHEET_URL) as resp:
                if resp.status != 200:
                    logger.error(f"Failed to fetch XP sheet: HTTP Status {resp.status}")
                    return self._cache_data, self._cache_skipped
                xp_content = await resp.text()

            report_content = None
            if REPORTS_SHEET_URL:
                try:
                    async with session.get(REPORTS_SHEET_URL) as r_resp:
                        if r_resp.status == 200:
                            report_content = await r_resp.text()
                        else:
                            logger.warning(f"Failed to fetch Reports sheet: HTTP Status {r_resp.status}")
                except Exception as e:
                    logger.warning(f"Exception while fetching Reports sheet: {e}")

        except Exception as e:
            logger.error(f"Exception while fetching XP data: {e}")
            return self._cache_data, self._cache_skipped

        xp_reader = csv.reader(io.StringIO(xp_content))
        xp_rows = list(xp_reader)
        if not xp_rows:
            return self._cache_data, self._cache_skipped

        skipped_count = 0
        parsed_compat = []
        parsed_active = []
        parsed_graveyard = []
        players_dict = {}
        char_map = {}

        for r_idx, row in enumerate(xp_rows[1:], start=2):
            # Left Table: Columns B to N (Active Roster)
            if len(row) > 2:
                char_name   = row[1].strip() if len(row) > 1 else ""
                player_name = row[2].strip() if len(row) > 2 else ""

                if char_name and player_name:
                    norm_p = normalize_name(player_name)
                    norm_c = normalize_name(char_name)
                    if norm_p not in players_dict:
                        players_dict[norm_p] = player_name

                    active_entry = {
                        "row":         r_idx,
                        "char_name":   char_name,
                        "player_name": player_name,
                        "norm_player": norm_p,
                        "norm_char":   norm_c,
                        "xp":          row[3].strip() if len(row) > 3 else "0",
                        "rank":        row[4].strip() if len(row) > 4 else "-",
                        "level":       row[5].strip() if len(row) > 5 else "1",
                        "missions":    row[6].strip() if len(row) > 6 else "0",
                        "rank_xp":     row[7].strip() if len(row) > 7 else "0",
                        "task_xp":     row[8].strip() if len(row) > 8 else "0",
                        "report_xp":   row[9].strip() if len(row) > 9 else "0",
                        "bailiff_xp":  row[10].strip() if len(row) > 10 else "0",
                        "ex_xp":       row[11].strip() if len(row) > 11 else "0",
                        "wiki_xp":     row[12].strip() if len(row) > 12 else "0",
                        "inactive_xp": row[13].strip() if len(row) > 13 else "0",
                        "type":        "Active"
                    }
                    parsed_active.append(active_entry)
                    parsed_compat.append({
                        "char_name":   char_name,
                        "player_name": player_name,
                        "xp":          active_entry["xp"],
                        "rank":        active_entry["rank"],
                    })
                    char_map.setdefault(norm_c, []).append(active_entry)
                elif len(row) >= 5 and (not char_name or not player_name):
                    skipped_count += 1

            # Right Table: Columns P to V (Archived / Graveyard / Retired Roster)
            if len(row) > 16:
                char_name   = row[15].strip() if len(row) > 15 else ""
                player_name = row[16].strip() if len(row) > 16 else ""

                if char_name and player_name:
                    norm_p = normalize_name(player_name)
                    norm_c = normalize_name(char_name)
                    if norm_p not in players_dict:
                        players_dict[norm_p] = player_name

                    graveyard_entry = {
                        "row":         r_idx,
                        "char_name":   char_name,
                        "player_name": player_name,
                        "norm_player": norm_p,
                        "norm_char":   norm_c,
                        "xp":          row[17].strip() if len(row) > 17 else "0",
                        "status":      row[18].strip() if len(row) > 18 else "-",
                        "games":       row[19].strip() if len(row) > 19 else "0",
                        "missions":    row[20].strip() if len(row) > 20 else "0",
                        "reports":     row[21].strip() if len(row) > 21 else "0",
                        "type":        "Archived"
                    }
                    parsed_graveyard.append(graveyard_entry)
                    char_map.setdefault(norm_c, []).append(graveyard_entry)

        # Parse Görev Raporları Column AM (GM Column, idx 38)
        gm_counts = {}
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
        self._cache_skipped = skipped_count
        self._cache_timestamp = now
        return self._cache_data, self._cache_skipped

    # ─────────────────────────────────────────────
    #  !gm  –  Player Character & XP Tracker Query
    # ─────────────────────────────────────────────
    @commands.command(name="gm", aliases=["player", "oyuncu", "gmcheck", "pinfo"])
    async def gm_player_command(self, ctx: commands.Context, *, player_query: str = None):
        """
        Player XP Tracker ve Görev Raporları üzerindeki verileri sorgular.
        B & C (Aktif), P & Q (Arşiv/Graveyard) ve AM (GM Seansları) eşleştirir.
        
        Kullanım: !gm <oyuncunun tam ismi> (Örn: !gm Erdem Uğur Şahin)
        """
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None

        if not player_query or not player_query.strip():
            embed = discord.Embed(
                title="🛡️ GM Oyuncu Sorgulama Terminali",
                description=(
                    "Player XP Tracker ve Görev Raporları tablosundan bir oyuncunun tüm aktif, arşivlenmiş karakterlerini ve GM seanslarını listeler.\n\n"
                    "**Kullanım:** `!gm <oyuncunun tam ismi>`\n"
                    "**Örnek:** `!gm Erdem Uğur Şahin`\n\n"
                    "📌 **Taranan Veriler:**\n"
                    "• **Aktif Karakterler:** B (Karakter) ve C (Oyuncu) Sütunları\n"
                    "• **Arşiv Karakterler:** P (Karakter) ve Q (Oyuncu) Sütunları\n"
                    "• **GM Seansları:** Görev Raporları AM (GM) Sütunu"
                ),
                color=discord.Color.gold()
            )
            if avatar_url:
                embed.set_footer(text="Mineria RPG • GM Player Tracker", icon_url=avatar_url)
            else:
                embed.set_footer(text="Mineria RPG • GM Player Tracker")
            return await ctx.send(embed=embed)

        raw_query = player_query.strip().strip('"\'')
        query_norm = normalize_name(raw_query)

        # Check if data needs fetching
        msg = None
        if not self._cache_active or (time.time() - self._cache_timestamp >= self._cache_ttl):
            msg = await ctx.send("🔄 Player XP Tracker ve Görev Raporları taranıyor...")

        await self.fetch_xp_data()

        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

        if not self._cache_players:
            return await ctx.send("❌ Player XP Tracker verisi alınamadı veya tablo boş.")

        # --- Player Match Logic ---
        target_player = None
        match_note = None

        # 1. Exact match on player name (normalized)
        if query_norm in self._cache_players:
            target_player = self._cache_players[query_norm]

        # 2. Check if the query matches a character name in active or archived roster
        if not target_player and query_norm in self._cache_char_map:
            owners = list({e["player_name"] for e in self._cache_char_map[query_norm]})
            if len(owners) == 1:
                target_player = owners[0]
                matched_char_name = self._cache_char_map[query_norm][0]["char_name"]
                match_note = f"💡 Karakter Eşleşmesi: **{matched_char_name}** ➔ **{target_player}**"

        # 3. Partial match on player names
        if not target_player:
            candidates = [p for norm_p, p in self._cache_players.items() if query_norm in norm_p or norm_p in query_norm]
            if len(candidates) == 1:
                target_player = candidates[0]
            elif len(candidates) > 1:
                cand_list = "\n".join([f"• `{p}`" for p in candidates[:15]])
                if len(candidates) > 15:
                    cand_list += f"\n*...ve {len(candidates) - 15} oyuncu daha.*"
                embed = discord.Embed(
                    title=f"🔢 Birden Fazla Oyuncu Bulundu ({len(candidates)})",
                    description=(
                        f"**'{raw_query}'** aramasına uyan oyuncular:\n\n"
                        f"{cand_list}\n\n"
                        f"Lütfen oyuncunun tam adını yazarak tekrar deneyin:\n"
                        f"`!gm <oyuncunun tam ismi>`"
                    ),
                    color=discord.Color.orange()
                )
                if avatar_url:
                    embed.set_footer(text="Mineria RPG • GM Player Tracker", icon_url=avatar_url)
                return await ctx.send(embed=embed)

        # 4. Partial match on character names
        if not target_player:
            char_candidates = [norm_c for norm_c in self._cache_char_map if query_norm in norm_c]
            if len(char_candidates) == 1:
                matched_entries = self._cache_char_map[char_candidates[0]]
                owners = list({e["player_name"] for e in matched_entries})
                if len(owners) == 1:
                    target_player = owners[0]
                    match_note = f"💡 Karakter Eşleşmesi: **{matched_entries[0]['char_name']}** ➔ **{target_player}**"

        # 5. Not found -> Suggest close player names
        if not target_player:
            close_matches = difflib.get_close_matches(query_norm, list(self._cache_players.keys()), n=4, cutoff=0.5)
            embed = discord.Embed(
                title="❌ Oyuncu Bulunamadı",
                description=f"Player XP Tracker üzerinde **{raw_query}** adına ait oyuncu veya karakter kaydı bulunamadı.",
                color=discord.Color.red()
            )
            if close_matches:
                sug_list = "\n".join([f"• `!gm {self._cache_players[c]}`" for c in close_matches])
                embed.add_field(name="💡 Bunu mu demek istediniz?", value=sug_list, inline=False)
            if avatar_url:
                embed.set_footer(text="Mineria RPG • GM Player Tracker", icon_url=avatar_url)
            return await ctx.send(embed=embed)

        # --- Build Player Overview ---
        target_norm = normalize_name(target_player)
        active_list = [c for c in self._cache_active if c["norm_player"] == target_norm]
        graveyard_list = [c for c in self._cache_graveyard if c["norm_player"] == target_norm]
        total_chars = len(active_list) + len(graveyard_list)

        # Count graveyard categories
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

        # GM Breakdown calculation across all characters
        gm_breakdown = []
        total_gm_count = 0
        for c in active_list + graveyard_list:
            c_norm = c["norm_char"]
            gm_c = self._cache_gm_counts.get(c_norm, 0)
            if gm_c > 0:
                is_active = (c["type"] == "Active")
                status_label = f"Aktif - {c['rank']}" if is_active else f"Arşiv - {c['status']}"
                gm_breakdown.append({
                    "char_name": c["char_name"],
                    "count": gm_c,
                    "status_label": status_label
                })
                total_gm_count += gm_c

        gm_breakdown.sort(key=lambda x: x["count"], reverse=True)

        embed = discord.Embed(
            title=f"🛡️ GM Oyuncu Profili: {target_player}",
            color=discord.Color.gold()
        )

        status_breakdown = []
        if kia_count: status_breakdown.append(f"{kia_count} KIA")
        if mia_count: status_breakdown.append(f"{mia_count} MIA")
        if inaktif_count: status_breakdown.append(f"{inaktif_count} Inaktif")
        if other_count: status_breakdown.append(f"{other_count} Diğer")
        status_str = f" [{', '.join(status_breakdown)}]" if status_breakdown else ""

        desc_lines = []
        if match_note:
            desc_lines.append(match_note)

        desc_lines.extend([
            f"**Oyuncu:** `{target_player}`",
            f"**Toplam GM'lik:** **{total_gm_count} Seans**",
            f"**Toplam Karakter:** **{total_chars}** ( {len(active_list)} Aktif •  {len(graveyard_list)} Arşiv)",
            "──────────────────────────────",
        ])

        # --- GM Breakdown Section ---
        if gm_breakdown:
            desc_lines.append(f"**🎲 GM'lik Yapan Karakterler ({len(gm_breakdown)})**")
            for g in gm_breakdown:
                desc_lines.append(f"• **{g['char_name']}**: **{g['count']}** GM Seansı *({g['status_label']})*")
            
            other_chars_count = total_chars - len(gm_breakdown)
            if other_chars_count > 0:
                desc_lines.append(f"\n*Diğer {other_chars_count} karakterin kayıtlı GM seansı bulunmuyor.*")
        else:
            desc_lines.append("🎲 **GM'lik Geçmişi:** *Kayıtlı GM seansı bulunamadı.*")

        embed.description = "\n".join(desc_lines)

        if avatar_url:
            embed.set_footer(text="Mineria RPG • Player XP Tracker", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Player XP Tracker")

        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────
    #  !best  –  Player Best Character Mission Ranking
    # ─────────────────────────────────────────────
    @commands.command(name="best", aliases=["top", "mostmissions", "gorevler", "bestchar"])
    async def best_character_command(self, ctx: commands.Context, *, player_query: str = None):
        """
        Oyuncunun en fazla göreve/oyuna giden karakterlerini çoktan aza doğru sıralar.
        
        Kullanım: !best <oyuncunun tam ismi> (Örn: !best Erdem Uğur Şahin)
        """
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None

        if not player_query or not player_query.strip():
            embed = discord.Embed(
                title="🏆 En Çok Göreve Giden Karakterler",
                description=(
                    "Bir oyuncunun tüm aktif ve arşiv karakterlerini en çok göreve/oyuna gitme sırasına göre listeler.\n\n"
                    "**Kullanım:** `!best <oyuncunun tam ismi>`\n"
                    "**Örnek:** `!best Erdem Uğur Şahin`\n\n"
                    "**Alternatif Komutlar:** `!m best`, `!top`, `!gorevler`, `!bestchar`"
                ),
                color=discord.Color.gold()
            )
            if avatar_url:
                embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
            else:
                embed.set_footer(text="Mineria RPG • Character Rankings")
            return await ctx.send(embed=embed)

        raw_query = player_query.strip().strip('"\'')
        query_norm = normalize_name(raw_query)

        # Check if data needs fetching
        msg = None
        if not self._cache_active or (time.time() - self._cache_timestamp >= self._cache_ttl):
            msg = await ctx.send("🔄 Player XP Tracker verileri taranıyor...")

        await self.fetch_xp_data()

        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

        if not self._cache_players:
            return await ctx.send("❌ Player XP Tracker verisi alınamadı veya tablo boş.")

        # --- Player Match Logic ---
        target_player = None
        match_note = None

        # 1. Exact match on player name (normalized)
        if query_norm in self._cache_players:
            target_player = self._cache_players[query_norm]

        # 2. Check if the query matches a character name in active or archived roster
        if not target_player and query_norm in self._cache_char_map:
            owners = list({e["player_name"] for e in self._cache_char_map[query_norm]})
            if len(owners) == 1:
                target_player = owners[0]
                matched_char_name = self._cache_char_map[query_norm][0]["char_name"]
                match_note = f"💡 Karakter Eşleşmesi: **{matched_char_name}** ➔ **{target_player}**"

        # 3. Partial match on player names
        if not target_player:
            candidates = [p for norm_p, p in self._cache_players.items() if query_norm in norm_p or norm_p in query_norm]
            if len(candidates) == 1:
                target_player = candidates[0]
            elif len(candidates) > 1:
                cand_list = "\n".join([f"• `{p}`" for p in candidates[:15]])
                if len(candidates) > 15:
                    cand_list += f"\n*...ve {len(candidates) - 15} oyuncu daha.*"
                embed = discord.Embed(
                    title=f"🔢 Birden Fazla Oyuncu Bulundu ({len(candidates)})",
                    description=(
                        f"**'{raw_query}'** aramasına uyan oyuncular:\n\n"
                        f"{cand_list}\n\n"
                        f"Lütfen oyuncunun tam adını yazarak tekrar deneyin:\n"
                        f"`!best <oyuncunun tam ismi>`"
                    ),
                    color=discord.Color.orange()
                )
                if avatar_url:
                    embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
                return await ctx.send(embed=embed)

        # 4. Partial match on character names
        if not target_player:
            char_candidates = [norm_c for norm_c in self._cache_char_map if query_norm in norm_c]
            if len(char_candidates) == 1:
                matched_entries = self._cache_char_map[char_candidates[0]]
                owners = list({e["player_name"] for e in matched_entries})
                if len(owners) == 1:
                    target_player = owners[0]
                    match_note = f"💡 Karakter Eşleşmesi: **{matched_entries[0]['char_name']}** ➔ **{target_player}**"

        # 5. Not found -> Suggest close player names
        if not target_player:
            close_matches = difflib.get_close_matches(query_norm, list(self._cache_players.keys()), n=4, cutoff=0.5)
            embed = discord.Embed(
                title="❌ Oyuncu Bulunamadı",
                description=f"Player XP Tracker üzerinde **{raw_query}** adına ait oyuncu veya karakter kaydı bulunamadı.",
                color=discord.Color.red()
            )
            if close_matches:
                sug_list = "\n".join([f"• `!best {self._cache_players[c]}`" for c in close_matches])
                embed.add_field(name="💡 Bunu mu demek istediniz?", value=sug_list, inline=False)
            if avatar_url:
                embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
            return await ctx.send(embed=embed)

        # --- Build Best Rankings ---
        target_norm = normalize_name(target_player)
        active_list = [c for c in self._cache_active if c["norm_player"] == target_norm]
        graveyard_list = [c for c in self._cache_graveyard if c["norm_player"] == target_norm]

        def _safe_int(val):
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
                "status_label": f"Aktif - {c['rank']}",
                "missions_count": _safe_int(c["missions"]),
                "xp_int": _safe_int(c["xp"]),
            })

        for c in graveyard_list:
            all_ranked_chars.append({
                "char_name": c["char_name"],
                "norm_char": c["norm_char"],
                "type": "Archived",
                "status_label": f"Arşiv - {c['status']}",
                "missions_count": _safe_int(c["games"]),
                "xp_int": _safe_int(c["xp"]),
            })

        if not all_ranked_chars:
            return await ctx.send(f"❌ **{target_player}** adlı oyuncuya ait karakter bulunamadı.")

        all_ranked_chars.sort(key=lambda x: (x["missions_count"], x["xp_int"]), reverse=True)

        total_missions = sum(c["missions_count"] for c in all_ranked_chars)
        total_xp = sum(c["xp_int"] for c in all_ranked_chars)

        embed = discord.Embed(
            title=f"🏆 En Çok Göreve Giden Karakterler: {target_player}",
            color=discord.Color.gold()
        )

        desc_lines = []
        if match_note:
            desc_lines.append(match_note)

        desc_lines.extend([
            f"**Oyuncu:** `{target_player}`",
            f"**Toplam Karakter:** **{len(all_ranked_chars)}** ( {len(active_list)} Aktif •  {len(graveyard_list)} Arşiv)",
            f"**Toplam Görev:** **{total_missions:,}** • **Toplam XP:** **{total_xp:,} XP**",
            "──────────────────────────────",
        ])

        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        ranking_lines = []
        for rank, c in enumerate(all_ranked_chars, start=1):
            medal = medals.get(rank, f"`#{rank:02d}`")
            gm_c = self._cache_gm_counts.get(c["norm_char"], 0)
            gm_badge = f" • 🎲 **{gm_c} GM**" if gm_c > 0 else ""
            line = f"{medal} **{c['char_name']}** — **{c['missions_count']}** Görev *({c['status_label']} • {format_number(c['xp_int'])} XP)*{gm_badge}"
            ranking_lines.append(line)

        # Place ranking directly into description (Discord supports up to 4096 characters)
        desc_lines.append("\n".join(ranking_lines))
        embed.description = "\n".join(desc_lines)

        if avatar_url:
            embed.set_footer(text="Mineria RPG • Character Rankings", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Character Rankings")

        await ctx.send(embed=embed)

    # ─────────────────────────────────────────────
    #  !d  –  Duplicate Active Character Check
    # ─────────────────────────────────────────────
    @commands.command(name="d", aliases=["dup", "checkdup"])
    async def duplicate_check_command(self, ctx: commands.Context, *args):
        """
        Scans the Google Sheet for players violating character limit rules.

        Usage: !d [refresh]
        """
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        force_refresh = any(arg.lower() in ("refresh", "reload", "r") for arg in args)
        
        msg = None
        # Send fetching indicator only if cache is cold or forced refresh
        if force_refresh or not self._cache_data or (time.time() - self._cache_timestamp >= self._cache_ttl):
            msg = await ctx.send("🔄 Fetching XP table data...")

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

        # Case-insensitive player grouping to prevent capitalization bypass
        players: Dict[str, Tuple[str, list]] = {}  # normalized_name -> (display_name, char_entries)
        for entry in active_chars:
            raw_p = entry["player_name"].strip()
            norm_p = normalize_name(raw_p)
            if norm_p not in players:
                players[norm_p] = (raw_p, [])
            players[norm_p][1].append(entry)

        violations: Dict[str, Tuple[str, list, str]] = {}  # norm_p -> (display_name, chars, reason)

        for norm_p, (display_name, chars) in players.items():
            if len(chars) <= 1:
                continue

            if len(chars) >= 3:
                reason = f"**3+ Character Violation** ({len(chars)} active characters)"
                violations[norm_p] = (display_name, chars, reason)
            elif len(chars) == 2:
                c1_rank = chars[0].get('rank', '').lower()
                c2_rank = chars[1].get('rank', '').lower()

                c1_is_clerk = "clerk" in c1_rank
                c2_is_clerk = "clerk" in c2_rank

                clerk_count = (1 if c1_is_clerk else 0) + (1 if c2_is_clerk else 0)

                # Helper to check if a non-clerk rank is Senior (Kıdemli), Expert (Uzman), or Wanderer (Gezgin)
                def is_qualified_ranked(r_str):
                    return any(k in r_str for k in QUALIFIED_RANKS)

                if clerk_count == 2:
                    reason = "🚨 **2 Clerk Character Violation**"
                    violations[norm_p] = (display_name, chars, reason)
                elif clerk_count == 0:
                    if any(k in c1_rank for k in ["aday", "candidate"]) and any(k in c2_rank for k in ["aday", "candidate"]):
                        reason = "🚨 **2 Candidate Character Violation**"
                    else:
                        reason = "🚨 **2 Ranked Character Violation** (Missing Clerk character)"
                    violations[norm_p] = (display_name, chars, reason)
                elif clerk_count == 1:
                    # Exactly one is clerk, find the other character
                    other_rank = c2_rank if c1_is_clerk else c1_rank
                    
                    if is_qualified_ranked(other_rank):
                        # VALID COMBO! (Senior + Clerk, Expert + Clerk, OR Wanderer + Clerk)
                        pass
                    elif any(k in other_rank for k in ["aday", "candidate"]):
                        reason = "🚨 **Candidate + Clerk Violation** (Only Senior/Expert/Wanderer + Clerk allowed)"
                        violations[norm_p] = (display_name, chars, reason)
                    elif any(k in other_rank for k in ["üye", "uye", "member"]):
                        reason = "🚨 **Member + Clerk Violation** (Only Senior/Expert/Wanderer + Clerk allowed)"
                        violations[norm_p] = (display_name, chars, reason)
                    else:
                        reason = "🚨 **Invalid Duo Violation** (Only Senior/Expert/Wanderer + Clerk allowed)"
                        violations[norm_p] = (display_name, chars, reason)

        if msg:
            try:
                await msg.delete()
            except Exception:
                pass

        has_violations = bool(violations)
        embed = discord.Embed(
            title="🔍 Duplicate Player Check",
            color=discord.Color.red() if has_violations else discord.Color.green()
        )

        embed.add_field(
            name="📊 Scan Summary",
            value=(
                f"📋 Scanned: **{len(data)}** entries\n"
                f"🟢 Active:   **{len(active_chars)}** characters\n"
                f"🛌 Inactive: **{inactive_count}** (ignored)\n"
                f"⚠️  Skipped:  **{skipped}** rows (missing data)"
            ),
            inline=True
        )

        if has_violations:
            embed.add_field(
                name="🚨 Status",
                value=f"**{len(violations)}** player(s) in violation",
                inline=True
            )
        else:
            embed.add_field(
                name="✅ Status",
                value="All active players are **compliant**!",
                inline=True
            )

        embed.add_field(
            name="📜 Allowed Rule",
            value="Max **1 Ranked** (Senior / Expert / Wanderer) + **1 Clerk** per player",
            inline=False
        )

        if has_violations:
            embed.add_field(name="\u200b", value="─" * 30, inline=False)
            items_added = 0
            for norm_p, (display_name, chars, reason) in violations.items():
                if items_added >= 15:
                    embed.add_field(
                        name="⚠️ Other Violations",
                        value=f"*...and {len(violations) - items_added} more player(s) in violation.*",
                        inline=False
                    )
                    break

                char_lines = []
                for c in chars:
                    r_lower  = c.get('rank', '').lower()
                    is_clerk = "clerk" in r_lower
                    role_tag = "🟡 Clerk" if is_clerk else "🔴 Ranked"
                    char_name = c.get('char_name', 'Unknown')
                    rank_name = c.get('rank', 'Unknown')
                    char_lines.append(f"{role_tag} **{char_name}** — *{rank_name}*")
                
                val_text = f"**Reason:** {reason}\n" + "\n".join(char_lines)
                if len(val_text) > 1000:
                    val_text = val_text[:990] + "\n*...*"

                embed.add_field(
                    name=f"🚧 {display_name} ({len(chars)} Characters)",
                    value=val_text,
                    inline=False
                )
                items_added += 1

        else:
            embed.add_field(
                name="✅ Result",
                value="No violations found. The server is clean! 🎉",
                inline=False
            )

        if avatar_url:
            embed.set_footer(text="Mineria RPG • Rule Enforcement", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Rule Enforcement")
            
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OneTimeCommands(bot))
