"""Reconstruct gradation from a multi-size plotter marker.

A flat PLT marker draws each piece at every size, at arbitrary position /
rotation / mirror. To recover the grade we must, per piece name:
  1. pick one clean shape per size,
  2. align the larger sizes onto the base size (Procrustes: mirror + rotation +
     point-offset over an arc-length resample),
  3. keep the aligned, nested boundaries — their point-wise difference IS the
     grade.

The aligned boundaries (all sharing point count and a common reference centre)
are emitted nested in one ASTM block so Gerber reads the piece as graded.
"""

import math
from collections import defaultdict
from piece_grouper import area


def resample(poly, n=96):
    """Resample a closed polygon to n points equally spaced by arc length."""
    if len(poly) < 3:
        return list(poly)
    pts = list(poly) + [poly[0]]
    seg = [0.0]
    for i in range(1, len(pts)):
        seg.append(seg[-1] + math.hypot(pts[i][0] - pts[i - 1][0],
                                        pts[i][1] - pts[i - 1][1]))
    total = seg[-1] or 1.0
    out = []
    j = 1
    for k in range(n):
        d = total * k / n
        while j < len(seg) and seg[j] < d:
            j += 1
        jj = min(j, len(pts) - 1)
        span = (seg[jj] - seg[jj - 1]) or 1.0
        t = (d - seg[jj - 1]) / span
        out.append((pts[jj - 1][0] + t * (pts[jj][0] - pts[jj - 1][0]),
                    pts[jj - 1][1] + t * (pts[jj][1] - pts[jj - 1][1])))
    return out


def _centroid(rp):
    cx = sum(x for x, y in rp) / len(rp)
    cy = sum(y for x, y in rp) / len(rp)
    return cx, cy


def align_to(src_poly, ref_poly, n=96):
    """Align src onto ref (both closed polygons). Returns src resampled to n
    points, mirrored/rotated/offset to best match ref, and re-centred on ref's
    centroid so the two nest concentrically. Also returns the mean residual mm.
    """
    a = resample(ref_poly, n)
    b = resample(src_poly, n)
    rcx, rcy = _centroid(a)
    bcx, bcy = _centroid(b)
    a0 = [(x - rcx, y - rcy) for x, y in a]
    b0 = [(x - bcx, y - bcy) for x, y in b]

    best = (float('inf'), None)
    step = 2
    for mir in (1, -1):
        bm = [(x * mir, y) for x, y in b0]
        for off in range(n):
            num = den = 0.0
            for i in range(0, n, step):
                bx, by = bm[(i + off) % n]
                ax, ay = a0[i]
                num += ax * by - ay * bx
                den += ax * bx + ay * by
            th = math.atan2(num, den)
            c, s = math.cos(th), math.sin(th)
            d = 0.0
            for i in range(0, n, step):
                bx, by = bm[(i + off) % n]
                rx = c * bx + s * by
                ry = -s * bx + c * by
                d += (a0[i][0] - rx) ** 2 + (a0[i][1] - ry) ** 2
            if d < best[0]:
                best = (d, (off, mir, th))

    off, mir, th = best[1]
    bm = [(x * mir, y) for x, y in b0]
    c, s = math.cos(th), math.sin(th)
    aligned = []
    resid = 0.0
    for i in range(n):
        bx, by = bm[(i + off) % n]
        rx = c * bx + s * by
        ry = -s * bx + c * by
        aligned.append((rx + rcx, ry + rcy))
        resid += math.hypot(rx - a0[i][0], ry - a0[i][1])
    return aligned, (resid / n) * 0.0254


def build_graded_pieces(ensembles, n=96):
    """Group ensembles by piece name, pair sizes, align them, and return graded
    pieces. Only names present at >= 2 sizes are graded; the rest pass through
    as single-size pieces."""
    byname = defaultdict(lambda: defaultdict(list))
    singles = []
    for e in ensembles:
        nm = str(e.get('piece_id', '') or '')
        sz = str(e.get('size', '') or '')
        if nm and sz:
            byname[nm][sz].append(e)
        else:
            singles.append(e)

    graded = []
    for nm, bysz in byname.items():
        sizes = sorted(bysz.keys())
        if len(sizes) < 2:
            for e in bysz[sizes[0]]:
                singles.append(e)
            continue
        # one representative (largest area) shape per size
        reps = {sz: max(lst, key=lambda e: area(e['outer']))
                for sz, lst in bysz.items()}
        base_sz = sizes[0]
        base = reps[base_sz]
        base_rs = resample(base['outer'], n)
        boundaries = {base_sz: base_rs}
        resid = {}
        for sz in sizes[1:]:
            al, r = align_to(reps[sz]['outer'], base['outer'], n)
            boundaries[sz] = al
            resid[sz] = r
        graded.append({
            'name': nm,
            'sizes': sizes,
            'base_size': base_sz,
            'boundaries': boundaries,
            'notches': base.get('notches', []),
            'internals': base.get('internals', []),
            'residual_mm': resid,
        })
    return graded, singles
