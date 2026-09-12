"""
How much memory does this backend actually need?

Kept in the repository so the answer can be re-derived rather than believed. It decides
the hosting plan, and it was wrong for a long time: render.yaml, docs/DEPLOY.md and
phases.md all said "roughly 700 MB", reasoned from what the models are rather than
measured, and all three recommended Render's starter plan on that basis. Starter has
the same 512 MB as free, so the recommendation cost money and would not have worked.

What it found, whole app plus one real matte of a 3072x4080 phone photo:

    u2net                 729 MB
    u2netp                544 MB
    u2netp, capped 1600   519 MB
    LOCAL_VISION=off      227 MB

Two conclusions worth keeping. Nothing that loads a model fits 512 MB, so LOCAL_VISION
is the only lever that changes the hosting answer. And capping the working resolution
- which looked like the obvious fix - bought 10 to 25 MB, because the cost is the ONNX
weights and their allocation arenas, not the image buffers.

Measures resident set size at three points, in one process, without any HTTP:

    1. after importing the app          what a request-less container holds
    2. after the matting model loads    rembg / U^2-Net
    3. after OCR loads                  rapidocr

Needs psutil, which is not in requirements.txt because nothing in the running app uses
it:  pip install psutil

Run from backend/:  python measure_mem.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def rss_mb() -> float:
    """Resident set size of this process, in MB."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        pass
    # Windows, no psutil: ask the OS directly.
    try:
        import ctypes
        import ctypes.wintypes as wt

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t),
                        ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t),
                        ("PeakPagefileUsage", ctypes.c_size_t)]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return pmc.WorkingSetSize / (1024 * 1024)
    except Exception:
        return -1.0


def show(label: str, base: float = 0.0) -> float:
    mb = rss_mb()
    delta = f"  (+{mb - base:.0f})" if base else ""
    print(f"  {label:<42} {mb:7.0f} MB{delta}")
    return mb


def main() -> None:
    print("\nresident memory, measured\n")
    start = show("bare python")

    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

    import main  # noqa: F401
    after_app = show("after importing the whole app", start)

    # The matting model. This is the one the 700 MB estimate was mostly about.
    try:
        from rembg import new_session
        session = new_session("u2net")
        after_u2net = show("after u2net loads (rembg)", after_app)
    except Exception as e:
        print(f"  u2net failed to load: {type(e).__name__}: {e}")
        after_u2net = after_app
        session = None

    # OCR.
    try:
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        after_ocr = show("after rapidocr loads", after_u2net)
    except Exception as e:
        print(f"  rapidocr failed to load: {type(e).__name__}: {e}")
        after_ocr = after_u2net
        ocr = None

    # And the small matting model, for comparison - it is the obvious lever if the
    # total does not fit a 512 MB box.
    try:
        small = new_session("u2netp")
        print()
        print("  u2netp (the small matting model) also loaded, for comparison:")
        show("  with both u2net and u2netp resident", after_ocr)
    except Exception as e:
        print(f"  u2netp: {type(e).__name__}: {e}")

    peak = rss_mb()
    print()
    print(f"  peak observed: {peak:.0f} MB")
    print()
    for limit, name in ((512, "Render free / Starter"), (2048, "Render Standard"),
                        (16384, "HF Space CPU basic")):
        verdict = "fits" if peak < limit * 0.8 else (
            "tight" if peak < limit else "DOES NOT FIT")
        print(f"    {name:<24} {limit:>6} MB   {verdict}")
    print()
    print("  Note: this is one process holding every model at once, which is what")
    print("  the server looks like after its first photograph. Python does not")
    print("  return freed arena memory to the OS promptly, so RSS after a request")
    print("  is a fair proxy for what a container needs provisioned.")


if __name__ == "__main__":
    main()
