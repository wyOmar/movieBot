import os
import sqlite3
import discord
import asyncio
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TARGET_CHANNEL_ID = 1535387050944241818

def migrate_database():
    print("Step 1: Updating database structure...")
    conn = sqlite3.connect("movies.db")
    cursor = conn.cursor()

    # Ensure watched_at column exists
    cursor.execute("PRAGMA table_info(movies)")
    columns = [col[1] for col in cursor.fetchall()]
    if "watched_at" not in columns:
        cursor.execute("ALTER TABLE movies ADD COLUMN watched_at TIMESTAMP")

    # Set timestamp for existing watched movies if currently NULL
    cursor.execute("SELECT id FROM movies WHERE watched = 1 AND watched_at IS NULL ORDER BY id ASC")
    watched_movies = cursor.fetchall()

    for idx, (m_id,) in enumerate(watched_movies):
        # Give sequential mock timestamps based on ID order
        timestamp_str = f"2026-01-01 00:{idx:02d}:00"
        cursor.execute("UPDATE movies SET watched_at = ? WHERE id = ?", (timestamp_str, m_id))

    conn.commit()
    conn.close()
    print("Database structure updated.")

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

def get_movie_details(conn, movie_id: int):
    cursor = conn.cursor()
    cursor.execute("SELECT id, tmdb_id, title, recommender_id, message_id, watched FROM movies WHERE id = ?", (movie_id,))
    movie = cursor.fetchone()
    if not movie:
        return None
        
    cursor.execute("SELECT user_id FROM interested WHERE movie_id = ?", (movie_id,))
    interested_ids = [row[0] for row in cursor.fetchall()]
    
    return {
        "id": movie[0],
        "tmdb_id": movie[1],
        "title": movie[2],
        "recommender_id": movie[3],
        "message_id": movie[4],
        "watched": bool(movie[5]),
        "interested_ids": interested_ids
    }

async def run_discord_cleanup():
    print("Step 2: Connecting to Discord and updating messages...")
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        print(f"Logged in as {client.user}. Running message migration...")
        channel = client.get_channel(TARGET_CHANNEL_ID)
        if not channel:
            channel = await client.fetch_channel(TARGET_CHANNEL_ID)

        conn = sqlite3.connect("movies.db")
        cursor = conn.cursor()

        # Gather watched movies (sorted oldest watched to newest watched)
        cursor.execute("SELECT id FROM movies WHERE watched = 1 ORDER BY watched_at ASC, id ASC")
        watched_ids = [r[0] for r in cursor.fetchall()]

        # Gather unwatched movies (sorted by ID)
        cursor.execute("SELECT id FROM movies WHERE watched = 0 ORDER BY id ASC")
        unwatched_ids = [r[0] for r in cursor.fetchall()]

        all_movie_ids = watched_ids + unwatched_ids

        # Gather existing message IDs chronologically
        cursor.execute("SELECT message_id FROM movies WHERE message_id IS NOT NULL")
        message_ids = sorted([r[0] for r in cursor.fetchall() if r[0] is not None])

        # Swap message slots in DB
        for i, m_id in enumerate(all_movie_ids):
            if i < len(message_ids):
                cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (message_ids[i], m_id))

        conn.commit()

        # Update message contents in Discord
        last_watched_id = watched_ids[-1] if watched_ids else None
        
        for i, m_id in enumerate(all_movie_ids):
            if i < len(message_ids):
                target_msg_id = message_ids[i]
                details = get_movie_details(conn, m_id)
                if details:
                    is_last = (m_id == last_watched_id)
                    content = format_movie_display(
                        details["id"], details["tmdb_id"], details["title"], details["recommender_id"],
                        details["interested_ids"], details["watched"], is_last_watched=is_last
                    )
                    try:
                        msg = await channel.fetch_message(target_msg_id)
                        await msg.edit(content=content)
                        await asyncio.sleep(0.5) # Rate limit safety
                    except discord.NotFound:
                        print(f"Message {target_msg_id} not found in channel.")
                    except Exception as e:
                        print(f"Error editing message {target_msg_id}: {e}")

        conn.close()
        print("Migration complete! Closing client...")
        await client.close()

    await client.start(TOKEN)

if __name__ == "__main__":
    migrate_database()
    asyncio.run(run_discord_cleanup())