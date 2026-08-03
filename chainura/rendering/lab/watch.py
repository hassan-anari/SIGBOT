from __future__ import annotations
import time
from pathlib import Path
from chainura.rendering.pillow.errors import RendererLabError

def watch(render,template:Path,layout_file:Path,output:Path,interval:float=0.5)->None:
    watched=(template,layout_file); stamps=None
    try:
        while True:
            current=tuple(path.stat().st_mtime_ns if path.exists() else None for path in watched)
            if current != stamps:
                started=time.perf_counter()
                try:
                    render(); print(f"rendered {output} in {(time.perf_counter()-started)*1000:.1f} ms",flush=True)
                except RendererLabError as exc: print(f"render error: {exc}",flush=True)
                stamps=current
            time.sleep(interval)
    except KeyboardInterrupt: print("watch stopped",flush=True)
