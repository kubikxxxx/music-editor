# src/ml/labels_index.py
from __future__ import annotations
import csv, os, random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

CANON = {
    "samba": "samba",
    "cha cha": "cha cha",
    "rumba": "rumba",
    "paso doble": "paso doble",
    "jive": "jive",
    "waltz": "waltz",                     # slow waltz
    "viennese waltz": "viennese waltz",
    "tango": "tango",
    "slowfox": "slowfox",
    "quickstep": "quickstep",
}

@dataclass
class LabelsIndex:
    id_to_label: Dict[str, str] = field(default_factory=dict)
    label_to_ids: Dict[str, List[str]] = field(default_factory=dict)

    @classmethod
    def try_load(cls, csv_path: str, library=None) -> Optional["LabelsIndex"]:
        if not csv_path or not os.path.isfile(csv_path):
            return None
        id_to_label: Dict[str, str] = {}
        label_to_ids: Dict[str, List[str]] = {v: [] for v in CANON.values()}

        # (volitelné) načtení override
        override_path = os.path.join(os.path.dirname(csv_path), "labels_override.csv")
        overrides: Dict[str, str] = {}
        if os.path.isfile(override_path):
            try:
                with open(override_path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        tid = (row.get("track_id") or "").strip()
                        lab = (row.get("label") or "").strip().lower()
                        if tid and lab in CANON:
                            overrides[tid] = CANON[lab]
            except Exception:
                pass

        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                tid = (row.get("track_id") or "").strip()
                lab = (row.get("label") or "").strip().lower()
                if not tid or not lab:
                    continue
                lab = CANON.get(lab)
                if not lab:
                    continue

                # pokud je v knihovně smazaný soubor, přeskoč
                if library:
                    try:
                        p = library.get_track_path(tid)
                        if not p or not os.path.isfile(p):
                            continue
                    except Exception:
                        continue

                # aplikuj override (má přednost)
                lab = overrides.get(tid, lab)

                id_to_label[tid] = lab
                label_to_ids.setdefault(lab, []).append(tid)

        return cls(id_to_label=id_to_label, label_to_ids=label_to_ids)

    def candidates(
        self,
        label: str,
        library,
        *,
        favorites_only: bool = False,
        query_substr: str = "",
    ) -> List[Tuple[str, str, int]]:
        """
        Vrátí [(track_id, path, duration_ms), ...] pro zadaný label,
        s ohledem na UI filtr (oblíbené, query).
        """
        lab = CANON.get(label.lower())
        if not lab:
            return []

        ids = self.label_to_ids.get(lab, [])
        out: List[Tuple[str, str, int]] = []

        q = (query_substr or "").strip().lower()
        for tid in ids:
            try:
                tpath = library.get_track_path(tid)
                if not tpath or not os.path.isfile(tpath):
                    continue
                tmeta = next((x for x in library.list_tracks() if x.id == tid), None)
                if not tmeta:
                    continue
                title = (tmeta.title or "").lower()
                if q and q not in title:
                    continue
                if favorites_only:
                    # knihovna nemusí mít API → zkusíme lokální fallback přes title/flag, jinak projde vše
                    is_fav = False
                    try:
                        is_fav = bool(library.is_favorite(tid))
                    except Exception:
                        pass
                    if not is_fav:
                        continue
                dur = int(getattr(tmeta, "duration_ms", 0) or 0)
                out.append((tid, tpath, dur))
            except Exception:
                continue
        return out

    def pick_random(
        self,
        label: str,
        library,
        *,
        favorites_only: bool = False,
        query_substr: str = "",
        used_ids: Optional[Set[str]] = None,
    ) -> Optional[Tuple[str, int]]:
        cands = self.candidates(label, library, favorites_only=favorites_only, query_substr=query_substr)
        if not cands:
            return None
        if used_ids:
            fresh = [c for c in cands if c[0] not in used_ids]
            if fresh:
                cands = fresh
        tid, path, dur = random.choice(cands)
        if used_ids is not None:
            used_ids.add(tid)
        return path, dur
