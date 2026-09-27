"""Persistent storage for DM templates, send history, and sender rate limits."""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "vixen_ai.sqlite3"


class DMStore:
    def __init__(self, db_path=DB_PATH):
        self.db_path = Path(db_path)
        self._lock = threading.RLock()
        self._initialized = False

    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        if not self._initialized:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dm_templates (
                    template_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    embed_json TEXT NOT NULL,
                    creator_id INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    UNIQUE(guild_id, name)
                );
                CREATE TABLE IF NOT EXISTS dm_history (
                    dm_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    recipient_id INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL,
                    template_id INTEGER,
                    status TEXT NOT NULL,
                    error TEXT,
                    embed_json TEXT NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dm_history_guild_time
                    ON dm_history(guild_id, timestamp DESC);
                CREATE TABLE IF NOT EXISTS dm_send_reservations (
                    reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_dm_send_reservations
                    ON dm_send_reservations(sender_id, timestamp);
                """
            )
            self._initialized = True
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_template(self, guild_id, name, embed, creator_id):
        now = time.time()
        data = json.dumps(embed, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO dm_templates(guild_id, name, embed_json, creator_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (int(guild_id), str(name).strip()[:100], data, int(creator_id), now, now),
            )
            return cursor.lastrowid

    def update_template(self, guild_id, template_id, name, embed):
        data = json.dumps(embed, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """UPDATE dm_templates SET name = ?, embed_json = ?, updated_at = ?
                   WHERE guild_id = ? AND template_id = ?""",
                (str(name).strip()[:100], data, time.time(), int(guild_id), int(template_id)),
            )
            return cursor.rowcount == 1

    def duplicate_template(self, guild_id, template_id, name, creator_id):
        source = self.get_template(guild_id, template_id)
        if source is None:
            return None
        return self.create_template(guild_id, name, source["embed"], creator_id)

    def delete_template(self, guild_id, template_id):
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM dm_templates WHERE guild_id = ? AND template_id = ?",
                (int(guild_id), int(template_id)),
            )
            return cursor.rowcount == 1

    @staticmethod
    def _template(row):
        result = dict(row)
        result["embed"] = json.loads(result.pop("embed_json"))
        return result

    def get_template(self, guild_id, template_id):
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM dm_templates WHERE guild_id = ? AND template_id = ?",
                (int(guild_id), int(template_id)),
            ).fetchone()
        return self._template(row) if row else None

    def list_templates(self, guild_id):
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM dm_templates WHERE guild_id = ? ORDER BY name COLLATE NOCASE",
                (int(guild_id),),
            ).fetchall()
        return [self._template(row) for row in rows]

    def record_send(self, guild_id, recipient_id, sender_id, template_id, status, error, embed):
        data = json.dumps(embed, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connection() as connection:
            cursor = connection.execute(
                """INSERT INTO dm_history(
                    guild_id, recipient_id, sender_id, template_id, status, error, embed_json, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(guild_id), int(recipient_id), int(sender_id),
                    int(template_id) if template_id is not None else None,
                    str(status)[:30], str(error or "")[:200], data, time.time(),
                ),
            )
            return cursor.lastrowid

    def recent_history(self, guild_id, limit=100):
        limit = max(1, min(500, int(limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                """SELECT dm_id, guild_id, recipient_id, sender_id, template_id, status, error, timestamp
                   FROM dm_history WHERE guild_id = ? ORDER BY dm_id DESC LIMIT ?""",
                (int(guild_id), limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def reserve_send(self, sender_id, maximum=25, now=None):
        limit = max(1, min(100, int(maximum)))
        current = time.time() if now is None else float(now)
        cutoff = current - 3600
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM dm_send_reservations WHERE timestamp <= ?", (cutoff,))
            count = connection.execute(
                "SELECT COUNT(*) FROM dm_send_reservations WHERE sender_id = ? AND timestamp > ?",
                (int(sender_id), cutoff),
            ).fetchone()[0]
            if count >= limit:
                return False
            connection.execute(
                "INSERT INTO dm_send_reservations(sender_id, timestamp) VALUES (?, ?)",
                (int(sender_id), current),
            )
            return True


dm_store = DMStore()