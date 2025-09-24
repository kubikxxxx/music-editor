import os
import hashlib
import math
import subprocess
from typing import List, Tuple, Optional

from pydub import AudioSegment

# cache do projektu
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PMDATA_DIR = os.path.join(PROJECT_ROOT, ".pmdata")
CACHE_DIR = os.path.join(PMDATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


def _hash_key(path: str, tempo: float, regions: List[Tuple[int, int, float]]) -> str:
    h = hashlib.sha1()
    h.update(os.path.abspath(path).encode("utf-8"))
    h.update(f"|tempo={tempo:.6f}".encode("utf-8"))
    for a, b, g in regions:
        h.update(f"|{a}-{b}@{g:.2f}".encode("utf-8"))
    # zahrň i mtime originálu, ať se cache invaliduje při změně souboru
    try:
        h.update(str(os.path.getmtime(path)).encode("utf-8"))
    except Exception:
        pass
    return h.hexdigest()


def _ffmpeg_atempo_chain(tempo: float) -> str:
    """
    Vrátí řetěz atempo filtrů pro libovolný faktor (>0).
    FFmpeg 'atempo' umí 0.5..2.0; vyšší/nižší se dělí na více kroků.
    """
    if tempo <= 0:
        tempo = 1.0
    parts = []
    remaining = tempo
    # rozlož na kroky v rozsahu 0.5..2.0
    while remaining < 0.5:
        parts.append(0.5)
        remaining /= 0.5
    while remaining > 2.0:
        parts.append(2.0)
        remaining /= 2.0
    parts.append(remaining)
    return ",".join([f"atempo={p:.6f}" for p in parts])


def render_variant(original_path: str,
                   tempo_factor: float = 1.0,
                   gain_regions: Optional[List[Tuple[int, int, float]]] = None) -> str:
    """
    Vytvoří do cache novou audio variantu:
      - volitelně aplikuje gain (dB) na zadané úseky [(a_ms, b_ms, gain_db), ...] – nedestruktivně,
      - volitelně změní tempo bez změny výšky (FFmpeg atempo),
    a vrátí cestu k WAV souboru v .pmdata/cache.

    Délka stopy se u gain nemění; u tempa se změní podle faktoru.
    """
    gain_regions = gain_regions or []
    key = _hash_key(original_path, tempo_factor, gain_regions)
    out_wav = os.path.join(CACHE_DIR, f"{key}.wav")
    if os.path.isfile(out_wav):
        return out_wav

    # 1) načti originál přes pydub
    seg = AudioSegment.from_file(original_path)

    # 2) aplikuj gain na vybrané úseky (non-destructive)
    if gain_regions:
        # zajisti pořadí a validitu
        fixed: List[Tuple[int, int, float]] = []
        for a, b, g in gain_regions:
            a = max(0, int(a)); b = max(a, int(b))
            if b > a:
                fixed.append((a, b, float(g)))
        fixed.sort(key=lambda t: t[0])

        # postupně seskládej
        out = AudioSegment.silent(duration=0, frame_rate=seg.frame_rate)
        cur = 0
        for a, b, g in fixed:
            if cur < a:
                out += seg[cur:a]
            chunk = seg[a:b] + g  # apply gain in dB
            out += chunk
            cur = b
        if cur < len(seg):
            out += seg[cur:]
        seg = out

    # 3) export do dočasného WAV
    tmp0 = os.path.join(CACHE_DIR, f"{key}_pre.wav")
    seg.export(tmp0, format="wav")

    # 4) případně změna tempa (FFmpeg atempo)
    if abs(tempo_factor - 1.0) < 1e-9:
        os.replace(tmp0, out_wav)
        return out_wav

    atempo = _ffmpeg_atempo_chain(tempo_factor)
    cmd = [
        "ffmpeg", "-y",
        "-i", tmp0,
        "-filter:a", atempo,
        "-vn",
        out_wav
    ]
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        try:
            os.remove(tmp0)
        except Exception:
            pass

    return out_wav


# zpětná kompatibilita s dřívějším kódem
def render_tempo_variant(original_path: str, tempo_factor: float = 1.0) -> str:
    return render_variant(original_path, tempo_factor=tempo_factor, gain_regions=[])
