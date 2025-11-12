# src/ml/features.py
from __future__ import annotations
import numpy as np
import librosa
try:
    from librosa.feature.rhythm import tempo as lf_tempo  # librosa >= 0.10
except Exception:
    from librosa.beat import tempo as lf_tempo

def extract_features(path: str, *, sr: int = 22050, max_duration_s: float = 60.0,
                     return_aux: bool = False):
    """
    Vrátí:
      - pokud return_aux=False: np.ndarray tvaru (n_features,)
      - pokud return_aux=True:  (np.ndarray, {"tempo_med": float, "tempo_std": float})
    Feature set = [tempo_med, tempo_std] + log-mel (mean/std), chroma (mean/std),
                  spectral centroid (mean/std), rolloff (mean/std), ZCR (mean/std).
    Bez výjimek – na fail vrací None (a prázdné aux).
    """
    try:
        y, _sr = librosa.load(path, sr=sr, mono=True, duration=max_duration_s)
        if y is None or len(y) == 0:
            return (None, {}) if return_aux else None

        # Tempo (pro BPM pravidla, např. waltz vs. viennese)

        tempi = lf_tempo(y=y, sr=_sr, aggregate=None)
        tempo_med = float(np.median(tempi)) if tempi is not None and len(tempi) else 0.0
        tempo_std = float(np.std(tempi)) if tempi is not None and len(tempi) else 0.0
        feats = [tempo_med, tempo_std]

        # Log-mel
        S = librosa.feature.melspectrogram(y=y, sr=_sr, n_mels=128, fmin=30, fmax=_sr//2)
        S_db = librosa.power_to_db(S + 1e-12)
        feats += list(S_db.mean(axis=1))
        feats += list(S_db.std(axis=1))

        # Chroma
        chroma = librosa.feature.chroma_cqt(y=y, sr=_sr)
        feats += list(chroma.mean(axis=1))
        feats += list(chroma.std(axis=1))

        # Spectral + ZCR
        cent = librosa.feature.spectral_centroid(y=y, sr=_sr)
        roll = librosa.feature.spectral_rolloff(y=y, sr=_sr)
        zcr = librosa.feature.zero_crossing_rate(y)
        feats += [float(cent.mean()), float(cent.std())]
        feats += [float(roll.mean()), float(roll.std())]
        feats += [float(zcr.mean()), float(zcr.std())]

        vec = np.asarray(feats, dtype=np.float32)
        if return_aux:
            return vec, {"tempo_med": tempo_med, "tempo_std": tempo_std}
        return vec
    except Exception:
        return (None, {}) if return_aux else None
