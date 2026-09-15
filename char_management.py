import discord
from char_utils import load_json, save_json, get_file_lock

async def handle_edit(cog, ctx):
        """Edit saved character details."""
        embed = discord.Embed(title="✏️ Edit Character", color=discord.Color.blue())
        embed.description = "Modify an existing character's data."
        embed.add_field(name="Class", value="`!char edit class <Name> <NewClass>`")
        embed.add_field(name="Stats", value="`!char edit stat <Name> <Stat> <NewValue>`")
        await ctx.send(embed=embed)


async def handle_edit_class(cog, ctx, *args):
        """Edits a character's class."""
        if not args:
            embed = discord.Embed(title="✏️ Edit Class", color=discord.Color.blue())
            embed.description = "Modify a character's class."
            embed.add_field(name="Usage", value="`!char edit class <Name> <NewClass>`")
            embed.add_field(name="Example", value="`!char edit class \"Kiros Enuma\" LL Guardian`")
            return await ctx.send(embed=embed)

        full_input = " ".join(args).strip()
        uid = str(ctx.author.id)

        async with get_file_lock("characters.json"):
            characters = await load_json("characters.json", force_reload=True)
            if uid not in characters or not characters[uid]:
                return await ctx.send("❌ You don't have any saved characters.")

            # Attempt to match existing character name from user's roster
            matched_char = None
            new_class = ""
            user_chars = characters[uid]
            
            # Check from longest character name to shortest
            for c in sorted(user_chars, key=lambda x: len(x["name"]), reverse=True):
                c_name = c["name"]
                if full_input.lower().startswith(c_name.lower()):
                    matched_char = c
                    new_class = full_input[len(c_name):].strip()
                    break

            # Fallback if quotes were used: e.g. "Kiros Enuma" Wizard
            if not matched_char:
                if len(args) >= 2:
                    name_guess = args[0].strip('"\',')
                    new_class = " ".join(args[1:]).strip()
                    matched_char = next((c for c in user_chars if c["name"].lower() == name_guess.lower()), None)

            if not matched_char or not new_class:
                return await ctx.send("❌ Could not match character or missing new class.\nUsage: `!char edit class <Name> <NewClass>`")

            old_class = matched_char.get("class", "None")
            matched_char["class"] = new_class
            await save_json("characters.json", characters)
        
        embed = discord.Embed(

            title="✏️ Class Updated",
            description=f"**{matched_char['name']}**: {old_class} ➡️ **{new_class}**",
            color=discord.Color.green()
        )
        avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Character Management", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Character Management")
        await ctx.send(embed=embed)


async def handle_edit_stat(cog, ctx, *args):
        """Edits a character's specific stat."""
        if not args:
            embed = discord.Embed(title="✏️ Edit Stat", color=discord.Color.blue())
            embed.description = "Modify a character's stat value directly."
            embed.add_field(name="Usage", value="`!char edit stat <Name> <Stat> <Value>`")
            embed.add_field(name="Example", value="`!char edit stat \"Kiros Enuma\" STR 18`")
            return await ctx.send(embed=embed)

        tokens = list(args)
        valid_stats = {"STR", "DEX", "CON", "INT", "WIS", "CHA"}
        
        char_name = None
        stat = None
        value = None

        # Pattern 1: <Name...> <Stat> <Value>
        if len(tokens) >= 3 and tokens[-2].upper() in valid_stats and tokens[-1].lstrip('-+').isdigit():
            char_name = " ".join(tokens[:-2]).strip('"\',')
            stat = tokens[-2].upper()
            value = int(tokens[-1])
        # Pattern 2: <Name...> <Value> <Stat>
        elif len(tokens) >= 3 and tokens[-1].upper() in valid_stats and tokens[-2].lstrip('-+').isdigit():
            char_name = " ".join(tokens[:-2]).strip('"\',')
            stat = tokens[-1].upper()
            value = int(tokens[-2])
        else:
            return await ctx.send("❌ Invalid format.\nUsage: `!char edit stat <Name> <Stat> <Value>` (e.g. `!char edit stat Kiros STR 18`)")

        uid = str(ctx.author.id)
        async with get_file_lock("characters.json"):
            characters = await load_json("characters.json", force_reload=True)
            if uid not in characters or not characters[uid]:
                return await ctx.send("❌ You don't have any saved characters.")

            char_data = next((c for c in characters[uid] if c["name"].lower() == char_name.lower()), None)
            if not char_data:
                return await ctx.send(f"❌ Character **{char_name}** not found.")

            old_val = char_data["stats"].get(stat, 0)
            char_data["stats"][stat] = value
            await save_json("characters.json", characters)
        
        embed = discord.Embed(

            title="✏️ Stat Updated",
            description=f"**{char_data['name']}** {stat}: {old_val} ➡️ **{value}**",
            color=discord.Color.green()
        )
        avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Character Management", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Character Management")
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
            name = name.strip().strip('"\'')
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
             history_val = str(char_data["stat_history"])
             if len(history_val) > 1020:
                 history_val = history_val[:1015] + "..."
             embed.add_field(name="📊 Stats History", value=history_val, inline=False)

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


async def handle_rename(cog, ctx, *args):
        """Renames a character."""
        if not args:
            embed = discord.Embed(title="✏️ Rename Character", color=discord.Color.blue())
            embed.description = "Change the name of one of your characters."
            embed.add_field(name="Usage", value="`!char rename <OldName> <NewName>`\n`!char rename \"Old Name\" \"New Name\"`")
            embed.add_field(name="Example", value="`!char rename \"Kiros Enuma\" \"Kiros Prime\"`")
            return await ctx.send(embed=embed)

        uid = str(ctx.author.id)
        async with get_file_lock("characters.json"):
            characters = await load_json("characters.json", force_reload=True)
            if uid not in characters or not characters[uid]:
                return await ctx.send("❌ No characters found.")

            full_input = " ".join(args).strip()
            user_chars = characters[uid]
            old_char = None
            new_name = None

            # Check arrow syntax: !char rename Old Name -> New Name
            if "->" in full_input:
                parts = full_input.split("->", 1)
                old_guess = parts[0].strip().strip('"\'')
                new_name = parts[1].strip().strip('"\'')
                old_char = next((c for c in user_chars if c["name"].lower() == old_guess.lower()), None)
            else:
                # Match existing character from user's roster
                for c in sorted(user_chars, key=lambda x: len(x["name"]), reverse=True):
                    c_name = c["name"]
                    if full_input.lower().startswith(c_name.lower()):
                        old_char = c
                        new_name = full_input[len(c_name):].strip().strip('"\'')
                        break
                
                # Fallback for 2 quoted args or simple 2 args
                if not old_char and len(args) >= 2:
                    old_guess = args[0].strip('"\'')
                    new_name = " ".join(args[1:]).strip().strip('"\'')
                    old_char = next((c for c in user_chars if c["name"].lower() == old_guess.lower()), None)

            if not old_char or not new_name:
                return await ctx.send("❌ Usage: `!char rename <OldName> <NewName>` (e.g. `!char rename \"Old Name\" \"New Name\"`)")

            old_name = old_char["name"]
            # Check duplicate name
            if any(c["name"].lower() == new_name.lower() and c is not old_char for c in user_chars):
                return await ctx.send(f"❌ You already have a character named **{new_name}**.")

            old_char["name"] = new_name
            await save_json("characters.json", characters)

        embed = discord.Embed(
            title="✏️ Character Renamed",
            description=f"Character **{old_name}** renamed to **{new_name}**.",
            color=discord.Color.orange()
        )
        avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Character Management", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Character Management")
        await ctx.send(embed=embed)


async def handle_delete_char(cog, ctx, *, name: str = None):
        """Deletes a character."""
        if not name:
             embed = discord.Embed(title="🗑️ Delete Character", color=discord.Color.red())
             embed.description = "Permanently delete a character."
             embed.add_field(name="Usage", value="`!char delete <Name>`")
             return await ctx.send(embed=embed)

        name = name.strip().strip('"\'')
        if not name:
            return await ctx.send("❌ Invalid name.")

        uid = str(ctx.author.id)
        async with get_file_lock("characters.json"):
            characters = await load_json("characters.json", force_reload=True)
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


