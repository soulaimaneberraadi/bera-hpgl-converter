"""Group HPGL sub-polygons into piece ensembles — outer contours + internal features."""

import math, os
from typing import List, Tuple, Dict, Optional
from hpgl_parser import HpglParser


Point = Tuple[float, float]


def area(poly: List[Point]) -> float:
    """Shoelace formula, returns area in mils²."""
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def centroid(poly: List[Point]) -> Point:
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    return cx, cy


def point_segment_distance(px: float, py: float,
                           ax: float, ay: float,
                           bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    nx, ny = ax + t * dx, ay + t * dy
    return math.hypot(px - nx, py - ny)


def poly_bbox(poly: List[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def point_in_polygon(px: float, py: float, poly: List[Point]) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if ((y1 > py) != (y2 > py)) and \
           px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def point_on_edge(px: float, py: float, poly: List[Point],
                  tol: float = 50.0) -> bool:
    n = len(poly)
    for i in range(n):
        d = point_segment_distance(px, py,
                                   poly[i][0], poly[i][1],
                                   poly[(i + 1) % n][0],
                                   poly[(i + 1) % n][1])
        if d < tol:
            return True
    return False


def polygon_centroid_distance(poly: List[Point],
                              other: List[Point]) -> float:
    cx1, cy1 = centroid(poly)
    cx2, cy2 = centroid(other)
    return math.hypot(cx1 - cx2, cy1 - cy2)


def classify_feature(poly: List[Point], outer: List[Point]) -> str:
    a = area(poly)
    cx, cy = centroid(poly)
    inside = point_in_polygon(cx, cy, outer)
    on_edge = point_on_edge(cx, cy, outer)

    if a < 500 and on_edge:
        return 'notch'
    if a < 500:
        return 'notch'

    bbox = poly_bbox(poly)
    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    aspect = max(bw, bh) / (min(bw, bh) + 1e-6)

    if aspect > 5 and a < 20000:
        outer_bbox = poly_bbox(outer)
        ocx = (outer_bbox[0] + outer_bbox[2]) / 2.0
        ocy = (outer_bbox[1] + outer_bbox[3]) / 2.0
        outer_diag = math.hypot(outer_bbox[2] - outer_bbox[0],
                                outer_bbox[3] - outer_bbox[1])
        if math.hypot(cx - ocx, cy - ocy) < outer_diag * 0.3:
            return 'grain_line'

    if a < 3000 and aspect < 1.5 and inside:
        return 'buttonhole'
    if a < 10000 and inside and len(poly) <= 6:
        return 'dart'
    if inside:
        return 'internal'

    return 'unknown'


def is_contained(inner: List[Point], outer: List[Point]) -> bool:
    """A polygon is internal to `outer` only if most of its vertices lie inside
    it. Separate pieces in a marker never overlap, so this cleanly separates
    real internal features (notches/darts/grain lines) from distinct pieces."""
    if len(inner) < 2:
        return False
    inside = sum(1 for x, y in inner if point_in_polygon(x, y, outer))
    return inside >= len(inner) * 0.5


def extract_notches(poly: List[Point], ret_eps: float = 45.0,
                    tick_max: float = 380.0, dedup: float = 60.0):
    """Separate notches (crans) from the boundary path.

    In plotter output a notch is drawn as a short out-and-back excursion in the
    contour (go out to a tip, return to the same point). Those spikes make CAD
    importers fail with 'zero-length segment'. This removes them from the
    boundary and returns their base positions so they can be re-emitted as
    proper notch marks on ASTM layer 4.

    Returns (clean_boundary, [notch_point, ...]).
    """
    pts = list(poly)
    notches: List[Point] = []
    guard = 0
    while len(pts) > 4 and guard < 20000:
        guard += 1
        removed = False
        n = len(pts)
        for i in range(1, n - 1):
            a, b, d = pts[i - 1], pts[i], pts[i + 1]
            back = math.hypot(a[0] - d[0], a[1] - d[1])   # returns to start?
            tick = math.hypot(a[0] - b[0], a[1] - b[1])   # short excursion?
            if back < ret_eps and 1.0 < tick < tick_max:
                notches.append(a)
                del pts[i:i + 2]      # drop tip + return, keep base
                removed = True
                break
        if not removed:
            break
    uniq: List[Point] = []
    for nx, ny in notches:
        if all(math.hypot(nx - ux, ny - uy) > dedup for ux, uy in uniq):
            uniq.append((nx, ny))
    return pts, uniq


def label_inside(poly: List[Point], labels) -> Optional[tuple]:
    """Pick the plotter label whose text position falls inside this piece.
    Plotter labels are placed geometrically inside their piece, not in draw
    order, so sequence-based association is wrong — this fixes the piece
    name/size by containment. Returns (x, y, size, name, full) or None."""
    if not labels:
        return None
    cx, cy = centroid(poly)
    best, bestd = None, float('inf')
    for lab in labels:
        lx, ly = lab[0], lab[1]
        if point_in_polygon(lx, ly, poly):
            d = math.hypot(lx - cx, ly - cy)
            if d < bestd:
                bestd, best = d, lab
    return best


def group_pieces(parser: HpglParser) -> List[dict]:
    """Group polygons into piece ensembles by GEOMETRIC CONTAINMENT, not by
    label. Each non-contained polygon is its own piece (boundary on AAMA layer
    1); only polygons truly nested inside become internal features. Notches are
    extracted off the boundary onto their own list (ASTM layer 4). Piece
    name/size are assigned by GEOMETRIC label containment. This keeps every
    distinct piece a separate cuttable piece for Gerber AccuMark / PDS."""
    parser.parse()
    polys = [p for p in parser.pieces if area(p['polygon']) >= 1]
    polys.sort(key=lambda x: area(x['polygon']), reverse=True)
    n = len(polys)
    used = [False] * n

    ensembles = []
    for i in range(n):
        if used[i]:
            continue
        used[i] = True
        host = polys[i]
        outer_raw = host['polygon']
        outer_poly, notches = extract_notches(outer_raw)
        # geometric label association (correct name/size for this piece)
        lab = label_inside(outer_raw, getattr(parser, 'labels', None))
        pid = lab[3] if lab else host['piece_id']
        sz = lab[2] if lab else host.get('size', '')
        internals = []
        for j in range(i + 1, n):
            if used[j]:
                continue
            if is_contained(polys[j]['polygon'], outer_raw):
                used[j] = True
                internals.append({
                    'polygon': polys[j]['polygon'],
                    'type': classify_feature(polys[j]['polygon'], outer_raw),
                })
        ensembles.append({
            'piece_id': pid,
            'size': sz,
            'outer': outer_poly,
            'notches': notches,
            'internals': internals,
            'area_mm2': area(outer_poly) * 0.0254 * 0.0254,
        })
    return ensembles


def ensemble_to_dxf(ensemble: dict, unit: str = 'mm') -> str:
    scale = 0.0254 if unit == 'mm' else 0.00254 if unit == 'cm' else 0.001 if unit == 'inch' else 1.0

    def scale_pts(pts):
        return [(x * scale, y * scale) for x, y in pts]

    dxf = '0\nSECTION\n2\nENTITIES\n'
    outer = scale_pts(ensemble['outer'])
    dxf += f'0\nLWPOLYLINE\n8\nOUTER\n62\n1\n100\nAcDbPolyline\n90\n{len(outer)}\n70\n1\n'
    for x, y in outer:
        dxf += f'10\n{x:.6f}\n20\n{y:.6f}\n'
    for feat in ensemble['internals']:
        poly = scale_pts(feat['polygon'])
        layer = f'INTERNAL_{feat["type"].upper()}'
        dxf += f'0\nLWPOLYLINE\n8\n{layer}\n62\n3\n100\nAcDbPolyline\n90\n{len(poly)}\n70\n1\n'
        for x, y in poly:
            dxf += f'10\n{x:.6f}\n20\n{y:.6f}\n'
    dxf += '0\nENDSEC\n0\nEOF'
    return dxf


if __name__ == '__main__':
    path = r'C:\Users\HP\Desktop\cao\HPGL\LADIES-BLOUSE1.PLT'
    parser = HpglParser(path)
    ensembles = group_pieces(parser)

    print(f'File: {os.path.basename(path)}')
    print(f'Ensembles found: {len(ensembles)}')
    print()

    for ens in ensembles:
        print(f'Piece {ens["piece_id"]:6s} | Size {ens["size"]:4s} | '
              f'Outer area: {ens["area_mm2"]:.1f} mm\xb2 | '
              f'Internals: {len(ens["internals"])}')
        for feat in ens['internals']:
            a = area(feat['polygon'])
            print(f'    {feat["type"]:12s}  area={a:.1f} mils\xb2  '
                  f'n={len(feat["polygon"])} pts')
    print()

    out_dir = os.path.join(os.path.dirname(__file__), 'output')
    os.makedirs(out_dir, exist_ok=True)

    dxf_all = []
    for ens in ensembles:
        dxf_all.append(ensemble_to_dxf(ens, 'mm'))
    combined = '\n'.join(dxf_all)

    dxf_path = os.path.join(out_dir, 'LADIES-BLOUSE1_ENSEMBLES.dxf')
    with open(dxf_path, 'w') as f:
        f.write(combined)
    print(f'DXF written to {dxf_path}')
