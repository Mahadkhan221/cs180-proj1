"""Print the current contents of results/summary.json, grouped by image."""

import json
import os
import sys

path = os.path.join('results', 'summary.json')
runs = json.load(open(path))
only = sys.argv[1] if len(sys.argv) > 1 else ''

print(f'{len(runs)} runs recorded')
for r in runs:
    if only and not r['image'].endswith(only):
        continue
    g, rr = r['g'], r['r']
    print(f"  {r['image']:24s} {r['method']:8s} {r['metric']:5s} "
          f"G ({g[0]:5d},{g[1]:5d})  R ({rr[0]:5d},{rr[1]:5d})  "
          f"{r['seconds']:7.1f}s")
