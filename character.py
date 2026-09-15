import discord
from discord import app_commands
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
from log_handler import logger

# =================================================================================================
# CHARACTER COG
# =================================================================================================

class CharacterCog(commands.Cog, name="Character"):
    """
    Commands for Character Creation, Management, and Stat Rolling.
    """
    char_group = app_commands.Group(name="char", description="Character creation and management commands")
    
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

    # ==========================
    # SLASH COMMANDS & AUTOCOMPLETE
    # ==========================

    async def _user_characters_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
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
        except Exception as e:
            logger.error(f"Error in user character autocomplete: {e}")
            return []

    async def _classes_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        try:
            classes = await load_json("classes.json")
            choices = []
            curr_lower = current.lower().strip()
            for cname in classes.keys():
                if not curr_lower or curr_lower in cname.lower():
                    choices.append(app_commands.Choice(name=cname[:100], value=cname[:100]))
                    if len(choices) >= 25:
                        break
            return choices
        except Exception:
            return []

    @char_group.command(name="create", description="Start creating a new character for a race")
    @app_commands.describe(race="The race of your character (e.g. Human, Elf, Dwarf)")
    async def slash_create(self, interaction: discord.Interaction, race: str):
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_create(self, adapter, race_name=race)

    @slash_create.autocomplete("race")
    async def slash_create_race_auto(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
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
        except Exception as e:
            logger.error(f"Error in race autocomplete: {e}")
            return []

    @char_group.command(name="info", description="View detailed character sheet and statistics")
    @app_commands.describe(name="Name of your saved character")
    async def slash_info(self, interaction: discord.Interaction, name: Optional[str] = None):
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_info(self, adapter, name=name)

    @slash_info.autocomplete("name")
    async def slash_info_name_auto(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @char_group.command(name="list", description="List all characters you have created")
    async def slash_list(self, interaction: discord.Interaction):
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_list_chars(self, adapter)

    @char_group.command(name="edit_class", description="Change a saved character's class")
    @app_commands.describe(name="Name of your character", new_class="The new class to assign")
    async def slash_edit_class(self, interaction: discord.Interaction, name: str, new_class: str):
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_edit_class(self, adapter, name, new_class)

    @slash_edit_class.autocomplete("name")
    async def slash_edit_class_name_auto(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @slash_edit_class.autocomplete("new_class")
    async def slash_edit_class_auto(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
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
    async def slash_edit_stat(self, interaction: discord.Interaction, name: str, stat: str, new_value: int):
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_edit_stat(self, adapter, name, stat, str(new_value))

    @slash_edit_stat.autocomplete("name")
    async def slash_edit_stat_name_auto(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

    @char_group.command(name="delete", description="Delete a saved character permanently")
    @app_commands.describe(name="Name of your character to delete")
    async def slash_delete(self, interaction: discord.Interaction, name: str):
        adapter = InteractionContextAdapter(interaction, self.bot)
        await handle_delete_char(self, adapter, name=name)

    @slash_delete.autocomplete("name")
    async def slash_delete_name_auto(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return await self._user_characters_autocomplete(interaction, current)

async def setup(bot):
    await bot.add_cog(CharacterCog(bot))