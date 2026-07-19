"""Definitively locate a dataset plate in the LoC Prokudin-Gorskii collection.

Keyword search fails when the catalog's transliterated-Russian title shares no
words with the dataset's English nickname (`siren`, `icon`, `church`). And the
collection's JSON index turned out to be incomplete -- it omits negatives that
demonstrably exist (e.g. prok 01728, 01522) -- so it can't be trusted for an
exhaustive search either.

Instead this enumerates the negatives directly by their predictable URL:

    .../service/pnp/prok/{id//100*100:05d}/{id:05d}_150px.jpg

which is a ~3KB, 58x150 grayscale image of the whole three-frame plate. That
is the same kind of image as the dataset plates in data/, so the two can be
compared directly -- far more discriminative than matching against the color
composites (correct matches score ~1.0, wrong ones ~0.6).

Each candidate is scored against the target by normalized cross-correlation
on a mean-subtracted, unit-norm, border-trimmed downsample.

Thumbnails cache in data/neg_thumbs/, so a re-run costs nothing.

Usage:
    python find_plate.py siren icon church
    python find_plate.py --max-id 2600 --cached-only siren
"""

import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

THUMBS = os.path.join("data", "neg_thumbs")
BASE = "https://tile.loc.gov/storage-services/service/pnp/prok"
DEFAULT_MAX_ID = 2600


def thumb_url(pid):
    return f"{BASE}/{pid // 100 * 100:05d}/{pid:05d}_150px.jpg"


def openable(path):
    """True if path is a complete, decodable image. A truncated download
    still leaves a plausible file on disk; scoring a half-decoded image
    would silently corrupt the search."""
    try:
        with Image.open(path) as im:
            im.load()
        return True
    except Exception:
        return False


def fetch(pid, tries=2):
    """Fetch one thumbnail. Used by check_matcher; the sweep uses fetch_many,
    which is ~40x faster."""
    dst = os.path.join(THUMBS, f"{pid:05d}.jpg")
    if os.path.exists(dst):
        return dst if openable(dst) else None
    for i in range(tries):
        subprocess.run(["curl.exe", "-sS", "--max-time", "30", "-o", dst,
                        thumb_url(pid)], capture_output=True)
        if os.path.exists(dst) and openable(dst):
            return dst
        time.sleep(0.5 * (i + 1))
    if os.path.exists(dst):
        os.remove(dst)
    return None


def fetch_many(pids, rate=8.0, batch=60, parallel=4):
    """Fetch thumbnails politely, honouring rate limits.

    An earlier version fired ~2500 requests with --parallel-max 16 and no
    rate cap; the tile server 429'd the whole sweep, and because it also
    passed -f, the throttled responses were discarded silently and the
    search then ran against a fraction of the collection while appearing to
    succeed. So: keep concurrency to what a browser would use, cap the
    overall request rate, treat 429 as a signal to back off exponentially
    rather than something to ignore, and report what actually landed.

    404s are expected and cheap -- the id space has gaps.
    """
    todo = [p for p in pids
            if not os.path.exists(os.path.join(THUMBS, f"{p:05d}.jpg"))]
    if not todo:
        return 0, 0
    interval = 1.0 / rate
    got = missing = 0
    backoff = 5.0
    i = 0
    while i < len(todo):
        chunk, cfg = todo[i:i + batch], os.path.join(THUMBS, "_batch.cfg")
        with open(cfg, "w") as f:
            for p in chunk:
                dst = os.path.join(THUMBS, f"{p:05d}.jpg").replace("\\", "/")
                f.write(f'url = "{thumb_url(p)}"\noutput = "{dst}"\n')
        started = time.time()
        # -w prints each transfer's status so throttling is visible.
        r = subprocess.run(
            ["curl.exe", "-sS", "-f", "--parallel",
             "--parallel-max", str(parallel), "--max-time", "45", "-K", cfg,
             "-w", "%{http_code}\n"], capture_output=True)
        codes = [c for c in r.stdout.decode("ascii", "ignore").split() if c]
        if "429" in codes:
            for p in chunk:  # drop anything half-written by a throttled call
                d = os.path.join(THUMBS, f"{p:05d}.jpg")
                if os.path.exists(d) and not openable(d):
                    os.remove(d)
            print(f"  rate limited; backing off {backoff:.0f}s", flush=True)
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        backoff = 5.0
        for p in chunk:
            d = os.path.join(THUMBS, f"{p:05d}.jpg")
            if os.path.exists(d) and openable(d):
                got += 1
            else:
                if os.path.exists(d):
                    os.remove(d)
                missing += 1
        i += batch
        print(f"  {min(i, len(todo))}/{len(todo)} attempted "
              f"(id<={chunk[-1]}), {got} fetched", flush=True)
        elapsed = time.time() - started
        pause = len(chunk) * interval - elapsed
        if pause > 0:
            time.sleep(pause)
    if os.path.exists(os.path.join(THUMBS, "_batch.cfg")):
        os.remove(os.path.join(THUMBS, "_batch.cfg"))
    return got, missing


def vec(path, size=(24, 72)):
    """Mean-subtracted unit-norm vector of a three-frame plate image.

    A 6% border is trimmed: plates carry ragged edges and handwritten
    catalog numbers that differ between the dataset scan and LoC's, and at
    this resolution those would meaningfully skew the correlation.
    """
    with Image.open(path) as im:
        im = im.convert("L")
        w, h = im.size
        m = 0.06
        im = im.crop((int(w * m), int(h * m),
                      int(w * (1 - m)), int(h * (1 - m))))
        a = np.asarray(im.resize(size), dtype=np.float64)
    a -= a.mean()
    n = np.sqrt((a * a).sum())
    return a.ravel() / n if n else a.ravel()


def main():
    args = sys.argv[1:]
    cached_only = "--cached-only" in args
    max_id = DEFAULT_MAX_ID
    if "--max-id" in args:
        max_id = int(args[args.index("--max-id") + 1])
    targets = [a for a in args if not a.startswith("--") and not a.isdigit()]
    if not targets:
        print(__doc__)
        return

    os.makedirs(THUMBS, exist_ok=True)
    tvecs = {}
    for t in targets:
        p = os.path.join("data", f"{t}.jpg")
        if not os.path.exists(p):
            print(f"no such plate: {p}")
            continue
        tvecs[t] = vec(p)
    if not tvecs:
        return

    if not cached_only:
        print(f"fetching thumbnails for ids 1..{max_id}", flush=True)
        got, missing = fetch_many(range(1, max_id + 1))
        print(f"  fetched {got} new, {missing} absent (id gaps)", flush=True)

    best = {t: [] for t in tvecs}
    scanned = 0
    for pid in range(1, max_id + 1):
        path = os.path.join(THUMBS, f"{pid:05d}.jpg")
        if not os.path.exists(path):
            continue
        try:
            v = vec(path)
        except Exception:
            continue  # unreadable/truncated: skip rather than score garbage
        scanned += 1
        for t, tv in tvecs.items():
            best[t].append((float(tv @ v), pid))

    print(f"\nbest matches ({scanned} negatives compared):")
    for t in tvecs:
        ranked = sorted(best[t], reverse=True)[:4]
        if not ranked:
            print(f"  {t}: no candidates")
            continue
        top, runner = ranked[0], (ranked[1] if len(ranked) > 1 else (0, None))
        flag = "" if top[0] >= 0.9 and top[0] - runner[0] >= 0.15 \
            else "   <-- NOT conclusive, verify by eye"
        print(f"  {t}: prok {top[1]:05d}  ncc={top[0]:.3f}{flag}")
        print("        runners-up: "
              + ", ".join(f"{p}:{s:.2f}" for s, p in ranked[1:]))


if __name__ == "__main__":
    main()
