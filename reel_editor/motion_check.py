#!/usr/bin/env python3
"""Camera-smoothness check for any rendered video (run before delivering anything with pans/pushes/tracking).

Counts visible jolts: frame-to-frame changes in picture motion over 1 px (phase correlation on the centre).
usage: python -m reel_editor.motion_check video.mp4 [other.mp4 ...] [--at 3,9,15] [--len 20] [--region center|face]
       [--json]
--region face measures only the upper half of a vertical frame (where the face is), so animated captions in the
lower half do not count as camera jolts. The pipeline also checks every punch-in clip on its own.

Reference: a smooth, locked-framing edit with one eased push lands around 11-22 jolts per 20 s of talking head;
a stepping/wobbling virtual camera measured 52-78. Rule of thumb: over ~25 per 20 s means the camera is stepping,
fix it before delivery. Hard cuts (B-roll in/out, punch-in in/out) count as one jolt each, which is expected.
"""
import json
import sys

import cv2
import numpy as np

LIMIT_PER_20S = 25


REGIONS = {"center": (1 / 5, 4 / 5), "face": (1 / 20, 1 / 2)}


def jolts(path, t0, t1, region="center"):
    c = cv2.VideoCapture(path)
    c.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
    prev, sh = None, []
    H, W = int(c.get(4)), int(c.get(3))
    while c.get(cv2.CAP_PROP_POS_MSEC) < t1 * 1000:
        ok, f = c.read()
        if not ok:
            break
        y0, y1 = REGIONS.get(region, REGIONS["center"])
        g = cv2.cvtColor(f[int(H * y0):int(H * y1), W // 6:W * 5 // 6], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None:
            sh.append(cv2.phaseCorrelate(prev, g)[0])
        prev = g
    if len(sh) < 3:
        return 0, len(sh)
    acc = np.abs(np.diff(np.array(sh), axis=0))
    return int((acc.max(1) > 1.0).sum()), len(sh)


def check(path, at=None, ln=20.0, region="center"):
    c = cv2.VideoCapture(path)
    fps = c.get(5) or 30
    dur = c.get(7) / max(1, fps)
    ln = min(ln, dur)
    pts = at or ([0.0] if dur <= ln * 1.5 else [max(0.0, dur * k / 4 - ln / 2) for k in (1, 2, 3)])
    out = []
    for t in pts:
        b, n = jolts(path, t, t + ln, region)
        per20 = b * 20.0 / max(1e-6, n / fps)
        out.append({"start": round(t, 1), "jolts": b, "frames": n, "per_20s": round(per20, 1),
                    "smooth": per20 <= LIMIT_PER_20S})
    return out


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    at, ln, vids, as_json, region = None, 20.0, [], False, "center"
    i = 0
    while i < len(args):
        if args[i] == "--at":
            at = [float(x) for x in args[i + 1].split(",")]; i += 2
        elif args[i] == "--len":
            ln = float(args[i + 1]); i += 2
        elif args[i] == "--region":
            region = args[i + 1]; i += 2
        elif args[i] == "--json":
            as_json = True; i += 1
        else:
            vids.append(args[i]); i += 1
    allres, ok = {}, True
    for v in vids:
        res = check(v, at, ln, region)
        allres[v] = res
        for r in res:
            ok = ok and r["smooth"]
            if not as_json:
                print(f"{v} @ {r['start']:.0f}s: {r['jolts']} jolts in {r['frames']} frames "
                      f"({r['per_20s']:.0f} per 20 s) -> {'smooth' if r['smooth'] else 'BUMPY'}")
    if as_json:
        print(json.dumps(allres))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
