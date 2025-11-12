# src/ml/infer.py
from __future__ import annotations
import os, sys, joblib, numpy as np
from pathlib import Path
from typing import Tuple, Dict, Any
from ml.features import extract_features

# Musí souhlasit s tréninkem (nebo se načtou z uloženého modelu)
CLASSES = [
     "cha cha", "jive","paso doble", "quickstep", "rumba",  "samba", "slowfox", "tango", "viennese waltz", "waltz"
]

class DanceAI:
    """
    On-the-fly klasifikace tanečního stylu:
    - Načte pipeline (např. sklearn) z joblib
    - Při predict() extrahuje featury a vrátí (label, confidence, aux)
    - Žádné ukládání do CSV, jen krátká RAM cache v rámci běhu aplikace
    """
    def __init__(self, model_path: str | None = None, *, min_conf: float = 0.60, max_sec: float = 45.0):
        self.model_path = model_path or self._default_model_path()
        self.min_conf = float(min_conf)
        self.max_sec = float(max_sec)
        self._pipe = None
        self._classes = None
        self._cache: Dict[str, Dict[str, Any]] = {}  # path -> {sig, label, conf, aux}

    def _default_model_path(self) -> str:
        # PyInstaller bundle?
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            p = Path(sys._MEIPASS) / "ml" / "models" / "dance_cls.joblib"  # type: ignore[attr-defined]
            if p.exists():
                return str(p)
        # Dev (spuštění ze zdrojáků)
        here = Path(__file__).resolve().parent
        return str(here / "models" / "dance_cls.joblib")


    def predict_proba_all(self, path: str) -> Tuple[Dict[str, float], Dict[str, Any]]:
        """
        Vrátí (probs, aux), kde probs je dict {label: probability 0..1} pro všechny třídy.
        Aplikuje stejnou heuristiku na waltz/viennese *jen pro primární label* ne,
        zde vracíme syrové proby z modelu (užitečné pro "co všechno by to mohlo být").
        """
        self._load()

        feats, aux = extract_features(path, max_duration_s=self.max_sec, return_aux=True)
        if feats is None:
            raise RuntimeError("Feature extraction failed")

        proba = self._pipe.predict_proba(feats.reshape(1, -1))[0]
        classes = list(self._classes or CLASSES)
        if len(proba) != len(classes):
            # ošetření, kdy se tréninkové třídy liší od CLASSES
            classes = classes[:len(proba)]
        probs = {cls: float(p) for cls, p in zip(classes, proba)}
        return probs, aux

    def _load(self):
        if self._pipe is None:
            data = joblib.load(self.model_path)
            # očekáváme dict {"pipe":..., "classes":[...]} – ale umíme i čistý pipeline
            if isinstance(data, dict) and "pipe" in data:
                self._pipe = data["pipe"]
                self._classes = data.get("classes", CLASSES)
            else:
                self._pipe = data
                self._classes = CLASSES

    def _sig(self, path: str) -> tuple:
        try:
            st = os.stat(path)
            return (path, int(st.st_mtime), int(st.st_size))
        except Exception:
            return (path, 0, 0)

    def predict(self, path: str) -> Tuple[str, float, Dict[str, Any]]:
        """
        Vrátí (label, confidence, aux), kde aux obsahuje např. {"tempo_med": ...}
        Může vyhodit RuntimeError, pokud se nepodaří extrahovat featury.
        """
        self._load()
        sg = self._sig(path)
        ent = self._cache.get(path)
        if ent and ent.get("sig") == sg:
            return ent["label"], ent["conf"], ent["aux"]

        feats, aux = extract_features(path, max_duration_s=self.max_sec, return_aux=True)
        if feats is None:
            raise RuntimeError("Feature extraction failed")

        proba = self._pipe.predict_proba(feats.reshape(1, -1))[0]
        idx = int(np.argmax(proba))
        label = self._classes[idx]
        conf = float(proba[idx])

        # Heuristika pro waltz vs. viennese waltz – podle tempa
        t = float(aux.get("tempo_med", 0.0))
        if label in ("waltz", "viennese waltz"):
            if t >= 55.0:
                label = "viennese waltz"
            elif t <= 35.0:
                label = "waltz"

        self._cache[path] = {"sig": sg, "label": label, "conf": conf, "aux": aux}
        return label, conf, aux

    @staticmethod
    def labels_equal(target: str, predicted: str) -> bool:
        return target.strip().lower() == predicted.strip().lower()
