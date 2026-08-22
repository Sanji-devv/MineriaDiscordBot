import discord
from typing import Dict, Any, List, Optional, Tuple, Union
import json
import aiofiles
import random
import statistics
from pathlib import Path
import copy
import time
from log_handler import logger

# =================================================================================================
# CONSTANTS & PATHS
# =================================================================================================

DATA_DIR = Path(__file__).parent / "datas"
_JSON_CACHE: Dict[str, Tuple[float, Any]] = {}

# =================================================================================================
# HELPER FUNCTIONS
# =================================================================================================

async def load_json(filename: str, force_reload: bool = False) -> Union[Dict, List, Any]:
    """Loads JSON data with in-memory caching for instant access."""
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
        return data
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"❌ Failed to load JSON file '{filename}': {e}")
        raise

async def save_json(filename: str, data: Any) -> None:
    """Saves data to a JSON file and updates the in-memory cache."""
    path = DATA_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    _JSON_CACHE[filename] = (time.time(), copy.deepcopy(data))
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(json.dumps(data, indent=4))


def roll_stat_detailed(num_dice: int) -> Tuple[List[int], List[int]]:
    """Rolls N d6 and returns (all_rolls, top_3)."""
    all_rolls = [random.randint(1, 6) for _ in range(num_dice)]
    kept_rolls = sorted(all_rolls, reverse=True)[:3]
    return all_rolls, kept_rolls

def get_recommendations(stats: Dict[str, int], classes: List[dict]) -> List[dict]:
    """
    Generates class recommendations based on stats.
    Returns: List of dicts with 'name' and 'score'.
    """

    recs = []
    for cls in classes:
        primaries = cls.get("primary_stats", [])
        secondaries = cls.get("secondary_stats", [])
        
        # Helper to get values for a list of stats
        get_vals = lambda slist: [stats.get(s, 10) for s in slist]

        # Score Calculation
        p_score = statistics.mean(get_vals(primaries)) * 1.0 if primaries else 0
        s_score = statistics.mean(get_vals(secondaries)) * 0.5 if secondaries else 0
        
        total_score = p_score + s_score
        
        # Add slight randomization to break ties
        variance = random.uniform(0.95, 1.05)
        
        recs.append({
            "name": cls.get("name", "Unknown"),
            "score": total_score * variance
        })
        
    return sorted(recs, key=lambda x: x["score"], reverse=True)[:5]

# =================================================================================================
# VIEWS
# =================================================================================================

class BonusSelectView(discord.ui.View):
    def __init__(self, cog, ctx, creation, roll_history, bonus_val):
        super().__init__(timeout=300)
        self.cog = cog
        self.ctx = ctx
        self.creation = creation
        self.roll_history = roll_history
        self.bonus_val = bonus_val
        
        stats = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
        for stat in stats:
            btn = discord.ui.Button(label=f"+{bonus_val} {stat}", style=discord.ButtonStyle.secondary, custom_id=stat)
            btn.callback = self.make_callback(stat)
            self.add_item(btn)

    def make_callback(self, stat):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.ctx.author.id:
                return await interaction.response.send_message("❌ Not your session!", ephemeral=True)
            
            if self.cog.active_creations.get(interaction.user.id) is not self.creation:
                return await interaction.response.send_message("❌ This creation session is no longer active!", ephemeral=True)
            
            # Apply
            if stat not in self.creation["stats"]:
                 self.creation["stats"][stat] = 0
            self.creation["stats"][stat] += self.bonus_val
            
            # Disable UIs
            for child in self.children:
                child.disabled = True
                if child.custom_id == stat:
                    child.style = discord.ButtonStyle.success
            
            # Update Embed
            new_history = self.roll_history + f"\n✨ **Flexible Bonus**: Applied **+{self.bonus_val} {stat}**"
            self.creation["stat_history"] = new_history
            
            racial_mods = self.cog.parse_racial_modifiers(self.creation["race_data"])
            embed = self.cog.generate_stat_embed(self.ctx, self.creation, new_history, racial_mods)
            
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
        return callback

