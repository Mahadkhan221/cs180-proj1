"""Regression check: align.py must recover known synthetic offsets.

Run after any change to align.py.
    python make_test_plate.py && python test_align.py
"""

import sys

from align import align_pyramid, align_single_scale, imread, split_plate
from make_test_plate import LARGE_G, LARGE_R, SMALL_G, SMALL_R

failures = []


def check(label, got, want, must_match=True):
    ok = (got == want) if must_match else (got != want)
    verdict = "PASS" if ok else "FAIL"
    print(f"  [{verdict}] {label}: got {got}, want "
          f"{'==' if must_match else '!='} {want}")
    if not ok:
        failures.append(label)


print("small plate, single-scale (must recover exactly):")
r, g, b = split_plate(imread("test/small_plate.png"))
for m in ("ssd", "ncc"):
    check(f"single/{m} G", align_single_scale(g, b, m), SMALL_G)
    check(f"single/{m} R", align_single_scale(r, b, m), SMALL_R)

print("\nlarge plate, pyramid (must recover exactly):")
r, g, b = split_plate(imread("test/large_plate.png"))
for m in ("ssd", "ncc"):
    check(f"pyramid/{m} G", align_pyramid(g, b, m), LARGE_G)
    check(f"pyramid/{m} R", align_pyramid(r, b, m), LARGE_R)

print("\nlarge plate, single-scale +/-15 (must FAIL to reach the true offset,")
print("which is what motivates the pyramid):")
check("single/ssd R cannot reach true offset",
      align_single_scale(r, b, "ssd"), LARGE_R, must_match=False)

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    sys.exit(1)
print("all checks passed")
