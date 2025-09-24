import os
import tempfile
import ffmpeg




def render_tempo_variant(src_path: str, tempo_factor: float = 1.0) -> str:
    tempo_factor = max(0.5, min(2.0, float(tempo_factor)))
    base, ext = os.path.splitext(os.path.basename(src_path))
    tmp_dir = tempfile.gettempdir()
    out_path = os.path.join(tmp_dir, f"{base}.tmp.{tempo_factor:.2f}.wav")


    (
    ffmpeg
    .input(src_path)
    .audio
    .filter('atempo', tempo_factor)
    .output(out_path, format='wav', acodec='pcm_s16le', ac=2)
    .overwrite_output()
    .run(quiet=True)
    )
    return out_path