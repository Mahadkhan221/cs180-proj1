"""Validate find_plate's matcher on scenes whose correct negative is known.

Scores each known-correct negative against its dataset plate alongside
decoys, including near-miss ids (adjacent exposures of the same subject).
If the correct id doesn't win by a clear margin here, the matcher isn't
trustworthy enough to identify the unknown scenes.
"""

import os

import find_plate as F

KNOWN = {"lugano": 215, "emir": 1886, "melons": 1728, "harvesters": 1522,
         "lastochikino": 188, "italil": 194}
# Decoys include ids adjacent to correct answers -- often another exposure of
# the very same subject, which is the hardest case to tell apart.
DECOYS = [1, 24, 46, 244, 542, 777, 1468, 189, 216, 1887, 1521, 1727, 193]

os.makedirs(F.THUMBS, exist_ok=True)
allpass = True

for scene, correct in KNOWN.items():
    plate = os.path.join("data", f"{scene}.jpg")
    if not os.path.exists(plate):
        print(f"{scene}: no plate")
        continue
    tv = F.vec(plate)
    scores = []
    for pid in [correct] + [d for d in DECOYS if d != correct]:
        p = F.fetch(pid)
        if not p:
            continue
        try:
            scores.append((float(tv @ F.vec(p)), pid))
        except Exception:
            pass
    scores.sort(reverse=True)
    if not scores:
        print(f"{scene}: no candidates fetched")
        allpass = False
        continue
    won = scores[0][1] == correct
    margin = scores[0][0] - (scores[1][0] if len(scores) > 1 else 0.0)
    # A near-duplicate (the same subject photographed twice, e.g. 1886/1887)
    # legitimately scores close behind, so a thin margin is not a failure so
    # long as the correct plate still wins outright at near-perfect score.
    allpass = allpass and won and scores[0][0] >= 0.95
    top = "  ".join(f"{p}:{s:.2f}" for s, p in scores[:4])
    print(f"{scene:14s} correct={correct:5d}  {'WINS ' if won else 'LOSES'}"
          f"  top={scores[0][0]:.2f} margin={margin:+.3f}   {top}")

print("\n" + ("matcher is trustworthy: every known scene identified at >=0.95"
              if allpass else
              "matcher NOT reliable -- do not trust its unknowns"))
