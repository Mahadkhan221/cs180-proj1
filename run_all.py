"""Run the full project: every required alignment on every plate in data/.

Produces:
  results/<name>_<method>_<metric>.jpg   colorized outputs
  results/summary.json                   offsets + runtimes for the writeup

Runs:
  1. single-scale, SSD + NCC, on the three small jpgs
     (cathedral, monastery, tobolsk)
  2. pyramid, SSD + NCC, on every image in data/ (jpgs and tifs)
  3. one deliberate failure demo: single-scale on a large tif, to show why
     the pyramid is needed (window +/-15 cannot reach the true offset)

Five scenes exist at both resolutions (emir, melons, self_portrait,
three_generations, harvesters), so output names carry the source extension
to keep the low-res and hi-res results from colliding.

Usage:
    python run_all.py [--skip-existing] [--ext .jpg|.tif]
    python run_all.py --render-only   # rebuild images from recorded offsets
"""

import glob
import json
import os
import sys

import numpy as np

from align import colorize, imread, imsave, split_plate

SMALL = ("cathedral", "monastery", "tobolsk")
FAILURE_DEMO = "melons"  # large tif whose true offset far exceeds +/-15

# Plates where a raw-intensity metric misaligns because the three exposures
# differ too much in brightness (emir's robe, church's sky/water). The 'edge'
# metric (NCC on gradient magnitude) is run on these as well.
TROUBLE = ("emir", "church", "railroad")


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def out_name(image, method, metric):
    """Output filename for a run. Includes the source extension because the
    same scene can exist as both .jpg and .tif."""
    base, ext = os.path.splitext(image)
    return f"{base}_{ext.lstrip('.')}_{method}_{metric}.jpg"


def render_only():
    """Rebuild every output image from the offsets already in summary.json.

    Used after an output-naming change so the searches don't have to re-run.
    """
    summary_path = os.path.join("results", "summary.json")
    summary = json.load(open(summary_path))

    # Drop every previously generated output so stale names can't survive.
    for f in glob.glob(os.path.join("results", "*.jpg")):
        os.remove(f)
    for f in glob.glob(os.path.join("results", "thumbs", "*.jpg")):
        os.remove(f)

    cache = {}  # one plate read serves all metrics for that image
    for r in sorted(summary, key=lambda x: x["image"]):
        path = os.path.join("data", r["image"])
        if not os.path.exists(path):
            print(f"  missing {path}, skipping")
            continue
        if path not in cache:
            cache = {path: split_plate(imread(path))}
        red, green, blue = cache[path]
        gx, gy = r["g"]
        rx, ry = r["r"]
        rgb = np.dstack([
            np.roll(red, shift=(ry, rx), axis=(0, 1)),
            np.roll(green, shift=(gy, gx), axis=(0, 1)),
            blue,
        ])
        r["output"] = out_name(r["image"], r["method"], r["metric"])
        imsave(os.path.join("results", r["output"]), rgb)
        print(f"  rendered {r['output']}")
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=1)
    print(f"re-rendered {len(summary)} outputs")


def main():
    skip = "--skip-existing" in sys.argv
    os.makedirs("results", exist_ok=True)
    if "--render-only" in sys.argv:
        render_only()
        return
    summary_path = os.path.join("results", "summary.json")
    summary = []
    if skip and os.path.exists(summary_path):
        summary = json.load(open(summary_path))
    done = {(r["image"], r["method"], r["metric"]) for r in summary}

    exts = [".jpg", ".tif"]
    if "--ext" in sys.argv:
        exts = [sys.argv[sys.argv.index("--ext") + 1]]
    images = sorted(p for e in exts
                    for p in glob.glob(os.path.join("data", "*" + e)))

    jobs = []
    for path in images:
        name = stem(path)
        if name in SMALL:
            jobs += [(path, "single", "ssd"), (path, "single", "ncc")]
        if name == FAILURE_DEMO and path.endswith(".tif"):
            jobs += [(path, "single", "ssd")]
        jobs += [(path, "pyramid", "ssd"), (path, "pyramid", "ncc")]
        if name in TROUBLE:
            jobs += [(path, "pyramid", "edge")]

    for path, method, metric in jobs:
        name = stem(path)
        key = (os.path.basename(path), method, metric)
        if key in done:
            continue
        rgb, g_off, r_off, secs = colorize(path, method=method, metric=metric)
        out_file = out_name(os.path.basename(path), method, metric)
        imsave(os.path.join("results", out_file), rgb)
        summary.append({
            "image": os.path.basename(path),
            "method": method,
            "metric": metric,
            "g": list(g_off),
            "r": list(r_off),
            "seconds": round(secs, 1),
            "output": out_file,
        })
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=1)

    print(f"\n{len(summary)} runs recorded in {summary_path}")


if __name__ == "__main__":
    main()
