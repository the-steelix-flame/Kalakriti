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

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# The U^2-Net weights are ~176 MB and rembg downloads them on first use. Fetching
# them at build time means the first artisan to upload a photo does not wait for a
# download, and the container works on a host with no outbound access to that CDN.
RUN python -c "from rembg.sessions import new_session; new_session('u2net')" || \
    echo "warning: could not pre-fetch u2net; it will download on first use"

ENV PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

# One worker. The vision models hold ~600 MB of ONNX sessions per process, so a
# second worker doubles memory for no throughput gain - the job queue inside the
# process already gives concurrency where it helps.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1"]
