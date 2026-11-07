"""XDG storage, schema versioning and portable bookmarks."""
import json
import os
from pathlib import Path
import sqlite3
from .model import ReceiverConfig


def data_dir():
    path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "touchwave"
    path.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    def __init__(self, path=None):
        self.root = Path(path) if path else data_dir()
        self.root.mkdir(parents=True, exist_ok=True)
        self.recordings = self.root / "recordings"
        self.recordings.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / "touchwave.sqlite3")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            raise RuntimeError("Database was created by a newer TouchWave")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS preferences(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bookmarks(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                tags TEXT NOT NULL, config TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS recordings(path TEXT PRIMARY KEY, metadata TEXT NOT NULL);
            PRAGMA user_version=1;
        """)
        self.recover_recordings()

    def recover_recordings(self):
        """Index captures finalized while the UI was closing or restarting."""
        known = {row[0] for row in self.db.execute("SELECT path FROM recordings")}
        sidecars = list(self.recordings.glob("*.json")) + list(self.recordings.glob("*.sigmf-meta"))
        for sidecar in sidecars:
            try:
                doc = json.loads(sidecar.read_text())
                if sidecar.suffix == ".sigmf-meta":
                    metadata = doc["global"]["touchwave:recording"]
                    path = sidecar.with_suffix(".sigmf-data")
                else:
                    metadata = doc
                    path = sidecar.with_suffix(".wav")
                if str(path) in known or not path.exists():
                    continue
                if metadata["kind"] not in ("audio", "iq") or metadata["seconds"] < 0:
                    continue
                metadata["path"] = str(path)
                self.index_recording(metadata)
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def load_config(self):
        row = self.db.execute("SELECT value FROM preferences WHERE key='receiver'").fetchone()
        try:
            return ReceiverConfig.from_dict(json.loads(row[0])) if row else ReceiverConfig()
        except (ValueError, TypeError):
            return ReceiverConfig()

    def save_config(self, config):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO preferences VALUES('receiver',?)", (json.dumps(config.to_dict()),))

    def add_bookmark(self, name, tags, config):
        name = name.strip()
        if not name:
            raise ValueError("Bookmark needs a name")
        config.validate()
        with self.db:
            cursor = self.db.execute("INSERT INTO bookmarks(name,tags,config) VALUES(?,?,?)",
                                     (name, tags.strip(), json.dumps(config.to_dict())))
        return cursor.lastrowid

    def bookmarks(self, query=""):
        rows = self.db.execute("SELECT id,name,tags,config FROM bookmarks ORDER BY name COLLATE NOCASE").fetchall()
        return [dict(id=r[0], name=r[1], tags=r[2], config=json.loads(r[3])) for r in rows
                if query.casefold() in (r[1] + " " + r[2] + " " + str(json.loads(r[3])["frequency"])).casefold()]

    def delete_bookmark(self, ident):
        with self.db:
            self.db.execute("DELETE FROM bookmarks WHERE id=?", (ident,))

    def export_bookmarks(self, path):
        Path(path).write_text(json.dumps({"version": 1, "bookmarks": self.bookmarks()}, indent=2))

    def import_bookmarks(self, path):
        doc = json.loads(Path(path).read_text())
        if doc.get("version") != 1 or not isinstance(doc.get("bookmarks"), list):
            raise ValueError("Unsupported bookmark file")
        items = []
        for row in doc["bookmarks"]:
            name, tags = str(row["name"]).strip(), str(row.get("tags", "")).strip()
            if not name:
                raise ValueError("Bookmark needs a name")
            items.append((name, tags, json.dumps(ReceiverConfig.from_dict(row["config"]).to_dict())))
        with self.db:
            self.db.executemany("INSERT INTO bookmarks(name,tags,config) VALUES(?,?,?)", items)
        return len(items)

    def index_recording(self, metadata):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO recordings VALUES(?,?)",
                            (metadata["path"], json.dumps(metadata)))

    def list_recordings(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT metadata FROM recordings ORDER BY rowid DESC")]

    def close(self):
        self.db.close()
