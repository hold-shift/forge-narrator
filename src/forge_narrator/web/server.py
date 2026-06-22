"""FastAPI backend for the local console (Spec C §2, §4–7).

Two front-ends, one pipeline: this server wraps the SAME estimate/cost/cache and
generate code the CLI uses (Spec C §8) — no pipeline logic is reimplemented here.

Security (Spec C §9): bind 127.0.0.1 only; the ElevenLabs key is read server-side
by the pipeline (`get_api_key`) and is NEVER sent to the browser, logged, or placed
in any SSE event.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from ..cache import BlockCache
from ..cost import estimate_manifest
from ..manifest import Manifest, ManifestError, load_manifest

_HERE = Path(__file__).resolve().parent
_INDEX = _HERE / "static" / "index.html"
_REPO_ROOT = _HERE.parents[2]  # web → forge_narrator → src → repo root

# Only these output files are ever served from a run's out dir (no traversal).
_PREVIEW_FILES = ("document.mp3", "document.marks.json", "document.blocks.json")


@dataclass
class Run:
    run_id: str
    manifest_path: Path
    manifest: Manifest
    slug: str
    status: str = "uploaded"          # uploaded → running → done | error
    queue: queue.Queue = field(default_factory=queue.Queue)
    summary: dict | None = None
    error: dict | None = None
    thread: threading.Thread | None = None


def create_app(*, out_root: str, cache_dir: str, char_cap: int | None) -> FastAPI:
    app = FastAPI(title="forge-narrator console")
    app.state.out_root = Path(out_root)
    app.state.cache_dir = cache_dir
    app.state.char_cap = char_cap
    app.state.work_dir = Path("work")
    app.state.runs = {}

    def _run(run_id: str) -> Run:
        run = app.state.runs.get(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="unknown run_id")
        return run

    def _cache() -> BlockCache:
        # Fresh handle per request; cheap. Reads enabled (cache hits are free).
        return BlockCache(app.state.cache_dir, enabled=True)

    def _inspect_payload(run: Run) -> dict:
        est = estimate_manifest(run.manifest, _cache())
        cap = app.state.char_cap
        over_cap = cap is not None and est.uncached_chars > cap
        return {
            "run_id": run.run_id,
            "slug": run.manifest.slug,
            "voice": run.manifest.voice,
            "model": run.manifest.model,
            "block_total": est.total_blocks,
            "block_narratable": est.total_blocks,  # all manifest blocks are narratable
            "blocks_cached": est.cached_blocks,
            "blocks_to_synth": est.uncached_blocks,
            "chars_total": est.total_chars,
            "chars_to_synth": est.uncached_chars,
            "credits_est": est.credits,
            "cost_usd_est": round(est.cost_usd, 2),
            "char_cap": cap,
            "over_cap": over_cap,
        }

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_INDEX)

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)) -> JSONResponse:
        name = Path(file.filename or "manifest").name
        if not (name.endswith(".zip") or name.endswith(".json")):
            raise HTTPException(status_code=400, detail="expected a .zip or .json manifest")
        run_id = uuid.uuid4().hex
        work = app.state.work_dir / run_id
        work.mkdir(parents=True, exist_ok=True)
        dest = work / name
        dest.write_bytes(await file.read())
        try:
            manifest = load_manifest(dest)
        except ManifestError as e:
            raise HTTPException(status_code=400, detail=f"not a valid manifest: {e}")
        run = Run(run_id=run_id, manifest_path=dest, manifest=manifest, slug=manifest.slug)
        app.state.runs[run_id] = run
        return JSONResponse({"run_id": run_id, "slug": manifest.slug})

    @app.get("/api/inspect/{run_id}")
    def inspect(run_id: str) -> JSONResponse:
        return JSONResponse(_inspect_payload(_run(run_id)))

    @app.post("/api/generate/{run_id}")
    def start_generate(run_id: str) -> JSONResponse:
        run = _run(run_id)
        if run.status == "running":
            raise HTTPException(status_code=409, detail="run already in progress")
        # Server-side cost gate (Spec C §6): re-check the cap regardless of the UI.
        payload = _inspect_payload(run)
        if payload["over_cap"]:
            raise HTTPException(
                status_code=409,
                detail=f"over character cap ({payload['chars_to_synth']:,} > "
                       f"{payload['char_cap']:,}); refusing to synthesise",
            )
        run.status = "running"
        run.queue = queue.Queue()
        run.summary = None
        run.error = None
        run.thread = threading.Thread(
            target=_generate_worker, args=(app, run), daemon=True
        )
        run.thread.start()
        return JSONResponse({"started": True})

    @app.get("/api/progress/{run_id}")
    async def progress(run_id: str, request: Request) -> StreamingResponse:
        run = _run(run_id)

        async def event_stream():
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = run.queue.get_nowait()
                except queue.Empty:
                    # Run finished before/around connect with an empty queue:
                    # emit the terminal event from stored state (covers reconnect).
                    if run.status == "done" and run.summary is not None:
                        yield _sse("done", run.summary)
                        return
                    if run.status == "error" and run.error is not None:
                        yield _sse("error", run.error)
                        return
                    await asyncio.sleep(0.1)
                    continue
                kind = item.get("type")
                if kind == "progress":
                    yield _sse("progress", item["event"])
                elif kind == "done":
                    yield _sse("done", item["summary"])
                    return
                elif kind == "error":
                    yield _sse("error", item["error"])
                    return

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/preview/{slug}/")
    def preview_index(slug: str) -> HTMLResponse:
        return HTMLResponse(_player_html())

    @app.get("/preview/{slug}/{filename}")
    def preview_file(slug: str, filename: str):
        if filename in ("", "player.html", "index.html"):
            return HTMLResponse(_player_html())
        if filename not in _PREVIEW_FILES:
            raise HTTPException(status_code=404, detail="not found")
        path = app.state.out_root / slug / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"{filename} not generated yet")
        # FileResponse honours HTTP Range → <audio> seeking works (Spec C §7).
        return FileResponse(path)

    return app


def _generate_worker(app: FastAPI, run: Run) -> None:
    """Background generation; pushes progress/terminal events onto the run queue."""
    from ..pipeline import generate  # lazy: keeps import-time light

    def on_progress(event: dict) -> None:
        run.queue.put({"type": "progress", "event": event})

    cache = BlockCache(app.state.cache_dir, enabled=True)
    try:
        summary = generate(
            run.manifest, cache,
            out_root=app.state.out_root, on_progress=on_progress,
        )
        run.summary = summary
        run.status = "done"
        run.queue.put({"type": "done", "summary": summary})
    except Exception as e:  # surface cleanly; message carries no key
        block_index = getattr(e, "block_index", None)
        run.error = {"message": str(e), "block_index": block_index}
        run.status = "error"
        run.queue.put({"type": "error", "error": run.error})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


_PLAYER_CACHE: dict[str, str] = {}


def _player_html() -> str:
    """The validated POC sync checker (poc/player.html) pointed at the document.*
    files. Reused unchanged in contract (Spec C §7); only the two resource
    references are rewritten. Served via FileResponse routes so Range works."""
    if "html" not in _PLAYER_CACHE:
        src_path = _REPO_ROOT / "poc" / "player.html"
        if not src_path.is_file():
            src_path = Path("poc/player.html")  # fallback: cwd
        html = src_path.read_text(encoding="utf-8")
        html = re.sub(r'src="[^"]*\.mp3"', 'src="document.mp3"', html, count=1)
        html = re.sub(r"""fetch\((['"])[^'"]*\.marks\.json\1\)""",
                      "fetch('document.marks.json')", html, count=1)
        _PLAYER_CACHE["html"] = html
    return _PLAYER_CACHE["html"]


def run_server(*, host: str, port: int, out_root: str, cache_dir: str,
               char_cap: int | None) -> None:
    """Launch uvicorn bound to ``host`` (always 127.0.0.1 from the CLI)."""
    try:
        import uvicorn
    except ImportError as e:
        raise SystemExit(
            "the web console needs fastapi + uvicorn — install with: "
            "pip install -e '.[web]'"
        ) from e
    app = create_app(out_root=out_root, cache_dir=cache_dir, char_cap=char_cap)
    print(f"forge-narrator console → http://{host}:{port}  (Ctrl-C to stop)")
    uvicorn.run(app, host=host, port=port, log_level="info")
