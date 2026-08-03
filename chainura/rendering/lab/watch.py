from __future__ import annotations
import time
import math
from pathlib import Path
from chainura.rendering.pillow.errors import RendererLabError

def validate_watch_interval(value: float | str) -> float:
    try: result=float(value)
    except (TypeError,ValueError) as exc: raise ValueError("watch interval must be a finite positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError("watch interval must be a finite positive number")
    return result


def watch(render,template:Path,layout_file:Path,output:Path,interval:float=0.5)->None:
    interval=validate_watch_interval(interval)
    watched=(template,layout_file); stamps=None
    try:
        while True:
            try: current=tuple(path.stat().st_mtime_ns if path.exists() else None for path in watched)
            except OSError as exc:
                print(f"render error: {exc}",flush=True); time.sleep(interval); continue
            if current != stamps:
                reload_layout=stamps is not None and current[1] != stamps[1]
                started=time.perf_counter()
                try:
                    render(reload_layout); print(f"rendered {output} in {(time.perf_counter()-started)*1000:.1f} ms",flush=True)
                except (RendererLabError,SyntaxError,ImportError,TypeError,ValueError,OSError) as exc:
                    print(f"render error: {exc}",flush=True)
                stamps=current
            time.sleep(interval)
    except KeyboardInterrupt: print("watch stopped",flush=True)
