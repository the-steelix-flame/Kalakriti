"""
Entry point for a Hugging Face Space that is not a Docker Space.

Why this file exists
--------------------
A Docker Space builds `Dockerfile` and runs whatever it says. Every other Space SDK
does something simpler: it installs `requirements.txt`, installs the apt packages in
`packages.txt`, and then runs one Python file. This is that file.

So the Dockerfile is still in the repository and still correct - switching back is a
one-line change to `sdk:` in the README front matter - but nothing reads it while the
Space is configured this way.

What is different without Docker
--------------------------------
Two things, and both are handled below rather than left as surprises.

1. **The matting weights are not pre-fetched at build time.** The Dockerfile ran
   `new_session('u2net')` during the build, so the 176 MB download had already
   happened before the first artisan uploaded anything. Here the build only installs
   packages, so that download would otherwise land on whoever takes the first
   photograph after a restart - about a minute of staring at a spinner. `_prewarm()`
   below starts it on a background thread while uvicorn is coming up.

2. **Nothing sets `PORT`.** Spaces expose 7860, so that is the default.

Everything else - Postgres, object storage, the keys - comes from the Space's secrets
exactly as it would under Docker, because it all comes from the environment either
way.
"""
from __future__ import annotations

import logging
import os
import sys
import threading

# The application lives in backend/, and this file has to be at the repository root
# because that is where a Space looks for it. Put backend/ on the path before
# importing anything from it, and make it the working directory too: several modules
# resolve relative paths (MEDIA_DIR, the passport key file) against the process's cwd,
# and getting that wrong turns into "permission denied" on the first photograph rather
# than anything that mentions a directory.
ROOT = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("space")


def _prewarm() -> None:
    """
    Fetch the U^2-Net weights in the background, before anybody needs them.

    Failure here is deliberately not fatal and not even loud. rembg downloads them
    again on first use, so the only cost of this not working is that one artisan
    waits. A Space that refuses to start because a CDN was slow would be a much worse
    trade.
    """
    try:
        # Whatever imaging.py is configured to use, not a hardcoded name. Warming a
        # different model from the one that serves requests would download 176 MB to
        # no purpose and leave the real one cold.
        from imaging import LOCAL_VISION, MODEL

        # And nothing at all when the models are switched off. This call reaches
        # rembg directly rather than through imaging._rembg_session(), so it does not
        # inherit that function's guard - without this check a 512 MB host would
        # download 176 MB and load it at startup, which is the exact thing
        # LOCAL_VISION=off exists to prevent.
        if not LOCAL_VISION:
            log.info("LOCAL_VISION=off - not pre-fetching any matting model")
            return

        # Top-level, not rembg.sessions - that submodule exports the session
        # classes, not the factory, and getting it wrong fails silently
        # behind the except below.
        from rembg import new_session

        log.info("pre-fetching matting weights (%s)…", MODEL)
        new_session(MODEL)
        log.info("%s ready", MODEL)
    except Exception as exc:                                 # noqa: BLE001
        log.warning("could not pre-fetch the matting model (%s); it will download on "
                    "first use", exc)


def main() -> None:
    import uvicorn

    # Import after the path and cwd are set, or `import db` inside main.py fails.
    from main import app  # noqa: PLC0415

    threading.Thread(target=_prewarm, daemon=True).start()

    port = int(os.getenv("PORT", "7860"))
    log.info("serving on 0.0.0.0:%d", port)

    # One worker, for the same reason the Dockerfile gives: the vision models hold
    # roughly 600 MB of ONNX sessions per process, so a second worker doubles memory
    # for no throughput gain. The job queue inside the process provides concurrency
    # where it actually helps.
    uvicorn.run(app, host="0.0.0.0", port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
