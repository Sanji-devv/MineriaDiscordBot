import discord
from discord.ext import commands
import os
import aiohttp
import csv
from io import StringIO
import time
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from dotenv import load_dotenv
from log_handler import logger

# Load environment variables
load_dotenv(Path(__file__).parent / ".env")

# Pre-defined XP threshold table
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

# Pre-sorted level thresholds for instant lookup
XP_LEVELS: List[Tuple[int, int]] = sorted(XP_TABLE.items(), key=lambda x: x[0])

# Pre-compiled translation table for Turkish and lowercase character normalization
TR_MAP = str.maketrans({
    'İ': 'i', 'I': 'i', 'ı': 'i',
    'Ş': 's', 'ş': 's',
    'Ğ': 'g', 'ğ': 'g',
    'Ü': 'u', 'ü': 'u',
    'Ö': 'o', 'ö': 'o',
    'Ç': 'c', 'ç': 'c'
})

def normalize_str(s: str) -> str:
    """Normalize string for robust, case-insensitive, Turkish-character-friendly comparisons."""
    if not s:
        return ""
    return " ".join(s.translate(TR_MAP).lower().split())

def parse_xp_value(val: Any) -> float:
    """Helper to safely parse numeric XP values from sheet cells across all number formats."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    val_str = str(val).strip()
    if not val_str:
        return 0.0
    
    # Remove quotes, currency symbols, and extra spaces
    val_str = val_str.replace('"', '').replace("'", "").replace(" ", "").replace("\u00a0", "")
    
    # If both comma and dot exist (e.g. "1,200.50" or "1.200,50")
    if "," in val_str and "." in val_str:
        if val_str.rfind(",") > val_str.rfind("."):  # European: 1.200,50 -> 1200.50
            val_str = val_str.replace(".", "").replace(",", ".")
        else:  # US: 1,200.50 -> 1200.50
            val_str = val_str.replace(",", "")
    elif "." in val_str:
        # If multiple dots (e.g. 1.200.000) or single dot followed by 3 digits (e.g. 15.000) -> thousand separator
        parts = val_str.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            val_str = val_str.replace(".", "")
    elif "," in val_str:
        # If comma (e.g. 1,200 or 1,200,000 or 1200,5)
        parts = val_str.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            val_str = val_str.replace(",", "")
        else:
            val_str = val_str.replace(",", ".")
        
    try:
        return float(val_str)
    except ValueError:
        return 0.0


class KiaCog(commands.Cog, name="KIA"):
    """
    Commands to calculate KIA and MIA starting XP from the Google Sheet with high-performance caching.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache_data: List[Dict[str, Any]] = []
        self._cache_timestamp: float = 0.0
        self._cache_ttl: float = 180.0  # 3 minutes cache TTL

    async def _get_session(self) -> aiohttp.ClientSession:
        """Reuse or create persistent aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def cog_unload(self):
        """Cleanup persistent resources on cog unload."""
        if self._session and not self._session.closed:
            await self._session.close()

    @staticmethod
    def get_level_info(current_xp: float) -> Tuple[int, float, int]:
        """
        Returns the current level, XP needed for next level, and the next level.
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
            return 20, 0.0, 20  # Max level
            
        xp_needed = XP_TABLE[next_level] - current_xp
        return current_level, xp_needed, next_level

    async def fetch_sheet_data(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetches and pre-processes Google Sheet character data with in-memory TTL cache.
        """
        now = time.time()
        if not force_refresh and self._cache_data and (now - self._cache_timestamp < self._cache_ttl):
            return self._cache_data

        sheet_url = os.getenv("XP_SHEET_URL")
        if not sheet_url:
            raise ValueError("XP_SHEET_URL not found in .env file.")

        session = await self._get_session()
        async with session.get(sheet_url) as response:
            if response.status != 200:
                raise ConnectionError(f"HTTP Status {response.status}")
            csv_data = await response.text()

        reader = csv.reader(StringIO(csv_data))
        rows = list(reader)
        if not rows:
            return []

        parsed_characters: List[Dict[str, Any]] = []
        for row in rows[1:]:  # Skip headers
            if len(row) < 2:
                continue
            
            raw_name = row[1].strip()
            if not raw_name:
                continue

            full_xp = 0.0
            for idx in [10, 11, 12]:  # Fixed XP: K, L, M
                if idx < len(row):
                    full_xp += parse_xp_value(row[idx])

            task_xp = 0.0
            for idx in [8, 9]:  # Task XP: I, J
                if idx < len(row):
                    task_xp += parse_xp_value(row[idx])

            parsed_characters.append({
                "raw_name": raw_name,
                "norm_name": normalize_str(raw_name),
                "full_xp": full_xp,
                "task_xp": task_xp
            })

        self._cache_data = parsed_characters
        self._cache_timestamp = now
        return self._cache_data

    async def fetch_and_calculate_xp(self, ctx: commands.Context, char_name: str, multiplier: float, title: str, color: discord.Color):
        char_name = char_name.strip().strip('"\'')
        if not char_name:
            await ctx.send("❌ Please provide a character name. Example: `!kia Varka`")
            return

        async with ctx.typing():
            try:
                data = await self.fetch_sheet_data()
                if not data:
                    await ctx.send("❌ Error: The Google Sheet data is empty.")
                    return

                clean_target = normalize_str(char_name)
                matched_char: Optional[Dict[str, Any]] = None

                # 1. Exact match (case-insensitive & normalized)
                for char in data:
                    if char["norm_name"] == clean_target:
                        matched_char = char
                        break

                # 2. Partial / Prefix match fallback if exact match not found
                if not matched_char:
                    partial_matches = [char for char in data if clean_target in char["norm_name"]]
                    
                    if len(partial_matches) == 1:
                        matched_char = partial_matches[0]
                    elif len(partial_matches) > 1:
                        options = ", ".join(f"**{c['raw_name']}**" for c in partial_matches[:5])
                        await ctx.send(f"⚠️ Multiple characters found matching `{char_name}`: {options}. Please be more specific.")
                        return

                if not matched_char:
                    await ctx.send(f"❌ Error: Could not find a character named **{char_name}** in the XP sheet.")
                    return

                full_xp = matched_char["full_xp"]
                task_xp = matched_char["task_xp"]
                display_name = matched_char["raw_name"]

                # Calculate final XP and level
                added_xp = task_xp * multiplier
                final_xp = full_xp + added_xp
                level, xp_needed, next_level = self.get_level_info(final_xp)
                pct = int(multiplier * 100)

                # Create Response Embed
                embed = discord.Embed(
                    title=title,
                    description=f"Data retrieved for **{display_name}**.",
                    color=color
                )
                
                embed.add_field(name="Fixed XP (K, L, M)", value=f"{full_xp:,.0f} XP", inline=True)
                embed.add_field(name=f"Added XP ({pct}%)", value=f"{added_xp:,.0f} XP\n*(from {task_xp:,.0f} Task XP)*", inline=True)
                embed.add_field(name="\u200b", value="\u200b", inline=True) # Spacer
                
                embed.add_field(name="Total Starting XP", value=f"**{final_xp:,.0f} XP**", inline=False)
                
                embed.add_field(name="🎖️ Starting Level", value=f"**Level {level}**", inline=True)
                if level < 20:
                    embed.add_field(name="📈 Next Level", value=f"**{xp_needed:,.0f} XP** remaining for Level {next_level}.", inline=True)
                else:
                    embed.add_field(name="📈 Next Level", value="Maximum Level Reached", inline=True)

                avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
                if avatar_url:
                    embed.set_footer(text="Mineria RPG • System", icon_url=avatar_url)
                else:
                    embed.set_footer(text="Mineria RPG • System")

                await ctx.send(embed=embed)

            except ValueError as e:
                await ctx.send(f"❌ Configuration Error: `{str(e)}`")
            except ConnectionError as e:
                await ctx.send(f"❌ Error: Could not reach Google Sheets ({str(e)}). Please check the connection.")
            except Exception as e:
                logger.error(f"Error during {title} command: {e}", exc_info=True)
                await ctx.send(f"❌ An error occurred: `{str(e)}`")

    @commands.command(name="kia")
    async def kia_command(self, ctx: commands.Context, *, char_name: str):
        """
        Calculates dead character's starting XP: (K+L+M) + (0.5 * (I+J)).
        Usage: !kia <character name>
        """
        await self.fetch_and_calculate_xp(ctx, char_name, 0.5, "💀 KIA XP Calculation", discord.Color.dark_red())

    @commands.command(name="mia")
    async def mia_command(self, ctx: commands.Context, *, char_name: str):
        """
        Calculates missing character's starting XP: (K+L+M) + (0.9 * (I+J)).
        Usage: !mia <character name>
        """
        await self.fetch_and_calculate_xp(ctx, char_name, 0.9, "🕵️ MIA XP Calculation", discord.Color.gold())


async def setup(bot):
    await bot.add_cog(KiaCog(bot))


