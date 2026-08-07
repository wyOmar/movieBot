import os
import re
import sqlite3
import requests
import asyncio
import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
TMDB_API_KEY = os.getenv("TMDB_API_KEY")
TARGET_CHANNEL_ID = 1535387050944241818

# User ID Mapping
USERS = {
    "pepeo": 1415378937596612699,
    "mt3b": 1476379231154999458,
    "m3tv": 1476379231154999458,
    "sarmen": 290988711364263936,
    "reallysmart": 267792992771768340,
    "earth": 292413956436262913,
    "wyoming": 348883315626868737,
}

# Raw Spreadsheet Data
DATA = [
    # --- SCREENSHOT 1: WATCHED MOVIES (BLUE) ---
    {"title": "Marty Supreme", "rec": "pepeo", "interested": ["wyoming", "pepeo"], "watched": True},
    {"title": "Obsession", "rec": "wyoming", "interested": ["wyoming", "pepeo"], "watched": True},
    {"title": "Detached", "rec": "sarmen", "interested": ["wyoming", "pepeo"], "watched": True},
    {"title": "Memento", "rec": "mt3b", "interested": ["wyoming", "pepeo", "m3tv"], "watched": True},
    {"title": "Shutter Island", "rec": "wyoming", "interested": ["wyoming", "pepeo", "m3tv"], "watched": True},
    {"title": "The Social Network", "rec": "wyoming", "interested": ["wyoming", "reallysmart"], "watched": True},
    {"title": "Killers of the Flower Moon", "rec": "reallysmart", "interested": ["wyoming", "pepeo", "reallysmart"], "watched": True},
    {"title": "Legend", "rec": "sarmen", "interested": ["wyoming", "sarmen"], "watched": True},
    {"title": "Tuner", "rec": "wyoming", "interested": ["wyoming", "pepeo"], "watched": True},
    {"title": "Oldboy", "rec": "pepeo", "interested": ["wyoming", "pepeo", "m3tv", "earth"], "watched": True},
    {"title": "Atonement", "rec": "earth", "interested": ["wyoming", "pepeo", "m3tv", "earth"], "watched": True},
    {"title": "Superman", "rec": "wyoming", "interested": ["wyoming", "pepeo"], "watched": True},
    {"title": "Eternal Sunshine of the Spotless Mind", "rec": "pepeo", "interested": ["wyoming", "pepeo", "m3tv"], "watched": True},

    # --- SCREENSHOT 2: UNWATCHED MOVIES ---
    {"title": "The Odyssey", "rec": "mt3b", "interested": ["wyoming", "pepeo", "m3tv"], "watched": False},
    {"title": "Minions & Monsters", "rec": "wyoming", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "The Pianist", "rec": "pepeo", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "Everything Everywhere All at Once", "rec": "pepeo", "interested": ["wyoming", "pepeo", "m3tv"], "watched": False},
    {"title": "There Will Be Blood", "rec": "pepeo", "interested": ["wyoming", "pepeo", "sarmen"], "watched": False},
    {"title": "Whiplash", "rec": "pepeo", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "Green Book", "rec": "pepeo", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "The Green Mile", "rec": "pepeo", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "The Devil Wears Prada", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "The Devil Wears Prada 2", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Blade Runner 2049", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Zootopia", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Zootopia 2", "rec": "wyoming", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "The SpongeBob SquarePants Movie", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Despicable Me 4", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "The Martian", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "The Drama", "rec": "wyoming", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "American Pie", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Stand and Deliver", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "The Furious", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "Porco Rosso", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "Ping Pong the Animation", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "Adaptation", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "The Pursuit of Happyness", "rec": "reallysmart", "interested": ["wyoming", "pepeo", "reallysmart"], "watched": False},
    {"title": "Batman Begins", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "The Dark Knight", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "The Dark Knight Rises", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "Coach Carter", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "Avatar: The Last Airbender", "rec": "reallysmart", "interested": ["wyoming", "reallysmart"], "watched": False},
    {"title": "The Machinist", "rec": "reallysmart", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "King Richard", "rec": "reallysmart", "interested": ["wyoming"], "watched": False},
    {"title": "Cars", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Cars 2", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Cars 3", "rec": "wyoming", "interested": ["wyoming"], "watched": False},
    {"title": "Hoppers", "rec": "reallysmart", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "Memories of Murder", "rec": "pepeo", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "The Spectacular Now", "rec": "pepeo", "interested": ["wyoming", "pepeo"], "watched": False},
    {"title": "Heat", "rec": "earth", "interested": ["wyoming"], "watched": False},
]

def search_tmdb(query: str):
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
        print(f"Error fetching TMDb for {query}: {e}")
    return {"tmdb_id": abs(hash(query)) % (10**8), "title": query}

def format_movie_display(movie_id: int, tmdb_id: int, full_title: str, recommender_id: int, interested_ids: list, watched: bool) -> str:
    sugg_mention = f"<@{recommender_id}>"
    interested_mentions = " ".join([f"<@{uid}>" for uid in interested_ids]) if interested_ids else "None"
    tmdb_url = f"https://www.themoviedb.org/movie/{tmdb_id}"
    
    match = re.search(r"^(.*?)\s*(\(\d{4}\))?$", full_title)
    if match:
        clean_name = match.group(1).strip()
        year_str = f" {match.group(2)}" if match.group(2) else ""
    else:
        clean_name = full_title
        year_str = ""

    if watched:
        return f"`{movie_id}` [{clean_name}]({tmdb_url}) ~~{year_str} | Recommender: {sugg_mention} | Interested: {interested_mentions}~~ ✅"
    else:
        return f"`{movie_id}` [{clean_name}{year_str}]({tmdb_url}) | Recommender: {sugg_mention} | Interested: {interested_mentions}"

class MigrationClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)

    async def on_ready(self):
        print(f"Logged in as {self.user}. Beginning migration process...")
        channel = self.get_channel(TARGET_CHANNEL_ID)
        if not channel:
            print("Target channel not found. Check your TARGET_CHANNEL_ID.")
            await self.close()
            return

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

        seen_tmdb_ids = set()

        for idx, item in enumerate(DATA, start=1):
            tmdb_info = search_tmdb(item["title"])
            tmdb_id = tmdb_info["tmdb_id"]

            if tmdb_id in seen_tmdb_ids:
                tmdb_id = 9000000 + idx
            seen_tmdb_ids.add(tmdb_id)

            rec_id = USERS[item["rec"].lower()]
            interested_uids = list(set([USERS[name.lower()] for name in item["interested"] if name.lower() in USERS]))

            if rec_id not in interested_uids:
                interested_uids.append(rec_id)

            cursor.execute(
                "INSERT INTO movies (tmdb_id, title, recommender_id, watched) VALUES (?, ?, ?, ?)",
                (tmdb_id, tmdb_info["title"], rec_id, item["watched"])
            )
            movie_id = cursor.lastrowid

            for uid in interested_uids:
                cursor.execute("INSERT OR IGNORE INTO interested (movie_id, user_id) VALUES (?, ?)", (movie_id, uid))

            msg_content = format_movie_display(
                movie_id, tmdb_id, tmdb_info["title"], rec_id, interested_uids, item["watched"]
            )
            msg = await channel.send(msg_content)

            cursor.execute("UPDATE movies SET message_id = ? WHERE id = ?", (msg.id, movie_id))
            conn.commit()

            print(f"Migrated [{movie_id}]: {tmdb_info['title']}")
            await asyncio.sleep(1)

        conn.close()
        print("Migration Complete!")
        await self.close()

if __name__ == "__main__":
    client = MigrationClient()
    client.run(TOKEN)