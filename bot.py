import os
import sqlite3
import requests
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "348883315626868737"))
TMDB_API_KEY = os.getenv("TMDB_API_KEY")

# 2. Initialize Bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 3. Database Setup
def init_db():
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER UNIQUE NOT NULL,
            title TEXT NOT NULL,
            tmdb_url TEXT NOT NULL,
            recommender_id INTEGER NOT NULL,
            watched BOOLEAN DEFAULT 0
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interested (
            movie_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (movie_id, user_id),
            FOREIGN KEY (movie_id) REFERENCES movies (id) ON DELETE CASCADE
        )
    """)
    conn.commit()
    conn.close()

init_db()

# 4. Helper Functions
def search_tmdb(title: str):
    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": title, "include_adult": "false"}
    try:
        response = requests.get(url, params=params)
        if response.status_code == 200:
            results = response.json().get("results", [])
            if results:
                first_hit = results[0]
                tmdb_id = first_hit["id"]
                year = first_hit.get("release_date", "")[:4]
                formatted_title = f"{first_hit['title']} ({year})" if year else first_hit['title']
                tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
                
                return {
                    "tmdb_id": tmdb_id,
                    "title": formatted_title,
                    "url": tmdb_url
                }
    except Exception as e:
        print(f"TMDb Fetch Error: {e}")
    return None

def format_user_mention(guild: discord.Guild, user_id: int) -> str:
    member = guild.get_member(user_id) if guild else None
    if member:
        return member.mention
    return f"<@{user_id}>"

# 5. Dynamic Watchlist View
class WatchlistSelect(discord.ui.Select):
    def __init__(self, movies, user_interested_ids, current_page, total_pages):
        self.movies = movies
        user_interested_set = set(user_interested_ids)
        
        options = []
        for m_id, title in movies:
            is_interested = m_id in user_interested_set
            options.append(
                discord.SelectOption(
                    label=title[:100],
                    value=str(m_id),
                    emoji="✅" if is_interested else "❌",
                    default=is_interested,
                    description="Toggle watch interest"
                )
            )
            
        super().__init__(
            placeholder=f"Select movies to toggle... (Page {current_page + 1}/{total_pages})",
            min_values=0,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        selected_ids = {int(val) for val in self.values}

        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()

        for m_id, _ in self.movies:
            if m_id in selected_ids:
                cursor.execute("INSERT OR IGNORE INTO interested (movie_id, user_id) VALUES (?, ?)", (m_id, user_id))
            else:
                cursor.execute("DELETE FROM interested WHERE movie_id = ? AND user_id = ?", (m_id, user_id))

        conn.commit()
        conn.close()

        await self.view.update_message(interaction)

class WatchlistView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.page = 0
        self.per_page = 10  # Increased to 10 per page

    async def get_page_data(self):
        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()

        cursor.execute("SELECT id, title, recommender_id FROM movies WHERE watched = 0 ORDER BY id ASC")
        all_movies = cursor.fetchall()

        total_pages = max(1, (len(all_movies) + self.per_page - 1) // self.per_page)
        
        start = self.page * self.per_page
        end = start + self.per_page
        page_movies = all_movies[start:end]

        embed = discord.Embed(title="🎬 Server Watchlist", color=discord.Color.blue())
        embed.set_footer(text=f"Page {self.page + 1} of {total_pages}")

        lines = []
        user_interested_movie_ids = set()

        for m_id, title, rec_id in page_movies:
            rec_mention = format_user_mention(self.interaction.guild, rec_id)

            cursor.execute("SELECT user_id FROM interested WHERE movie_id = ?", (m_id,))
            interested_ids = [row[0] for row in cursor.fetchall()]
            
            if self.interaction.user.id in interested_ids:
                user_interested_movie_ids.add(m_id)

            names = [format_user_mention(self.interaction.guild, uid) for uid in interested_ids]
            interested_str = ", ".join(names) if names else "None"

            # Simplified single/double line layout
            lines.append(f"**{title}** {rec_mention}\nInterested: {interested_str}\n")

        embed.description = "\n".join(lines) if lines else "No active movies on the list!"
        conn.close()
        return embed, page_movies, total_pages, user_interested_movie_ids

    async def build_components(self):
        self.clear_items()
        embed, page_movies, total_pages, user_interested = await self.get_page_data()

        if self.page >= total_pages:
            self.page = max(0, total_pages - 1)

        prev_btn = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
        next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=(self.page >= total_pages - 1))

        async def prev_callback(interaction: discord.Interaction):
            self.page -= 1
            await self.update_message(interaction)

        async def next_callback(interaction: discord.Interaction):
            self.page += 1
            await self.update_message(interaction)

        prev_btn.callback = prev_callback
        next_btn.callback = next_callback

        self.add_item(prev_btn)
        self.add_item(next_btn)

        if page_movies:
            dropdown_items = [(m[0], m[1]) for m in page_movies]
            self.add_item(WatchlistSelect(dropdown_items, user_interested, self.page, total_pages))

        return embed

    async def update_message(self, interaction: discord.Interaction):
        embed = await self.build_components()
        await interaction.response.edit_message(embed=embed, view=self)

# 6. Multi-Select Batch Movie Removal
class MultiRemoveSelectView(discord.ui.View):
    def __init__(self, movies: list):
        super().__init__(timeout=60)
        
        options = [
            discord.SelectOption(label=title[:100], value=str(m_id), emoji="🗑️") 
            for m_id, title in movies[:25]
        ]
        
        select = discord.ui.Select(
            placeholder="Select one or multiple movies to delete...",
            min_values=1,
            max_values=len(options),
            options=options
        )
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_ids = [int(val) for val in interaction.data["values"]]
        
        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()

        cursor.executemany("DELETE FROM movies WHERE id = ?", [(m_id,) for m_id in selected_ids])
        conn.commit()
        conn.close()

        await interaction.response.edit_message(
            content=f"🗑️ Successfully removed **{len(selected_ids)}** movie(s) from the list.", 
            view=None
        )

# 7. Watched Movies Pagination View
class WatchedListView(discord.ui.View):
    def __init__(self, interaction: discord.Interaction):
        super().__init__(timeout=180)
        self.interaction = interaction
        self.page = 0
        self.per_page = 10

    async def get_page_data(self):
        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()

        cursor.execute("SELECT title, recommender_id FROM movies WHERE watched = 1 ORDER BY id DESC")
        all_movies = cursor.fetchall()

        total_pages = max(1, (len(all_movies) + self.per_page - 1) // self.per_page)
        
        start = self.page * self.per_page
        end = start + self.per_page
        page_movies = all_movies[start:end]

        embed = discord.Embed(title="🍿 Watched Movies History", color=discord.Color.green())
        embed.set_footer(text=f"Page {self.page + 1} of {total_pages}")

        lines = []
        for title, rec_id in page_movies:
            rec_mention = format_user_mention(self.interaction.guild, rec_id)
            lines.append(f"✅ **{title}** — {rec_mention}")

        embed.description = "\n".join(lines) if lines else "No watched movies recorded yet."
        conn.close()
        return embed, total_pages

    async def build_components(self):
        self.clear_items()
        embed, total_pages = await self.get_page_data()

        prev_btn = discord.ui.Button(label="◀ Previous", style=discord.ButtonStyle.secondary, disabled=(self.page == 0))
        next_btn = discord.ui.Button(label="Next ▶", style=discord.ButtonStyle.secondary, disabled=(self.page >= total_pages - 1))

        async def prev_callback(interaction: discord.Interaction):
            self.page -= 1
            await self.update_message(interaction)

        async def next_callback(interaction: discord.Interaction):
            self.page += 1
            await self.update_message(interaction)

        prev_btn.callback = prev_callback
        next_btn.callback = next_callback

        self.add_item(prev_btn)
        self.add_item(next_btn)
        return embed

    async def update_message(self, interaction: discord.Interaction):
        embed = await self.build_components()
        await interaction.response.edit_message(embed=embed, view=self)

# 8. Commands Setup
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Sync error: {e}")

@bot.tree.command(name="suggest", description="Suggest a movie to watch (verified via TMDb).")
async def suggest(interaction: discord.Interaction, title: str):
    user_id = interaction.user.id

    movie_info = search_tmdb(title)
    if not movie_info:
        await interaction.response.send_message(
            f"❌ Could not find **{title}** on TMDb. Please check your spelling.", 
            ephemeral=True
        )
        return

    tmdb_id = movie_info["tmdb_id"]
    official_title = movie_info["title"]
    tmdb_url = movie_info["url"]

    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM movies WHERE recommender_id = ? AND watched = 0", (user_id,))
    if cursor.fetchone()[0] >= 15:
        conn.close()
        await interaction.response.send_message("❌ You have reached your limit of 15 active movie suggestions.", ephemeral=True)
        return

    cursor.execute("SELECT title FROM movies WHERE tmdb_id = ?", (tmdb_id,))
    existing = cursor.fetchone()
    if existing:
        conn.close()
        await interaction.response.send_message(f"❌ **{existing[0]}** is already on the watch list!", ephemeral=True)
        return

    cursor.execute(
        "INSERT INTO movies (tmdb_id, title, tmdb_url, recommender_id) VALUES (?, ?, ?, ?)",
        (tmdb_id, official_title, tmdb_url, user_id)
    )
    movie_id = cursor.lastrowid
    cursor.execute("INSERT INTO interested (movie_id, user_id) VALUES (?, ?)", (movie_id, user_id))

    conn.commit()
    conn.close()

    await interaction.response.send_message(f"✅ Found & added **[{official_title}](<{tmdb_url}>)** to the watchlist!")

@bot.tree.command(name="list", description="View the active movie watchlist.")
async def list_movies(interaction: discord.Interaction):
    view = WatchlistView(interaction)
    embed = await view.build_components()
    await interaction.response.send_message(embed=embed, view=view)

@bot.tree.command(name="remove", description="Batch delete movie suggestions via multi-select dropdown.")
async def remove_movie(interaction: discord.Interaction):
    user_id = interaction.user.id
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()

    if user_id == ADMIN_ID:
        cursor.execute("SELECT id, title FROM movies WHERE watched = 0 ORDER BY id DESC")
    else:
        cursor.execute("SELECT id, title FROM movies WHERE recommender_id = ? AND watched = 0 ORDER BY id DESC", (user_id,))

    movies = cursor.fetchall()
    conn.close()

    if not movies:
        await interaction.response.send_message("❌ No eligible movies found for you to remove.", ephemeral=True)
        return

    view = MultiRemoveSelectView(movies)
    await interaction.response.send_message("Select movie(s) to remove:", view=view, ephemeral=True)

@bot.tree.command(name="mark_watched", description="[ADMIN ONLY] Toggle a movie's watched status by freetyping the title.")
async def mark_watched(interaction: discord.Interaction, title: str):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("❌ Only the admin can mark movies as watched.", ephemeral=True)
        return

    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id, title, watched FROM movies WHERE LOWER(title) LIKE LOWER(?)", (f"%{title.strip()}%",))
    movie = cursor.fetchone()

    if not movie:
        conn.close()
        await interaction.response.send_message(f"❌ Movie matching **{title}** not found.", ephemeral=True)
        return

    movie_id, official_title, is_watched = movie
    new_status = 0 if is_watched else 1

    cursor.execute("UPDATE movies SET watched = ? WHERE id = ?", (new_status, movie_id))
    conn.commit()
    conn.close()

    if new_status == 1:
        await interaction.response.send_message(f"🎉 Marked **{official_title}** as watched!")
    else:
        await interaction.response.send_message(f"🔄 Restored **{official_title}** back to the active watchlist!")

@bot.tree.command(name="watched", description="View the list of movies that have been watched.")
async def watched(interaction: discord.Interaction):
    view = WatchedListView(interaction)
    embed = await view.build_components()
    await interaction.response.send_message(embed=embed, view=view)

bot.run(TOKEN)