"""
Mineria Discord Bot - Documents & Tactical Maps Module
======================================================
Provides interactive browsing, fuzzy matching, and secure delivery of campaign
PDF rulebooks, form templates, and tactical battlemaps stored in mineria_files/.
Includes TTL caching to minimize disk I/O and strict path traversal validation.
"""

import time
import asyncio
import difflib
from pathlib import Path
from typing import List, Tuple, Optional

import discord
from discord import app_commands
from discord.ext import commands

from character import InteractionContextAdapter


class Documents(commands.Cog, name="Documents"):
    """Cog handling campaign documents and tactical battlemaps."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        base_dir = Path(__file__).parent
        self.docs_dir = base_dir / "mineria_files" / "docs"
        self.maps_dir = base_dir / "mineria_files" / "maps"

        # Ensure target storage folders exist
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.maps_dir.mkdir(parents=True, exist_ok=True)

        # In-memory TTL caches to avoid repeated disk scans: (timestamp, cached_items)
        self._docs_cache: Tuple[float, List[Tuple[Path, float]]] = (0.0, [])
        self._maps_cache: Tuple[float, List[Path]] = (0.0, [])
        self._cache_ttl = 60  # Cache directory listings for 60 seconds

    # =========================================================================
    # SECTION 1: DIRECTORY SCANNING & TTL CACHING
    # =========================================================================

    async def _get_docs(self) -> List[Tuple[Path, float]]:
        """
        Retrieves cached list of document paths and file sizes (in MB).
        Refreshes cache via background thread when TTL expires.
        """
        now = time.time()
        if self._docs_cache[1] and (now - self._docs_cache[0] < self._cache_ttl):
            return self._docs_cache[1]

        def _scan() -> List[Tuple[Path, float]]:
            files = [f for f in self.docs_dir.iterdir() if f.is_file()]
            results = []
            for f in sorted(files, key=lambda x: x.name.lower()):
                try:
                    size_mb = f.stat().st_size / (1024 * 1024)
                except Exception:
                    size_mb = 0.0
                results.append((f, size_mb))
            return results

        docs = await asyncio.to_thread(_scan)
        self._docs_cache = (now, docs)
        return docs

    async def _get_maps(self) -> List[Path]:
        """
        Retrieves cached list of map file paths.
        Refreshes cache via background thread when TTL expires.
        """
        now = time.time()
        if self._maps_cache[1] and (now - self._maps_cache[0] < self._cache_ttl):
            return self._maps_cache[1]

        def _scan() -> List[Path]:
            files = [f for f in self.maps_dir.iterdir() if f.is_file()]
            return sorted(files, key=lambda x: x.stem.lower())

        maps = await asyncio.to_thread(_scan)
        self._maps_cache = (now, maps)
        return maps

    # =========================================================================
    # SECTION 2: DOCUMENT RETRIEVAL & LISTING (!doc, /doc)
    # =========================================================================

    @commands.group(name="doc", invoke_without_command=True)
    async def doc_command(self, ctx: commands.Context, *, query: Optional[str] = None) -> None:
        """Lists all downloadable campaign documents, or sends a specific file by name."""
        # Handle list shortcut (e.g. "!doc" or "!doc list")
        if not query or query.lower().strip() == "list":
            await self._list_docs(ctx)
            return

        await self._send_doc(ctx, query)

    @doc_command.command(name="list")
    async def doc_list(self, ctx: commands.Context) -> None:
        """Lists all available documents in mineria_files/docs/."""
        await self._list_docs(ctx)

    async def _list_docs(self, ctx: commands.Context | InteractionContextAdapter) -> None:
        """Builds and sends an embed catalog of all available documents."""
        doc_entries = await self._get_docs()
        if not doc_entries:
            await ctx.send("The `mineria_files/docs/` directory is currently empty.")
            return

        embed = discord.Embed(
            title="Available Campaign Documents",
            description="Use `!doc <name>` or `/doc` to download a specific document.",
            color=discord.Color.gold()
        )
        file_list = [f"**{f.name}** ({size:.2f} MB)" for f, size in doc_entries]

        # Prevent hitting Discord embed field length limits (1024 characters)
        value_text = ""
        for idx, item in enumerate(file_list):
            if len(value_text) + len(item) + 50 > 1024:
                value_text += f"\n*... and {len(file_list) - idx} additional files.*"
                break
            value_text += ("\n" if value_text else "") + item

        embed.add_field(name=f"Files ({len(doc_entries)})", value=value_text or "No files.", inline=False)

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Documents", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Documents")

        await ctx.send(embed=embed)

    async def _send_doc(self, ctx: commands.Context | InteractionContextAdapter, query: str) -> None:
        """Validates file safety, locates document via exact or fuzzy match, and sends as Discord attachment."""
        doc_entries = await self._get_docs()
        if not doc_entries:
            await ctx.send("The `mineria_files/docs/` directory is currently empty.")
            return

        files = [f for f, _ in doc_entries]
        base_dir = self.docs_dir.resolve()

        try:
            target_file = (self.docs_dir / query).resolve()
        except Exception:
            await ctx.send("Invalid filename format provided.")
            return

        # Security check: Prevent path traversal attacks (e.g. "../../secret.txt")
        if not target_file.is_relative_to(base_dir):
            await ctx.send("Access Denied: Path traversal detected.")
            return

        # If file doesn't exist by exact path, attempt fuzzy matching
        if not target_file.exists():
            direct_match = next(
                (f for f in files if f.name.lower() == query.lower() or f.stem.lower() == query.lower()),
                None
            )
            if direct_match:
                target_file = direct_match
            else:
                # Fuzzy matching using difflib
                file_names = [f.name for f in files]
                matches = await asyncio.to_thread(difflib.get_close_matches, query, file_names, 1, 0.5)
                if matches:
                    target_file = self.docs_dir / matches[0]
                else:
                    await ctx.send(
                        f"Document **{query}** not found.\n"
                        "Use `!doc list` to view all available campaign documents."
                    )
                    return

        # Deliver file as Discord attachment
        try:
            await ctx.send(f"Downloading **{target_file.name}**...", file=discord.File(target_file))
        except discord.HTTPException:
            await ctx.send("File exceeds Discord upload limit for this server (Limit: 8 MB / 50 MB).")
        except Exception as exc:
            await ctx.send(f"Error uploading document: {exc}")

    # =========================================================================
    # SECTION 3: MAP RETRIEVAL & LISTING (!map, /map)
    # =========================================================================

    @commands.group(name="map", invoke_without_command=True)
    async def map_group(self, ctx: commands.Context, *, name: Optional[str] = None) -> None:
        """Lists all tactical battlemaps, or displays a map image by name."""
        if not name or name.lower().strip() == "list":
            await self._list_maps(ctx)
        else:
            await self._send_map(ctx, name)

    @map_group.command(name="list")
    async def map_list(self, ctx: commands.Context) -> None:
        """Lists all available maps in mineria_files/maps/."""
        await self._list_maps(ctx)

    async def _list_maps(self, ctx: commands.Context | InteractionContextAdapter) -> None:
        """Builds and sends an embed catalog of all tactical battlemaps."""
        maps = await self._get_maps()
        if not maps:
            await ctx.send("The `mineria_files/maps/` directory is currently empty.")
            return

        embed = discord.Embed(
            title="Available Tactical Battlemaps",
            description="Use `!map <name>` or `/map` to display a specific map.",
            color=discord.Color.blue()
        )
        map_list = [f"**{f.stem}**" for f in maps]

        # Prevent hitting Discord embed field length limits
        value_text = ""
        for idx, item in enumerate(map_list):
            if len(value_text) + len(item) + 50 > 1024:
                value_text += f"\n*... and {len(map_list) - idx} additional maps.*"
                break
            value_text += ("\n" if value_text else "") + item

        embed.add_field(name=f"Maps ({len(maps)})", value=value_text or "No maps.", inline=False)

        avatar_url = self.bot.user.display_avatar.url if (self.bot.user and self.bot.user.display_avatar) else None
        if avatar_url:
            embed.set_footer(text="Mineria RPG • Maps", icon_url=avatar_url)
        else:
            embed.set_footer(text="Mineria RPG • Maps")

        await ctx.send(embed=embed)

    async def _send_map(self, ctx: commands.Context | InteractionContextAdapter, name: str) -> None:
        """Finds a tactical battlemap via exact or fuzzy match and displays it."""
        maps = await self._get_maps()
        if not maps:
            await ctx.send("No maps found in `mineria_files/maps/`.")
            return

        match = next((f for f in maps if f.stem.lower() == name.lower()), None)

        if not match:
            stems = [f.stem for f in maps]
            fuzzy = await asyncio.to_thread(difflib.get_close_matches, name, stems, 1, 0.4)
            if fuzzy:
                match = next(f for f in maps if f.stem == fuzzy[0])
            else:
                await ctx.send(
                    f"Map **{name}** not found.\n"
                    "Use `!map list` to view all available tactical battlemaps."
                )
                return

        try:
            await ctx.send(f"**{match.stem}**", file=discord.File(match))
        except discord.HTTPException:
            await ctx.send("Map image exceeds Discord direct upload size limits.")
        except Exception as exc:
            await ctx.send(f"Error uploading map image: {exc}")

    # =========================================================================
    # SECTION 4: SLASH COMMANDS & AUTOCOMPLETE PROVIDERS
    # =========================================================================

    @app_commands.command(name="doc", description="Download a campaign PDF or view available documents")
    @app_commands.describe(title="Name of the PDF document to view or download")
    async def slash_doc(self, interaction: discord.Interaction, title: Optional[str] = None) -> None:
        """Slash command for accessing documents with autocomplete."""
        adapter = InteractionContextAdapter(interaction, self.bot)
        if not title or title.lower().strip() == "list":
            await self._list_docs(adapter)
        else:
            await self._send_doc(adapter, title)

    @slash_doc.autocomplete("title")
    async def slash_doc_title_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Provides dynamic autocomplete choices for document filenames."""
        docs = await self._get_docs()
        curr_lower = current.lower().strip()
        choices = []

        for f, size in docs:
            if not curr_lower or curr_lower in f.name.lower():
                label = f"{f.name} ({size:.2f} MB)"
                if len(label) > 100:
                    label = label[:97] + "..."
                choices.append(app_commands.Choice(name=label, value=f.name))
                if len(choices) >= 25:
                    break

        return choices

    @app_commands.command(name="map", description="Display a tactical battlemap or view available maps")
    @app_commands.describe(name="Name of the battlemap to display")
    async def slash_map(self, interaction: discord.Interaction, name: Optional[str] = None) -> None:
        """Slash command for displaying maps with autocomplete."""
        adapter = InteractionContextAdapter(interaction, self.bot)
        if not name or name.lower().strip() == "list":
            await self._list_maps(adapter)
        else:
            await self._send_map(adapter, name)

    @slash_map.autocomplete("name")
    async def slash_map_name_auto(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Provides dynamic autocomplete choices for map names."""
        maps = await self._get_maps()
        curr_lower = current.lower().strip()
        choices = []

        for f in maps:
            if not curr_lower or curr_lower in f.stem.lower():
                choices.append(app_commands.Choice(name=f.stem[:100], value=f.stem[:100]))
                if len(choices) >= 25:
                    break

        return choices


async def setup(bot: commands.Bot) -> None:
    """Extension entry point for loading the Documents Cog."""
    await bot.add_cog(Documents(bot))
