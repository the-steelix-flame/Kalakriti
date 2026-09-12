# Kalakriti backend.
#
# python:3.12-slim rather than alpine: onnxruntime and opencv ship manylinux wheels,
# and on alpine (musl) pip has to build them from source, which turns a two-minute
# image build into forty and produces a larger image anyway.
FROM python:3.12-slim

# opencv and onnxruntime need these at runtime. Installed before the pip layer so a
# change to requirements.txt does not re-run apt.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

# A non-root user with uid 1000, because Hugging Face Spaces runs the container as
# that uid and nothing else. As root the image also runs anywhere, but every file
# this process writes at runtime - the Ed25519 passport key, the rembg weights, the
# media directory when object storage is not configured - would land in a directory
# uid 1000 cannot write, and the first photograph would fail on a permission error
# rather than on anything to do with the photograph.
RUN useradd -m -u 1000 kalakriti
ENV HOME=/home/kalakriti
WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
RUN chown -R kalakriti:kalakriti /app
USER kalakriti

# Whether this image runs the local vision models at all, and which matting model.
#
# Measured, whole app plus one real matte of a 3072x4080 phone photo:
#
#     u2net                 729 MB
#     u2netp                544 MB
#     u2netp, capped 1600   519 MB
#     LOCAL_VISION=off      227 MB
#
# A 512 MB container - Render free and Render Starter are both 512 MB - cannot run any
# configuration that loads a model. So a small host builds with
# `--build-arg LOCAL_VISION=off` and gives up local background removal and offline OCR.
# Everything on NVIDIA's endpoint is untouched by this: the listing copy, the pricing,
# the HSN code, the translations, and the vision model that reads the photograph are
# all remote.
#
# u2netp is the middle option and still does not fit 512 MB, so it is only worth
# choosing on a host somewhere between the two - it is looser around fringes and
# frayed selvedge, which is exactly where handloom lives.
ARG LOCAL_VISION=on
ARG REMBG_MODEL=u2net
ENV LOCAL_VISION=${LOCAL_VISION} REMBG_MODEL=${REMBG_MODEL}

# The U^2-Net weights are ~176 MB and rembg downloads them on first use. Fetching them
# at build time means the first artisan to upload a photo does not wait for the
# download, and the container works on a host with no outbound access to that CDN. Run
# as the same user that reads them later, or they land in /root and are unreadable.
#
# Skipped when LOCAL_VISION is off, because nothing would ever open them: it would be
# 176 MB of image layer and a minute of build time for a file the process refuses to
# load.
#
# The `|| echo` is a deliberate soft failure - a CDN outage should not fail a build
# when the model can still be fetched at runtime. It did once hide a real ImportError,
# so the python call is the working one and the message says what to expect.
RUN if [ "$LOCAL_VISION" = "off" ]; then \
      echo "LOCAL_VISION=off - skipping the matting model download"; \
    else \
      python -c "import os;from rembg import new_session;new_session(os.getenv('REMBG_MODEL','u2net'))" \
        || echo "warning: could not pre-fetch the matting model, it downloads on first use"; \
    fi

# 7860 is the port Hugging Face Spaces expects, and it is declared again as
# `app_port` in the README front matter. Render, Fly and Railway all inject their own
# PORT, which overrides this, so one default serves every host.
ENV PORT=7860 PYTHONUNBUFFERED=1
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# One worker, and on a 512 MB host that is not a preference. Measured, a process that
# has matted one photograph holds 729 MB; a second worker would double that for no
# throughput gain, because the job queue inside the process already gives concurrency
# where concurrency helps. With LOCAL_VISION=off a process settles near 227 MB, which
# would fit two - but the work is then almost entirely waiting on NVIDIA's endpoint,
# which one async worker handles without a second copy of the app.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
