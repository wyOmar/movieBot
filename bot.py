import os
import re
import asyncio
import sqlite3
import requests
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv

# 1. Load Environment Variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "348883315626868737"))
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TARGET_CHANNEL_ID = 1535387050944241818

# 2. Initialize Bot
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Global concurrency lock to prevent race conditions during message edits
channel_lock = asyncio.Lock()

# 3. Database Setup
def init_db():
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER UNIQUE NOT NULL,
            title TEXT NOT NULL,
            recommender_id INTEGER NOT NULL,
            message_id INTEGER,
            watched BOOLEAN DEFAULT 0,
            watched_at TIMESTAMP
        )
    """)
    
    cursor.execute("PRAGMA table_info(movies)")
    columns = [col[1] for col in cursor.fetchall()]
    if "watched_at" not in columns:
        cursor.execute("ALTER TABLE movies ADD COLUMN watched_at TIMESTAMP")
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS interested (
            movie_id INTEGER,
            user_id INTEGER,
            PRIMARY KEY (movie_id, user_id),
            FOREIGN KEY (movie_id) REFERENCES movies (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    conn.close()

init_db()

# 4. Helper Functions & Embed Builder
def build_info_embed() -> discord.Embed:
    embed = discord.Embed(
        title="The Movie List",
        color=0xE50914
    )
    embed.add_field(
        name="How to Use the Bot",
        value=(
            f"**Suggest a film:** Post the movie title or a TMDb link directly in <#{TARGET_CHANNEL_ID}>.\n"
            "**Mark interest:** React with an emoji to the movie's message, type its ID number, or re-type the title."
        ),
        inline=False
    )
    embed.add_field(
        name="The Rules",
        value=(
            f"**Suggesting:** Anyone can add films to the list in <#{TARGET_CHANNEL_ID}>.\n"
            "**Interested:** If you mark yourself as interested, we do our best to only watch it when everyone is here. :lying_face: \n"
            "**Movie Time:** We aim to start at 20:00 UTC every bloody day (I don't care if you're from Kuwait!)\n"
            "**The Pick:** Movie decision is SAME DAY and starts 1 hour before movie time.\n"
            "**Get Ban:** If we sit through your pick and 50%+ of the viewers agrees it was doodoo, you get banned from suggesting for 3 movies. Don't recommend idiot movies."
        ),
        inline=False
    )
    return embed

def fetch_tmdb_by_id(tmdb_id: int):
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
    params = {"api_key": TMDB_API_KEY}
    try:
        res = requests.get(url, params=params)
        if res.status_code == 200:
            data = res.json()
            year = data.get("release_date", "")[:4]
            title = f"{data['title']} ({year})" if year else data['title']
            return {"tmdb_id": data["id"], "title": title}
    except Exception as e:
        print(f"TMDb Fetch Error: {e}")
    return None

def search_tmdb_by_query(query: str):
    url = "https://api.themoviedb.org/3/search/movie"
    params = {"api_key": TMDB_API_KEY, "query": query, "include_adult": "false"}
    try:
        res = requests.get(url, params=params)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                hit = results[0]
                year = hit.get("release_date", "")[:4]
                title = f"{hit['title']} ({year})" if year else hit['title']
                return {"tmdb_id": hit["id"], "title": title}
    except Exception as e:
        print(f"TMDb Search Error: {e}")
    return None

def format_movie_display(movie_id: int, tmdb_id: int, title: str, recommender_id: int, interested_ids: list, watched: bool, is_last_watched: bool = False) -> str:
    sugg_mention = f"<@{recommender_id}>"
    interested_mentions = " ".join([f"<@{uid}>" for uid in interested_ids]) if interested_ids else "None"
    tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
    
    details = f"{sugg_mention} | Interested: {interested_mentions}"
    if watched:
        details = f"~~{details}~~ ✅"
        
    text = f"`{movie_id}` [{title}]({tmdb_url}) | {details}"
    if is_last_watched:
        text += "\n─────────────────── 🎬 Watched ───────────────────"
    return text

def get_movie_details(movie_id: int):
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, tmdb_id, title, recommender_id, message_id, watched FROM movies WHERE id = ?", (movie_id,))
    movie = cursor.fetchone()
    
    if not movie:
        conn.close()
        return None
        
    cursor.execute("SELECT user_id FROM interested WHERE movie_id = ?", (movie_id,))
    interested_ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    return {
        "id": movie[0],
        "tmdb_id": movie[1],
        "title": movie[2],
        "recommender_id": movie[3],
        "message_id": movie[4],
        "watched": bool(movie[5]),
        "interested_ids": interested_ids
    }

def get_last_watched_movie_id():
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM movies WHERE watched = 1 ORDER BY watched_at DESC, id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

async def refresh_movie_message(channel: discord.TextChannel, movie_id: int):
    details = get_movie_details(movie_id)
    if not details or not details["message_id"]:
        return
        
    last_watched_id = get_last_watched_movie_id()
    is_last = (movie_id == last_watched_id)

    try:
        msg = await channel.fetch_message(details["message_id"])
        content = format_movie_display(
            details["id"], details["tmdb_id"], details["title"], details["recommender_id"], 
            details["interested_ids"], details["watched"], is_last_watched=is_last
        )
        if msg.content != content:
            await msg.edit(content=content)
    except discord.NotFound:
        pass

async def toggle_watched_and_swap(channel: discord.TextChannel, target_id: int):
    """Direct 1-to-1 swap for instantaneous updates without rate limits."""
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id, message_id, watched FROM movies WHERE id = ?", (target_id,))
    target = cursor.fetchone()
    if not target:
        conn.close()
        return

    m_id, target_msg_id, is_watched = target[0], target[1], bool(target[2])

    if not is_watched:
        cursor.execute("SELECT id FROM movies WHERE watched = 1 ORDER BY watched_at DESC, id DESC LIMIT 1")
        old_last_watched = cursor.fetchone()

        cursor.execute("SELECT id, message_id FROM movies WHERE watched = 0 AND message_id IS NOT NULL ORDER BY message_id ASC")
        unwatched_movies = cursor.fetchall()

        if not unwatched_movies:
            conn.close()
            return

        first_unwatched_id, first_unwatched_msg_id = unwatched_movies[0]

        if m_id == first_unwatched_id:
            cursor.execute("UPDATE movies SET watched = 1, watched_at = CURRENT_TIMESTAMP WHERE id = ?", (m_id,))
            conn.commit()
            conn.close()

            if old_last_watched:
                await refresh_movie_message(channel, old_last_watched[0])
            await refresh_movie_message(channel, m_id)
        else:
            cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (first_unwatched_msg_id, m_id))
            cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (target_msg_id, first_unwatched_id))
            cursor.execute("UPDATE movies SET watched = 1, watched_at = CURRENT_TIMESTAMP WHERE id = ?", (m_id,))
            conn.commit()
            conn.close()

            if old_last_watched:
                await refresh_movie_message(channel, old_last_watched[0])
            await refresh_movie_message(channel, m_id)
            await refresh_movie_message(channel, first_unwatched_id)

    else:
        cursor.execute("SELECT id, message_id FROM movies WHERE watched = 1 ORDER BY watched_at DESC, id DESC LIMIT 1")
        last_watched = cursor.fetchone()

        if not last_watched:
            conn.close()
            return

        last_watched_id, last_watched_msg_id = last_watched[0], last_watched[1]

        if m_id == last_watched_id:
            cursor.execute("UPDATE movies SET watched = 0, watched_at = NULL WHERE id = ?", (m_id,))
            conn.commit()
            cursor.execute("SELECT id FROM movies WHERE watched = 1 ORDER BY watched_at DESC, id DESC LIMIT 1")
            new_last = cursor.fetchone()
            conn.close()

            await refresh_movie_message(channel, m_id)
            if new_last:
                await refresh_movie_message(channel, new_last[0])
        else:
            cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (last_watched_msg_id, m_id))
            cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (target_msg_id, last_watched_id))
            cursor.execute("UPDATE movies SET watched = 0, watched_at = NULL WHERE id = ?", (m_id,))
            conn.commit()
            conn.close()

            await refresh_movie_message(channel, m_id)
            await refresh_movie_message(channel, last_watched_id)

def toggle_interest(movie_id: int, user_id: int):
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM interested WHERE movie_id = ? AND user_id = ?", (movie_id, user_id))
    if cursor.fetchone():
        cursor.execute("DELETE FROM interested WHERE movie_id = ? AND user_id = ?", (movie_id, user_id))
    else:
        cursor.execute("INSERT INTO interested (movie_id, user_id) VALUES (?, ?)", (movie_id, user_id))
    conn.commit()
    conn.close()

def is_user_banned(user_id: int) -> bool:
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,))
    banned = cursor.fetchone() is not None
    conn.close()
    return banned

async def send_temp_message(channel, content: str, delay: int = 5):
    msg = await channel.send(content)
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except discord.HTTPException:
        pass

# 5. Dynamic Delete Button View
class QuickDeleteView(discord.ui.View):
    def __init__(self, movie_id: int, suggester_id: int):
        super().__init__(timeout=60)
        self.movie_id = movie_id
        self.suggester_id = suggester_id

    @discord.ui.button(label="Delete Suggestion", style=discord.ButtonStyle.danger)
    async def delete_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_user_banned(interaction.user.id):
            await interaction.response.send_message("❌ You are banned from interacting with the bot.", ephemeral=True)
            return

        if interaction.user.id != self.suggester_id and interaction.user.id != ADMIN_ID:
            await interaction.response.send_message("❌ Only the suggester or admin can delete this.", ephemeral=True)
            return

        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM movies WHERE id = ?", (self.movie_id,))
        cursor.execute("DELETE FROM interested WHERE movie_id = ?", (self.movie_id,))
        conn.commit()
        conn.close()

        await interaction.message.delete()

# 6. Reaction Event Handler for Toggle Interest
@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.channel_id != TARGET_CHANNEL_ID:
        return

    if payload.user_id == bot.user.id or is_user_banned(payload.user_id):
        return

    async with channel_lock:
        channel = bot.get_channel(payload.channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(payload.channel_id)
            except Exception:
                return

        try:
            msg = await channel.fetch_message(payload.message_id)
            await msg.remove_reaction(payload.emoji, payload.member)
        except Exception:
            pass

        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, watched FROM movies WHERE message_id = ?", (payload.message_id,))
        movie = cursor.fetchone()
        conn.close()

        if movie:
            m_id, watched = movie[0], bool(movie[1])
            if not watched:
                toggle_interest(m_id, payload.user_id)
                await refresh_movie_message(channel, m_id)

# 7. Slash Command (/info)
@bot.tree.command(name="info", description="View the movie night rules and bot usage guide")
async def info_slash(interaction: discord.Interaction):
    if interaction.channel_id == TARGET_CHANNEL_ID:
        await interaction.response.send_message("❌ Don't clutter the movie list! Use this command in another channel.", ephemeral=True)
        return
    await interaction.response.send_message(embed=build_info_embed())

# 8. Bot Events & Message Handlers
@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} global application command(s).")
    except Exception as e:
        print(f"Failed to sync slash commands: {e}")
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.channel_id != TARGET_CHANNEL_ID:
        return

    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM movies WHERE message_id = ?", (payload.message_id,))
    conn.commit()
    conn.close()

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    user_id = message.author.id

    # --- PREFIX !INFO COMMAND ---
    if content == "!info":
        if message.channel.id == TARGET_CHANNEL_ID:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            return
        await message.channel.send(embed=build_info_embed())
        return

    # --- OWNER !PING COMMAND ---
    if content == "!ping" and user_id == ADMIN_ID:
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT i.user_id 
            FROM interested i
            JOIN movies m ON i.movie_id = m.id
            WHERE m.watched = 0
        """)
        users = cursor.fetchall()
        conn.close()

        if users:
            pings = " ".join([f"<@{row[0]}>" for row in users])
            await message.channel.send(f"🔔 Movie Night Ping! {pings}")
        else:
            await send_temp_message(message.channel, "No users with active interest found.", 5)
        return

    # Ignore non-whitelisted messages outside the target movie channel
    if message.channel.id != TARGET_CHANNEL_ID:
        return

    try:
        await message.delete()
    except discord.HTTPException:
        pass

    if is_user_banned(user_id) and user_id != ADMIN_ID:
        return

    async with channel_lock:
        # --- ADMIN COMMANDS ---
        if user_id == ADMIN_ID:
            if content.startswith("!ban "):
                try:
                    target_user_id = int(content.split()[1])
                    conn = sqlite3.connect("movies.db")
                    cursor = conn.cursor()
                    cursor.execute("SELECT 1 FROM banned_users WHERE user_id = ?", (target_user_id,))
                    if cursor.fetchone():
                        cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (target_user_id,))
                    else:
                        cursor.execute("INSERT INTO banned_users (user_id) VALUES (?)", (target_user_id,))
                    conn.commit()
                    conn.close()
                except (IndexError, ValueError):
                    pass
                return

            elif content.startswith("!remove "):
                try:
                    target_user_id = int(content.split()[1])
                    conn = sqlite3.connect("movies.db")
                    cursor = conn.cursor()
                    
                    cursor.execute("SELECT id, message_id FROM movies WHERE recommender_id = ?", (target_user_id,))
                    suggested_movies = cursor.fetchall()
                    
                    for m_id, msg_id in suggested_movies:
                        if msg_id:
                            try:
                                msg = await message.channel.fetch_message(msg_id)
                                await msg.delete()
                            except discord.NotFound:
                                pass
                        cursor.execute("DELETE FROM interested WHERE movie_id = ?", (m_id,))
                        cursor.execute("DELETE FROM movies WHERE id = ?", (m_id,))
                    
                    cursor.execute("SELECT movie_id FROM interested WHERE user_id = ?", (target_user_id,))
                    movies_to_update = [row[0] for row in cursor.fetchall()]
                    
                    cursor.execute("DELETE FROM interested WHERE user_id = ?", (target_user_id,))
                    conn.commit()
                    conn.close()

                    for m_id in movies_to_update:
                        await refresh_movie_message(message.channel, m_id)

                except (IndexError, ValueError):
                    pass
                return

            elif content.startswith("!watched "):
                try:
                    m_id = int(content.split()[1])
                    await toggle_watched_and_swap(message.channel, m_id)
                except (IndexError, ValueError):
                    pass
                return

            elif content.startswith("!delete "):
                try:
                    m_id = int(content.split()[1])
                    details = get_movie_details(m_id)
                    if details and details["message_id"]:
                        try:
                            msg = await message.channel.fetch_message(details["message_id"])
                            await msg.delete()
                        except discord.NotFound:
                            pass
                    conn = sqlite3.connect("movies.db")
                    cursor = conn.cursor()
                    cursor.execute("DELETE FROM movies WHERE id = ?", (m_id,))
                    cursor.execute("DELETE FROM interested WHERE movie_id = ?", (m_id,))
                    conn.commit()
                    conn.close()
                except (IndexError, ValueError):
                    pass
                return

            elif content.startswith("!suggest "):
                try:
                    parts = content.split()
                    m_id = int(parts[1])
                    new_user_id = int(parts[2])
                    conn = sqlite3.connect("movies.db")
                    cursor = conn.cursor()
                    cursor.execute("UPDATE movies SET recommender_id = ? WHERE id = ?", (new_user_id, m_id))
                    conn.commit()
                    conn.close()
                    await refresh_movie_message(message.channel, m_id)
                except (IndexError, ValueError):
                    pass
                return

        # --- INDEX SELECTION ---
        if content.isdigit():
            target_id = int(content)
            details = get_movie_details(target_id)
            if details and not details["watched"]:
                toggle_interest(target_id, user_id)
                await refresh_movie_message(message.channel, target_id)
            return

        # --- TMDB URL OR TITLE PROCESSING ---
        tmdb_info = None
        url_match = re.search(r"themoviedb\.org/movie/(\d+)", content)
        
        if url_match:
            tmdb_id = int(url_match.group(1))
            tmdb_info = fetch_tmdb_by_id(tmdb_id)
        else:
            tmdb_info = search_tmdb_by_query(content)

        if not tmdb_info:
            return

        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, watched FROM movies WHERE tmdb_id = ?", (tmdb_info["tmdb_id"],))
        existing = cursor.fetchone()

        if existing:
            m_id, watched = existing[0], bool(existing[1])
            conn.close()
            if not watched:
                toggle_interest(m_id, user_id)
                await refresh_movie_message(channel, m_id)
            return

        cursor.execute("SELECT COUNT(*) FROM movies WHERE recommender_id = ? AND watched = 0", (user_id,))
        unwatched_count = cursor.fetchone()[0]
        
        if unwatched_count >= 15:
            conn.close()
            await send_temp_message(message.channel, "You are only allowed to have 15 unwatched suggestions at once you bum!", delay=6)
            return

        cursor.execute(
            "INSERT INTO movies (tmdb_id, title, recommender_id) VALUES (?, ?, ?)",
            (tmdb_info["tmdb_id"], tmdb_info["title"], user_id)
        )
        new_movie_id = cursor.lastrowid

        cursor.execute("INSERT INTO interested (movie_id, user_id) VALUES (?, ?)", (new_movie_id, user_id))
        conn.commit()
        conn.close()

        view = QuickDeleteView(new_movie_id, user_id)
        formatted_msg = format_movie_display(new_movie_id, tmdb_info["tmdb_id"], tmdb_info["title"], user_id, [user_id], False)
        sent_msg = await message.channel.send(formatted_msg, view=view)

        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (sent_msg.id, new_movie_id))
        conn.commit()
        conn.close()

        asyncio.create_task(remove_view_after_timeout(sent_msg, 60))

async def remove_view_after_timeout(msg: discord.Message, timeout: int):
    await asyncio.sleep(timeout)
    try:
        await msg.edit(view=None)
    except discord.NotFound:
        pass

bot.run(TOKEN)