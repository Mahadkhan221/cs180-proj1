"""CS 180 Project 1: Colorizing the Prokudin-Gorskii Photo Collection.

Splits a glass-plate scan (stacked B, G, R exposures, top to bottom) into
three channels, aligns G and R to the B channel, and stacks them into one
RGB image.

Alignment is implemented from scratch:
  - ssd_score / ncc_score        : the two required metrics
  - align_single_scale           : exhaustive window search
  - align_pyramid                : recursive coarse-to-fine search
Library calls are limited to image read/write/resize-level operations
(PIL for I/O, numpy for array math), per the project rules.

Displacements are reported as (x, y) = (column shift, row shift) applied
to the moving channel with np.roll.

Usage:
    python align.py data/cathedral.jpg --single
    python align.py data/emir.tif --pyramid
    python align.py data/emir.tif --pyramid --metric ncc --out results/
"""

import argparse
import os
import time

import numpy as np
from PIL import Image

# PIL refuses very large images by default; the LoC tifs are ~70MP.
Image.MAX_IMAGE_PIXELS = None


# ---------------------------------------------------------------- I/O helpers

def imread(path):
    """Read an image as float64 grayscale in [0, 1] (handles 8- and 16-bit)."""
    img = Image.open(path)
    arr = np.asarray(img)
    if arr.ndim == 3:  # some scans are saved with redundant color channels
        arr = arr[..., :3].mean(axis=2)
    if arr.dtype == np.uint8:
        return arr.astype(np.float64) / 255.0
    if arr.dtype in (np.uint16, np.int32, np.uint32):
        return arr.astype(np.float64) / 65535.0
    arr = arr.astype(np.float64)
    return arr / arr.max() if arr.max() > 1.0 else arr


def imsave(path, im):
    """Save a float [0, 1] image (H, W) or (H, W, 3) as 8-bit."""
    out = (np.clip(im, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    Image.fromarray(out).save(path)


def split_plate(im):
    """Split a vertically stacked plate (B, G, R top to bottom) into R, G, B."""
    h = im.shape[0] // 3
    b = im[0 * h:1 * h]
    g = im[1 * h:2 * h]
    r = im[2 * h:3 * h]
    return r, g, b


# -------------------------------------------------------------------- metrics

def _interior(im, ref, dx, dy, border_x, border_y):
    """Interior crops of ref and of im shifted by (dx, dy), without copying.

    The shifted image np.roll(im, (dy, dx)) restricted to the interior window
    equals im[y0-dy:y1-dy, x0-dx:x1-dx] as long as the border swallows the
    shift, so we slice instead of rolling (no wrap-around pixels ever scored).
    """
    h, w = ref.shape
    y0, y1 = border_y, h - border_y
    x0, x1 = border_x, w - border_x
    a = im[y0 - dy:y1 - dy, x0 - dx:x1 - dx]
    b = ref[y0:y1, x0:x1]
    return a, b


def ssd_score(a, b):
    """Euclidean distance sqrt(sum((a-b)^2)). Lower is better."""
    d = a - b
    return np.sqrt(np.sum(d * d))


def ncc_score(a, b):
    """Zero-mean normalized cross-correlation. Higher is better."""
    a = a - a.mean()
    b = b - b.mean()
    denom = np.sqrt(np.sum(a * a) * np.sum(b * b))
    if denom == 0:
        return 0.0
    return np.sum(a * b) / denom


def _grad_mag(im):
    """Gradient magnitude via forward differences (for the 'edge' metric)."""
    gy = np.zeros_like(im)
    gx = np.zeros_like(im)
    gy[:-1, :] = im[1:, :] - im[:-1, :]
    gx[:, :-1] = im[:, 1:] - im[:, :-1]
    return np.sqrt(gx * gx + gy * gy)


# Metric registry: (score_fn, preprocess_fn, higher_is_better)
METRICS = {
    "ssd": (ssd_score, None, False),
    "ncc": (ncc_score, None, True),
    # Bells & whistles: NCC on gradient magnitudes instead of raw intensity.
    # Channels of e.g. emir.tif differ wildly in brightness, but their edges
    # coincide, so matching edge structure is robust to brightness mismatch.
    "edge": (ncc_score, _grad_mag, True),
}


# ------------------------------------------------------------------ alignment

def align_single_scale(im, ref, metric="ncc", window=15, center=(0, 0),
                       border_frac=0.12, _preprocessed=False):
    """Exhaustive search for the (dx, dy) displacement of `im` onto `ref`.

    Searches the square window center +/- `window` in both axes and returns
    the displacement whose interior-crop score is best. Borders are excluded
    from scoring (they carry scan artifacts and the wrap/undefined region).
    """
    score_fn, pre_fn, higher_better = METRICS[metric]
    if pre_fn is not None and not _preprocessed:
        im, ref = pre_fn(im), pre_fn(ref)

    cx, cy = center
    max_shift = max(abs(cx), abs(cy)) + window
    h, w = ref.shape
    border_y = max(int(h * border_frac), max_shift + 1)
    border_x = max(int(w * border_frac), max_shift + 1)
    if 2 * border_y >= h or 2 * border_x >= w:
        raise ValueError(
            f"image {w}x{h} too small for shift {max_shift} plus borders")

    best = None
    best_d = (0, 0)
    for dy in range(cy - window, cy + window + 1):
        for dx in range(cx - window, cx + window + 1):
            a, b = _interior(im, ref, dx, dy, border_x, border_y)
            s = score_fn(a, b)
            if best is None or (s > best if higher_better else s < best):
                best, best_d = s, (dx, dy)
    return best_d


def downsample(im):
    """2x downsample by 2x2 block averaging (acts as a light anti-alias)."""
    h2, w2 = (im.shape[0] // 2) * 2, (im.shape[1] // 2) * 2
    im = im[:h2, :w2]
    return (im[0::2, 0::2] + im[1::2, 0::2] +
            im[0::2, 1::2] + im[1::2, 1::2]) / 4.0


def align_pyramid(im, ref, metric="ncc", min_size=400, window=15,
                  refine_window=2, _preprocessed=False):
    """Coarse-to-fine displacement search.

    Recursively downsamples 2x until the image is at most `min_size` on its
    long side, runs the exhaustive search there, then walks back up: at each
    finer level the coarse estimate is doubled and refined with a small
    +/- `refine_window` search around it.
    """
    _, pre_fn, _ = METRICS[metric]
    if pre_fn is not None and not _preprocessed:
        im, ref = pre_fn(im), pre_fn(ref)

    if max(ref.shape) <= min_size:
        return align_single_scale(im, ref, metric, window=window,
                                  _preprocessed=True)

    coarse = align_pyramid(downsample(im), downsample(ref), metric,
                           min_size=min_size, window=window,
                           refine_window=refine_window, _preprocessed=True)
    center = (2 * coarse[0], 2 * coarse[1])
    return align_single_scale(im, ref, metric, window=refine_window,
                              center=center, _preprocessed=True)


# ------------------------------------------------------------------- pipeline

def colorize(path, method="pyramid", metric="ncc", verbose=True):
    """Full pipeline: read plate, split, align G and R to B, stack RGB.

    Returns (rgb_image, g_offset, r_offset) with offsets as (x, y).
    """
    plate = imread(path)
    r, g, b = split_plate(plate)

    align = align_single_scale if method == "single" else align_pyramid
    t0 = time.time()
    gx, gy = align(g, b, metric=metric)
    rx, ry = align(r, b, metric=metric)
    elapsed = time.time() - t0

    if verbose:
        name = os.path.basename(path)
        print(f"{name} [{method}/{metric}] "
              f"G: ({gx}, {gy})  R: ({rx}, {ry})  ({elapsed:.1f}s)")

    g_aligned = np.roll(g, shift=(gy, gx), axis=(0, 1))
    r_aligned = np.roll(r, shift=(ry, rx), axis=(0, 1))
    rgb = np.dstack([r_aligned, g_aligned, b])
    return rgb, (gx, gy), (rx, ry), elapsed


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("image", help="path to a glass-plate scan (jpg/tif/png)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--single", action="store_true",
                      help="single-scale exhaustive search (small images)")
    mode.add_argument("--pyramid", action="store_true",
                      help="coarse-to-fine pyramid search (default)")
    p.add_argument("--metric", default="ncc", choices=sorted(METRICS),
                   help="matching metric (default: ncc)")
    p.add_argument("--out", default="results",
                   help="output directory (default: results/)")
    args = p.parse_args()

    method = "single" if args.single else "pyramid"
    rgb, g_off, r_off, _ = colorize(args.image, method=method,
                                    metric=args.metric)

    os.makedirs(args.out, exist_ok=True)
    # Same naming as run_all.py: the source extension is part of the name
    # because five scenes exist as both .jpg and .tif, and without it the
    # hi-res result silently overwrites the low-res one.
    stem, ext = os.path.splitext(os.path.basename(args.image))
    out_path = os.path.join(
        args.out, f"{stem}_{ext.lstrip('.')}_{method}_{args.metric}.jpg")
    imsave(out_path, rgb)
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()
