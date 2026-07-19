"""Resolve and fetch full-resolution Prokudin-Gorskii glass-plate negatives
from the Library of Congress (public domain).

Why this exists: the course data.zip on Google Drive requires a signed-in
human download. The hi-res .tif scans in it are LoC masters, which LoC
serves publicly from tile.loc.gov. We search the collection for each scene,
derive the b&w triple-frame negative id (`prok NNNNN`; color composites are
`prokc` with id = prok id + 20001), verify the candidate visually against
the course's low-res plate via NCC, and download the master TIFF.

Usage:
    python fetch_loc.py            # resolve + download everything in SCENES
    python fetch_loc.py --dry-run  # resolve + verify only
"""

import json
import os
import subprocess
import sys
import time

import numpy as np
from PIL import Image

TILE = "https://tile.loc.gov/storage-services"

# scene name -> LoC search query, or a list of queries to try. Scenes present
# in data/<name>.jpg are verified against that plate by NCC; extras (not in
# data/) are downloaded as-is. Multiple queries are useful when the LoC
# catalog title doesn't match the dataset's nickname.
SCENES = {
    "emir": "Emir Bukharskii",
    "melons": "melon",
    "self_portrait": "Karolitskhali",
    "three_generations": "Kalganov",
    "harvesters": "Grechanki",
    "siren": ["siren", "lilac", "sirenn"],
    "lugano": ["Lugano", "Lugano Switzerland"],
    "lastochikino": ["Lastochkino", "Lastochkino gnezdo", "Alupka dacha"],
    "italil": ["Italiia", "Italy", "Italii"],
    "icon": ["ikonostas", "raka", "sobor vnutrennii vid"],
    "church": ["tserkov reka", "tserkov", "Belozersk tserkov"],
}
EXTRAS = {
    "railroad": "handcar Petrozavodsk",
    "chapel": "chasovnia",
}

# Scenes whose LoC catalog title shares no words with the dataset nickname,
# so keyword search cannot find them. These ids were located by
# find_plate.py, which cross-correlates the low-res plate against every
# negative's thumbnail in the collection. Verified the same way as the rest.
OVERRIDE_IDS = {
    "siren": 171,   # "Sirenn" -- lilac; matched at ncc 0.99
}


def curl_json(url, tries=5):
    for i in range(tries):
        out = subprocess.run(
            ["curl.exe", "-sS", "--compressed", "--max-time", "60", url],
            capture_output=True).stdout
        try:
            return json.loads(out.decode("utf-8"))
        except json.JSONDecodeError:  # LoC intermittently truncates responses
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"unparseable response after {tries} tries: {url}")


def curl_download(url, dest):
    r = subprocess.run(["curl.exe", "-sS", "--max-time", "600",
                        "-o", dest, url])
    return r.returncode == 0 and os.path.exists(dest)


def curl_head_len(url):
    out = subprocess.run(
        ["curl.exe", "-sSI", "--max-time", "30", url],
        capture_output=True).stdout.decode("utf-8", "replace").lower()
    if "200 ok" not in out.split("\n")[0]:
        return None
    for line in out.split("\n"):
        if line.startswith("content-length:"):
            return int(line.split(":")[1].strip())
    return None


def search_collection(query, count=20):
    url = ("https://www.loc.gov/collections/prokudin-gorskii/"
           f"?q={query.replace(' ', '+')}&fo=json&c={count}")
    d = curl_json(url)
    return d.get("results", [])


def negative_ids(results):
    """prok negative ids implied by search results, best candidates first."""
    ids = []
    for r in results:
        for aka in r.get("aka", []):
            if "loc.pnp/prok." in aka:
                ids.append(int(aka.rsplit(".", 1)[1]))
            elif "loc.pnp/prokc." in aka:
                ids.append(int(aka.rsplit(".", 1)[1]) - 20001)
    seen, out = set(), []
    for i in ids:
        if 0 < i < 20000 and i not in seen:
            seen.add(i)
            out.append(i)
    return out


def prok_url(neg_id, kind):
    folder = f"{neg_id // 100 * 100:05d}"
    if kind == "master":
        return f"{TILE}/master/pnp/prok/{folder}/{neg_id:05d}a.tif"
    return f"{TILE}/service/pnp/prok/{folder}/{neg_id:05d}v.jpg"


def load_small(path_or_bytes, size=(48, 144)):
    im = Image.open(path_or_bytes).convert("L").resize(size)
    a = np.asarray(im, dtype=np.float64)
    a -= a.mean()
    n = np.sqrt((a * a).sum())
    return a / n if n else a


def ncc_to_plate(neg_id, plate_path, tmp):
    """NCC between the LoC preview of a negative and our low-res plate."""
    prev = os.path.join(tmp, f"prok_{neg_id:05d}v.jpg")
    if not os.path.exists(prev):
        if not curl_download(prok_url(neg_id, "preview"), prev):
            return -1.0
    try:
        a = load_small(prev)
        b = load_small(plate_path)
    except Exception:
        return -1.0
    return float((a * b).sum())


def resolve(name, query, tmp, verify_plate=None):
    if name in OVERRIDE_IDS:
        neg = OVERRIDE_IDS[name]
        score = (ncc_to_plate(neg, verify_plate, tmp) if verify_plate else None)
        url = prok_url(neg, "master")
        size = curl_head_len(url)
        if size is None:
            print(f"[{name}] override prok {neg:05d} has no master", flush=True)
            return None
        note = f" (ncc {score:.2f})" if score is not None else ""
        print(f"[{name}] -> prok {neg:05d}{note}  master "
              f"{size / 1e6:.0f} MB  [id from find_plate.py]", flush=True)
        return neg, url, size

    queries = [query] if isinstance(query, str) else list(query)
    best_overall = None  # (score, id)
    ids = []
    for q in queries:
        print(f"[{name}] searching: {q}", flush=True)
        found = negative_ids(search_collection(q))
        time.sleep(3)  # stay polite to the search API
        if not found:
            print(f"[{name}]   no prok/prokc candidates", flush=True)
            continue
        ids = found
        if not verify_plate:
            break
        scored = [(ncc_to_plate(i, verify_plate, tmp), i) for i in found[:8]]
        scored.sort(reverse=True)
        print(f"[{name}]   top: "
              + ", ".join(f"({s:.2f}, {i})" for s, i in scored[:4]), flush=True)
        if best_overall is None or scored[0] > best_overall:
            best_overall = scored[0]
        if best_overall[0] >= 0.95:  # unambiguous match, stop searching
            break

    if verify_plate:
        if best_overall is None:
            print(f"[{name}] no candidates from any query", flush=True)
            return None
        score, neg = best_overall
        if score < 0.8:
            print(f"[{name}] best match only {score:.2f}, too weak to trust "
                  f"&mdash; skipping".replace("&mdash;", "-"), flush=True)
            return None
    else:
        if not ids:
            print(f"[{name}] no candidates found", flush=True)
            return None
        neg = ids[0]
    url = prok_url(neg, "master")
    size = curl_head_len(url)
    if size is None:
        print(f"[{name}] master not found at {url}", flush=True)
        return None
    print(f"[{name}] -> prok {neg:05d}  master {size / 1e6:.0f} MB", flush=True)
    return neg, url, size


def main():
    dry = "--dry-run" in sys.argv
    tmp = os.path.join("data", "loc_previews")
    os.makedirs(tmp, exist_ok=True)
    plan = []
    for name, q in SCENES.items():
        plate = os.path.join("data", f"{name}.jpg")
        r = resolve(name, q, tmp, verify_plate=plate)
        if r:
            plan.append((name, r[1], r[2]))
    for name, q in EXTRAS.items():
        r = resolve(name, q, tmp, verify_plate=None)
        if r:
            plan.append((name, r[1], r[2]))

    total = sum(s for _, _, s in plan)
    print(f"\nplan: {len(plan)} masters, {total / 1e6:.0f} MB total", flush=True)
    if dry:
        return
    for name, url, size in plan:
        dest = os.path.join("data", f"{name}.tif")
        if os.path.exists(dest) and os.path.getsize(dest) == size:
            print(f"{name}.tif already present", flush=True)
            continue
        print(f"downloading {name}.tif ({size / 1e6:.0f} MB)...", flush=True)
        ok = curl_download(url, dest)
        got = os.path.getsize(dest) if os.path.exists(dest) else 0
        print(f"  {'ok' if ok and got == size else 'INCOMPLETE'} ({got / 1e6:.0f} MB)",
              flush=True)


if __name__ == "__main__":
    main()
