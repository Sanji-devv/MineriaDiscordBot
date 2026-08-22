import discord
from discord.ext import commands
from pathlib import Path
import difflib
import asyncio
import time
from typing import List, Tuple


class Documents(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        base_dir = Path(__file__).parent
        self.docs_dir = base_dir / "mineria_files" / "docs"
        self.maps_dir = base_dir / "mineria_files" / "maps"
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.maps_dir.mkdir(parents=True, exist_ok=True)

        self._docs_cache: Tuple[float, List[Tuple[Path, float]]] = (0, [])
        self._maps_cache: Tuple[float, List[Path]] = (0, [])
        self._cache_ttl = 60  # Cache directory listings for 60 seconds

    async def _get_docs(self) -> List[Tuple[Path, float]]:
        """Returns cached (file_path, size_mb) list."""
        now = time.time()
        if self._docs_cache[1] and (now - self._docs_cache[0] < self._cache_ttl):
            return self._docs_cache[1]

        def _scan():
            files = [f for f in self.docs_dir.iterdir() if f.is_file()]
            res = []
            for f in sorted(files, key=lambda x: x.name.lower()):
                try:
                    size_mb = f.stat().st_size / (1024 * 1024)
                except Exception:
                    size_mb = 0.0
                res.append((f, size_mb))
            return res

        docs = await asyncio.to_thread(_scan)
        self._docs_cache = (now, docs)
        return docs

    async def _get_maps(self) -> List[Path]:
        """Returns cached map Path list."""
        now = time.time()
        if self._maps_cache[1] and (now - self._maps_cache[0] < self._cache_ttl):
            return self._maps_cache[1]

        def _scan():
            files = [f for f in self.maps_dir.iterdir() if f.is_file()]
            return sorted(files, key=lambda x: x.stem.lower())

        maps = await asyncio.to_thread(_scan)
        self._maps_cache = (now, maps)
        return maps

    # ─────────────────────────────────────────────
    #  !doc  –  list or download a document
    # ─────────────────────────────────────────────
    @commands.group(name="doc", invoke_without_command=True)
    async def doc_command(self, ctx, *, query: str = None):
        """Lists all downloadable files, or downloads one by name."""
        if not query or query.lower().strip() == "list":
            await self._list_docs(ctx)
            return

        await self._send_doc(ctx, query)

    @doc_command.command(name="list")
    async def doc_list(self, ctx):
        """Lists all available documents."""
        await self._list_docs(ctx)

    async def _list_docs(self, ctx):
        doc_entries = await self._get_docs()
        if not doc_entries:
            await ctx.send("📂 The `mineria_files/docs/` directory is currently empty.")
            return

        embed = discord.Embed(
            title="📂 Available Documents",
            description="Use `!doc <name>` to download a file.",
            color=discord.Color.gold()
        )
        file_list = [f"📄 **{f.name}** ({size:.2f} MB)" for f, size in doc_entries]
            
        value_text = ""
        for idx, item in enumerate(file_list):
            if len(value_text) + len(item) + 50 > 1024:
                value_text += f"\n*... and {len(file_list) - idx} more files.*"
                break
            value_text += ("\n" if value_text else "") + item

        embed.add_field(name=f"Files ({len(doc_entries)})", value=value_text or "No files.", inline=False)
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Documents", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Documents")
        await ctx.send(embed=embed)

    async def _send_doc(self, ctx, query: str):
        doc_entries = await self._get_docs()
        if not doc_entries:
            await ctx.send("📂 The `mineria_files/docs/` directory is currently empty.")
            return

        files = [f for f, _ in doc_entries]
        base_dir = self.docs_dir.resolve()
        try:
            target_file = (self.docs_dir / query).resolve()
        except Exception:
            await ctx.send("❌ Invalid file path.")
            return

        if not target_file.is_relative_to(base_dir):
            await ctx.send("❌ Access Denied: Path traversal detected.")
            return

        if not target_file.exists():
            file_names = [f.name for f in files]
            matches = await asyncio.to_thread(difflib.get_close_matches, query, file_names, 1, 0.5)
            if matches:
                target_file = self.docs_dir / matches[0]
            else:
                await ctx.send(
                    f"❌ File **{query}** not found.\n"
                    "Use `!doc list` to see all available files."
                )
                return

        try:
            await ctx.send(f"📥 Downloading **{target_file.name}**...", file=discord.File(target_file))
        except discord.HTTPException:
            await ctx.send("❌ File is too large to upload directly to Discord (Limit: 8 MB / 50 MB).")
        except Exception as e:
            await ctx.send(f"❌ Error uploading file: {e}")

    # ─────────────────────────────────────────────
    #  !map  –  list maps or send one by name
    # ─────────────────────────────────────────────
    @commands.group(name="map", invoke_without_command=True)
    async def map_group(self, ctx, *, name: str = None):
        """Lists all maps, or use `!map <name>` to display one."""
        if not name or name.lower().strip() == "list":
            await self._list_maps(ctx)
        else:
            await self._send_map(ctx, name)

    @map_group.command(name="list")
    async def map_list(self, ctx):
        """Lists all available maps."""
        await self._list_maps(ctx)

    async def _list_maps(self, ctx):
        maps = await self._get_maps()
        if not maps:
            await ctx.send("🗺️ The `mineria_files/maps/` directory is currently empty.")
            return

        embed = discord.Embed(
            title="🗺️ Available Maps",
            description="Use `!map <name>` to display a map.",
            color=discord.Color.blue()
        )
        map_list = [f"🖼️ **{f.stem}**" for f in maps]
        
        value_text = ""
        for idx, item in enumerate(map_list):
            if len(value_text) + len(item) + 50 > 1024:
                value_text += f"\n*... and {len(map_list) - idx} more maps.*"
                break
            value_text += ("\n" if value_text else "") + item

        embed.add_field(name=f"Maps ({len(maps)})", value=value_text or "No maps.", inline=False)
        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Maps", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Maps")
        await ctx.send(embed=embed)

    async def _send_map(self, ctx, name: str):
        """Find and send a map image by name (fuzzy match)."""
        maps = await self._get_maps()
        if not maps:
            await ctx.send("🗺️ No maps found in `mineria_files/maps/`.")
            return

        match = next((f for f in maps if f.stem.lower() == name.lower()), None)

        if not match:
            stems = [f.stem for f in maps]
            fuzzy = await asyncio.to_thread(difflib.get_close_matches, name, stems, 1, 0.4)
            if fuzzy:
                match = next(f for f in maps if f.stem == fuzzy[0])
            else:
                await ctx.send(
                    f"❌ Map **{name}** not found. "
                    "Use `!map list` to see all available maps."
                )
                return

        try:
            await ctx.send(f"🗺️ **{match.stem}**", file=discord.File(match))
        except discord.HTTPException:
            await ctx.send("❌ Map file is too large to upload directly to Discord.")
        except Exception as e:
            await ctx.send(f"❌ Error uploading map: {e}")


async def setup(bot):
    await bot.add_cog(Documents(bot))


