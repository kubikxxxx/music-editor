import os
import shutil
import sqlite3
import hashlib
from dataclasses import dataclass
from typing import List, Optional, Tuple

# Ukládáme POUZE do .pmdata v kořeni projektu
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PMDATA_DIR = os.path.join(PROJECT_ROOT, ".pmdata")
LIB_DIR = os.path.join(PMDATA_DIR, "library")
DB_PATH = os.path.join(PMDATA_DIR, "library.sqlite3")

os.makedirs(LIB_DIR, exist_ok=True)
os.makedirs(PMDATA_DIR, exist_ok=True)

EXPECTED_SCHEMA = {
    "id": "TEXT",
    "title": "TEXT",
    "path": "TEXT",
    "duration_ms": "INTEGER",
}

@dataclass
class Track:
    id: str
    title: str
    path: str
    duration_ms: int

# ---------------- schema helpers ----------------

def _table_info(conn: sqlite3.Connection, table: str) -> list[tuple]:
    # cid, name, type, notnull, dflt_value, pk
    return conn.execute(f"PRAGMA table_info({table});").fetchall()

def _tracks_schema_ok(conn: sqlite3.Connection) -> bool:
    try:
        info = _table_info(conn, "tracks")
    except sqlite3.OperationalError:
        return False
    if not info:
        return False
    # name->(type,notnull,pk)
    got = {row[1].lower(): (row[2].upper(), row[3], row[5]) for row in info}
    # musí existovat všechny sloupce s oček. typem
    for col, typ in EXPECTED_SCHEMA.items():
        if col not in got:
            return False
        if got[col][0] != typ:
            return False
    # id má být PK
    if got["id"][2] != 1:
        return False
    # title NOT NULL a duration_ms NOT NULL
    if got["title"][1] != 1:
        return False
    if got["duration_ms"][1] != 1:
        return False
    return True

def _create_tracks(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracks(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            path TEXT,
            duration_ms INTEGER NOT NULL
        );
        """
    )
    conn.commit()

def _migrate_tracks(conn: sqlite3.Connection):
    """
    Recreate-and-copy migration:
      - vytvoří tracks_new s korektním schématem,
      - zkusí překlopit data z existující tracks (CAST na správné typy),
      - přejmenuje.
    """
    # když tabulka vůbec není, jen ji vytvoř
    try:
        info = _table_info(conn, "tracks")
    except sqlite3.OperationalError:
        _create_tracks(conn)
        return

    conn.execute("DROP TABLE IF EXISTS tracks_new;")
    conn.execute(
        """
        CREATE TABLE tracks_new(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            path TEXT,
            duration_ms INTEGER NOT NULL
        );
        """
    )
    # zjisti, které sloupce máme k dispozici
    cols = {row[1].lower() for row in info}

    # připrav SELECT s CASTy a NULL fallbacky
    sel_id = "CAST(id AS TEXT)" if "id" in cols else "NULL"
    sel_title = "CAST(title AS TEXT)" if "title" in cols else "NULL"
    sel_path = "CAST(path AS TEXT)" if "path" in cols else "NULL"
    # různé legacy názvy pro délku
    if "duration_ms" in cols:
        sel_dur = "CAST(duration_ms AS INTEGER)"
    elif "duration" in cols:
        sel_dur = "CAST(duration AS INTEGER)"
    elif "length_ms" in cols:
        sel_dur = "CAST(length_ms AS INTEGER)"
    else:
        sel_dur = "0"

    # překlop data (ty, co mají aspoň id+title)
    conn.execute(
        f"""
        INSERT OR IGNORE INTO tracks_new(id, title, path, duration_ms)
        SELECT {sel_id} AS id,
               COALESCE({sel_title}, '') AS title,
               {sel_path} AS path,
               COALESCE({sel_dur}, 0) AS duration_ms
        FROM tracks
        """
    )
    conn.execute("DROP TABLE tracks;")
    conn.execute("ALTER TABLE tracks_new RENAME TO tracks;")
    conn.commit()

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    # 1) vytvoř, pokud neexistuje
    _create_tracks(conn)
    # 2) rychlá migrace: přidej path, pokud chybí
    try:
        conn.execute("SELECT path FROM tracks LIMIT 1;")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE tracks ADD COLUMN path TEXT;")
        conn.commit()
    # 3) validace schématu (řeší datatype mismatch)
    if not _tracks_schema_ok(conn):
        _migrate_tracks(conn)
    return conn

# ---------------- utils ----------------

def _hash_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def _safe_title_from_path(path: str) -> str:
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    return name

# ---------------- Library API ----------------

class Library:
    def __init__(self):
        self.conn = _connect()

    def locations(self) -> Tuple[str, str]:
        return LIB_DIR, DB_PATH

    def add_file(self, src_path: str) -> Track:
        if not os.path.isfile(src_path):
            raise FileNotFoundError(src_path)

        file_hash = _hash_file(src_path)
        ext = os.path.splitext(src_path)[1] or ".bin"
        dest_name = f"{file_hash}{ext.lower()}"
        dest_path = os.path.join(LIB_DIR, dest_name)

        if not os.path.isfile(dest_path):
            shutil.copy2(src_path, dest_path)

        # zjištění délky přes pydub/ffmpeg
        from pydub import AudioSegment
        duration_ms = int(len(AudioSegment.from_file(dest_path)))

        title = _safe_title_from_path(src_path)
        self.conn.execute(
            "INSERT OR REPLACE INTO tracks(id, title, path, duration_ms) VALUES(?,?,?,?)",
            (str(file_hash), str(title), str(dest_path), int(duration_ms)),
        )
        self.conn.commit()
        return Track(file_hash, title, dest_path, duration_ms)

    def remove(self, track_id: str, delete_file: bool = True):
        row = self.conn.execute("SELECT path FROM tracks WHERE id=?", (track_id,)).fetchone()
        if row:
            path = row[0]
            if delete_file and path and os.path.isfile(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            self.conn.execute("DELETE FROM tracks WHERE id=?", (track_id,))
            self.conn.commit()

    def list_tracks(self) -> List[Track]:
        rows = self.conn.execute(
            "SELECT id, title, path, duration_ms FROM tracks ORDER BY title COLLATE NOCASE"
        ).fetchall()
        return [Track(*r) for r in rows]

    def get_track_path(self, track_id: str) -> Optional[str]:
        row = self.conn.execute("SELECT path FROM tracks WHERE id=?", (track_id,)).fetchone()
        return row[0] if row else None

    # údržba
    def verify_integrity(self) -> Tuple[int, int]:
        rows = self.conn.execute("SELECT id, path FROM tracks").fetchall()
        removed = 0
        for tid, path in rows:
            if not path or not os.path.isfile(path):
                self.conn.execute("DELETE FROM tracks WHERE id=?", (tid,))
                removed += 1
        self.conn.commit()
        return len(rows), removed

    def bulk_repair(self) -> Tuple[int, int]:
        return self.verify_integrity()

    def relink_missing_by_basename(self, search_root: str) -> Tuple[int, int]:
        if not os.path.isdir(search_root):
            raise NotADirectoryError(search_root)

        index = {}
        for root, _dirs, files in os.walk(search_root):
            for fn in files:
                name, _ = os.path.splitext(fn)
                index.setdefault(name.lower(), os.path.join(root, fn))

        rows = self.conn.execute("SELECT id, title, path FROM tracks").fetchall()
        missing = 0
        relinked = 0
        for tid, title, old_path in rows:
            if old_path and os.path.isfile(old_path):
                continue
            missing += 1
            candidates = [title]
            if old_path:
                candidates.append(os.path.splitext(os.path.basename(old_path))[0])
            found = None
            for c in candidates:
                if c and c.lower() in index:
                    found = index[c.lower()]
                    break
            if found and os.path.isfile(found):
                try:
                    tr = self.add_file(found)
                    self.remove(tid, delete_file=False)
                    relinked += 1
                except Exception:
                    pass
        self.conn.commit()
        return missing, relinked
