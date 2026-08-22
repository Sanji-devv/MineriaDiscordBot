import discord
from discord.ext import commands, tasks
import csv
import io
import aiohttp
import time
from typing import Tuple, List, Dict, Any, Optional
from pathlib import Path
import os
from dotenv import load_dotenv
from log_handler import logger

load_dotenv(Path(__file__).parent / ".env")
XP_SHEET_URL = os.getenv("XP_SHEET_URL")

INACTIVE_KEYWORDS = (
    "inactive", "inaktif", "in-aktif",
    "dead", "ölü", "olu",
    "left", "leave", "ayrıldı", "ayrildi",
    "pasif", "ex", "emekli"
)

QUALIFIED_RANKS = ("kıdemli", "kidemli", "uzman", "gezgin", "senior", "expert", "wanderer")

class OneTimeCommands(commands.Cog):
    """XP table queries and duplicate player detection with high-performance caching."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache_data: List[Dict[str, Any]] = []
        self._cache_skipped: int = 0
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 300.0  # 5 minutes cache TTL
        self.auto_refresh_xp.start()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Reuse or create persistent aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    def cog_unload(self):
        self.auto_refresh_xp.cancel()
        if self._session and not self._session.closed:
            self.bot.loop.create_task(self._session.close())

    @tasks.loop(minutes=5)
    async def auto_refresh_xp(self):
        """Background task to keep XP sheet cache fresh."""
        if XP_SHEET_URL:
            await self.fetch_xp_data(force_refresh=True)

    @auto_refresh_xp.before_loop
    async def before_auto_refresh(self):
        await self.bot.wait_until_ready()

    async def fetch_xp_data(self, force_refresh: bool = False) -> Tuple[List[Dict[str, Any]], int]:
        """
        Fetches and parses the XP Google Sheet data with in-memory TTL caching.

        Returns:
            Tuple: (List of character dicts, Count of skipped/invalid rows)
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
                content = await resp.text()
        except Exception as e:
            logger.error(f"Exception while fetching XP data: {e}")
            return self._cache_data, self._cache_skipped

        reader = csv.reader(io.StringIO(content))
        rows = list(reader)
        if not rows:
            return self._cache_data, self._cache_skipped

        skipped_count = 0
        parsed = []

        for row in rows[1:]:
            if len(row) < 5:
                skipped_count += 1
                continue

            char_name   = row[1].strip()
            player_name = row[2].strip()
            xp          = row[3].strip()
            rank        = row[4].strip()

            if not player_name or not char_name:
                skipped_count += 1
                continue

            parsed.append({
                "char_name":   char_name,
                "player_name": player_name,
                "xp":          xp,
                "rank":        rank,
            })

        self._cache_data = parsed
        self._cache_skipped = skipped_count
        self._cache_timestamp = now
        return self._cache_data, self._cache_skipped

    @commands.command(name="d", aliases=["dup", "checkdup"])
    async def duplicate_check_command(self, ctx: commands.Context, *args):
        """
        Scans the Google Sheet for players violating character limit rules.

        Usage: !d [refresh]
        """
        force_refresh = any(arg.lower() in ("refresh", "reload", "r") for arg in args)
        
        msg = None
        # Send fetching indicator only if cache is cold or forced refresh
        if force_refresh or not self._cache_data or (time.time() - self._cache_timestamp >= self._cache_ttl):
            msg = await ctx.send("🔄 Fetching XP table data...")

        data, skipped = await self.fetch_xp_data(force_refresh=force_refresh)
        if not data and skipped == 0:
            err_msg = "❌ Error: Failed to fetch XP table data or sheet is empty. Please check logs."
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
            norm_p = raw_p.lower()
            if norm_p not in players:
                players[norm_p] = (raw_p, [])
            players[norm_p][1].append(entry)

        violations: Dict[str, Tuple[str, list, str]] = {}  # norm_p -> (display_name, chars, reason)

        for norm_p, (display_name, chars) in players.items():
            if len(chars) <= 1:
                continue

            if len(chars) >= 3:
                reason = f"🚨 **3+ Character Violation** ({len(chars)} active characters)"
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

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Rule Enforcement", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Rule Enforcement")
            
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(OneTimeCommands(bot))
