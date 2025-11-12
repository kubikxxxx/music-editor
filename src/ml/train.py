# src/ml/train.py
from __future__ import annotations

import os, sys, csv, time, json, math, random, inspect, argparse
from collections import Counter, defaultdict
from typing import List, Dict, Tuple, Optional

import numpy as np

# --- Ztišit běžné warningy (žádné spamování konzole) ---
import warnings
from sklearn.exceptions import ConvergenceWarning, UndefinedMetricWarning
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=ConvergenceWarning)
warnings.filterwarnings("ignore", category=UndefinedMetricWarning)
warnings.filterwarnings("ignore", message="X does not have valid feature names", category=UserWarning)

# --- Třetí strany ---
from tqdm import tqdm

from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.pipeline import Pipeline
from sklearn.model_selection import StratifiedKFold, RandomizedSearchCV
from sklearn.metrics import accuracy_score, classification_report
from sklearn.utils.class_weight import compute_sample_weight

from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import HistGradientBoostingClassifier, ExtraTreesClassifier

from sklearn.base import BaseEstimator
import joblib

# --- Tvoje featurizace ---
from ml.features import extract_features  # používáme kompat wrapper níže


# ---------------------------
# Pomocné utilitky
# ---------------------------

def _extract_compat(path: str,
                    start_ms: Optional[int],
                    dur_ms: Optional[int]) -> np.ndarray:
    """
    Bezpečně volá extract_features. Pokud funkce nepodporuje segmenty, zavolá ji bez nich.
    Vrací 1D float32 vektor.
    """
    try:
        sig = inspect.signature(extract_features)
        kwargs = {}
        if "start_ms" in sig.parameters and start_ms is not None:
            kwargs["start_ms"] = int(start_ms)
        if "dur_ms" in sig.parameters and dur_ms is not None:
            kwargs["dur_ms"] = int(dur_ms)
        if kwargs:
            v = extract_features(path, **kwargs)
        else:
            v = extract_features(path)
    except TypeError:
        v = extract_features(path)
    arr = np.asarray(v, dtype=np.float32).ravel()
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0, copy=False)
    return arr


def index_search_dirs(search_dirs: List[str]) -> Dict[str, str]:
    """
    Projde dané složky rekurzivně a mapuje lowercase basename -> plná cesta.
    """
    filemap: Dict[str, str] = {}
    for root_dir in search_dirs:
        if not root_dir:
            continue
        root_dir = os.path.abspath(root_dir)
        if not os.path.isdir(root_dir):
            continue
        for r, _dirs, files in os.walk(root_dir):
            for f in files:
                low = f.lower()
                if low not in filemap:
                    filemap[low] = os.path.join(r, f)
    return filemap


def locate_file(name_or_path: str, filemap: Dict[str, str]) -> Optional[str]:
    """
    Zkusí najít soubor: 1) když existuje absolutně, 2) přes mapu basename,
    3) zkusí dotvořit příponu .mp3/.wav/.flac, pokud není.
    """
    if not name_or_path:
        return None
    # absolutní/relativní cesta?
    p = name_or_path
    if os.path.isabs(p) or os.path.sep in p:
        if os.path.isfile(p):
            return os.path.abspath(p)
        # fallback: basename lookup
        bn = os.path.basename(p).lower()
        return filemap.get(bn)

    # jen jméno
    bn = name_or_path.lower()
    if bn in filemap:
        return filemap[bn]

    # zkus přípony
    stem, ext = os.path.splitext(bn)
    if not ext:
        for e in (".mp3", ".wav", ".flac", ".m4a", ".ogg"):
            cand = stem + e
            if cand in filemap:
                return filemap[cand]
    return None


def read_csv_rows(path: str, limit: Optional[int]=None) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        for i, row in enumerate(rd, start=1):
            rows.append({k: (v or "").strip() for k, v in row.items()})
            if limit and len(rows) >= limit:
                break
    return rows


def parse_int(v: str) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except Exception:
        return None


# ---------------------------
# Načtení datasetu
# ---------------------------

def load_dataset(labels_csv: str,
                 search_dirs: List[str],
                 limit: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray, List[str]]:
    """
    Vrátí (X, y, classes_in_order). X shape = [N, D]
    Očekává CSV se sloupci aspoň: file, label
    Volitelně: start_ms, dur_ms  (pro segmenty)
    """
    print(f"[INFO] Načítám CSV: {labels_csv}")
    rows = read_csv_rows(labels_csv, limit=limit)
    print(f"[INFO] Řádků v CSV: {len(rows)}")

    # indexuj search dirs
    print("[INFO] Indexuji --search-dirs…")
    filemap = index_search_dirs(search_dirs)

    feats: List[np.ndarray] = []
    labels: List[str] = []

    # typické názvy sloupců
    # (podporujeme: file | path | filename), label, start_ms, dur_ms
    def pick_file_col(r):
        for k in ("file", "path", "filename"):
            if k in r and r[k]:
                return r[k]
        return ""

    # průběh
    bad = 0
    with tqdm(total=len(rows), desc="Extrahuji příznaky", unit="track") as pbar:
        for r in rows:
            file_field = pick_file_col(r)
            lab = (r.get("label") or r.get("dance") or "").strip()
            if not file_field or not lab:
                bad += 1
                pbar.update(1)
                continue

            fpath = locate_file(file_field, filemap)
            if not fpath or not os.path.isfile(fpath):
                bad += 1
                pbar.update(1)
                continue

            start_ms = parse_int(r.get("start_ms"))
            dur_ms   = parse_int(r.get("dur_ms"))

            try:
                v = _extract_compat(fpath, start_ms, dur_ms)
                if not np.any(np.isfinite(v)):
                    bad += 1
                    pbar.update(1)
                    continue
                feats.append(v)
                labels.append(lab)
            except KeyboardInterrupt:
                raise
            except Exception:
                bad += 1
            pbar.update(1)

    if bad:
        print(f"[WARN] Vynecháno záznamů: {bad}")

    if not feats:
        print("[ERROR] Žádná data k tréninku (zkontroluj --search-dirs a cesty v CSV).")
        sys.exit(2)

    # sjednoť dimenzi feature (padding/trunc)
    max_d = max(len(v) for v in feats)
    arr = np.zeros((len(feats), max_d), dtype=np.float32)
    for i, v in enumerate(feats):
        L = min(len(v), max_d)
        arr[i, :L] = v[:L]

    y = np.array(labels, dtype=object)
    classes = sorted(set(labels))
    print(f"[INFO] Dataset: {arr.shape[0]} vzorků, {arr.shape[1]} features")
    print(f"[INFO] Počty na třídu: {json.dumps(dict(Counter(labels)), ensure_ascii=False)}")
    return arr, y, classes


# ---------------------------
# Kandidáti modelů + hledání
# ---------------------------

def build_candidates(n_features: int, seed: int) -> List[Tuple[str, Pipeline, Dict[str, object]]]:
    """
    Vrací seznam (name, pipeline, param_distributions) pro RandomizedSearch.
    Vše pojmenované tak, aby finální krok byl 'clf' – kvůli sample_weight.
    """
    rng = np.random.RandomState(seed)

    # LogisticRegression (saga), škáluj + volitelná PCA
    pipe_lr = Pipeline([
        ("sc", StandardScaler(with_mean=True, with_std=True)),
        ("pca", PCA(n_components=0.98, svd_solver="full", random_state=seed)),
        ("clf", LogisticRegression(
            solver="saga", penalty="elasticnet", l1_ratio=0.3,
            max_iter=20000, n_jobs=-1, verbose=0, random_state=seed
        )),
    ])
    params_lr = {
        "pca__n_components": [0.90, 0.95, 0.98, 0.99, None],
        "clf__C": np.logspace(-3, 2, 20),
        "clf__l1_ratio": np.linspace(0.0, 1.0, 11),
        "clf__penalty": ["l2", "elasticnet"],
    }

    # HistGradientBoosting – velmi silný na tabulárních datech
    pipe_hgb = Pipeline([
        ("sc", StandardScaler(with_mean=True, with_std=True)),  # HGB zvládne i bez scaleru, ale sjednotíme
        ("pca", PCA(n_components=None, svd_solver="full", random_state=seed)),
        ("clf", HistGradientBoostingClassifier(
            max_depth=None, learning_rate=0.1, max_leaf_nodes=63,
            l2_regularization=0.0, early_stopping=False, random_state=seed
        )),
    ])
    params_hgb = {
        "pca__n_components": [None, 0.98, 0.995],
        "clf__learning_rate": np.logspace(-2.3, -0.5, 12),
        "clf__max_leaf_nodes": [31, 63, 127, 255],
        "clf__max_depth": [None, 5, 7, 9, 12, 16],
        "clf__l2_regularization": np.logspace(-4, 0, 10),
        "clf__min_samples_leaf": [5, 10, 20, 30],
    }

    # ExtraTrees – robustní stromový ansámbl
    pipe_et = Pipeline([
        ("sc", StandardScaler(with_mean=True, with_std=True)),
        ("pca", PCA(n_components=None, svd_solver="full", random_state=seed)),
        ("clf", ExtraTreesClassifier(
            n_estimators=800, random_state=seed, n_jobs=-1, bootstrap=False
        )),
    ])
    params_et = {
        "pca__n_components": [None, 0.98, 0.995],
        "clf__n_estimators": [600, 800, 1000, 1200],
        "clf__max_features": ["sqrt", "log2", 0.3, 0.5, 0.7],
        "clf__min_samples_leaf": [1, 2, 3, 4, 5, 10],
        "clf__class_weight": [None, "balanced", "balanced_subsample"],
    }

    return [
        ("LogReg(saga)", pipe_lr, params_lr),
        ("HistGB", pipe_hgb, params_hgb),
        ("ExtraTrees", pipe_et, params_et),
    ]


def pick_and_fit_best_on_fold(Xtr, ytr, Xval, yval, k_inner: int, n_iter: int,
                              seed: int) -> Tuple[str, Pipeline, float, dict]:
    """
    Pro daný fold vyzkouší několik kandidátů s RandomizedSearchCV (3-fold),
    vrátí nejlepší pipeline (již natrénovanou) a její accuracy na validační sadě.
    """
    candidates = build_candidates(Xtr.shape[1], seed)
    skf_inner = StratifiedKFold(n_splits=max(3, k_inner), shuffle=True, random_state=seed)

    best_score = -1.0
    best_est: Optional[Pipeline] = None
    best_name = ""
    best_params: dict = {}

    # sample weights pro vyvážení tříd
    sw = compute_sample_weight(class_weight="balanced", y=ytr)

    for name, pipe, search_space in candidates:
        print(f"  [SEARCH] {name}: RandomizedSearch({n_iter} iter, 3-fold)...")
        rs = RandomizedSearchCV(
            estimator=pipe,
            param_distributions=search_space,
            n_iter=n_iter,
            scoring="accuracy",
            n_jobs=-1,
            cv=skf_inner,
            random_state=seed,
            verbose=2,
            refit=True,
        )
        t0 = time.perf_counter()
        # sample_weight míří na finální krok 'clf'
        rs.fit(Xtr, ytr, **{"clf__sample_weight": sw})
        dt = time.perf_counter() - t0

        est = rs.best_estimator_
        preds = est.predict(Xval)
        acc = float(accuracy_score(yval, preds))

        print(f"    ↳ best={rs.best_score_:.4f} (CV), val_acc={acc:.4f} in {dt:.1f}s")
        print(f"    ↳ params: {rs.best_params_}")

        if acc > best_score:
            best_score = acc
            best_est = est
            best_name = name
            best_params = rs.best_params_

    assert best_est is not None
    return best_name, best_est, best_score, best_params


# ---------------------------
# Hlavní trénovací procedura
# ---------------------------

def train_and_eval(X: np.ndarray, y: np.ndarray, kfold: int,
                   seed: int, n_iter: int) -> Pipeline:
    """
    Vnější CV přes kfold; v každém foldu proběhne RandomizedSearch (3-fold) a
    zvolí se nejlepší kandidát. Po CV se nejčastěji vítězná rodina modelu
    dofituje na plných datech (opět s RandomizedSearch) a uloží.
    """
    skf = StratifiedKFold(n_splits=max(2, kfold), shuffle=True, random_state=seed)

    fold_acc: List[float] = []
    family_win_counter = Counter()
    last_report = None
    best_fold_models: List[Tuple[str, Pipeline, float, dict]] = []

    print(f"\n=== CROSS-VALIDATION ({kfold} folds) ===")
    for fi, (tr_idx, va_idx) in enumerate(skf.split(X, y), start=1):
        Xtr, Xval = X[tr_idx], X[va_idx]
        ytr, yval = y[tr_idx], y[va_idx]

        print(f"[FOLD {fi}] trénuji… (N={len(Xtr)} / val={len(Xval)})")
        t0 = time.perf_counter()
        name, est, acc, params = pick_and_fit_best_on_fold(
            Xtr, ytr, Xval, yval, k_inner=3, n_iter=n_iter, seed=seed + fi
        )
        dt = time.perf_counter() - t0
        print(f"[FOLD {fi}] best={name}  acc={acc:.3f}  ({dt:.1f}s)")

        preds = est.predict(Xval)
        last_report = classification_report(yval, preds, digits=3, zero_division=0)
        print(last_report)

        fold_acc.append(acc)
        family_win_counter[name] += 1
        best_fold_models.append((name, est, acc, params))

    mu = float(np.mean(fold_acc))
    sd = float(np.std(fold_acc))
    print("\n=== CV SUMMARY ===")
    print(f"ACC mean: {mu:.3f} ± {sd:.3f}")
    print("Family wins:", dict(family_win_counter))
    if last_report:
        print("\nLast fold classification report:\n" + last_report)

    # Vyber rodinu, která nejčastěji vyhrála
    if family_win_counter:
        winner_family = max(family_win_counter.items(), key=lambda kv: kv[1])[0]
    else:
        winner_family = best_fold_models[-1][0]

    # Finální refit na všech datech – znovu RandomizedSearch pro vítěznou rodinu
    print("\n[INFO] Fit na plných datech… (winner family =", winner_family, ")")
    # najdi odpovídající candidate template
    cand_map = {n: (p, s) for n, p, s in build_candidates(X.shape[1], seed)}
    base_pipe, search_space = cand_map[winner_family]

    sw_full = compute_sample_weight(class_weight="balanced", y=y)
    rs_full = RandomizedSearchCV(
        estimator=base_pipe,
        param_distributions=search_space,
        n_iter=max(40, n_iter),  # na plných datech klidně více iterací
        scoring="accuracy",
        n_jobs=-1,
        cv=StratifiedKFold(n_splits=3, shuffle=True, random_state=seed),
        random_state=seed,
        verbose=2,
        refit=True,
    )
    rs_full.fit(X, y, **{"clf__sample_weight": sw_full})
    print("[INFO] Best full-data params:", rs_full.best_params_)
    print("[INFO] Best full-data CV score:", rs_full.best_score_)
    return rs_full.best_estimator_


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True, help="CSV s trénovacími daty (file,label[,start_ms,dur_ms])")
    ap.add_argument("--out", required=True, help="Cesta k uloženému .joblib")
    ap.add_argument("--kfold", type=int, default=8, help="Počet foldů ve vnějším CV (default 8)")
    ap.add_argument("--search-dirs", nargs="*", default=[os.path.join(".pmdata", "library"), "."],
                    help="Složky, kde se hledají audio soubory")
    ap.add_argument("--limit", type=int, default=None, help="Omez počet řádků z CSV (debug)")
    ap.add_argument("--n-iter", type=int, default=25, help="Počet iterací RandomizedSearch pro každý kandidát ve foldu")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)

    X, y, classes = load_dataset(args.labels, args.search_dirs, limit=args.limit)

    t0 = time.perf_counter()
    best_pipe = train_and_eval(X, y, kfold=max(2, int(args.kfold)),
                               seed=int(args.seed), n_iter=int(args.n_iter))
    dt = time.perf_counter() - t0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    payload = {
        "pipeline": best_pipe,
        "classes": list(sorted(set(y.tolist()))),
        "meta": {
            "kfold": int(args.kfold),
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": dt,
        },
    }
    joblib.dump(payload, args.out)
    print(f"\n[SAVED] {args.out}  (třídy: {payload['classes']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
