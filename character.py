import discord
from discord.ext import commands
from typing import Dict, Any, List, Optional, Tuple, Union
import re
import json
import aiofiles
import random
import statistics
from char_creation import *
from char_management import *
from char_utils import *
from pathlib import Path

# =================================================================================================
# CHARACTER COG
# =================================================================================================

class CharacterCog(commands.Cog, name="Character"):
    """
    Commands for Character Creation, Management, and Stat Rolling.
    """
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_creations: Dict[int, Dict[str, Any]] = {}

    def parse_racial_modifiers(self, race_data: dict) -> Dict[str, int]:
        """
        Parses racial attribute modifiers from structured JSON.
        """
        mods = {s: 0 for s in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]}
        
        # New Structured Format
        if "modifiers" in race_data:
            for k, v in race_data["modifiers"].items():
                if k in mods:
                    mods[k] = v
            
            if race_data.get("flexible_stat", 0) > 0:
                mods["ANY"] = race_data["flexible_stat"]
            return mods

        # Fallback for old data (Should not trigger if data is migrated)
        stat_map = {
            "Strength": "STR", "Dexterity": "DEX", "Constitution": "CON", 
            "Intelligence": "INT", "Wisdom": "WIS", "Charisma": "CHA"
        }
        
        # Legacy Regex Parsing...
        regex = r"([\+\-]\d+)\s*(\w+)"
        for key in ["Ability Score Plus", "Ability Score Minus"]:
            text = race_data.get(key, "")
            if text and text not in ["None", ""]:
                if "to one ability score" in text.lower(): mods["ANY"] = 2
                for val, name in re.findall(regex, text):
                    for full_name, short_code in stat_map.items():
                        if full_name.lower() in name.lower() or short_code.lower() == name.lower():
                            mods[short_code] += int(val)
                            break
        return mods

    def generate_stat_embed(self, ctx, creation, rolls_text, racial_mods):
        """Generates the Stat Result Embed."""
        embed = discord.Embed(
            title="🎲 Stat Roll Results",
            description=f"Rolled by **{ctx.author.display_name}**\n\u200b\n" + "─"*35, # Spacer for width
            color=discord.Color.gold()
        )

        details_val = rolls_text
        if len(details_val) > 1020:
            details_val = details_val[:1015] + "..."
        embed.add_field(name="📊 Details", value=details_val, inline=False)
        
        final_stats = creation["stats"]

        # Display Stats (Formatted like !char info)
        def fmt_stat_dr(label, key, emoji):
            val = final_stats.get(key, 10)
            mod = (val - 10) // 2
            sign = "+" if mod >= 0 else ""
            return f"{emoji} **{label}**: {val} (`{sign}{mod}`)"

        col1_list = [fmt_stat_dr("STR", "STR", "💪"), fmt_stat_dr("DEX", "DEX", "🏃"), fmt_stat_dr("CON", "CON", "❤️")]
        col2_list = [fmt_stat_dr("INT", "INT", "🧠"), fmt_stat_dr("WIS", "WIS", "🦉"), fmt_stat_dr("CHA", "CHA", "🎭")]
        
        embed.add_field(name="🛡️ Physical", value="\n".join(col1_list), inline=True)
        embed.add_field(name="🔮 Mental", value="\n".join(col2_list), inline=True)

        # Racial info
        plus = creation["race_data"].get("Ability Score Plus", "None") # Keep legacy text for display
        
        # If new structured data exists, format it nicely
        if "modifiers" in creation["race_data"]:
             mods = creation["race_data"]["modifiers"]
             mod_strs = [f"{('+' if v>0 else '')}{v} {k}" for k,v in mods.items()]
             plus = ", ".join(mod_strs)
        
        adj_text = f"**Race**: {creation['race_name']}\n**Mods**: {plus}"
        
        if racial_mods.get("ANY"):
             adj_text += f"\n\n✨ **Flexible Bonus Available!**\nClick a button below to apply +{racial_mods['ANY']}!"
             
        embed.add_field(name="🧬 Traits", value=adj_text, inline=False)
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Use !char save <name> to finalize.", icon_url=avatar_url)
        else:
            embed.set_footer(text="Use !char save <name> to finalize.")
        return embed

    # ==========================
    # MAIN COMMAND GROUP
    # ==========================

    @commands.group(name="char", invoke_without_command=True)
    async def char(self, ctx: commands.Context):
        """Root command for Character Management."""
        embed = discord.Embed(title="👤 Character Commands", color=discord.Color.gold())
        embed.description = (
            "**Creation**\n"
            "`!char create <race>` - Start creation\n"
            "`!char dr <stats>` - Distribute dice\n"
            "`!char add/remove <stat> <val>` - Tweak stats\n"
            "`!char save <name>` - Finalize character\n\n"
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

    # ==========================
    # CREATION FLOW
    # ==========================

    @char.command(name="create")
    async def create(self, ctx: commands.Context, *, race_name: str = None):
        await handle_create(self, ctx, race_name)

    @char.command(name="dr")
    async def distribute(self, ctx: commands.Context, *args):
        await handle_distribute(self, ctx, *args)

    @char.command(name="add")
    async def add_stat(self, ctx: commands.Context, *args):
        await handle_add_stat(self, ctx, *args)

    @char.command(name="remove")
    async def remove_stat(self, ctx: commands.Context, *args):
        await handle_remove_stat(self, ctx, *args)

    @char.command(name="save")
    async def save_char(self, ctx: commands.Context, *, name: str = None):
        await handle_save_char(self, ctx, name=name)

    @commands.group(name="rec", invoke_without_command=True)
    async def rec(self, ctx: commands.Context):
        await handle_rec(self, ctx)

    @rec.command(name="open")
    async def rec_open(self, ctx: commands.Context):
        await handle_rec_open(self, ctx)

    @rec.command(name="close")
    async def rec_close(self, ctx: commands.Context):
        await handle_rec_close(self, ctx)

    @char.group(name="edit", invoke_without_command=True)
    async def edit(self, ctx: commands.Context):
        await handle_edit(self, ctx)

    @edit.command(name="class")
    async def edit_class(self, ctx: commands.Context, *args):
        await handle_edit_class(self, ctx, *args)

    @edit.command(name="stat")
    async def edit_stat(self, ctx: commands.Context, *args):
        await handle_edit_stat(self, ctx, *args)

    @char.command(name="info")
    async def info(self, ctx: commands.Context, *, name: str = None):
        await handle_info(self, ctx, name=name)

    @char.command(name="list")
    async def list_chars(self, ctx: commands.Context):
        await handle_list_chars(self, ctx)

    @char.command(name="rename")
    async def rename(self, ctx: commands.Context, *args):
        await handle_rename(self, ctx, *args)

    @char.command(name="delete")
    async def delete_char(self, ctx: commands.Context, *, name: str = None):
        await handle_delete_char(self, ctx, name=name)

async def setup(bot):
    await bot.add_cog(CharacterCog(bot))