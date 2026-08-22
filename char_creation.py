import discord
import random
import statistics
from char_utils import load_json, save_json, roll_stat_detailed, get_recommendations, BonusSelectView

async def handle_create(cog, ctx, race_name: str = None):
        """
        Initiates the character creation process for a given race.
        Usage: !char create Human
        """
        if not race_name:
            return await ctx.send("❌ Usage: `!char create <race>` (e.g., `!char create Human`)")

        races = await load_json("races.json")
        target_race = next((r for r in races if r.lower() == race_name.lower()), None)
        if not target_race:
            return await ctx.send(f"❌ Race **{race_name}** not found.")

        # Setup Creation Context
        race_data = races[target_race]
        dice_points = 41 - race_data.get("Race Points", 10)

        cog.active_creations[ctx.author.id] = {
            "race_name": target_race,
            "race_data": race_data,
            "dice_points": dice_points,
            "stats": {}
        }

        embed = discord.Embed(
            title=f"✨ {target_race} Creation Started",
            description=f"**{ctx.author.display_name}**, your journey begins.",
            color=discord.Color.gold()
        )
        embed.add_field(name="🎲 Dice Points", value=f"**{dice_points}** points available.", inline=True)
        # Generate dynamic example based on points
        avg = dice_points // 6
        rem = dice_points % 6
        # Distribute: First 5 stats get avg, last gets avg + remainder
        ex_parts = []
        stats_keys = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
        for i, key in enumerate(stats_keys):
            val = avg + rem if i == 5 else avg
            ex_parts.append(f"{key} {val}")
        example_cmd = " ".join(ex_parts)

        embed.add_field(name="👉 Next Step", value=f"Distribute using `!char dr`.\nEx: `!char dr {example_cmd}`", inline=False)
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Creation Mode", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Creation Mode")
        
        await ctx.send(embed=embed)


async def handle_distribute(cog, ctx, *args):
        """
        Distributes dice points and rolls for stats.
        Usage: !char dr STR 6 DEX 6 ... (Total dice must match available points)
        """
        user_id = ctx.author.id
        if user_id not in cog.active_creations:
            return await ctx.send(embed=discord.Embed(title="❌ No Character Creation Active", description="Use `!char create <race>` first to start building a character.", color=discord.Color.red()))

        creation = cog.active_creations[user_id]
        dice_points = creation["dice_points"]
        
        # Show Help if no args
        if not args:
            avg = dice_points // 6
            rem = dice_points % 6
            ex_parts = []
            stats_keys = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
            for i, key in enumerate(stats_keys):
                val = avg + rem if i == 5 else avg
                ex_parts.append(f"{key} {val}")
            example_cmd = " ".join(ex_parts)
            
            embed = discord.Embed(
                title="🎲 Distribute Rolls",
                description=f"You have **{dice_points}** points to distribute among 6 stats.\nMinimum **3**, Maximum **18** dice per stat.",
                color=discord.Color.blue()
            )
            embed.add_field(name="Usage", value=f"`!char dr <STR> <val> <DEX> <val> ...`", inline=False)
            embed.add_field(name="Example (Balanced)", value=f"`!char dr {example_cmd}`", inline=False)
            embed.set_footer(text="Tip: You can also just type numbers: !char dr 6 6 6 6 6 10")
            return await ctx.send(embed=embed)

        keys = ["STR", "DEX", "CON", "INT", "WIS", "CHA"]
        stats_to_set = {}

        # Parse Arguments
        # Mode 1: Pure numbers "6 6 6 6 6 6" (Assumes order: STR DEX CON INT WIS CHA)
        if len(args) == 6 and all(a.isdigit() for a in args):
            values = [int(a) for a in args]
            stats_to_set = dict(zip(keys, values))
        
        # Mode 2: Key-Value pairs "STR 6 DEX 6..."
        elif len(args) == 12:
            for i in range(0, 12, 2):
                stat_name = args[i].upper()
                if stat_name not in keys:
                    return await ctx.send(f"❌ Invalid stat name: **{args[i]}**.")
                if not args[i+1].isdigit():
                    return await ctx.send(f"❌ Invalid value for {stat_name}: **{args[i+1]}**.")
                stats_to_set[stat_name] = int(args[i+1])
            
            if len(stats_to_set) < 6:
                return await ctx.send("❌ Please provide values for ALL 6 stats.")
        else:
            embed = discord.Embed(title="❌ Invalid Format", color=discord.Color.red())
            embed.description = f"Please provide exactly 6 numbers or 6 key-value pairs.\nTarget Total: **{dice_points}**"
            return await ctx.send(embed=embed)

        # Validation
        current_total = sum(stats_to_set.values())
        if current_total != creation["dice_points"]:
            diff = creation["dice_points"] - current_total
            status = "missing" if diff > 0 else "too many"
            return await ctx.send(
                f"❌ **Point Mismatch!**\n"
                f"Target: **{creation['dice_points']}** | Current: **{current_total}**\n"
                f"You are {status} **{abs(diff)}** dice."
            )

        if any(v < 3 for v in stats_to_set.values()):
            return await ctx.send("❌ Each stat must have at least **3** dice.")

        if any(v > 18 for v in stats_to_set.values()):
            return await ctx.send("❌ Each stat can have at most **18** dice.")

        # Roll Logic
        final_stats = {}
        racial_mods = cog.parse_racial_modifiers(creation["race_data"])
        rolls_text = ""

        for stat, num in stats_to_set.items():
            all_rolls, top_rolls = roll_stat_detailed(num)
            
            # Recalculate based on sorting for display
            sorted_rolls = sorted(all_rolls, reverse=True)
            kept_part = sorted_rolls[:3]
            dropped_part = sorted_rolls[3:]
            
            base_total = sum(kept_part)
            mod = racial_mods.get(stat, 0)
            final_val = base_total + mod
            final_stats[stat] = final_val
            
            # Formatting: [**6**, **5**, **5**, ~~1~~]
            kept_formatted = [f"**{r}**" for r in kept_part]
            dropped_formatted = [f"~~{r}~~" for r in dropped_part]
            full_list_str = ", ".join(kept_formatted + dropped_formatted)
            
            mod_str = f" {'+' if mod >= 0 else '-'} {abs(mod)} (Race)" if mod != 0 else ""
            
            # Detailed Line: STR: [**6**, **5**, **4**, ~~1~~] -> 15 + 0 = 15
            rolls_text += f"**{stat}**: [{full_list_str}] -> **{base_total}**{mod_str} = **{final_val}**\n"
        
        creation["stats"] = final_stats  # Update creation stats for embed generation
        embed_stats = cog.generate_stat_embed(ctx, creation, rolls_text, racial_mods)
        
        # View for Flexible Bonus
        view = None
        if racial_mods.get("ANY") and racial_mods["ANY"] > 0:
             view = BonusSelectView(cog, ctx, creation, rolls_text, racial_mods["ANY"])

        creation["stats"] = final_stats
        creation["stat_history"] = rolls_text
        
        await ctx.send(embed=embed_stats, view=view)

        # Recommendations
        settings = await load_json("user_settings.json")
        user_settings = settings.get(str(ctx.author.id), {})
        
        if user_settings.get("show_recommendations", True):
            classes_data = await load_json("classes.json")
            classes = classes_data.get("classes", []) if isinstance(classes_data, dict) else []
            recommendations = get_recommendations(final_stats, classes)
            if recommendations:
                embed_recs = discord.Embed(
                    title="🛡️ Class Recommendations",
                    description="\n".join([f"• **{r['name']}**" for r in recommendations]),
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed_recs)


async def handle_add_stat(cog, ctx, stat: str = None, value: int = None):
        """Adds a bonus value to a stat during creation."""
        if not stat or value is None:
            embed = discord.Embed(title="➕ Add Stat Bonus", color=discord.Color.blue())
            embed.description = "Manually adds a value to a stat during creation."
            embed.add_field(name="Usage", value="`!char add <STAT> <VALUE>`")
            embed.add_field(name="Example", value="`!char add STR 2`")
            return await ctx.send(embed=embed)

        user_id = ctx.author.id
        if user_id not in cog.active_creations:
             return await ctx.send("❌ No active character creation.")
        
        creation = cog.active_creations[user_id]
        if "stats" not in creation or not creation["stats"]:
             return await ctx.send("❌ Roll stats first with `!char dr`.")

        stat = stat.upper()
        if stat not in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]:
            return await ctx.send("❌ Invalid stat name.")

        creation["stats"][stat] += value
        await ctx.send(f"✅ **{stat}** updated to **{creation['stats'][stat]}**")


async def handle_remove_stat(cog, ctx, stat: str = None, value: int = None):
        """Removes a value from a stat during creation."""
        if not stat or value is None:
            embed = discord.Embed(title="➖ Remove Stat Bonus", color=discord.Color.blue())
            embed.description = "Manually removes a value from a stat during creation."
            embed.add_field(name="Usage", value="`!char remove <STAT> <VALUE>`")
            embed.add_field(name="Example", value="`!char remove DEX 2`")
            return await ctx.send(embed=embed)

        user_id = ctx.author.id
        if user_id not in cog.active_creations:
             return await ctx.send("❌ No active character creation.")
        
        creation = cog.active_creations[user_id]
        if not creation.get("stats"):
             return await ctx.send("❌ Roll stats first.")

        stat = stat.upper()
        if stat not in ["STR", "DEX", "CON", "INT", "WIS", "CHA"]:
            return await ctx.send("❌ Invalid stat name.")

        creation["stats"][stat] -= value
        await ctx.send(f"✅ **{stat}** updated to **{creation['stats'][stat]}**")


async def handle_save_char(cog, ctx, *, name: str = None):
        """Finalizes and saves the character."""
        user_id = ctx.author.id
        if user_id not in cog.active_creations or not cog.active_creations[user_id]["stats"]:
            return await ctx.send("❌ No pending creation to save. Use `!char create` first.")
        
        if not name: 
            embed = discord.Embed(title="💾 Save Character", color=discord.Color.blue())
            embed.description = "Finalizes your character creation and saves it to the database."
            embed.add_field(name="Usage", value="`!char save <Name>`")
            embed.add_field(name="Example", value="`!char save Valeros`")
            return await ctx.send(embed=embed)

        name = name.strip()
        if not name:
            return await ctx.send("❌ Invalid name.")

        creation = cog.active_creations[user_id]
        characters = await load_json("characters.json")
        uid = str(user_id)
        
        if uid not in characters: characters[uid] = []
        if any(c["name"].lower() == name.lower() for c in characters[uid]):
            return await ctx.send(f"❌ Name **{name}** is taken.")

        new_char = {
            "name": name,
            "race": creation["race_name"],
            "class": "None",
            "stats": creation["stats"],
            "created_at": str(ctx.message.created_at)
        }

        if "stat_history" in creation:
             new_char["stat_history"] = creation["stat_history"]
        
        characters[uid].append(new_char)
        await save_json("characters.json", characters)
        del cog.active_creations[user_id]
        
        embed = discord.Embed(
            title="💾 Character Saved",
            description=f"**{name}** ({creation['race_name']}) has been created!",
            color=discord.Color.green()
        )
        avatar_url = cog.bot.user.display_avatar.url if (cog.bot.user and cog.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Saved", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Saved")
        await ctx.send(embed=embed)

    # ==========================
    # REC SETTINGS
    # ==========================
    

async def handle_rec(cog, ctx):
        """Settings for Class Recommendations."""
        # This was already sending a simple message, upgrading to Embed
        embed = discord.Embed(title="🛡️ Recommendation Settings", color=discord.Color.blue())
        embed.description = "Toggle automatic class recommendations during character creation."
        embed.add_field(name="Commands", value="`!rec open` - Enable\n`!rec close` - Disable")
        await ctx.send(embed=embed)


async def handle_rec_open(cog, ctx):
        """Enables class recommendations."""
        settings = await load_json("user_settings.json")
        uid = str(ctx.author.id)
        if uid not in settings: settings[uid] = {}
        settings[uid]["show_recommendations"] = True
        await save_json("user_settings.json", settings)
        await ctx.send("✅ Recommendations **Enabled**.")


async def handle_rec_close(cog, ctx):
        """Disables class recommendations."""
        settings = await load_json("user_settings.json")
        uid = str(ctx.author.id)
        if uid not in settings: settings[uid] = {}
        settings[uid]["show_recommendations"] = False
        await save_json("user_settings.json", settings)
        await ctx.send("❌ Recommendations **Disabled**.")

    # ==========================
    # EDITING COMMANDS
    # ==========================

