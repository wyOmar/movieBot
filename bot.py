import os
import re
import asyncio
import sqlite3
import requests
import discord
from discord.ext import commands
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
    
    # Check if watched_at column exists for older database versions
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

# 4. Helper Functions
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
    
    # Removed "Recommender:" label
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

async def reorder_and_sync_channel(channel: discord.TextChannel):
    """Reorders messages so watched movies are at the top (oldest -> newest watched) followed by unwatched movies."""
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()

    # Watched movies ordered from oldest watched to most recent
    cursor.execute("SELECT id FROM movies WHERE watched = 1 ORDER BY watched_at ASC, id ASC")
    watched_ids = [r[0] for r in cursor.fetchall()]

    # Unwatched movies ordered by ID
    cursor.execute("SELECT id FROM movies WHERE watched = 0 ORDER BY id ASC")
    unwatched_ids = [r[0] for r in cursor.fetchall()]

    all_movie_ids = watched_ids + unwatched_ids
    if not all_movie_ids:
        conn.close()
        return

    # Gather existing message IDs in chronological order
    cursor.execute("SELECT message_id FROM movies WHERE message_id IS NOT NULL")
    message_ids = sorted([r[0] for r in cursor.fetchall() if r[0] is not None])

    # Re-assign message slots
    for i, m_id in enumerate(all_movie_ids):
        if i < len(message_ids):
            cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (message_ids[i], m_id))

    conn.commit()
    conn.close()

    # Refresh message contents in Discord
    last_watched_id = watched_ids[-1] if watched_ids else None
    for i, m_id in enumerate(all_movie_ids):
        if i < len(message_ids):
            target_msg_id = message_ids[i]
            details = get_movie_details(m_id)
            if details:
                is_last = (m_id == last_watched_id)
                content = format_movie_display(
                    details["id"], details["tmdb_id"], details["title"], details["recommender_id"],
                    details["interested_ids"], details["watched"], is_last_watched=is_last
                )
                try:
                    msg = await channel.fetch_message(target_msg_id)
                    if msg.content != content:
                        await msg.edit(content=content)
                except discord.NotFound:
                    pass

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

async def send_temp_message(channel: discord.TextChannel, content: str, delay: int = 5):
    msg = await channel.send(content)
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except discord.NotFound:
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

    channel = bot.get_channel(payload.channel_id)
    if not channel:
        try:
            channel = await bot.fetch_channel(payload.channel_id)
        except Exception:
            return

    # Delete the user's reaction
    try:
        msg = await channel.fetch_message(payload.message_id)
        await msg.remove_reaction(payload.emoji, payload.member)
    except Exception:
        pass

    # Check if message corresponds to an active movie
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

# 7. Bot Events & Message Handlers
@bot.event
async def on_ready():
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
    if message.author.bot or message.channel.id != TARGET_CHANNEL_ID:
        return

    content = message.content.strip()
    user_id = message.author.id

    # Owner !ping command handling
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

    try:
        await message.delete()
    except discord.HTTPException:
        pass

    if is_user_banned(user_id) and user_id != ADMIN_ID:
        return

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
                conn = sqlite3.connect("movies.db")
                cursor = conn.cursor()
                cursor.execute("SELECT watched FROM movies WHERE id = ?", (m_id,))
                row = cursor.fetchone()
                if row:
                    new_watched = not bool(row[0])
                    if new_watched:
                        cursor.execute("UPDATE movies SET watched = 1, watched_at = CURRENT_TIMESTAMP WHERE id = ?", (m_id,))
                    else:
                        cursor.execute("UPDATE movies SET watched = 0, watched_at = NULL WHERE id = ?", (m_id,))
                    conn.commit()
                conn.close()
                await reorder_and_sync_channel(message.channel)
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

    # Check local DB
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, watched FROM movies WHERE tmdb_id = ?", (tmdb_info["tmdb_id"],))
    existing = cursor.fetchone()

    if existing:
        m_id, watched = existing[0], bool(existing[1])
        conn.close()
        if not watched:
            toggle_interest(m_id, user_id)
            await refresh_movie_message(message.channel, m_id)
        return

    # Enforce 15 unwatched limit check
    cursor.execute("SELECT COUNT(*) FROM movies WHERE recommender_id = ? AND watched = 0", (user_id,))
    unwatched_count = cursor.fetchone()[0]
    
    if unwatched_count >= 15:
        conn.close()
        await send_temp_message(message.channel, "You are only allowed to have 15 unwatched suggestions at once you bum!", delay=6)
        return

    # Add new movie
    cursor.execute(
        "INSERT INTO movies (tmdb_id, title, recommender_id) VALUES (?, ?, ?)",
        (tmdb_info["tmdb_id"], tmdb_info["title"], user_id)
    )
    new_movie_id = cursor.lastrowid

    # Auto-add suggester to interested list
    cursor.execute("INSERT INTO interested (movie_id, user_id) VALUES (?, ?)", (new_movie_id, user_id))
    conn.commit()
    conn.close()

    # Send message with 60s red delete button
    view = QuickDeleteView(new_movie_id, user_id)
    formatted_msg = format_movie_display(new_movie_id, tmdb_info["tmdb_id"], tmdb_info["title"], user_id, [user_id], False)
    sent_msg = await message.channel.send(formatted_msg, view=view)

    # Save message_id
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