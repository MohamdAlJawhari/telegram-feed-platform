import sqlite3

DATABASE_NAME = "telegram.db"


def get_connection():
    return sqlite3.connect(DATABASE_NAME)


def create_database():
    conn = get_connection()

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            channel_title TEXT NOT NULL,
            channel_username TEXT NOT NULL,

            message_id INTEGER NOT NULL,

            text TEXT,

            message_type TEXT,

            date TEXT,

            UNIQUE(channel_username, message_id)
        )
    """)

    conn.commit()
    conn.close()