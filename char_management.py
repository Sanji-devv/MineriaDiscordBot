import discord
from char_utils import load_json, save_json

async def handle_edit(cog, ctx):
        """Edit saved character details."""
        embed = discord.Embed(title="✏️ Edit Character", color=discord.Color.blue())
        embed.description = "Modify an existing character's data."
        embed.add_field(name="Class", value="`!char edit class <Name> <NewClass>`")
        embed.add_field(name="Stats", value="`!char edit stat <Name> <Stat> <NewValue>`")
        await ctx.send(embed=embed)


async def handle_edit_class(cog, ctx, name: str = None, new_class: str = None):
        """Edits a character's class."""
        if not name or not new_class:
            return await ctx.send("❌ Usage: `!char edit class <Name> <NewClass>`")

        name = name.strip()
        new_class = new_class.strip()
        if not name or not new_class:
            return await ctx.send("❌ Invalid name or class.")

        characters = await load_json("characters.json")
        uid = str(ctx.author.id)
        if uid not in characters: return await ctx.send("❌ No characters.")

        char_data = next((c for c in characters[uid] if c["name"].lower() == name.lower()), None)
        if not char_data: return await ctx.send(f"❌ Character **{name}** not found.")

        old_class = char_data.get("class", "None")
        char_data["class"] = new_class
        await save_json("characters.json", characters)
        
        embed = discord.Embed(
            title="✏️ Class Updated",
            description=f"**{char_data['name']}**: {old_class} ➡️ **{new_class}**",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)


async def handle_edit_stat(cog, ctx, name: str = None, stat: str = None, value: int = None):
        """Edits a character's specific stat."""
        if not name or not stat or value is None:
            return await ctx.send("❌ Usage: `!char edit stat <Name> <Stat> <Value>`")

        name = name.strip()
        stat = stat.strip()
        if not name or not stat:
            return await ctx.send("❌ Invalid name or stat.")

        characters = await load_json("characters.json")
        uid = str(ctx.author.id)
        if uid not in characters: return await ctx.send("❌ No characters.")

        char_data = next((c for c in characters[uid] if c["name"].lower() == name.lower()), None)
        if not char_data: return await ctx.send(f"❌ Character **{name}** not found.")

        stat = stat.upper()
        if stat not in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]:
            return await ctx.send("❌ Invalid stat.")

        old_val = char_data["stats"].get(stat, 0)
        char_data["stats"][stat] = value
        await save_json("characters.json", characters)
        
        embed = discord.Embed(
            title="✏️ Stat Updated",
            description=f"**{char_data['name']}** {stat}: {old_val} ➡️ **{value}**",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)

    # ==========================
    # ROSTER / INFO
    # ==========================


async def handle_info(cog, ctx, *, name: str = None):
        """Detailed view of a character."""
        characters = await load_json("characters.json")
        uid = str(ctx.author.id)
        user_chars = characters.get(uid, [])
        
        if not user_chars:
            return await ctx.send("❌ You don't have any saved characters.")

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
                    title="🔢 Multiple Characters Found",
                    description=f"Use `!char info <name>` to see details.\n\n**Your Characters:**\n{char_list}",
                    color=discord.Color.gold()
                )
                return await ctx.send(embed=embed)
        else:
            name = name.strip()
            char_data = next((c for c in user_chars if c["name"].lower() == name.lower()), None)
        
        if not char_data:
            return await ctx.send(f"❌ Character **{name}** not found.")
            
        char_class = char_data.get('class', 'Adventurer')
        if char_class == "None": char_class = "Adventurer"
        
        embed = discord.Embed(
            title=f"📜 {char_data['name']}",
            description=f"✨ **{char_data['race']}** • **{char_class}**",
            color=discord.Color.gold()
        )

        # 1. Show Detailed Roll History (if available) - Like "!char dr"
        if "stat_history" in char_data:
             embed.add_field(name="📊 Stats History", value=char_data["stat_history"], inline=False)

        # 2. Stats (Physical / Mental columns)
        stats = char_data.get("stats") or {}

        def fmt_stat(label, key, emoji):
            val = stats.get(key, 10)
            mod = (val - 10) // 2
            sign = "+" if mod >= 0 else ""
            return f"{emoji} **{label}**: {val} (`{sign}{mod}`)"

        col1 = [fmt_stat("STR", "STR", "💪"), fmt_stat("DEX", "DEX", "🏃"), fmt_stat("CON", "CON", "❤️")]
        col2 = [fmt_stat("INT", "INT", "🧠"), fmt_stat("WIS", "WIS", "🦉"), fmt_stat("CHA", "CHA", "🎭")]

        embed.add_field(name="🛡️ Physical", value="\n".join(col1), inline=True)
        embed.add_field(name="🔮 Mental",   value="\n".join(col2), inline=True)

        
        # Feat Display
        feats = char_data.get("feats") or {}
        if feats:
            feat_lines = []
            for slot, feat in feats.items():
                short_slot = str(slot).replace("Level", "Lvl").replace("Bonus Feat", "Bonus")
                feat_lines.append(f"• **{short_slot}**: {feat}")
            
            feats_text = "\n".join(feat_lines)
            if len(feats_text) > 1000: feats_text = feats_text[:990] + "..."
            embed.add_field(name="⚔️ Known Feats", value=feats_text, inline=False)

        created_at_raw = str(char_data.get("created_at") or "")
        created_at = created_at_raw.split(" ")[0] if created_at_raw else "N/A"
        footer_text = f"Mineria RPG • Created: {created_at}"
        avatar_url = ctx.bot.user.display_avatar.url if (ctx.bot.user and ctx.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text=footer_text, icon_url=avatar_url)
        else:
            embed.set_footer(text=footer_text)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
            
        await ctx.send(embed=embed)



async def handle_list_chars(cog, ctx):
        """Lists all characters owned by the user."""
        characters = await load_json("characters.json")
        user_chars = characters.get(str(ctx.author.id), [])
        
        if not user_chars:
            return await ctx.send("❌ You don't have any saved characters.")
            
        embed = discord.Embed(
            title="👥 Your Characters", 
            color=discord.Color.gold()
        )
        names = "\n".join([f"• **{c['name']}** ({c['race']} {c.get('class', 'None')})" for c in user_chars])
        if len(names) > 4000:
            # Safely truncate
            truncated = names[:3900]
            lines = truncated.splitlines()
            # If the last line is cut off, remove it
            if len(lines) > 1 and not names[len(truncated):].startswith("\n"):
                lines.pop()
            names = "\n".join(lines) + f"\n\n*... and {len(user_chars) - len(lines)} more characters.*"
        embed.description = names
        avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Roster", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Roster")
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)


async def handle_rename(cog, ctx, old_name: str = None, new_name: str = None):
        """Renames a character."""
        if not old_name or not new_name:
            embed = discord.Embed(title="✏️ Rename Character", color=discord.Color.blue())
            embed.description = "Change the name of one of your characters."
            embed.add_field(name="Usage", value="`!char rename <OldName> <NewName>`")
            return await ctx.send(embed=embed)

        old_name = old_name.strip()
        new_name = new_name.strip()
        if not old_name or not new_name:
            return await ctx.send("❌ Invalid name.")

        characters = await load_json("characters.json")
        uid = str(ctx.author.id)
        if uid not in characters: return await ctx.send("❌ No characters found.")

        for char_data in characters[uid]:
            if char_data["name"].lower() == old_name.lower():
                # Check duplication
                if any(c["name"].lower() == new_name.lower() and c is not char_data for c in characters[uid]):
                    return await ctx.send(f"❌ You already have a character named **{new_name}**.")
                
                char_data["name"] = new_name
                await save_json("characters.json", characters)
                embed = discord.Embed(
                    title="✏️ Character Renamed",
                    description=f"Character **{old_name}** renamed to **{new_name}**.",
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return
        
        
        await ctx.send(f"❌ Character **{old_name}** not found.")


async def handle_delete_char(cog, ctx, *, name: str = None):
        """Deletes a character."""
        if not name:
             embed = discord.Embed(title="🗑️ Delete Character", color=discord.Color.red())
             embed.description = "Permanently delete a character."
             embed.add_field(name="Usage", value="`!char delete <Name>`")
             return await ctx.send(embed=embed)

        name = name.strip()
        if not name:
            return await ctx.send("❌ Invalid name.")

        characters = await load_json("characters.json")
        uid = str(ctx.author.id)
        
        if uid not in characters or not characters[uid]:
            return await ctx.send("❌ You don't have any characters to delete.")

        # Filter out the character to delete
        original_count = len(characters[uid])
        characters[uid] = [c for c in characters[uid] if c["name"].lower() != name.lower()]
        
        if len(characters[uid]) == original_count:
             return await ctx.send(f"❌ Character **{name}** not found.")

        await save_json("characters.json", characters)
        
        embed = discord.Embed(
            title="🗑️ Character Deleted",
            description=f"Character **{name}** has been deleted.",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)

