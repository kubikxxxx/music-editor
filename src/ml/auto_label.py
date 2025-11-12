# src/ml/auto_label.py
from __future__ import annotations
import argparse, csv, os, re
from library.manager import Library
from ml.features import estimate_bpm

# --- regexy pro všechny standardní tance ---
RX = {
    "samba":            [r"\bsamba\b"],
    "cha cha":          [r"\bcha\b.*\bcha\b", r"\bchacha\b", r"\bcha-cha\b", r"\bcha\s*cha\b"],
    "rumba":            [r"\brumba\b", r"\brhumba\b"],
    "paso doble":       [r"\bpaso\b.*\bdoble\b", r"\bpasodoble\b", r"\bpaso\b"],
    "jive":             [r"\bjive\b"],
    # Waltz/Valčík: budeme rozlišovat až podle BPM
    "waltz_any":        [
        r"\bwaltz\b", r"\bslow\s*waltz\b",
        r"\bval[čc]ík\b", r"\bvals\b", r"\bvalse\b", r"\bwalzer\b", r"\bwiener\b", r"\bviennese\b"
    ],
    "tango":            [r"\btango\b"],
    "slowfox":          [r"\bslow\s*fox\b", r"\bslowfox\b", r"\bfoxtrot\b"],
    "quickstep":        [r"\bquick\s*step\b", r"\bquickstep\b"],
}
RXC = {k: [re.compile(p, re.I) for p in pats] for k, pats in RX.items()}

def label_from_title(title: str) -> str:
    """Vrátí kanonický label dle názvu; pro waltz/valčík vrací 'waltz_any' (rozhodne se později podle BPM)."""
    t = title.strip()
    for lab, pats in RXC.items():
        if any(rx.search(t) for rx in pats):
            return lab
    return "unknown"

def classify_waltz_from_bpm(bpm: float, title: str) -> str:
    """
    Valčík ≈ 60 MPM (~180 BPM), Slow Waltz ≈ 30 MPM (~90 BPM).
    Použijeme hranu 135 BPM.
    """
    if bpm and bpm > 0:
        return "viennese waltz" if bpm >= 135.0 else "waltz"
    # fallback: pokud je v názvu 'viennese/wiener/valčík', vezmi 'viennese waltz', jinak 'waltz'
    return "viennese waltz" if re.search(r"(viennese|wiener|val[čc]ík)", title, re.I) else "waltz"

def main(out_path: str) -> None:
    lib = Library()
    rows = []

    for t in lib.list_tracks():
        title = (t.title or "").strip()
        path = lib.get_track_path(t.id)
        if not path or not os.path.isfile(path):
            continue

        base = label_from_title(title)

        if base == "waltz_any":
            bpm = estimate_bpm(path)  # jen tady měříme BPM
            final_label = classify_waltz_from_bpm(bpm, title)
            bpm_out = round(float(bpm), 1) if bpm else ""
        else:
            final_label = base if base != "unknown" else ""
            bpm_out = ""  # u ostatních BPM nepočítáme (kvůli rychlosti)

        rows.append([t.id, title, os.path.basename(path), bpm_out, final_label])

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["track_id", "title", "file", "bpm", "label"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows → {out_path}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    main(args.out)
