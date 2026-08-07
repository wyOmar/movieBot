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

def format_movie_display(movie_id: int, tmdb_id: int, title: str, recommender_id: int, interested_ids: list, watched: bool) -> str:
    sugg_mention = f"<@{recommender_id}>"
    interested_mentions = " ".join([f"<@{uid}>" for uid in interested_ids]) if interested_ids else "None"
    tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
    
    details = f"Recommender: {sugg_mention} | Interested: {interested_mentions}"
    if watched:
        details = f"~~{details}~~ ✅"
        
    return f"`{movie_id}` [{title}]({tmdb_url}) | {details}"

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

async def refresh_movie_message(channel: discord.TextChannel, movie_id: int):
    details = get_movie_details(movie_id)
    if not details or not details["message_id"]:
        return
        
    try:
        msg = await channel.fetch_message(details["message_id"])
        content = format_movie_display(
            details["id"], details["tmdb_id"], details["title"], details["recommender_id"], details["interested_ids"], details["watched"]
        )
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

# 6. Bot Events & Message Handlers
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

    try:
        await message.delete()
    except discord.HTTPException:
        pass

    # Check if user is banned (ignore everything they send)
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
                
                # Delete movies they suggested (and clean up Discord messages)
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
                
                # Retrieve movies they were ONLY interested in (to update Discord messages)
                cursor.execute("SELECT movie_id FROM interested WHERE user_id = ?", (target_user_id,))
                movies_to_update = [row[0] for row in cursor.fetchall()]
                
                cursor.execute("DELETE FROM interested WHERE user_id = ?", (target_user_id,))
                conn.commit()
                conn.close()

                # Refresh messages to erase them from the interested lists visually
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
                cursor.execute("UPDATE movies SET watched = NOT watched WHERE id = ?", (m_id,))
                conn.commit()
                conn.close()
                await refresh_movie_message(message.channel, m_id)
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

    # --- INDEX SELECTION (Plain digits like 1 or 12) ---
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