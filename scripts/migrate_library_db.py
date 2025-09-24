import os
import shutil
import sqlite3
import sys
import time
from typing import Optional, Tuple

# --- umístění podle nové verze ---
APP_DIR_NEW = os.path.join(os.path.expanduser("~"), "AppData", "Local", "PracticeMaster")
LIB_DIR_NEW = os.path.join(APP_DIR_NEW, "library")
DB_PATH_NEW = os.path.join(APP_DIR_NEW, "library.sqlite3")

# --- staré umístění (legacy) ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PMDATA_DIR_OLD = os.path.join(PROJECT_ROOT, ".pmdata")
LIB_DIR_OLD = os.path.join(PMDATA_DIR_OLD, "library")
DB_PATH_OLD = os.path.join(PMDATA_DIR_OLD, "library.sqlite3")


def ensure_dirs(path: str):
    os.makedirs(path, exist_ok=True)


def backup_file(path: str) -> Optional[str]:
    if not os.path.isfile(path):
        return None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = f"{path}.{ts}.bak"
    shutil.copy2(path, bak)
    return bak


def table_info(conn: sqlite3.Connection, table: str) -> list[tuple]:
    cur = conn.execute(f"PRAGMA table_info({table});")
    return cur.fetchall()


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    info = table_info(conn, table)
    return any(col[1].lower() == column.lower() for col in info)


def create_table_if_missing(conn: sqlite3.Connection):
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


def migrate_add_path_column(conn: sqlite3.Connection) -> bool:
    """Vrátí True, pokud se sloupec přidal teď; False, pokud už tam byl."""
    create_table_if_missing(conn)
    if not column_exists(conn, "tracks", "path"):
        conn.execute("ALTER TABLE tracks ADD COLUMN path TEXT;")
        conn.commit()
        return True
    return False


def choose_db() -> Tuple[str, str, str]:
    """
    Vybere, kterou DB migrovat a kam:
    - pokud zadáš cestu jako 1. argument, použije ji (v místě)
    - jinak když existuje nová DB, použijeme ji
    - jinak když existuje stará DB, použijeme ji a pokusíme se ji přesunout do nového umístění
    Vrací (db_path, lib_dir, target_root)
    """
    # explicitní argument
    if len(sys.argv) > 1:
        db = os.path.abspath(sys.argv[1])
        lib = os.path.join(os.path.dirname(db), "library")
        return db, lib, os.path.dirname(db)

    # nová lokace
    if os.path.isfile(DB_PATH_NEW):
        return DB_PATH_NEW, LIB_DIR_NEW, APP_DIR_NEW

    # stará lokace
    if os.path.isfile(DB_PATH_OLD):
        return DB_PATH_OLD, LIB_DIR_OLD, PMDATA_DIR_OLD

    # nic nenalezeno → připrav novou cestu
    return DB_PATH_NEW, LIB_DIR_NEW, APP_DIR_NEW


def maybe_move_to_new_location(db_path: str, lib_dir: str) -> Tuple[str, str]:
    """
    Když je DB v legacy .pmdata a v novém místě nic není, přesune:
    - DB → %LOCALAPPDATA%\PracticeMaster\library.sqlite3
    - složku library → %LOCALAPPDATA%\PracticeMaster\library
    Vrací (new_db_path, new_lib_dir).
    """
    legacy = (os.path.abspath(db_path) == os.path.abspath(DB_PATH_OLD))
    new_exists = os.path.isfile(DB_PATH_NEW) or os.path.isdir(LIB_DIR_NEW)

    if legacy and not new_exists:
        print("[i] Moving legacy data to %LOCALAPPDATA%\\PracticeMaster …")
        ensure_dirs(APP_DIR_NEW)
        if os.path.isdir(lib_dir):
            shutil.move(lib_dir, LIB_DIR_NEW)
        if os.path.isfile(db_path):
            shutil.move(db_path, DB_PATH_NEW)
        return DB_PATH_NEW, LIB_DIR_NEW

    return db_path, lib_dir


def main():
    db_path, lib_dir, root = choose_db()

    print(f"[i] Selected DB: {db_path}")
    print(f"[i] Library dir : {lib_dir}")

    # když je to legacy a v novém místě nic není, přesunout
    db_path, lib_dir = maybe_move_to_new_location(db_path, lib_dir)

    # vytvořit cílové adresáře
    ensure_dirs(os.path.dirname(db_path))
    ensure_dirs(lib_dir)

    # když DB neexistuje → založit se správným schématem
    newly_created = False
    if not os.path.isfile(db_path):
        print("[i] DB not found → creating new DB")
        conn = sqlite3.connect(db_path)
        create_table_if_missing(conn)
        conn.close()
        newly_created = True

    # záloha
    if not newly_created:
        bak = backup_file(db_path)
        if bak:
            print(f"[i] Backup created: {bak}")

    # migrace
    conn = sqlite3.connect(db_path)
    try:
        create_table_if_missing(conn)
        added = migrate_add_path_column(conn)
        if added:
            print("[i] Column 'path' added.")
        else:
            print("[i] Column 'path' already present.")

        # volitelně: normalizace prázdných cest na NULL
        conn.execute("UPDATE tracks SET path = NULL WHERE path = ''")
        conn.commit()

        # info výpis
        cur = conn.execute("SELECT COUNT(*) FROM tracks")
        total = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM tracks WHERE path IS NULL OR path = ''")
        without_path = cur.fetchone()[0]
        print(f"[i] Tracks total: {total}, without path: {without_path}")

        print("\nDone. Teď můžeš v aplikaci použít:\n"
              "  • Hromadně opravit (odstraní chybějící soubory z DB)\n"
              "  • Relinkovat z adresáře… (doplní path podle názvu souboru)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
