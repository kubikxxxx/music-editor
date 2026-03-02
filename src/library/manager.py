# library/manager.py
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

os.makedirs(PMDATA_DIR, exist_ok=True)
os.makedirs(LIB_DIR, exist_ok=True)


EXPECTED_SCHEMA = {
    "id": "TEXT",
    "title": "TEXT",
    "path": "TEXT",
    "rel_path": "TEXT",          # NEW: relativní cesta uvnitř knihovny (podsložky)
    "duration_ms": "INTEGER",
}


@dataclass
class Track:
    id: str
    title: str
    path: str
    duration_ms: int
    rel_path: str = ""           # NEW


# ---------------- schema helpers ----------------

def _table_info(conn: sqlite3.Connection, table: str) -> list[tuple]:
    # cid, name, type, notnull, dflt_value, pk
    return conn.execute(f"PRAGMA table_info({table});").fetchall()


def _create_tracks(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tracks(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            path TEXT,
            rel_path TEXT,
            duration_ms INTEGER NOT NULL
        );
        """
    )
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection):
    """Lehká migrace: přidá chybějící sloupce (path/rel_path)."""
    info = _table_info(conn, "tracks")
    cols = {row[1].lower() for row in info}
    for col, typ in (("path", "TEXT"), ("rel_path", "TEXT")):
        if col not in cols:
            conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {typ};")
            conn.commit()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    _create_tracks(conn)
    _ensure_columns(conn)
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


def _sanitize_subdir(subdir: str) -> str:
    """Zamezí .., absolutním cestám apod. Vrací unix-like relativní podsložku."""
    s = (subdir or "").strip().replace("\\", "/")
    s = s.lstrip("/")
    # rozpadni a vyhoď nebezpečné segmenty
    parts = []
    for p in s.split("/"):
        p = p.strip()
        if not p or p in (".", ".."):
            continue
        # jemné čištění (můžeš přitvrdit, pokud chceš)
        p = p.replace(":", "").replace("|", "").replace("\0", "")
        parts.append(p)
    return "/".join(parts)


def _join_rel(*parts: str) -> str:
    return "/".join([p for p in ("/".join(parts)).replace("\\", "/").split("/") if p])


# ---------------- Library API ----------------

class Library:
    def __init__(self):
        self.conn = _connect()

    def locations(self) -> Tuple[str, str]:
        return LIB_DIR, DB_PATH

    # ------- listing --------

    def list_tracks(self) -> List[Track]:
        rows = self.conn.execute(
            "SELECT id, title, path, duration_ms, COALESCE(rel_path,'') FROM tracks "
            "ORDER BY title COLLATE NOCASE"
        ).fetchall()
        return [Track(id=r[0], title=r[1], path=r[2] or "", duration_ms=int(r[3] or 0), rel_path=r[4] or "") for r in rows]

    def list_tracks_raw(self) -> List[tuple]:
        """Pro UI strom: (id, title, path, rel_path, duration_ms)"""
        return self.conn.execute(
            "SELECT id, title, path, COALESCE(rel_path,''), duration_ms FROM tracks "
            "ORDER BY COALESCE(rel_path,''), title COLLATE NOCASE"
        ).fetchall()

    def get_track_path(self, track_id: str) -> Optional[str]:
        row = self.conn.execute("SELECT path FROM tracks WHERE id=?", (track_id,)).fetchone()
        return row[0] if row and row[0] else None

    def get_track_rel_path(self, track_id: str) -> str:
        row = self.conn.execute("SELECT COALESCE(rel_path,'') FROM tracks WHERE id=?", (track_id,)).fetchone()
        return (row[0] or "") if row else ""

    # ------- add/remove/move --------

    def add_file(self, src_path: str, subdir: str = "") -> Track:
        """
        Importuje soubor do knihovny.
        - fyzicky uloží do .pmdata/library/<subdir>/<hash>.<ext>
        - do DB uloží path + rel_path (pro strom)
        """
        if not os.path.isfile(src_path):
            raise FileNotFoundError(src_path)

        subdir = _sanitize_subdir(subdir)
        file_hash = _hash_file(src_path)
        ext = os.path.splitext(src_path)[1] or ".bin"

        dest_dir = os.path.join(LIB_DIR, subdir) if subdir else LIB_DIR
        os.makedirs(dest_dir, exist_ok=True)

        dest_name = f"{file_hash}{ext.lower()}"
        dest_path = os.path.join(dest_dir, dest_name)
        rel_path = _join_rel(subdir, dest_name)

        if not os.path.isfile(dest_path):
            shutil.copy2(src_path, dest_path)

        # délka přes pydub/ffmpeg
        from pydub import AudioSegment
        duration_ms = int(len(AudioSegment.from_file(dest_path)))

        title = _safe_title_from_path(src_path)
        self.conn.execute(
            "INSERT OR REPLACE INTO tracks(id, title, path, rel_path, duration_ms) VALUES(?,?,?,?,?)",
            (str(file_hash), str(title), str(dest_path), str(rel_path), int(duration_ms)),
        )
        self.conn.commit()
        return Track(file_hash, title, dest_path, duration_ms, rel_path=rel_path)

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

    def move_track(self, track_id: str, new_subdir: str) -> bool:
        """
        Přesune track do jiné podsložky v knihovně:
        - fyzicky přesune soubor
        - aktualizuje path + rel_path
        """
        new_subdir = _sanitize_subdir(new_subdir)

        row = self.conn.execute(
            "SELECT path, COALESCE(rel_path,''), title FROM tracks WHERE id=?",
            (track_id,)
        ).fetchone()
        if not row:
            return False

        old_path, old_rel, title = row[0] or "", row[1] or "", row[2] or ""
        if not old_path or not os.path.isfile(old_path):
            return False

        ext = os.path.splitext(old_path)[1] or ".bin"
        dest_dir = os.path.join(LIB_DIR, new_subdir) if new_subdir else LIB_DIR
        os.makedirs(dest_dir, exist_ok=True)

        # držíme stejné jméno (hash.ext)
        dest_name = os.path.basename(old_path)
        new_path = os.path.join(dest_dir, dest_name)
        new_rel = _join_rel(new_subdir, dest_name)

        # když cílový existuje, jen přepni DB (typicky duplicitní import)
        if os.path.abspath(new_path) != os.path.abspath(old_path):
            if not os.path.isfile(new_path):
                shutil.move(old_path, new_path)
            else:
                # cílový existuje → původní můžeš smazat
                try:
                    os.remove(old_path)
                except Exception:
                    pass

        self.conn.execute(
            "UPDATE tracks SET path=?, rel_path=? WHERE id=?",
            (str(new_path), str(new_rel), str(track_id))
        )
        self.conn.commit()
        return True

    # ------- maintenance --------

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
        """
        Najde chybějící soubory podle basename (title nebo basename z rel_path/path).
        Pokud najde, znovu je importuje (do stejné složky jako měl rel_path, pokud existuje).
        """
        if not os.path.isdir(search_root):
            raise NotADirectoryError(search_root)

        index = {}
        for root, _dirs, files in os.walk(search_root):
            for fn in files:
                name, _ = os.path.splitext(fn)
                index.setdefault(name.lower(), os.path.join(root, fn))

        rows = self.conn.execute("SELECT id, title, path, COALESCE(rel_path,'') FROM tracks").fetchall()
        missing = 0
        relinked = 0
        for tid, title, old_path, rel_path in rows:
            if old_path and os.path.isfile(old_path):
                continue
            missing += 1

            # kandidáti: title, basename(rel_path), basename(old_path)
            candidates = []
            if title: candidates.append(title)
            if rel_path:
                candidates.append(os.path.splitext(os.path.basename(rel_path))[0])
            if old_path:
                candidates.append(os.path.splitext(os.path.basename(old_path))[0])

            found = None
            for c in candidates:
                if c and c.lower() in index:
                    found = index[c.lower()]
                    break

            if found and os.path.isfile(found):
                try:
                    # zachovej složku podle rel_path, pokud existuje
                    subdir = ""
                    if rel_path:
                        subdir = os.path.dirname(rel_path).replace("\\", "/")
                    new_tr = self.add_file(found, subdir=subdir)
                    # smaž starý záznam, ale nešahat na soubor (neexistuje)
                    self.remove(tid, delete_file=False)
                    relinked += 1
                except Exception:
                    pass

        self.conn.commit()
        return missing, relinked