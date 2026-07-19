"""Build synthetic Prokudin-Gorskii-style test plates with known offsets.

Creates a textured ground-truth scene, displaces the G and R channels by
known amounts, and stacks B, G, R vertically (top to bottom) like a real
glass-plate scan. Because the aligner must undo the displacement we applied,
the expected recovered offset is exactly the offset we pass to np.roll here.

Outputs (in test/):
  small_plate.png  400x300 per channel, offsets within +/-15  -> single-scale
                   search must recover them exactly.
  large_plate.png  1600x1200 per channel, offsets up to 35px  -> single-scale
                   with a +/-15 window must FAIL (saturate at its window edge);
                   the pyramid must recover them.

Ground truth is printed and written to test/ground_truth.txt.
"""

import os

import numpy as np

from align import imsave

# Expected *recovered* displacement (x, y) per channel, i.e. the roll we
# apply when building the plate.
SMALL_G = (5, -8)
SMALL_R = (-11, 12)
LARGE_G = (14, 22)
LARGE_R = (-27, 35)


def make_scene(h, w, seed=180):
    """A textured RGB scene whose channels are strongly correlated.

    Real plate channels are three exposures of the same scene, so edges and
    texture coincide across channels (corr ~0.9+); only the tint differs.
    We build one shared luminance pattern and derive each channel from it
    with a mild gain plus a small independent tint field.
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    lum = 0.35 + 0.3 * (xx / w) + 0.2 * (yy / h)

    # Soft gaussian blobs (shared across channels).
    for _ in range(40):
        cy, cx = rng.uniform(0, h), rng.uniform(0, w)
        sig = rng.uniform(min(h, w) / 40, min(h, w) / 8)
        blob = np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sig ** 2))
        lum += rng.uniform(-0.35, 0.45) * blob
    # Hard-edged rectangles so there are sharp features to lock onto.
    for _ in range(15):
        y0 = int(rng.uniform(0, h * 0.9))
        x0 = int(rng.uniform(0, w * 0.9))
        hh = int(rng.uniform(h * 0.02, h * 0.15))
        ww = int(rng.uniform(w * 0.02, w * 0.15))
        lum[y0:y0 + hh, x0:x0 + ww] = rng.uniform(0.1, 1.0)

    # Smooth low-frequency tint fields, small relative to shared structure.
    def tint():
        t = np.zeros((h, w))
        for _ in range(6):
            cy, cx = rng.uniform(0, h), rng.uniform(0, w)
            sig = rng.uniform(min(h, w) / 6, min(h, w) / 3)
            t += rng.uniform(-1, 1) * np.exp(
                -((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * sig ** 2))
        return t

    noise = lambda: rng.normal(0, 0.01, size=(h, w))
    r = np.clip(1.00 * lum + 0.08 * tint() + noise(), 0, 1)
    g = np.clip(0.92 * lum + 0.08 * tint() + noise(), 0, 1)
    b = np.clip(0.85 * lum + 0.08 * tint() + noise(), 0, 1)
    return r, g, b


def make_plate(h, w, g_off, r_off, seed):
    r, g, b = make_scene(h, w, seed)
    g_shifted = np.roll(g, shift=(-g_off[1], -g_off[0]), axis=(0, 1))
    r_shifted = np.roll(r, shift=(-r_off[1], -r_off[0]), axis=(0, 1))
    return np.vstack([b, g_shifted, r_shifted])  # plate order: B, G, R


def main():
    os.makedirs("test", exist_ok=True)
    imsave("test/small_plate.png", make_plate(300, 400, SMALL_G, SMALL_R, 180))
    imsave("test/large_plate.png", make_plate(1200, 1600, LARGE_G, LARGE_R, 26))
    lines = [
        f"small_plate.png  expected G: {SMALL_G}  R: {SMALL_R}",
        f"large_plate.png  expected G: {LARGE_G}  R: {LARGE_R}",
    ]
    with open("test/ground_truth.txt", "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
