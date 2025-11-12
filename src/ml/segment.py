# src/ml/segment.py
from __future__ import annotations
import argparse, csv, os, sys
from typing import List, Optional, Tuple
from dataclasses import dataclass

# pydub → vyžaduje FFmpeg
from pydub import AudioSegment

@dataclass
class Row:
    track_id: str
    title: str
    file: str
    bpm: Optional[float]
    label: str

def read_labels_csv(path: str) -> List[Row]:
    rows: List[Row] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        r = csv.DictReader(f)
        for i, d in enumerate(r, start=1):
            track_id = (d.get("track_id") or "").strip()
            title    = (d.get("title") or "").strip()
            file_    = (d.get("file") or "").strip()
            label    = (d.get("label") or "").strip()
            bpm_txt  = (d.get("bpm") or "").strip()
            bpm = None
            try:
                if bpm_txt:
                    bpm = float(bpm_txt.replace(",", "."))
            except Exception:
                bpm = None
            if not label:
                # bez labelu to do segmentového datasetu nechceme
                continue
            rows.append(Row(track_id=track_id, title=title, file=file_, bpm=bpm, label=label))
    return rows

def ensure_dir(p: str):
    os.makedirs(p, exist_ok=True)

def find_audio_file(row: Row, search_dirs: List[str]) -> Optional[str]:
    """
    1) pokud 'file' je absolutní/relativní cesta a existuje → vezmi ji
    2) jinak hledej basename ve search_dirs (vč. podsložek)
    """
    cand = row.file
    if cand and os.path.isfile(cand):
        return os.path.abspath(cand)

    base = os.path.basename(cand) if cand else ""
    names_to_try = [base]
    # fallback: track_id s libovolnou příponou
    if row.track_id:
        names_to_try.append(row.track_id)

    # rychlý průchod: zkus přímé joiny bez rekurze
    for d in search_dirs:
        for nm in names_to_try:
            if not nm:
                continue
            p = os.path.join(d, nm)
            if os.path.isfile(p):
                return os.path.abspath(p)

    # rekurzivní hledání
    def _walk_find(name_wo_ext: str) -> Optional[str]:
        for d in search_dirs:
            for root, _dirs, files in os.walk(d):
                for fn in files:
                    if fn == base:  # přesná shoda
                        return os.path.abspath(os.path.join(root, fn))
                    stem, ext = os.path.splitext(fn)
                    if name_wo_ext and stem == name_wo_ext:
                        return os.path.abspath(os.path.join(root, fn))
        return None

    # nejdřív přesný soubor, pak stejné jméno bez přípony
    p = _walk_find("")
    if p:
        return p
    if row.track_id:
        p = _walk_find(row.track_id)
        if p:
            return p

    return None

def slice_track_to_segments(
    src_path: str,
    out_dir_for_label: str,
    seg_ms: int,
    stride_ms: int,
    fmt: str,
    mono: bool,
    target_sr: Optional[int],
    keep_last_partial: bool,
    base_name: str
) -> List[Tuple[str, int, int]]:
    """
    Vrátí list (out_path, start_ms, dur_ms) pro vytvořené segmenty.
    """
    try:
        audio = AudioSegment.from_file(src_path)
    except Exception as e:
        print(f"[WARN] Nelze načíst: {src_path} ({e})")
        return []

    if mono:
        audio = audio.set_channels(1)
    if target_sr:
        audio = audio.set_frame_rate(int(target_sr))

    total = len(audio)
    results: List[Tuple[str, int, int]] = []
    t = 0
    ensure_dir(out_dir_for_label)

    while t + seg_ms <= total:
        seg = audio[t:t + seg_ms]
        out_path = os.path.join(out_dir_for_label, f"{base_name}__s{t}ms.{fmt}")
        seg.export(out_path, format=fmt)
        results.append((out_path, t, seg_ms))
        t += stride_ms

    # poslední “nedojetý” kus
    if keep_last_partial and t < total:
        seg = audio[t:total]
        dur = len(seg)
        if dur >= int(0.4 * seg_ms):  # aspoň 40 % segmentu, ať to není moc krátké
            out_path = os.path.join(out_dir_for_label, f"{base_name}__s{t}ms.{fmt}")
            seg.export(out_path, format=fmt)
            results.append((out_path, t, dur))

    return results

def main():
    ap = argparse.ArgumentParser(description="Rozřeže dataset na 20s segmenty a vytvoří nové CSV.")
    ap.add_argument("--labels", required=True, help="Vstupní CSV (např. src/data/labels_auto.csv)")
    ap.add_argument("--out-dir", required=True, help="Kořenový výstup (např. src/data/segments)")
    ap.add_argument("--out-csv", required=True, help="Cesta k výstupnímu CSV (např. src/data/labels_segments.csv)")
    ap.add_argument("--segment-sec", type=int, default=20, help="Délka segmentu v sekundách (default 20)")
    ap.add_argument("--stride-sec", type=int, default=20, help="Krok v sekundách (default 20 = bez překryvu)")
    ap.add_argument("--search-dirs", nargs="*", default=[], help="Kde hledat zdrojové audio (složky)")
    ap.add_argument("--format", choices=["wav","mp3","flac"], default="wav", help="Formát výstupních segmentů (default wav)")
    ap.add_argument("--mono", action="store_true", help="Převést na mono před uložením")
    ap.add_argument("--sr", type=int, default=None, help="Target sample-rate (např. 22050 nebo 44100). Pokud nezadáš, ponechá se původní.")
    ap.add_argument("--keep-last-partial", action="store_true", help="Přidat i poslední kratší segment (<délka segmentu)")
    args = ap.parse_args()

    seg_ms = int(args.segment_sec * 1000)
    stride_ms = int(args.stride_sec * 1000)

    rows = read_labels_csv(args.labels)
    if not rows:
        print("[ERROR] Vstupní CSV neobsahuje žádné použitelné řádky (label/file).")
        sys.exit(2)

    ensure_dir(args.out_dir)
    out_csv_dir = os.path.dirname(os.path.abspath(args.out_csv))
    if out_csv_dir:
        ensure_dir(out_csv_dir)

    written_segments = 0
    missing = 0

    with open(args.out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        # držíme stejný “feeling” hlavičky + doplňujeme segment metadata
        w.writerow(["seg_id","parent_track_id","title","file","start_ms","duration_ms","bpm","label"])

        for i, row in enumerate(rows, start=1):
            src = find_audio_file(row, args.search_dirs)
            if not src:
                missing += 1
                print(f"[WARN] Nenalezen soubor pro řádek {i}: track_id={row.track_id} file='{row.file}'")
                continue

            # out cesta pro daný label
            out_dir_for_label = os.path.join(args.out_dir, row.label)
            base_name = row.track_id or os.path.splitext(os.path.basename(src))[0]

            segs = slice_track_to_segments(
                src_path=src,
                out_dir_for_label=out_dir_for_label,
                seg_ms=seg_ms,
                stride_ms=stride_ms,
                fmt=args.format,
                mono=bool(args.mono),
                target_sr=args.sr,
                keep_last_partial=bool(args.keep_last_partial),
                base_name=base_name
            )

            for out_path, start_ms, dur_ms in segs:
                seg_id = f"{base_name}__{start_ms}ms"
                w.writerow([
                    seg_id,
                    row.track_id,
                    row.title,
                    os.path.abspath(out_path),
                    start_ms,
                    dur_ms,
                    (row.bpm if row.bpm is not None else ""),
                    row.label
                ])
                written_segments += 1

    print(f"[DONE] Zapsáno segmentů: {written_segments}")
    if missing:
        print(f"[WARN] Řádků bez nalezeného souboru: {missing}")

if __name__ == "__main__":
    main()
