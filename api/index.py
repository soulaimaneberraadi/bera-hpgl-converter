"""BERA Converter - single-file Vercel serverless function (self-contained)."""
import http.server, urllib.parse, os, io, json, re, math, struct, zipfile
from http.server import BaseHTTPRequestHandler
from typing import List, Tuple, Dict, Optional
from collections import defaultdict


"""HPGL/PLT plotter file parser - extracts piece geometries as clean polygons with labels."""



class HpglParser:
    """Parse HPGL/PLT plotter files and extract piece geometries."""

    def __init__(self, path: Optional[str] = None, content: str = None):
        self.path = path
        if content is not None:
            self.content = content
        elif path:
            with open(path, 'r', encoding='latin-1') as f:
                self.content = f.read()
        else:
            self.content = ''
        self.commands = []
        self.pieces = []
        self.labels = []   # [(x, y, size, name, full)] — label with its position
        self.info = {}

    def tokenize(self):
        raw = self.content
        cmds = []
        i = 0
        while i < len(raw):
            m = re.match(r'([A-Z]{2})', raw[i:])
            if not m:
                i += 1
                continue
            cmd = m.group(1)
            i += m.end()
            if cmd == 'LB':
                end = raw.find('\x03', i)
                if end < 0:
                    args = raw[i:]
                    i = len(raw)
                else:
                    args = raw[i:end]
                    i = end + 1
                cmds.append((cmd, args.strip()))
                continue
            args = ''
            while i < len(raw):
                ch = raw[i]
                if ch == ';':
                    i += 1
                    break
                if ch == '\x03':
                    break
                if re.match(r'[A-Z]{2}', raw[i:]):
                    break
                args += ch
                i += 1
            cmds.append((cmd, args.strip().lstrip(',')))
        self.commands = cmds
        return cmds

    def parse_coords(self, s: str) -> List[Tuple[int, int]]:
        if not s:
            return []
        parts = re.split(r'[, ]+', s.strip())
        coords = []
        for i in range(0, len(parts) - 1, 2):
            try:
                x = int(parts[i])
                y = int(parts[i+1])
                coords.append((x, y))
            except (ValueError, IndexError):
                pass
        return coords

    def _name_size(self, lbl: str) -> Tuple[str, str]:
        """Extract (size, piece_name) from a plotter label. Two conventions
        are seen in the wild:
          '42 PID COUL 9MIJA 4555 A'  -> size LEADS  -> ('42', 'PID COUL')
          'S DOV 9MIJA A'             -> size LEADS  -> ('S', 'DOV')
          'GARNITEUR 9AMIJA 4555 44'  -> size TRAILS -> ('44', 'GARNITEUR')
        A token is size-like if it's 1-3 digits or S/M/L/X letters. A token is
        part of the model code (not the name) if it has a digit or contains
        'MIJA'. The name is the descriptive words before the model code."""
        toks = lbl.split()
        if not toks:
            return ('', lbl)

        def is_size_tok(t):
            return bool(re.fullmatch(r'\d{1,3}', t)) or \
                   bool(re.fullmatch(r'[SMLX]{1,4}', t, re.IGNORECASE))

        def is_code_tok(t):
            return bool(re.search(r'\d', t)) or 'MIJA' in t.upper()

        size = ''
        body = toks
        if len(toks) >= 2 and is_size_tok(toks[-1]) and \
                any(is_code_tok(t) for t in toks[:-1]):
            size = toks[-1]
            body = toks[:-1]
        elif is_size_tok(toks[0]):
            size = toks[0]
            body = toks[1:]

        name_toks = []
        for t in body:
            if is_code_tok(t):
                break
            name_toks.append(t)
        name = ' '.join(name_toks) if name_toks else (body[0] if body else lbl)
        return (size, name.strip())

    def label_to_tuple(self, s: str) -> Tuple[str, str, str]:
        s = s.strip()
        if not s:
            return ('', '', '')
        m = re.match(r'(\d+)\s+([A-Za-z]+)', s)
        if m:
            return (m.group(1), m.group(2), s)
        return ('', s, s)

    def parse(self):
        self.tokenize()
        pieces = []
        current_label = ''
        current_size = ''
        current_poly = []
        pen_down = False
        pos = (0, 0)
        pending_label = ''

        for cmd, args in self.commands:
            if cmd == 'IN':
                pass
            elif cmd in ('IP', 'SC'):
                parts = args.split(',')
                parts = [p for p in parts if p.strip()]
                if len(parts) >= 4:
                    try:
                        self.info[cmd.lower()] = tuple(int(p) for p in parts[:4])
                    except ValueError:
                        pass
            elif cmd in ('SP', 'VS', 'CS', 'SS', 'LT', 'DI', 'SI', 'LO'):
                pass
            elif cmd == 'PU':
                coords = self.parse_coords(args)
                if coords:
                    pos = coords[-1]
                pen_down = False
                if current_poly:
                    pieces.append({
                        'label': pending_label or current_label,
                        'polygon': current_poly,
                    })
                    current_poly = []
                    pending_label = ''
            elif cmd == 'PD':
                coords = self.parse_coords(args)
                if coords:
                    if not current_poly:
                        current_poly.append(pos)
                    current_poly.extend(coords)
                    pos = coords[-1]
                elif not current_poly:
                    current_poly.append(pos)
                pen_down = True
            elif cmd == 'PA':
                coords = self.parse_coords(args)
                if coords:
                    if pen_down or current_poly:
                        if not current_poly:
                            current_poly.append(pos)
                        current_poly.extend(coords)
                    pos = coords[-1]
            elif cmd == 'LB':
                current_label = args.strip()
                if current_label and 'MODELE' not in current_label.upper() \
                        and 'LA=' not in current_label and 'LO=' not in current_label:
                    _sz, _nm = self._name_size(current_label)
                    self.labels.append((pos[0], pos[1], _sz, _nm, current_label))
                t = self.label_to_tuple(current_label)
                pending_label = t[2]
                if current_poly:
                    pieces.append({
                        'label': pending_label,
                        'polygon': current_poly,
                    })
                    current_poly = []
                    pending_label = ''

        if current_poly:
            pieces.append({
                'label': pending_label or current_label,
                'polygon': current_poly,
            })

        # Filter and organize pieces
        filtered = []
        for p in pieces:
            poly = p['polygon']
            if len(poly) < 3:
                continue
            lbl = p['label']
            if 'MODELE' in lbl.upper() or 'LA=' in lbl or 'LO=' in lbl:
                continue
            size, pid = self._name_size(lbl)

            if len(poly) <= 5:
                continue

            filtered.append({
                'label': lbl, 'piece_id': pid, 'size': size,
                'polygon': poly,
            })

        self.pieces = filtered
        return filtered

    def get_piece_ids(self) -> List[str]:
        ids = set()
        for p in self.pieces:
            if p['piece_id']:
                ids.add(p['piece_id'])
        return sorted(ids)

    def get_pieces_by_id(self, pid: str) -> List[dict]:
        return [p for p in self.pieces if p['piece_id'] == pid]

    def mils_to(self, v, unit='mm'):
        if unit == 'mm':
            return v * 0.0254
        elif unit == 'cm':
            return v * 0.00254
        elif unit == 'inch':
            return v * 0.001
        return float(v)

    def piece_to_dxf(self, piece: dict, unit: str = 'mm') -> str:
        poly = piece['polygon']
        if not poly:
            return ''
        pts = [(self.mils_to(x, unit), self.mils_to(y, unit)) for x, y in poly]
        dxf = '0\nSECTION\n2\nENTITIES\n'
        dxf += f'0\nLWPOLYLINE\n8\n{piece["piece_id"]}\n62\n1\n100\nAcDbPolyline\n90\n{len(pts)}\n70\n1\n'
        for x, y in pts:
            dxf += f'10\n{x:.6f}\n20\n{y:.6f}\n'
        dxf += '0\nENDSEC\n0\nEOF'
        return dxf

    def all_to_dxf(self, unit='mm') -> str:
        dxf = '0\nSECTION\n2\nENTITIES\n'
        for p in self.pieces:
            poly = p['polygon']
            if len(poly) < 3:
                continue
            pts = [(self.mils_to(x, unit), self.mils_to(y, unit)) for x, y in poly]
            dxf += f'0\nLWPOLYLINE\n8\n{p["piece_id"]}\n62\n1\n100\nAcDbPolyline\n90\n{len(pts)}\n70\n1\n'
            for x, y in pts:
                dxf += f'10\n{x:.6f}\n20\n{y:.6f}\n'
        dxf += '0\nENDSEC\n0\nEOF'
        return dxf

    def pieces_to_svg(self, width=1200, height=900) -> str:
        if not self.pieces:
            return '<svg/>'
        all_pts = [pt for p in self.pieces for pt in p['polygon']]
        xs = [p[0] for p in all_pts] or [0, 100]
        ys = [p[1] for p in all_pts] or [0, 100]
        mnx, mxx = min(xs), max(xs)
        mny, mxy = min(ys), max(ys)
        cx = (mnx + mxx) / 2
        cy = (mny + mxy) / 2
        rx = (mxx - mnx) or 1
        ry = (mxy - mny) or 1
        sc = min(width / rx, height / ry) * 0.85

        def tr(x, y):
            return ((x - cx) * sc + width/2, (y - cy) * sc + height/2)

        colors = ['#e74c3c','#3498db','#2ecc71','#f39c12','#9b59b6','#1abc9c']
        paths = []
        for p in self.pieces:
            poly = p['polygon']
            if len(poly) < 2:
                continue
            pid = p['piece_id']
            ci = abs(hash(pid)) % len(colors)
            col = colors[ci]
            sx, sy = tr(poly[0][0], poly[0][1])
            d = f'M {sx:.2f} {sy:.2f}'
            for pt in poly[1:]:
                px, py = tr(pt[0], pt[1])
                d += f' L {px:.2f} {py:.2f}'
            d += ' Z'
            paths.append((d, col, pid, poly[0]))

        layers = ''
        for d, c, pid, origin in paths:
            layers += f'  <path d="{d}" fill="{c}" fill-opacity="0.25" stroke="{c}" stroke-width="1.5" />\n'
            ox, oy = tr(origin[0], origin[1])
            layers += f'  <text x="{ox}" y="{oy}" fill="{c}" font-size="9" font-family="monospace">{pid}</text>\n'

        stats = ''
        if 'ip' in self.info: stats += f'IP={self.info["ip"]} | '
        stats += f'Pieces: {len(self.pieces)}'
        hdr = f'<text x="10" y="20" font-family="monospace" font-size="12">{os.path.basename(self.path or "?")} | {stats}</text>\n'

        return f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">\n{hdr}{layers}</svg>'

"""Group HPGL sub-polygons into piece ensembles — outer contours + internal features."""



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


_SIZE_ORDER = ['XXXS', 'XXS', 'XS', 'S', 'M', 'L', 'XL', 'XXL', 'XXXL', 'XXXXL']


def size_sort_key(s):
    """Sort garment sizes correctly: letter sizes (XS..XXXXL) by their natural
    order, numeric sizes (34, 36, 42...) numerically, anything else falls back
    to plain string order."""
    su = str(s).upper()
    if su in _SIZE_ORDER:
        return (0, _SIZE_ORDER.index(su), '')
    try:
        return (1, float(su), '')
    except ValueError:
        return (2, 0.0, su)


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
        sizes = sorted(bysz.keys(), key=size_sort_key)
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


def build_astm_grade_data(ensembles, n=64):
    """Build genuine ANSI/AAMA grade-rule data: one BASE-SIZE boundary per
    piece plus a GLOBAL sequential rule table (rule N -> {size: (dx, dy)}).

    This mirrors the real structure found in a Gerber/Lectra-exported
    DXF+.RUL pair (reverse-engineered from a genuine sample): each boundary
    vertex gets a POINT + TEXT '# N' marker on layer 2, numbered sequentially
    across ALL pieces in file order; 'RULE: DELTA N' in the companion .RUL
    table holds that point's (dx, dy) offset from the base size, per size.
    Notches reuse their nearest boundary point's rule (they were found
    coincident with a boundary vertex in the reference sample).

    Returns (pieces, rules, singles):
      pieces: [{'name','sizes','base_size','boundary','point_rules',
                'notches','notch_rules','internals'}, ...]
      rules:  {rule_num: {size: (dx, dy)}}   (mils, same unit as input)
      singles: ensembles that couldn't be graded (only one size present)
    """
    byname = defaultdict(lambda: defaultdict(list))
    singles = []
    for e in ensembles:
        nm = str(e.get('piece_id', '') or '')
        sz = str(e.get('size', '') or '')
        if nm and sz:
            byname[nm][sz].append(e)
        else:
            singles.append(e)

    pieces = []
    rules = {}
    next_rule = 1

    for nm, bysz in byname.items():
        sizes = sorted(bysz.keys(), key=size_sort_key)
        if len(sizes) < 2:
            for e in bysz[sizes[0]]:
                singles.append(e)
            continue
        reps = {sz: max(lst, key=lambda e: area(e['outer']))
                for sz, lst in bysz.items()}
        base_sz = sizes[0]
        base = reps[base_sz]
        npts = n if len(base['outer']) > n else max(12, len(base['outer']))
        boundary = resample(base['outer'], npts)
        aligned = {base_sz: boundary}
        for sz in sizes[1:]:
            al, _resid = align_to(reps[sz]['outer'], base['outer'], npts)
            aligned[sz] = al

        point_rules = []
        for i in range(len(boundary)):
            rid = next_rule
            next_rule += 1
            bx, by = boundary[i]
            rules[rid] = {base_sz: (0.0, 0.0)}
            for sz in sizes[1:]:
                ax, ay = aligned[sz][i]
                rules[rid][sz] = (ax - bx, ay - by)
            point_rules.append(rid)

        notch_rules = []
        for nx, ny in base.get('notches', []):
            j = min(range(len(boundary)),
                    key=lambda k: math.hypot(boundary[k][0] - nx,
                                             boundary[k][1] - ny))
            notch_rules.append(point_rules[j])

        pieces.append({
            'name': nm,
            'sizes': sizes,
            'base_size': base_sz,
            'boundary': boundary,
            'point_rules': point_rules,
            'notches': base.get('notches', []),
            'notch_rules': notch_rules,
            'internals': base.get('internals', []),
        })

    return pieces, rules, singles

# ---- module aliases expected by the web layer ----
_group_pieces = group_pieces
_poly_area = area
HAS_GROUPER = True
HAS_GRADATION = True


"""BERA Converter Web — BERAMETHODE style — Full feature"""







PORT = int(os.environ.get('PORT', 9000))


# ── helpers ──────────────────────────────────────────────────────────────

def poly_area(poly):
    n = len(poly)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def mils_to(v, unit='mm'):
    if unit == 'mm':
        return v * 0.0254
    elif unit == 'cm':
        return v * 0.00254
    elif unit == 'inch':
        return v * 0.001
    return float(v)


def _unit_scale(unit):
    if unit == 'mm':
        return 0.0254
    elif unit == 'cm':
        return 0.00254
    elif unit == 'inch':
        return 0.001
    return 0.0254


def auto_detect_format(content):
    head = content[:4096]
    fmt = 'HPGL'
    enc = 'latin-1'
    try:
        head.encode('latin-1')
    except Exception:
        enc = 'utf-8'
    units = 'mm'
    sc_m = re.search(r'SC\s*([^;]+)', head, re.IGNORECASE)
    if sc_m:
        parts = re.findall(r'[\d.]+', sc_m.group(1))
        if len(parts) >= 4:
            try:
                vals = [int(p) for p in parts[:4]]
                avg = (abs(vals[1] - vals[0]) + abs(vals[3] - vals[2])) / 2
                if avg <= 1000:
                    units = 'mm'
                elif avg <= 10000:
                    units = 'cm'
            except ValueError:
                pass
    ip_m = re.search(r'IP\s*([^;]+)', head, re.IGNORECASE)
    if ip_m:
        parts = re.findall(r'[\d.]+', ip_m.group(1))
        if len(parts) >= 4:
            try:
                vals = [int(p) for p in parts[:4]]
                w = abs(vals[1] - vals[0])
                if 1000 <= w <= 1050:
                    units = 'inch'
            except ValueError:
                pass
    return fmt, enc, units


def bbox_of_pieces(pieces):
    xs = [pt[0] for p in pieces for pt in p['polygon']]
    ys = [pt[1] for p in pieces for pt in p['polygon']]
    if not xs:
        return {'min_x': 0, 'min_y': 0, 'max_x': 0, 'max_y': 0,
                'width': 0, 'height': 0, 'width_mm': 0, 'height_mm': 0}
    mnx, mxx = min(xs), max(xs)
    mny, mxy = min(ys), max(ys)
    return {'min_x': mnx, 'min_y': mny, 'max_x': mxx, 'max_y': mxy,
            'width': mxx - mnx, 'height': mxy - mny,
            'width_mm': (mxx - mnx) * 0.0254,
            'height_mm': (mxy - mny) * 0.0254}


def group_flat_pieces(pieces):
    groups = {}
    for p in pieces:
        key = (p['piece_id'], p.get('size', ''))
        groups.setdefault(key, []).append(p)
    ensembles = []
    for (pid, sz), polys in groups.items():
        if not polys:
            continue
        by_area = sorted(polys, key=lambda x: poly_area(x['polygon']), reverse=True)
        outer_poly = by_area[0]['polygon']
        outer_a = poly_area(outer_poly)
        internals = []
        for p in by_area[1:]:
            feat_poly = p['polygon']
            if poly_area(feat_poly) < 1:
                continue
            a = poly_area(feat_poly)
            ftype = 'notch' if a < 500 else 'internal'
            internals.append({'polygon': feat_poly, 'type': ftype})
        ensembles.append({
            'piece_id': pid,
            'size': sz,
            'outer': outer_poly,
            'internals': internals,
            'area_mm2': outer_a * 0.0254 * 0.0254,
        })
    return ensembles


def add_notch_counts(ensembles):
    for ens in ensembles:
        ens['notch_count'] = len(ens.get('notches', [])) + sum(
            1 for f in ens.get('internals', []) if f.get('type') == 'notch'
        )


def parse_marker_header(content):
    """Reverse-engineer the marker header (LBPLCT metadata line) written by the
    plotter: model, size/qty, length, efficiency (rendement), width (laize)."""
    m = re.search(
        r'PLCT:(.*?)MODELE:(.*?)/QTE:(.*?);.*?LO=([^ ]+ ?[^ ;]*)\s*'
        r'E=([\d.]+)%\s*LA=([\d.]+)CM\s*DATE:\s*([\d/.\-]+)',
        content)
    if not m:
        return None
    qte = m.group(3).strip()
    sm = re.search(r':([A-Za-z0-9]+)\s*/\s*(\d+)\s*$', qte)
    return {
        'placement': m.group(1).strip(),
        'modele': m.group(2).strip(),
        'qte': qte,
        'size': sm.group(1) if sm else '',
        'quantity': sm.group(2) if sm else '',
        'longueur': m.group(4).strip(),
        'rendement': m.group(5),
        'laize': m.group(6),
        'date': m.group(7),
    }


def sizes_found(ensembles):
    return sorted(set(e['size'] for e in ensembles if e.get('size')))


# ── Export functions ──────────────────────────────────────────────────────

def export_dxf(parser, unit='mm', outer_only=False, include_notches=True,
               include_labels=True):
    scale = _unit_scale(unit)
    dxf = '0\nSECTION\n2\nENTITIES\n'
    pieces = parser.pieces
    if outer_only and HAS_GROUPER:
        ensembles = _group_pieces(parser)
        for ens in ensembles:
            outer = ens.get('outer', [])
            if len(outer) < 3:
                continue
            pts = [(x * scale, y * scale) for x, y in outer]
            dxf += '0\nLWPOLYLINE\n8\n' + ens['piece_id'] + '\n62\n1\n100\nAcDbPolyline\n90\n' + str(len(pts)) + '\n70\n1\n'
            for x, y in pts:
                dxf += f'10\n{x:.6f}\n20\n{y:.6f}\n'
            if include_labels:
                cx = sum(p[0] for p in outer) / len(outer) * scale
                cy = sum(p[1] for p in outer) / len(outer) * scale
                dxf += f'0\nTEXT\n8\n{ens["piece_id"]}\n10\n{cx:.6f}\n20\n{cy:.6f}\n40\n{2*scale}\n1\n{ens["piece_id"]}\n'
            if include_notches:
                for feat in ens.get('internals', []):
                    if feat.get('type') == 'notch':
                        poly = feat['polygon']
                        pts = [(x * scale, y * scale) for x, y in poly]
                        dxf += '0\nLWPOLYLINE\n8\n' + ens['piece_id'] + '_NOTCH\n62\n3\n100\nAcDbPolyline\n90\n' + str(len(pts)) + '\n70\n0\n'
                        for x, y in pts:
                            dxf += f'10\n{x:.6f}\n20\n{y:.6f}\n'
        dxf += '0\nENDSEC\n0\nEOF'
        return dxf
    if outer_only and not HAS_GROUPER:
        by_id = {}
        for p in pieces:
            by_id.setdefault(p['piece_id'], []).append(p)
        pieces = [max(v, key=lambda x: poly_area(x['polygon'])) for v in by_id.values()]
    for p in pieces:
        poly = p['polygon']
        if len(poly) < 3:
            continue
        pts = [(x * scale, y * scale) for x, y in poly]
        dxf += '0\nLWPOLYLINE\n8\n' + p['piece_id'] + '\n62\n1\n100\nAcDbPolyline\n90\n' + str(len(pts)) + '\n70\n1\n'
        for x, y in pts:
            dxf += f'10\n{x:.6f}\n20\n{y:.6f}\n'
    dxf += '0\nENDSEC\n0\nEOF'
    return dxf


def export_svg(parser, unit='mm', outer_only=False, include_notches=True,
               include_labels=True):
    scale = _unit_scale(unit)
    all_pts = [pt for p in parser.pieces for pt in p['polygon']]
    if not all_pts:
        return '<svg/>'
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    mnx, mxx = min(xs), max(xs)
    mny, mxy = min(ys), max(ys)
    cx = (mnx + mxx) / 2
    cy = (mny + mxy) / 2
    rx = (mxx - mnx) or 1
    ry = (mxy - mny) or 1
    W, H = 1200, 900
    sc = min(W / rx, H / ry) * 0.85

    def tr(x, y):
        return ((x - cx) * sc + W / 2, (y - cy) * sc + H / 2)

    colors = ['#6366f1', '#059669', '#d97706', '#dc2626', '#7c3aed',
              '#0891b2', '#db2777', '#2563eb', '#84cc16', '#f97316']
    paths = []
    pieces_data = parser.pieces
    if outer_only and HAS_GROUPER:
        ensembles = _group_pieces(parser)
        pieces_data = []
        for ens in ensembles:
            outer = ens.get('outer', [])
            if len(outer) >= 3:
                pieces_data.append({'piece_id': ens['piece_id'],
                                    'size': ens.get('size', ''),
                                    'polygon': outer})
            if include_notches:
                for feat in ens.get('internals', []):
                    if feat.get('type') == 'notch':
                        pieces_data.append({'piece_id': ens['piece_id'] + '_notch',
                                            'size': '', 'polygon': feat['polygon']})
    elif outer_only and not HAS_GROUPER:
        by_id = {}
        for p in parser.pieces:
            by_id.setdefault(p['piece_id'], []).append(p)
        pieces_data = [max(v, key=lambda x: poly_area(x['polygon'])) for v in by_id.values()]
    for p in pieces_data:
        poly = p['polygon']
        if len(poly) < 2:
            continue
        pid = p['piece_id']
        ci = abs(hash(pid)) % len(colors)
        col = colors[ci]
        sx, sy = tr(poly[0][0], poly[0][1])
        d = f'M {sx:.2f} {sy:.2f}'
        for pt in poly[1:]:
            px, py = tr(pt[0], pt[1])
            d += f' L {px:.2f} {py:.2f}'
        d += ' Z'
        paths.append((d, col, pid, poly))
    layers = ''
    for d, c, pid, poly in paths:
        is_notch = '_notch' in pid
        opacity = '0.15' if is_notch else '0.25'
        lw = '0.8' if is_notch else '1.5'
        layers += f'  <path d="{d}" fill="{c}" fill-opacity="{opacity}" stroke="{c}" stroke-width="{lw}" />\n'
        if include_labels and not is_notch:
            ox = sum(p[0] for p in poly) / len(poly)
            oy = sum(p[1] for p in poly) / len(poly)
            ox, oy = tr(ox, oy)
            layers += f'  <text x="{ox}" y="{oy}" fill="{c}" font-size="11" font-family="Cairo,sans-serif" text-anchor="middle" font-weight="bold">{pid}</text>\n'
    fname = getattr(parser, 'path', '?') or '?'
    stats = f'Pieces: {len(parser.pieces)}'
    hdr = f'<text x="10" y="20" font-family="monospace" font-size="12">{os.path.basename(fname)} | {stats}</text>\n'
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">\n{hdr}{layers}</svg>'


def export_plt(parser, unit='mm', outer_only=False, include_notches=True,
               include_labels=True):
    lines = ['IN;']
    scale = _unit_scale(unit)
    pieces = parser.pieces
    if outer_only and HAS_GROUPER:
        ensembles = _group_pieces(parser)
        pieces = []
        for ens in ensembles:
            outer = ens.get('outer', [])
            if len(outer) >= 2:
                pieces.append({'piece_id': ens['piece_id'],
                               'size': ens.get('size', ''),
                               'polygon': outer})
            if include_notches:
                for feat in ens.get('internals', []):
                    if feat.get('type') == 'notch':
                        pieces.append({'piece_id': ens['piece_id'] + '_NOTCH',
                                       'size': '', 'polygon': feat['polygon']})
    elif outer_only and not HAS_GROUPER:
        by_id = {}
        for p in parser.pieces:
            by_id.setdefault(p['piece_id'], []).append(p)
        pieces = [max(v, key=lambda x: poly_area(x['polygon'])) for v in by_id.values()]
    for pp in pieces:
        poly = pp['polygon']
        if len(poly) < 2:
            continue
        label = pp.get('label', pp.get('piece_id', ''))
        lines.append(f'SP1;PU{poly[0][0]},{poly[0][1]};PD' + ','.join(f'{x},{y}' for x, y in poly) + ';')
        if include_labels and label:
            lines.append(f'LB{label}\x03')
        lines.append('PU;')
    return '\n'.join(lines)


def _clean_pts(pts, eps=0.05):
    """Drop consecutive near-duplicate vertices (they become zero-length
    segments that make Gerber/CAD importers warn) and any closing duplicate."""
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    for x, y in pts[1:]:
        px, py = out[-1]
        if abs(x - px) > eps or abs(y - py) > eps:
            out.append((x, y))
    if len(out) > 2 and abs(out[0][0] - out[-1][0]) <= eps \
            and abs(out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out


def _aama_polyline(layer, pts, closed):
    """Old-style (R12) POLYLINE/VERTEX/SEQEND on a numbered ASTM layer."""
    pts = _clean_pts(pts)
    out = [f'0\nPOLYLINE\n8\n{layer}\n66\n1\n70\n{1 if closed else 0}']
    for x, y in pts:
        out.append(f'0\nVERTEX\n8\n{layer}\n10\n{x:.4f}\n20\n{y:.4f}\n30\n0.0')
    out.append(f'0\nSEQEND\n8\n{layer}')
    return out


def _sanitize_name(raw, used):
    """A DXF block name Gerber can show as the piece name: keep readable chars,
    cap length, keep it unique."""
    s = re.sub(r'[^A-Za-z0-9 _.\-]', '', str(raw)).strip()
    s = re.sub(r'\s+', ' ', s)[:28] or 'PIECE'
    base, k = s, 2
    while s in used:
        s = f'{base}_{k}'
        k += 1
    used.add(s)
    return s


def export_dxf_aama(ensembles, unit='mm', model='', base_size=''):
    """ASTM D6673 / AAMA DXF for Gerber AccuMark PDS, Lectra, Optitex, Assyst.

    Each PIECE is a DXF BLOCK whose NAME is the real piece name (Gerber shows
    that as the piece name). Geometry is on standard NUMBERED layers; ENTITIES
    INSERTs each block; ASTM piece metadata (name/category/size) is XDATA.
      Layer 1 = boundary   4 = notches   7 = grain   8 = internal
      11 = cutout   13 = drill   15 = piece-name annotation
    """
    scale = _unit_scale(unit)
    category = re.sub(r'[^A-Za-z0-9 _.\-]', '', str(model)).strip()[:40]
    insunits = {'mm': '4', 'cm': '5', 'inch': '1'}.get(unit, '4')
    layers = [('0', 7), ('1', 7), ('4', 1), ('7', 5), ('8', 3),
              ('11', 4), ('13', 6), ('15', 2)]

    L = ['0\nSECTION\n2\nHEADER',
         '9\n$ACADVER\n1\nAC1009',
         f'9\n$INSUNITS\n70\n{insunits}',
         '9\n$MEASUREMENT\n70\n1',
         '0\nENDSEC']
    # TABLES: register ASTM_DXF app for XDATA, and all layers
    L.append('0\nSECTION\n2\nTABLES')
    L.append('0\nTABLE\n2\nAPPID\n70\n1\n0\nAPPID\n2\nASTM_DXF\n70\n0\n0\nENDTAB')
    L.append(f'0\nTABLE\n2\nLAYER\n70\n{len(layers)}')
    for name, col in layers:
        L.append(f'0\nLAYER\n2\n{name}\n70\n0\n62\n{col}\n6\nCONTINUOUS')
    L.append('0\nENDTAB\n0\nENDSEC')

    # BLOCKS: one block per piece
    blocks = []
    used_names = set()
    L.append('0\nSECTION\n2\nBLOCKS')
    for idx, ens in enumerate(ensembles, 1):
        pid = str(ens.get('piece_id', '?'))
        sz = str(ens.get('size', '') or '')
        bname = _sanitize_name(pid or f'PIECE_{idx}', used_names)
        blocks.append(bname)
        L.append(f'0\nBLOCK\n8\n0\n2\n{bname}\n70\n0'
                 f'\n10\n0.0\n20\n0.0\n30\n0.0\n3\n{bname}')
        outer = ens.get('outer', [])
        if len(outer) >= 3:
            pts = [(x * scale, y * scale) for x, y in outer]
            poly = _aama_polyline('1', pts, True)
            # ASTM piece metadata as XDATA on the boundary polyline
            piece_size = sz or base_size
            xd = f'\n1001\nASTM_DXF\n1000\nPIECE NAME;{bname}'
            if category:
                xd += f'\n1000\nPIECE CATEGORY;{category}'
            if piece_size:
                xd += f'\n1000\nSIZE;{piece_size}'
            xd += '\n1000\nQUANTITY;1\n1070\n1'
            poly[0] += xd
            L.extend(poly)
        for feat in ens.get('internals', []):
            fp = feat.get('polygon', [])
            ftype = feat.get('type', 'internal')
            layer = {'notch': '4', 'grain_line': '7', 'cutout': '11',
                     'drill': '13'}.get(ftype, '8')
            if len(fp) >= 2:
                pts = [(x * scale, y * scale) for x, y in fp]
                L.extend(_aama_polyline(layer, pts,
                                        ftype != 'grain_line' and len(fp) > 2))
        # notches (crans) as POINT marks on ASTM layer 4
        for nx, ny in ens.get('notches', []):
            L.append(f'0\nPOINT\n8\n4\n10\n{nx*scale:.4f}'
                     f'\n20\n{ny*scale:.4f}\n30\n0.0')
        if outer:
            cx = sum(p[0] for p in outer) / len(outer) * scale
            cy = sum(p[1] for p in outer) / len(outer) * scale
            L.append(f'0\nTEXT\n8\n15\n10\n{cx:.4f}\n20\n{cy:.4f}'
                     f'\n40\n{5*scale:.4f}\n1\n{bname}')
        L.append('0\nENDBLK\n8\n0')
    L.append('0\nENDSEC')

    # ENTITIES: place each piece block
    L.append('0\nSECTION\n2\nENTITIES')
    for bname in blocks:
        L.append(f'0\nINSERT\n8\n0\n2\n{bname}\n10\n0.0\n20\n0.0\n30\n0.0')
    L.append('0\nENDSEC\n0\nEOF')
    return '\n'.join(L)


def export_dxf_aama_graded(graded, singles, unit='mm', model=''):
    """Graded ASTM D6673 DXF. Each piece is ONE block holding its boundary at
    every size (nested, aligned to a common grade-reference point), so Gerber
    imports it as a gradable piece — one piece carrying all its sizes."""
    scale = _unit_scale(unit)
    insunits = {'mm': '4', 'cm': '5', 'inch': '1'}.get(unit, '4')
    category = re.sub(r'[^A-Za-z0-9 _.\-]', '', str(model)).strip()[:40]
    layers = [('0', 7), ('1', 7), ('4', 1), ('5', 6), ('7', 5),
              ('8', 3), ('11', 4), ('13', 6), ('15', 2)]

    L = ['0\nSECTION\n2\nHEADER', '9\n$ACADVER\n1\nAC1009',
         f'9\n$INSUNITS\n70\n{insunits}', '9\n$MEASUREMENT\n70\n1', '0\nENDSEC']
    L.append('0\nSECTION\n2\nTABLES')
    L.append('0\nTABLE\n2\nAPPID\n70\n1\n0\nAPPID\n2\nASTM_DXF\n70\n0\n0\nENDTAB')
    L.append(f'0\nTABLE\n2\nLAYER\n70\n{len(layers)}')
    for name, col in layers:
        L.append(f'0\nLAYER\n2\n{name}\n70\n0\n62\n{col}\n6\nCONTINUOUS')
    L.append('0\nENDTAB\n0\nENDSEC')

    blocks = []
    used = set()
    L.append('0\nSECTION\n2\nBLOCKS')

    def emit(bname, size_boundaries, base_size, notches):
        L.append(f'0\nBLOCK\n8\n0\n2\n{bname}\n70\n0'
                 f'\n10\n0.0\n20\n0.0\n30\n0.0\n3\n{bname}')
        base = size_boundaries[base_size]
        cx = sum(p[0] for p in base) / len(base) * scale
        cy = sum(p[1] for p in base) / len(base) * scale
        # grade reference point (layer 5)
        L.append(f'0\nPOINT\n8\n5\n10\n{cx:.4f}\n20\n{cy:.4f}\n30\n0.0')
        for sz in sorted(size_boundaries):
            pts = [(x * scale, y * scale) for x, y in size_boundaries[sz]]
            poly = _aama_polyline('1', pts, True)
            xd = f'\n1001\nASTM_DXF\n1000\nPIECE NAME;{bname}'
            if category:
                xd += f'\n1000\nPIECE CATEGORY;{category}'
            xd += f'\n1000\nSIZE;{sz}'
            if sz == base_size:
                xd += '\n1000\nBASE SIZE;1'
            xd += '\n1000\nQUANTITY;1\n1070\n1'
            poly[0] += xd
            L.extend(poly)
        for nx, ny in notches:
            L.append(f'0\nPOINT\n8\n4\n10\n{nx*scale:.4f}'
                     f'\n20\n{ny*scale:.4f}\n30\n0.0')
        L.append(f'0\nTEXT\n8\n15\n10\n{cx:.4f}\n20\n{cy:.4f}'
                 f'\n40\n{5*scale:.4f}\n1\n{bname}')
        L.append('0\nENDBLK\n8\n0')

    for g in graded:
        bname = _sanitize_name(g['name'], used)
        blocks.append(bname)
        emit(bname, g['boundaries'], g['base_size'], g.get('notches', []))
    for e in singles:
        bname = _sanitize_name(str(e.get('piece_id', '') or 'PIECE'), used)
        blocks.append(bname)
        sz = str(e.get('size', '') or '0')
        emit(bname, {sz: e['outer']}, sz, e.get('notches', []))

    L.append('0\nENDSEC')
    L.append('0\nSECTION\n2\nENTITIES')
    for bname in blocks:
        L.append(f'0\nINSERT\n8\n0\n2\n{bname}\n10\n0.0\n20\n0.0\n30\n0.0')
    L.append('0\nENDSEC\n0\nEOF')
    return '\n'.join(L)


def _rul_pair(dx, dy):
    """Format one (dx, dy) grade delta exactly like a genuine Lectra/AAMA
    .RUL file: right-justified 10-char X field, comma, right-justified 9-char
    Y field, no separator before the next pair (reverse-engineered byte-for-
    byte from a real Gerber/Lectra export)."""
    return f'{dx:>10.2f},{dy:>9.2f}'


def build_rul_text(rules, sizes, base_size, table_name='BERA_GRADE',
                   author='BERA Converter', units='METRIC'):
    """Emit an ANSI/AAMA grade-rule table (.RUL) — plain text, industry
    standard, matching exactly what Gerber AccuMark / Lectra export and
    import natively."""
    import datetime
    now = datetime.datetime.now()
    lines = [
        'ANSI/AAMA VERSION: 1.0.0',
        f'AUTHOR: {author}',
        f'CREATION DATE: {now.day}-{now.month}-{now.year}',
        f'CREATION TIME: {now.hour}:{now.minute:02d}',
        f'UNITS: {units}',
        f'GRADE RULE TABLE: {table_name}',
        f'NUMBER OF SIZES: {len(sizes):>2d}',
        'SIZE LIST: ' + ' '.join(sizes) + ' ',
        f'SAMPLE SIZE: {base_size}',
    ]
    for rid in sorted(rules):
        lines.append(f'RULE: DELTA {rid:>2d}')
        pairs = [_rul_pair(*rules[rid].get(sz, (0.0, 0.0))) for sz in sizes]
        for i in range(0, len(pairs), 4):
            lines.append(''.join(pairs[i:i + 4]))
    return '\r\n'.join(lines) + '\r\n'


def export_astm_dxf_and_rul(pieces, rules, unit='mm', model=''):
    """Emit a genuine graded ASTM/AAMA DXF + companion .RUL grade-rule table,
    matching the real Gerber/Lectra export structure byte-for-byte in intent:
    each piece is ONE base-size boundary (layer 1) with numbered grade points
    ('# N' TEXT + POINT on layer 2) referencing 'RULE: DELTA N' in the .RUL
    file. Gerber's own Gradation tools read this natively — no guessed nested
    outlines. Returns (dxf_text, rul_text)."""
    scale = _unit_scale(unit)
    text_h = {'mm': 4.0, 'cm': 0.4, 'inch': 0.16}.get(unit, 4.0)
    insunits = {'mm': '4', 'cm': '5', 'inch': '1'}.get(unit, '4')
    category = re.sub(r'[^A-Za-z0-9 _.\-]', '', str(model)).strip()[:40]
    layers = [('0', 7), ('1', 7), ('2', 1), ('4', 3), ('7', 5), ('8', 3),
              ('11', 4), ('13', 6), ('15', 2)]

    L = ['0\nSECTION\n2\nHEADER', '9\n$ACADVER\n1\nAC1009',
         f'9\n$INSUNITS\n70\n{insunits}', '9\n$MEASUREMENT\n70\n1',
         '0\nENDSEC']
    L.append('0\nSECTION\n2\nTABLES')
    L.append('0\nTABLE\n2\nAPPID\n70\n1\n0\nAPPID\n2\nASTM_DXF\n70\n0\n0\nENDTAB')
    L.append(f'0\nTABLE\n2\nLAYER\n70\n{len(layers)}')
    for name, col in layers:
        L.append(f'0\nLAYER\n2\n{name}\n70\n0\n62\n{col}\n6\nCONTINUOUS')
    L.append('0\nENDTAB\n0\nENDSEC')

    blocks = []
    used = set()
    L.append('0\nSECTION\n2\nBLOCKS')
    for pc in pieces:
        bname = _sanitize_name(pc['name'], used)
        blocks.append(bname)
        L.append(f'0\nBLOCK\n8\n0\n2\n{bname}\n70\n0'
                 f'\n10\n0.0\n20\n0.0\n30\n0.0\n3\n{bname}')

        boundary = pc['boundary']
        pts = [(x * scale, y * scale) for x, y in boundary]
        poly = _aama_polyline('1', pts, True)
        xd = f'\n1001\nASTM_DXF\n1000\nPIECE NAME;{bname}'
        if category:
            xd += f'\n1000\nPIECE CATEGORY;{category}'
        xd += (f'\n1000\nSIZE;{pc["base_size"]}\n1000\nGRADED;'
               + ','.join(pc['sizes']) + '\n1000\nQUANTITY;1\n1070\n1')
        poly[0] += xd
        L.extend(poly)

        for (x, y), rid in zip(boundary, pc['point_rules']):
            px, py = x * scale, y * scale
            L.append(f'0\nPOINT\n8\n2\n10\n{px:.4f}\n20\n{py:.4f}\n30\n0.0')
            L.append(f'0\nTEXT\n8\n2\n10\n{px:.4f}\n20\n{py:.4f}'
                     f'\n40\n{text_h:.4f}\n1\n# {rid}')

        for i, (nx, ny) in enumerate(pc.get('notches', [])):
            px, py = nx * scale, ny * scale
            L.append(f'0\nPOINT\n8\n4\n10\n{px:.4f}\n20\n{py:.4f}'
                     f'\n30\n4.0\n39\n6.0\n50\n0.0')
            if i < len(pc.get('notch_rules', [])):
                rid = pc['notch_rules'][i]
                L.append(f'0\nTEXT\n8\n2\n10\n{px:.4f}\n20\n{py:.4f}'
                         f'\n40\n{text_h:.4f}\n1\n# {rid}')

        cx = sum(p[0] for p in boundary) / len(boundary) * scale
        cy = sum(p[1] for p in boundary) / len(boundary) * scale
        L.append(f'0\nTEXT\n8\n15\n10\n{cx:.4f}\n20\n{cy:.4f}'
                 f'\n40\n{5*scale:.4f}\n1\n{bname}')
        L.append('0\nENDBLK\n8\n0')
    L.append('0\nENDSEC')

    L.append('0\nSECTION\n2\nENTITIES')
    for bname in blocks:
        L.append(f'0\nINSERT\n8\n0\n2\n{bname}\n10\n0.0\n20\n0.0\n30\n0.0')
    L.append('0\nENDSEC\n0\nEOF')
    dxf_text = '\n'.join(L)

    all_sizes = sorted({s for pc in pieces for s in pc['sizes']}, key=size_sort_key)
    base_size = pieces[0]['base_size'] if pieces else ''
    to_out = 1.0 if unit == 'mils' else _unit_scale(unit)
    scaled_rules = {rid: {sz: (dx * to_out, dy * to_out) for sz, (dx, dy) in d.items()}
                    for rid, d in rules.items()}
    units_word = 'ENGLISH' if unit == 'inch' else 'METRIC'
    rul_text = build_rul_text(scaled_rules, all_sizes, base_size,
                              table_name=re.sub(r'[^A-Za-z0-9_]', '_', category or 'MODEL')[:40] or 'MODEL',
                              units=units_word)
    return dxf_text, rul_text


def export_csv_report(ensembles, marker, unit='mm'):
    """Reverse-engineering report (CSV): marker header + piece table + notch
    table. UTF-8 with BOM so Excel opens Arabic/accents correctly."""
    to_cm = 0.00254  # mils -> cm
    rows = ['BERA Converter - Reverse Engineering Report']
    if marker:
        rows += [
            f'Placement,{marker.get("placement", "")}',
            f'Model,{marker.get("modele", "")}',
            f'Size/Qty,{marker.get("qte", "")}',
            f'Length,{marker.get("longueur", "")}',
            f'Laize(cm),{marker.get("laize", "")}',
            f'Efficiency(%),{marker.get("rendement", "")}',
            f'Date,{marker.get("date", "")}',
        ]
    total_n = sum(len(e.get('notches', [])) for e in ensembles)
    rows += [f'Total Pieces,{len(ensembles)}', f'Total Notches,{total_n}', '']
    rows.append('Piece,Size,Points,Area_cm2,Notches,Width_cm,Height_cm')
    for e in ensembles:
        outer = e.get('outer', [])
        xs = [p[0] for p in outer]
        ys = [p[1] for p in outer]
        w = (max(xs) - min(xs)) * to_cm if xs else 0
        h = (max(ys) - min(ys)) * to_cm if ys else 0
        name = str(e.get('piece_id', '')).replace(',', ' ')
        rows.append(f'{name},{e.get("size", "")},{len(outer)},'
                    f'{e.get("area_mm2", 0) / 100:.1f},'
                    f'{len(e.get("notches", []))},{w:.1f},{h:.1f}')
    rows += ['', 'Notch Detail (piece,x_mm,y_mm)']
    for e in ensembles:
        name = str(e.get('piece_id', '')).replace(',', ' ')
        for nx, ny in e.get('notches', []):
            rows.append(f'{name},{nx * 0.0254:.1f},{ny * 0.0254:.1f}')
    return '\r\n'.join(rows)


# ── HTML Template ────────────────────────────────────────────────────────

TEMPLATE = r"""<!DOCTYPE html>
<html lang="ar" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BERA Converter — HPGL DXF SVG PLT</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@300..700&display=swap" rel="stylesheet">
<script src="https://cdn.tailwindcss.com">
</script>
<script>
tailwind.config={
  darkMode:'class',
  theme:{extend:{fontFamily:{sans:['Cairo','system-ui','sans-serif']}}}
}
</script>
<style>
body{direction:rtl;font-family:'Cairo',sans-serif;
     background:
       radial-gradient(1100px 500px at 100% -5%, #ecfdf5 0%, transparent 55%),
       radial-gradient(900px 480px at 0% 0%, #eef2ff 0%, transparent 50%),
       #f8fafc;}
input,select,button,textarea{font-family:inherit}
#dropZone{direction:ltr}
#dropZone *{direction:rtl}
.card{box-shadow:0 1px 2px rgba(15,23,42,.04),0 8px 24px -16px rgba(15,23,42,.18)}
@keyframes fadeUp{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.fade{animation:fadeUp .35s ease both}
</style>
</head>
<body class="min-h-screen text-slate-900">

<!-- Header -->
<header class="h-14 sticky top-0 bg-white/70 backdrop-blur-xl border-b border-slate-200/60 z-50 px-5 flex items-center gap-3">
  <span class="w-8 h-8 rounded-lg bg-gradient-to-br from-emerald-500 to-indigo-600 flex items-center justify-center shadow-sm shrink-0">
    <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="white" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M4 5h11l5 5v9a0 0 0 0 1 0 0H4z"/><path d="M15 5v5h5"/><path d="M8 13h6M8 16h4"/>
    </svg>
  </span>
  <span class="font-extrabold tracking-tight text-[16px] leading-none">
    <span class="text-slate-900">BERA</span><span class="text-emerald-600">Converter</span>
  </span>
  <span class="hidden sm:inline text-[10px] text-slate-400 font-medium mr-1">HPGL → DXF · SVG · PLT · AAMA</span>
  <span class="mr-auto inline-flex items-center gap-1.5 text-[10px] font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200/70 rounded-full px-2.5 py-1">
    <span class="w-1.5 h-1.5 rounded-full bg-emerald-500"></span>Gerber / PDS Ready
  </span>
</header>

<main class="max-w-[1100px] mx-auto p-4 sm:p-5 space-y-4">

<!-- ═══════ Upload Section ═══════ -->
<div class="bg-white rounded-xl border border-slate-200 shadow-sm">
  <div class="px-5 h-10 flex items-center border-b border-slate-100">
    <span class="text-[11px] font-medium text-slate-500 uppercase tracking-wide">رفع ملف</span>
  </div>
  <div class="p-5 space-y-3">
    <!-- Drop zone -->
    <div id="dropZone"
         onclick="document.getElementById('fileInput').click()"
         class="border-2 border-dashed border-slate-200 rounded-lg p-8 text-center cursor-pointer transition-colors hover:border-indigo-400 hover:bg-indigo-50/30">
      <div class="text-slate-300 text-3xl mb-2">+</div>
      <p class="text-[13px] text-slate-500">اسحب ملف HPGL/PLT أو مجلد كامل</p>
      <p class="text-[10px] text-slate-400 mt-1">.plt .PLT .hpgl .HPGL</p>
    </div>
    <input type="file" id="fileInput" accept=".plt,.PLT,.hpgl,.HPGL" webkitdirectory class="hidden">

    <!-- File list (multiple files from folder) -->
    <div id="fileList" class="hidden space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-[12px] font-medium text-slate-600" id="fileCountLabel"></span>
        <button onclick="batchConvert()"
                class="h-7 px-3 rounded-md text-[11px] font-medium bg-slate-900 text-white hover:bg-slate-800 transition-colors">
          تحويل الكل
        </button>
      </div>
      <div class="max-h-[200px] overflow-y-auto rounded-lg border border-slate-100 divide-y divide-slate-100 text-[12px]" id="fileListItems"></div>
    </div>

    <!-- Info bar -->
    <div id="fileInfo" class="hidden">
      <div class="flex flex-wrap gap-x-5 gap-y-1 text-[12px] bg-slate-50/60 rounded-lg px-4 py-2.5 border border-slate-100">
        <span class="text-slate-400 font-medium">الاسم</span>
        <span id="fn" class="text-slate-700 font-semibold"></span>
        <span class="text-slate-400 font-medium mr-3">الحجم</span>
        <span id="fs" class="text-slate-700 tabular-nums"></span>
        <span class="text-slate-400 font-medium mr-3">القطع</span>
        <span id="pc" class="text-slate-700 tabular-nums"></span>
        <span class="text-slate-400 font-medium mr-3">الوحدات</span>
        <span id="un" class="text-slate-700 tabular-nums"></span>
        <span class="text-slate-400 font-medium mr-3">الصيغة</span>
        <span id="fmtDetected" class="text-slate-700 tabular-nums"></span>
      </div>
    </div>
  </div>
</div>

<!-- ═══════ Compare two files ═══════ -->
<div class="bg-white rounded-xl border border-slate-200 shadow-sm">
  <div onclick="toggleCompare()" class="px-5 h-10 flex items-center justify-between border-b border-slate-100 cursor-pointer select-none hover:bg-slate-50/40 transition-colors">
    <span class="text-[11px] font-medium text-slate-500 uppercase tracking-wide">مقارنة ملفّين (الفروق)</span>
    <span id="cmpArrow" class="text-slate-400 text-[10px]">▼</span>
  </div>
  <div id="comparePanel" class="hidden p-5 space-y-3">
    <div class="flex flex-wrap items-center gap-3 text-[12px]">
      <label class="flex items-center gap-2">
        <span class="text-[11px] font-medium text-slate-500">ملف A</span>
        <input type="file" id="cmpA" accept=".plt,.PLT,.hpgl,.HPGL" class="text-[11px]">
      </label>
      <label class="flex items-center gap-2">
        <span class="text-[11px] font-medium text-slate-500">ملف B</span>
        <input type="file" id="cmpB" accept=".plt,.PLT,.hpgl,.HPGL" class="text-[11px]">
      </label>
      <button onclick="doCompare()" class="h-8 px-4 rounded-md text-[12px] font-medium bg-indigo-600 text-white hover:bg-indigo-700 transition-colors">قارن</button>
    </div>
    <div id="cmpResult"></div>
  </div>
</div>

<!-- ═══════ Canvas Preview ═══════ -->
<div id="previewSection" class="hidden space-y-4">

  <!-- Marker header (reverse-engineered) -->
  <div id="markerCard" class="hidden bg-gradient-to-l from-emerald-50/70 to-indigo-50/50 rounded-xl border border-emerald-200/60 shadow-sm">
    <div class="px-5 h-10 flex items-center gap-2 border-b border-emerald-100/70">
      <span class="text-[11px] font-bold text-emerald-700 uppercase tracking-wide">بيانات المخطط · هندسة عكسية</span>
    </div>
    <div class="p-4 grid grid-cols-2 sm:grid-cols-4 gap-x-5 gap-y-2.5 text-[12px]">
      <div><div class="text-slate-400 text-[10px]">الموديل</div><div id="mkModel" class="text-slate-800 font-semibold"></div></div>
      <div><div class="text-slate-400 text-[10px]">المقاس / الكمية</div><div id="mkQte" class="text-slate-800 font-semibold"></div></div>
      <div><div class="text-slate-400 text-[10px]">القطع</div><div id="mkPieces" class="text-slate-800 font-semibold tabular-nums"></div></div>
      <div><div class="text-slate-400 text-[10px]">الإشارات (crans)</div><div id="mkNotches" class="text-emerald-700 font-bold tabular-nums"></div></div>
      <div><div class="text-slate-400 text-[10px]">الطول</div><div id="mkLen" class="text-slate-800 font-semibold tabular-nums"></div></div>
      <div><div class="text-slate-400 text-[10px]">العرض (laize)</div><div id="mkLaize" class="text-slate-800 font-semibold tabular-nums"></div></div>
      <div><div class="text-slate-400 text-[10px]">المردود</div><div id="mkEff" class="text-slate-800 font-semibold tabular-nums"></div></div>
      <div><div class="text-slate-400 text-[10px]">التاريخ</div><div id="mkDate" class="text-slate-800 font-semibold tabular-nums"></div></div>
    </div>
  </div>

  <div class="bg-white rounded-xl border border-slate-200 shadow-sm">
    <div class="px-5 h-10 flex items-center border-b border-slate-100">
      <span class="text-[11px] font-medium text-slate-500 uppercase tracking-wide">معاينة</span>
    </div>
    <div class="bg-slate-50/60 rounded-b-xl overflow-hidden">
      <canvas id="canvas" class="w-full h-[420px]"></canvas>
    </div>
  </div>

  <!-- ═══════ Metadata Panel (collapsible) ═══════ -->
  <div class="bg-white rounded-xl border border-slate-200 shadow-sm">
    <div onclick="toggleMeta()" class="px-5 h-10 flex items-center justify-between border-b border-slate-100 cursor-pointer select-none hover:bg-slate-50/40 transition-colors">
      <span class="text-[11px] font-medium text-slate-500 uppercase tracking-wide">المعلومات</span>
      <span id="metaArrow" class="text-slate-400 text-[10px] transition-transform">▼</span>
    </div>
    <div id="metaPanel" class="hidden p-4 text-[12px] space-y-1.5">
      <div class="grid grid-cols-2 gap-x-6 gap-y-1.5">
        <div><span class="text-slate-400">اسم الملف</span> <span id="metaFn" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">الحجم</span> <span id="metaSize" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">الصيغة</span> <span id="metaFormat" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">الترميز</span> <span id="metaEncoding" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">إجمالي القطع</span> <span id="metaTotalPieces" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">المعرفات الفريدة</span> <span id="metaUniqueIds" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">المقاسات</span> <span id="metaSizes" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">إجمالي الشقوق</span> <span id="metaNotches" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">العرض (mm)</span> <span id="metaW" class="text-slate-700 font-medium mr-2"></span></div>
        <div><span class="text-slate-400">الارتفاع (mm)</span> <span id="metaH" class="text-slate-700 font-medium mr-2"></span></div>
      </div>
    </div>
  </div>

  <!-- ═══════ Piece List ═══════ -->
  <div class="bg-white rounded-xl border border-slate-200 shadow-sm">
    <div class="px-5 h-10 flex items-center border-b border-slate-100">
      <span class="text-[11px] font-medium text-slate-500 uppercase tracking-wide">قائمة القطع</span>
    </div>
    <div class="max-h-[200px] overflow-y-auto">
      <table class="w-full text-[12px]">
        <thead>
          <tr class="bg-slate-50/60 text-slate-500 text-[11px] uppercase tracking-wide font-medium sticky top-0">
            <th class="px-4 py-2 text-right">Piece ID</th>
            <th class="px-4 py-2 text-right">Size</th>
            <th class="px-4 py-2 text-right tabular-nums">Points</th>
            <th class="px-4 py-2 text-right tabular-nums">Area mm²</th>
            <th class="px-4 py-2 text-right tabular-nums">Notches</th>
          </tr>
        </thead>
        <tbody id="pieceList" class="divide-y divide-slate-100"></tbody>
      </table>
    </div>
  </div>

  <!-- ═══════ Conversion Options ═══════ -->
  <div class="bg-white rounded-xl border border-slate-200 shadow-sm">
    <div class="px-5 h-10 flex items-center border-b border-slate-100">
      <span class="text-[11px] font-medium text-slate-500 uppercase tracking-wide">تحويل</span>
    </div>
    <div class="p-5 space-y-4">
      <div class="flex flex-wrap items-center gap-4">
        <label class="flex items-center gap-2 text-[12px] text-slate-600">
          <span class="text-[11px] font-medium text-slate-500">الصيغة</span>
          <select id="fmt" onchange="fmtHint()" class="h-8 px-2 rounded-md border border-slate-200 bg-white text-[12px] text-slate-700 focus:ring-2 focus:ring-emerald-100 focus:border-emerald-300">
            <option>AAMA DXF</option><option>DXF</option><option>SVG</option><option>PLT</option><option value="CSV">CSV تقرير</option>
          </select>
        </label>
        <span id="fmtHintBox" class="inline-flex items-center gap-1.5 text-[11px] font-medium text-emerald-700 bg-emerald-50 border border-emerald-200/70 rounded-full px-2.5 py-1"></span>
        <label class="flex items-center gap-2 text-[12px] text-slate-600">
          <span class="text-[11px] font-medium text-slate-500">الوحدة</span>
          <select id="unit" class="h-8 px-2 rounded-md border border-slate-200 bg-white text-[12px] text-slate-700 focus:ring-2 focus:ring-slate-100 focus:border-slate-300">
            <option>mm</option><option>cm</option><option>inch</option>
          </select>
        </label>
        <label class="flex items-center gap-2 text-[12px] text-slate-600">
          <span class="text-[11px] font-medium text-slate-500">المقاس</span>
          <select id="sizeFilter" class="h-8 px-2 rounded-md border border-slate-200 bg-white text-[12px] text-slate-700 focus:ring-2 focus:ring-emerald-100 focus:border-emerald-300">
            <option value="all">كل المقاسات</option>
          </select>
        </label>
        <label class="flex items-center gap-1.5 text-[12px] text-slate-600 cursor-pointer">
          <input type="checkbox" id="outerOnly" class="rounded border-slate-300">
          <span class="text-[11px] font-medium text-slate-500">الخارج فقط</span>
        </label>
        <label class="flex items-center gap-1.5 text-[12px] text-slate-600 cursor-pointer">
          <input type="checkbox" id="incNotches" checked class="rounded border-slate-300">
          <span class="text-[11px] font-medium text-slate-500">include notches</span>
        </label>
        <label class="flex items-center gap-1.5 text-[12px] text-slate-600 cursor-pointer">
          <input type="checkbox" id="incLabels" checked class="rounded border-slate-300">
          <span class="text-[11px] font-medium text-slate-500">include labels</span>
        </label>
        <label class="flex items-center gap-1.5 text-[12px] cursor-pointer" title="دمج مقاسات كل قطعة في قطعة واحدة متدرّجة (AAMA)">
          <input type="checkbox" id="gradeMerge" class="rounded border-emerald-400">
          <span class="text-[11px] font-bold text-emerald-700">تدرّج (دمج المقاسات)</span>
        </label>
        <button id="convertBtn" onclick="doConvert()"
                class="mr-auto h-9 px-5 rounded-lg text-[12.5px] font-bold text-white bg-gradient-to-l from-emerald-600 to-indigo-600 hover:from-emerald-500 hover:to-indigo-500 shadow-sm shadow-emerald-600/20 transition-all active:scale-[.98] disabled:opacity-40 inline-flex items-center gap-2">
          <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="m7 11 5 5 5-5"/><path d="M5 21h14"/></svg>
          تحويل وتنزيل
        </button>
      </div>
      <div id="status" class="hidden px-3 py-2 rounded-md text-[12px]"></div>
      <div id="dlArea" class="flex flex-wrap gap-2"></div>
    </div>
  </div>
</div>

</main>

<script>
const COLORS=['#6366f1','#059669','#d97706','#dc2626','#7c3aed','#0891b2','#db2777','#2563eb','#84cc16','#f97316'];
let currentFile=null, lastData=null, fileList=[], currentIdx=0;

// ── File input ──
document.getElementById('fileInput').onchange=function(e){
  const files=Array.from(e.target.files).filter(f=>/\.(plt|hpgl)$/i.test(f.name));
  if(!files.length)return;
  if(files.length===1){loadFile(files[0])}
  else{showFileList(files)} 
};

// ── Drop zone ──
const dz=document.getElementById('dropZone');
dz.ondragover=e=>{e.preventDefault();dz.classList.replace('border-slate-200','border-indigo-400');dz.classList.add('bg-indigo-50/30')};
dz.ondragleave=()=>{dz.classList.replace('border-indigo-400','border-slate-200');dz.classList.remove('bg-indigo-50/30')};
dz.ondrop=async function(e){
  e.preventDefault();
  dz.classList.replace('border-indigo-400','border-slate-200');dz.classList.remove('bg-indigo-50/30');
  const items=e.dataTransfer.items;
  if(!items||!items.length)return;
  const files=[];
  for(let i=0;i<items.length;i++){
    const entry=items[i].webkitGetAsEntry?items[i].webkitGetAsEntry():null;
    if(entry){await traverseEntry(entry,files)}
    else if(items[i].kind==='file'){const f=items[i].getAsFile();if(f&&/\.(plt|hpgl)$/i.test(f.name))files.push(f)}
  }
  if(!files.length){setStatus('err','لم يتم العثور على ملفات HPGL/PLT');return}
  if(files.length===1){loadFile(files[0])}
  else{showFileList(files)}
};

async function traverseEntry(entry,files){
  if(entry.isFile){
    const f=await new Promise((res,rej)=>entry.file(res,rej));
    if(f&&/\.(plt|hpgl)$/i.test(f.name))files.push(f);
  }else if(entry.isDirectory){
    const reader=entry.createReader();
    const entries=await new Promise((res,rej)=>reader.readEntries(res,rej));
    for(const e of entries)await traverseEntry(e,files);
  }
}

function showFileList(files){
  fileList=files;currentIdx=0;
  document.getElementById('fileList').classList.remove('hidden');
  const cnt=document.getElementById('fileCountLabel');
  cnt.textContent=files.length+' ملفات — انقر لمعاينة';
  const cont=document.getElementById('fileListItems');cont.innerHTML='';
  files.forEach((f,i)=>{
    const row=document.createElement('div');
    row.className='flex items-center gap-3 px-3 py-1.5 hover:bg-slate-50 cursor-pointer '+(i===0?'bg-indigo-50/40':'');
    row.innerHTML='<span class="text-indigo-600 font-medium text-[11px]">'+(i+1)+'.</span>'+
      '<span class="text-slate-700 flex-1 truncate">'+f.name+'</span>'+
      '<span class="text-slate-400 tabular-nums text-[11px]">'+(f.size/1024).toFixed(1)+' KB</span>';
    row.onclick=()=>{currentIdx=i;loadFile(f);cont.querySelectorAll('div').forEach(r=>r.classList.remove('bg-indigo-50/40'));row.classList.add('bg-indigo-50/40')};
    cont.appendChild(row);
  });
  loadFile(files[0]);
}

async function batchConvert(){
  const btn=document.querySelector('#fileList button');btn.disabled=true;
  const fmt=document.getElementById('fmt').value;
  const unit=document.getElementById('unit').value;
  const outer=document.getElementById('outerOnly').checked?'1':'0';
  const notches=document.getElementById('incNotches').checked?'1':'0';
  const labels=document.getElementById('incLabels').checked?'1':'0';
  let ok=0,err=0;
  for(let i=0;i<fileList.length;i++){
    const fd=new FormData();fd.append('file',fileList[i]);
    fd.append('format',fmt);fd.append('unit',unit);
    fd.append('outer_only',outer);fd.append('include_notches',notches);fd.append('include_labels',labels);
    try{
      const r=await fetch('/convert',{method:'POST',body:fd});
      if(!r.ok){err++;continue}
      const blob=await r.blob();
      const url=URL.createObjectURL(blob);
      const a=document.createElement('a');
      a.href=url;a.download=fileList[i].name.replace(/\.(plt|hpgl)$/i,'')+'.'+fmtExt(fmt);
      a.click();URL.revokeObjectURL(url);ok++;
    }catch(e){err++}
  }
  btn.disabled=false;
  setStatus(ok?'ok':'err',ok+'/'+fileList.length+' تحويل بنجاح'+(err?' ('+err+' خطأ)':''));
}

// ── Load & parse ──
async function loadFile(file){
  currentFile=file;
  const fd=new FormData();fd.append('file',file);
  setStatus('info','جاري تحليل الملف...');
  try{
    const r=await fetch('/upload',{method:'POST',body:fd});
    const d=await r.json();
    if(d.error){setStatus('err','خطأ: '+d.error);return}
    lastData=d;
    // Info bar
    document.getElementById('fn').textContent=d.filename;
    document.getElementById('fs').textContent=(d.size/1024).toFixed(1)+' KB';
    document.getElementById('pc').textContent=d.piece_count+' ('+d.piece_ids.join(' . ')+')';
    document.getElementById('un').textContent=d.units;
    document.getElementById('fmtDetected').textContent=d.format_detected;
    document.getElementById('fileInfo').classList.remove('hidden');
    const ps=document.getElementById('previewSection');
    ps.classList.remove('hidden');ps.classList.remove('fade');void ps.offsetWidth;ps.classList.add('fade');
    // Marker header (reverse-engineered)
    const mk=d.marker, mc=document.getElementById('markerCard');
    if(mk){
      mc.classList.remove('hidden');
      document.getElementById('mkModel').textContent=mk.modele||mk.placement||'—';
      document.getElementById('mkQte').textContent=mk.qte||'—';
      document.getElementById('mkLen').textContent=mk.longueur||'—';
      document.getElementById('mkLaize').textContent=(mk.laize||'—')+' سم';
      document.getElementById('mkEff').textContent=(mk.rendement||'—')+' %';
      document.getElementById('mkDate').textContent=mk.date||'—';
    }else{mc.classList.add('hidden')}
    document.getElementById('mkPieces').textContent=(d.real_piece_count||d.piece_count||0);
    document.getElementById('mkNotches').textContent=(d.total_notches||0);
    // populate size filter (extract clean pieces of one size, or all)
    const sf=document.getElementById('sizeFilter');
    sf.innerHTML='<option value="all">كل المقاسات</option>'+
      (d.sizes_found||[]).map(s=>'<option value="'+s+'">مقاس '+s+'</option>').join('');
    // Meta panel
    document.getElementById('metaFn').textContent=d.filename;
    document.getElementById('metaSize').textContent=(d.size/1024).toFixed(1)+' KB';
    document.getElementById('metaFormat').textContent=d.format_detected;
    document.getElementById('metaEncoding').textContent=d.encoding||'latin-1';
    document.getElementById('metaTotalPieces').textContent=d.piece_count;
    document.getElementById('metaUniqueIds').textContent=d.piece_ids.join(', ');
    document.getElementById('metaSizes').textContent=(d.sizes_found||[]).join(', ')||'—';
    const totalNotches=(d.ensembles||[]).reduce((s,e)=>s+(e.notch_count||0),0);
    document.getElementById('metaNotches').textContent=totalNotches;
    document.getElementById('metaW').textContent=(d.bbox.width_mm||0).toFixed(1);
    document.getElementById('metaH').textContent=(d.bbox.height_mm||0).toFixed(1);
    drawPreview(d);
    renderPieces(d);
    setStatus('ok','تم تحميل '+d.filename+' — '+d.piece_count+' قطع');
  }catch(e){setStatus('err','خطأ: '+e.message)}
}

// ── Canvas ──
function drawPreview(d){
  const c=document.getElementById('canvas'),ctx=c.getContext('2d');
  const rect=c.parentElement.getBoundingClientRect();
  c.width=(rect.width||800)*2;c.height=420*2;ctx.scale(2,2);
  const W=c.width/2,H=c.height/2;
  ctx.clearRect(0,0,W,H);
  if(!d.pieces||!d.pieces.length){
    ctx.fillStyle='#94a3b8';ctx.font='13px Cairo';ctx.textAlign='center';
    ctx.fillText('لا توجد قطع للعرض',W/2,H/2);return
  }
  let xs=[],ys=[];
  if(d.ensembles&&d.ensembles.length){
    for(const e of d.ensembles)for(const pt of e.outer){xs.push(pt[0]);ys.push(pt[1])}
  }else{
    for(const p of d.pieces)for(const pt of p.polygon){xs.push(pt[0]);ys.push(pt[1])}
  }
  if(!xs.length)return;
  const mnx=Math.min(...xs),mxx=Math.max(...xs),mny=Math.min(...ys),mxy=Math.max(...ys);
  const sc=Math.min((W-80)/(mxx-mnx||1),(H-80)/(mxy-mny||1));
  const tr=(x,y)=>[40+(x-mnx)*sc,40+(mxy-y)*sc];
  const cm={};(d.piece_ids||[]).forEach((id,i)=>cm[id]=COLORS[i%COLORS.length]);
  // Draw ensembles (outer + internals)
  if(d.ensembles&&d.ensembles.length){
    for(const e of d.ensembles){
      const col=cm[e.piece_id]||'#94a3b8';
      const drawPoly=(poly,fillCol,strokeCol,lw,dash)=>{
        const pts=poly.map(pt=>tr(pt[0],pt[1])).flat();
        if(pts.length<4)return;
        ctx.beginPath();ctx.moveTo(pts[0],pts[1]);
        for(let i=2;i<pts.length;i+=2)ctx.lineTo(pts[i],pts[i+1]);
        ctx.closePath();
        ctx.fillStyle=fillCol;ctx.fill();
        ctx.strokeStyle=strokeCol;ctx.lineWidth=lw;
        if(dash)ctx.setLineDash(dash);else ctx.setLineDash([]);
        ctx.stroke();ctx.setLineDash([]);
      };
      drawPoly(e.outer,col+'22',col,1.5,null);
      // label
      const outer=e.outer;
      const cx=outer.reduce((s,pt)=>s+pt[0],0)/outer.length;
      const cy=outer.reduce((s,pt)=>s+pt[1],0)/outer.length;
      const [lx,ly]=tr(cx,cy);
      ctx.fillStyle='#0f172a';ctx.font='bold 10px Cairo';ctx.textAlign='center';ctx.textBaseline='middle';
      ctx.fillText(e.piece_id,lx,ly);
      // internals
      for(const feat of e.internals||[]){
        if(feat.type==='notch'){
          drawPoly(feat.polygon,col+'11','#ef4444',0.8,[3,3]);
        }else{
          drawPoly(feat.polygon,col+'11',col,0.8,[2,2]);
        }
      }
    }
  }else{
    // Fallback: flat pieces
    for(const p of d.pieces){
      const col=cm[p.piece_id]||'#94a3b8';
      const poly=p.polygon.map(pt=>tr(pt[0],pt[1])).flat();
      if(poly.length<4)continue;
      ctx.beginPath();ctx.moveTo(poly[0],poly[1]);
      for(let i=2;i<poly.length;i+=2)ctx.lineTo(poly[i],poly[i+1]);
      ctx.closePath();
      ctx.fillStyle=col+'22';ctx.fill();ctx.strokeStyle=col;ctx.lineWidth=1.2;ctx.stroke();
      const cx=poly.reduce((s,_,i)=>s+(i%2?0:poly[i]),0)/(poly.length/2);
      const cy=poly.reduce((s,_,i)=>s+(i%2?poly[i]:0),0)/(poly.length/2);
      ctx.fillStyle='#0f172a';ctx.font='bold 10px Cairo';ctx.textAlign='center';
      ctx.fillText(p.piece_id,cx,cy);
    }
  }
}

// ── Piece table ──
function renderPieces(d){
  const t=document.getElementById('pieceList');t.innerHTML='';
  const data=d.ensembles&&d.ensembles.length?d.ensembles:d.pieces||[];
  if(!data.length){
    t.innerHTML='<tr><td colspan="5" class="px-4 py-3 text-slate-400 text-center text-[12px]">لا توجد قطع</td></tr>';
    return
  }
  for(const item of data){
    const tr=document.createElement('tr');tr.className='hover:bg-slate-50/50';
    const pid=item.piece_id||'—';
    const sz=(item.size!==undefined&&item.size!==null&&item.size!=='')?item.size:'—';
    const pts=item.outer?item.outer.length:(item.polygon?item.polygon.length:0);
    const area=item.area_mm2!==undefined?item.area_mm2.toFixed(1):(item.polygon?polyArea(item.polygon)*0.0254*0.0254:0).toFixed(1);
    const nch=item.notch_count!==undefined?item.notch_count:0;
    tr.innerHTML='<td class="px-4 py-1.5 text-slate-700 font-medium">'+pid+
      '</td><td class="px-4 py-1.5 text-slate-500">'+sz+
      '</td><td class="px-4 py-1.5 text-slate-500 tabular-nums">'+pts+
      '</td><td class="px-4 py-1.5 text-slate-500 tabular-nums">'+area+
      '</td><td class="px-4 py-1.5 text-slate-500 tabular-nums">'+(nch||'—')+'</td>';
    t.appendChild(tr);
  }
}

function polyArea(poly){
  let s=0;const n=poly.length;
  for(let i=0;i<n;i++){const [x1,y1]=poly[i],[x2,y2]=poly[(i+1)%n];s+=x1*y2-x2*y1}
  return Math.abs(s)/2
}

// ── Convert ──
async function doConvert(){
  const btn=document.getElementById('convertBtn');btn.disabled=true;
  setStatus('info','جاري التحويل...');
  const fd=new FormData();
  fd.append('file',currentFile);
  fd.append('format',document.getElementById('fmt').value);
  fd.append('unit',document.getElementById('unit').value);
  fd.append('outer_only',document.getElementById('outerOnly').checked?'1':'0');
  fd.append('include_notches',document.getElementById('incNotches').checked?'1':'0');
  fd.append('include_labels',document.getElementById('incLabels').checked?'1':'0');
  const szf=document.getElementById('sizeFilter').value;
  fd.append('size',szf);
  fd.append('grade',document.getElementById('gradeMerge').checked?'1':'0');
  try{
    const r=await fetch('/convert',{method:'POST',body:fd});
    if(!r.ok){const e=await r.json();setStatus('err','خطأ: '+(e.error||''));btn.disabled=false;return}
    const blob=await r.blob();
    const url=URL.createObjectURL(blob);
    const isZip=blob.type==='application/zip';
    const ext=isZip?'zip':fmtExt(document.getElementById('fmt').value);
    const szTag=(szf&&szf!=='all'&&!isZip)?(' '+szf):(isZip?' GRADED':'');
    const fn=currentFile.name.replace(/\.(plt|hpgl)$/i,'')+szTag+'.'+ext;
    const dl=document.getElementById('dlArea');
    dl.innerHTML='<a class="inline-flex h-8 px-3 rounded-md text-[12px] font-medium bg-emerald-600 text-white hover:bg-emerald-700 transition-colors items-center gap-1.5" href="'+url+'" download="'+fn+'">'+fn+'</a>';
    setStatus('ok','تم التحويل بنجاح');
  }catch(e){setStatus('err','خطأ: '+e.message)}
  btn.disabled=false;
}

// ── Format → file extension (AAMA/DXF both export .dxf so Gerber sees them) ──
function fmtExt(v){
  return ({'AAMA DXF':'dxf','DXF':'dxf','SVG':'svg','PLT':'plt','CSV':'csv'})[v]||'dxf';
}

// ── Format hint ──
const FMT_HINTS={
  'AAMA DXF':['✓ موصى به لـ Gerber AccuMark / PDS · Lectra · Optitex','emerald'],
  'DXF':['DXF عام — للعرض في AutoCAD','slate'],
  'SVG':['SVG — للعرض في المتصفح والطباعة','slate'],
  'PLT':['PLT — إعادة إخراج HPGL للراسمة','slate'],
  'CSV':['تقرير الهندسة العكسية — بيانات القطع + الإشارات (Excel)','slate']
};
function fmtHint(){
  const v=document.getElementById('fmt').value;
  const box=document.getElementById('fmtHintBox');
  const [txt,col]=FMT_HINTS[v]||['','slate'];
  box.textContent=txt;
  box.className='inline-flex items-center gap-1.5 text-[11px] font-medium rounded-full px-2.5 py-1 border '+
    (col==='emerald'
      ?'text-emerald-700 bg-emerald-50 border-emerald-200/70'
      :'text-slate-500 bg-slate-50 border-slate-200');
}

// ── Compare two files ──
function toggleCompare(){
  const p=document.getElementById('comparePanel');
  const a=document.getElementById('cmpArrow');
  p.classList.toggle('hidden');
  a.style.transform=p.classList.contains('hidden')?'rotate(0deg)':'rotate(180deg)';
}
async function doCompare(){
  const fa=document.getElementById('cmpA').files[0];
  const fb=document.getElementById('cmpB').files[0];
  const res=document.getElementById('cmpResult');
  if(!fa||!fb){res.innerHTML='<div class="text-[12px] text-red-600">اختر ملفّين أولاً</div>';return}
  res.innerHTML='<div class="text-[12px] text-slate-500">جاري المقارنة...</div>';
  const fd=new FormData();fd.append('fileA',fa);fd.append('fileB',fb);
  try{
    const r=await fetch('/compare',{method:'POST',body:fd});
    const d=await r.json();
    if(d.error){res.innerHTML='<div class="text-[12px] text-red-600">خطأ: '+d.error+'</div>';return}
    let h='<div class="grid grid-cols-2 gap-3 mb-3 text-[12px]">';
    h+='<div class="bg-slate-50 rounded-lg p-2.5 border border-slate-100"><div class="font-semibold text-slate-700 truncate">A: '+d.a.name+'</div><div class="text-slate-500">'+d.a.pieces+' قطعة · '+d.a.area+' cm² · '+d.a.notches+' إشارة</div></div>';
    h+='<div class="bg-slate-50 rounded-lg p-2.5 border border-slate-100"><div class="font-semibold text-slate-700 truncate">B: '+d.b.name+'</div><div class="text-slate-500">'+d.b.pieces+' قطعة · '+d.b.area+' cm² · '+d.b.notches+' إشارة</div></div>';
    h+='</div>';
    h+='<table class="w-full text-[12px]"><thead><tr class="text-slate-500 text-[11px] uppercase border-b border-slate-100"><th class="px-2 py-1.5 text-right">القطعة</th><th class="px-2 py-1.5 tabular-nums">A</th><th class="px-2 py-1.5 tabular-nums">B</th><th class="px-2 py-1.5 tabular-nums">الفرق</th></tr></thead><tbody>';
    for(const row of d.rows){
      const same=row.diff===0 && JSON.stringify(row.a_areas)===JSON.stringify(row.b_areas);
      const cls=row.diff!==0?'bg-amber-50':(same?'':'bg-yellow-50/40');
      const dtxt=row.diff>0?('+'+row.diff):(row.diff<0?row.diff:'=');
      const dcls=row.diff>0?'text-emerald-700':(row.diff<0?'text-red-600':'text-slate-400');
      h+='<tr class="'+cls+' border-b border-slate-50"><td class="px-2 py-1.5 text-slate-700 font-medium">'+row.name+'</td><td class="px-2 py-1.5 text-center tabular-nums">'+row.a_count+'</td><td class="px-2 py-1.5 text-center tabular-nums">'+row.b_count+'</td><td class="px-2 py-1.5 text-center font-bold tabular-nums '+dcls+'">'+dtxt+'</td></tr>';
    }
    h+='</tbody></table>';
    const totDiff=d.b.pieces-d.a.pieces;
    h+='<div class="mt-3 text-[12px] font-medium '+(totDiff===0?'text-emerald-700':'text-amber-700')+'">'+(totDiff===0?'✓ نفس عدد القطع':('فرق العدد الكلّي: '+(totDiff>0?'+':'')+totDiff+' قطعة'))+'</div>';
    res.innerHTML=h;
  }catch(e){res.innerHTML='<div class="text-[12px] text-red-600">خطأ: '+e.message+'</div>'}
}

// ── Meta toggle ──
function toggleMeta(){
  const p=document.getElementById('metaPanel');
  const a=document.getElementById('metaArrow');
  p.classList.toggle('hidden');
  a.style.transform=p.classList.contains('hidden')?'rotate(0deg)':'rotate(180deg)';
}

// ── Status ──
function setStatus(type,msg){
  const el=document.getElementById('status');el.className='';
  if(!msg){el.classList.add('hidden');return}
  el.classList.remove('hidden');
  if(type==='info'){el.className='bg-indigo-50 text-indigo-700 border border-indigo-100 px-3 py-2 rounded-md text-[12px]'}
  else if(type==='ok'){el.className='bg-emerald-50 text-emerald-700 border border-emerald-100 px-3 py-2 rounded-md text-[12px]'}
  else if(type==='err'){el.className='bg-red-50 text-red-600 border border-red-100 px-3 py-2 rounded-md text-[12px]'}
  el.innerHTML=msg;
}

// ── Init ──
fmtHint();
</script>
</body>
</html>"""


# ── HTTP Handler ──────────────────────────────────────────────────────────

def parse_multipart(body, boundary):
    fields = {}
    files = []
    delim = b'--' + boundary.encode()
    for part in body.split(delim):
        if not part.strip() or part.strip() == b'--':
            continue
        hi = part.find(b'\r\n\r\n')
        if hi < 0:
            continue
        hdr = part[:hi].decode('latin-1', errors='replace')
        data = part[hi + 4:]
        if data.endswith(b'\r\n'):
            data = data[:-2]
        name = filename = None
        for h in hdr.split('\r\n'):
            if h.lower().startswith('content-disposition'):
                for s in h.split(';'):
                    s = s.strip()
                    if s.startswith('name='):
                        name = s[5:].strip('"\'')
                    if s.startswith('filename='):
                        filename = s[9:].strip('"\'')
        if name:
            if filename:
                files.append((name, data, filename))
            else:
                fields[name] = data.decode('latin-1', errors='replace')
    return fields, files


class handler(BaseHTTPRequestHandler):

    def _fail(self, e):
        import traceback
        msg = ('BERA Converter error:' + chr(10) + chr(10) + traceback.format_exc()).encode('utf-8')
        try:
            self.send_response(500)
            self._cors()
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        except Exception:
            pass

    def do_GET(self):
        try:
            b = TEMPLATE.encode('utf-8')
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except Exception as e:
            self._fail(e)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            ct = self.headers.get('Content-Type', '')
            cl = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(cl)
            p = urllib.parse.urlparse(self.path).path
            if 'upload-batch' in p:
                self._upload_batch(body, ct)
            elif 'compare' in p:
                self._compare(body, ct)
            elif 'convert' in p:
                self._convert(body, ct)
            else:
                self._upload(body, ct)
        except Exception as e:
            self._fail(e)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _form(self, body, ct):
        if 'boundary=' in ct:
            b = ct.split('boundary=')[1].strip().strip('"')
            return parse_multipart(body, b)
        return {}, []

    def _read_file(self, fields, files):
        if files:
            _, data, fn = files[0]
            return data, fn
        fd = fields.get('file', b'')
        if isinstance(fd, tuple):
            return fd
        return fd, 'file.plt'

    def _parse_and_group(self, content, fn):
        p = HpglParser(content=content)
        p.parse()
        pieces = [{'piece_id': x['piece_id'], 'size': x.get('size', ''),
                   'polygon': x['polygon']} for x in p.pieces]
        fmt, enc, units = auto_detect_format(content)
        if HAS_GROUPER and len(p.pieces) > 1:
            ensembles = _group_pieces(p)
        else:
            ensembles = group_flat_pieces(pieces)
        add_notch_counts(ensembles)
        ids = p.get_piece_ids()
        bb = bbox_of_pieces(pieces)
        sz = sizes_found(ensembles)
        total_notches = sum(e.get('notch_count', 0) for e in ensembles)
        return {
            'filename': os.path.basename(fn) if fn else 'file.plt',
            'size': len(content),
            'piece_count': len(pieces),
            'real_piece_count': len(ensembles),
            'piece_ids': ids,
            'pieces': pieces,
            'ensembles': ensembles,
            'units': units,
            'format_detected': fmt,
            'encoding': enc,
            'sizes_found': sz,
            'bbox': bb,
            'total_notches': total_notches,
            'marker': parse_marker_header(content),
        }

    def _upload(self, body, ct):
        fields, files = self._form(body, ct)
        fd, fn = self._read_file(fields, files)
        if not fd or (isinstance(fd, bytes) and len(fd) < 4):
            return self._json({'error': 'الملف فارغ'}, 400)
        content = fd.decode('latin-1') if isinstance(fd, bytes) else fd
        try:
            result = self._parse_and_group(content, fn)
            self._json(result)
        except Exception as e:
            import traceback
            self._json({'error': f'{e}'}, 500)

    def _upload_batch(self, body, ct):
        _, files = self._form(body, ct)
        if not files:
            return self._json({'error': 'لم يتم رفع أي ملفات'}, 400)
        results = []
        for _, data, fn in files:
            if not data or len(data) < 4:
                continue
            try:
                content = data.decode('latin-1') if isinstance(data, bytes) else data
                r = self._parse_and_group(content, fn)
                results.append(r)
            except Exception as e:
                results.append({'filename': fn, 'error': str(e)})
        self._json({'files': results, 'count': len(results)})

    def _summary_by_name(self, content):
        p = HpglParser(content=content)
        p.parse()
        if HAS_GROUPER and len(p.pieces) > 1:
            ensembles = _group_pieces(p)
        else:
            ensembles = group_flat_pieces(
                [{'piece_id': x['piece_id'], 'size': x.get('size', ''),
                  'polygon': x['polygon']} for x in p.pieces])
        by = {}
        for e in ensembles:
            nm = str(e.get('piece_id', '') or '?')
            d = by.setdefault(nm, {'count': 0, 'areas': [], 'notches': 0})
            d['count'] += 1
            d['areas'].append(round(e['area_mm2'] / 100, 1))
            d['notches'] += len(e.get('notches', []))
        return {
            'pieces': len(ensembles),
            'by_name': by,
            'marker': parse_marker_header(content),
            'total_area': round(sum(e['area_mm2'] for e in ensembles) / 100),
            'total_notches': sum(len(e.get('notches', [])) for e in ensembles),
        }

    def _compare(self, body, ct):
        _, files = self._form(body, ct)
        if len(files) < 2:
            return self._json({'error': 'ارفع ملفّين للمقارنة'}, 400)
        try:
            ca = files[0][1].decode('latin-1')
            cb = files[1][1].decode('latin-1')
            A = self._summary_by_name(ca)
            B = self._summary_by_name(cb)
            names = sorted(set(list(A['by_name']) + list(B['by_name'])))
            rows = []
            for nm in names:
                a = A['by_name'].get(nm, {'count': 0, 'areas': [], 'notches': 0})
                b = B['by_name'].get(nm, {'count': 0, 'areas': [], 'notches': 0})
                rows.append({
                    'name': nm,
                    'a_count': a['count'], 'b_count': b['count'],
                    'a_areas': sorted(a['areas'], reverse=True),
                    'b_areas': sorted(b['areas'], reverse=True),
                    'diff': b['count'] - a['count'],
                })
            self._json({
                'a': {'name': files[0][2], 'pieces': A['pieces'],
                      'area': A['total_area'], 'notches': A['total_notches'],
                      'marker': A['marker']},
                'b': {'name': files[1][2], 'pieces': B['pieces'],
                      'area': B['total_area'], 'notches': B['total_notches'],
                      'marker': B['marker']},
                'rows': rows,
            })
        except Exception as e:
            self._json({'error': f'{e}'}, 500)

    def _convert(self, body, ct):
        fields, files = self._form(body, ct)
        fd, fn = self._read_file(fields, files)
        fmt = fields.get('format', 'DXF').lower().replace(' ', '-')
        unit = fields.get('unit', 'mm')
        outer = fields.get('outer_only', '0') == '1'
        inc_notches = fields.get('include_notches', '1') == '1'
        inc_labels = fields.get('include_labels', '1') == '1'
        size_sel = fields.get('size', 'all')

        def _size_filter(ens):
            if size_sel and size_sel != 'all':
                f = [e for e in ens if str(e.get('size', '')) == size_sel]
                return f or ens
            return ens
        if not fd:
            return self._json({'error': 'no file'}, 400)
        content = fd.decode('latin-1') if isinstance(fd, bytes) else fd
        try:
            p = HpglParser(content=content)
            p.parse()
            if fmt == 'dxf':
                out = export_dxf(p, unit, outer, inc_notches, inc_labels)
                cty = 'application/dxf'
            elif fmt == 'svg':
                out = export_svg(p, unit, outer, inc_notches, inc_labels)
                cty = 'image/svg+xml'
            elif fmt == 'plt':
                out = export_plt(p, unit, outer, inc_notches, inc_labels)
                cty = 'application/octet-stream'
            elif fmt in ('aama', 'aama-dxf', 'aamadxf'):
                if HAS_GROUPER and len(p.pieces) > 1:
                    ensembles = _group_pieces(p)
                else:
                    ensembles = group_flat_pieces(
                        [{'piece_id': x['piece_id'], 'size': x.get('size', ''),
                          'polygon': x['polygon']} for x in p.pieces]
                    )
                mk = parse_marker_header(content)
                model = (mk.get('modele') or mk.get('placement')) if mk else ''
                base_size = mk.get('size', '') if mk else ''
                grade = fields.get('grade', '0') == '1'
                if grade and HAS_GRADATION:
                    astm_pieces, astm_rules, astm_singles = build_astm_grade_data(ensembles)
                    if astm_pieces:
                        dxf_text, rul_text = export_astm_dxf_and_rul(
                            astm_pieces, astm_rules, unit, model)
                        base_name = re.sub(r'[^A-Za-z0-9 _.\-]', '', model or 'MODEL').strip() or 'MODEL'
                        buf = io.BytesIO()
                        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                            zf.writestr(f'{base_name}.DXF', dxf_text)
                            zf.writestr(f'{base_name}.RUL', rul_text)
                        self.send_response(200)
                        self._cors()
                        self.send_header('Content-Type', 'application/zip')
                        self.send_header(
                            'Content-Disposition',
                            f'attachment; filename="{base_name} GRADED.zip"')
                        self.send_header('Content-Length', str(buf.tell()))
                        self.end_headers()
                        self.wfile.write(buf.getvalue())
                        return
                    graded, singles = build_graded_pieces(ensembles)
                    out = export_dxf_aama_graded(graded, singles, unit, model)
                else:
                    ensembles = _size_filter(ensembles)
                    if outer:
                        for e in ensembles:
                            e['internals'] = []
                    out = export_dxf_aama(ensembles, unit, model, base_size)
                cty = 'application/dxf'
            elif fmt in ('csv', 'report', 'table', 'csv-تقرير', 'تقرير'):
                if HAS_GROUPER and len(p.pieces) > 1:
                    ensembles = _group_pieces(p)
                else:
                    ensembles = group_flat_pieces(
                        [{'piece_id': x['piece_id'], 'size': x.get('size', ''),
                          'polygon': x['polygon']} for x in p.pieces]
                    )
                ensembles = _size_filter(ensembles)
                out = export_csv_report(ensembles, parse_marker_header(content), unit)
                cty = 'text/csv; charset=utf-8'
            else:
                return self._json({'error': f'صيغة غير مدعومة: {fmt}'}, 400)
            is_csv = fmt in ('csv', 'report', 'table', 'csv-تقرير', 'تقرير')
            ext = ('dxf' if fmt in ('aama', 'aama-dxf', 'aamadxf')
                   else 'csv' if is_csv else fmt)
            b = out.encode('utf-8-sig') if is_csv \
                else out.encode('ascii', errors='replace')
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', cty)
            self.send_header('Content-Disposition',
                             f'attachment; filename="converted.{ext}"')
            self.send_header('Content-Length', str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        except Exception as e:
            import traceback
            self._json({'error': f'{e}'}, 500)

    def _json(self, d, s=200):
        b = json.dumps(d, ensure_ascii=False).encode('utf-8')
        self.send_response(s)
        self._cors()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


# ── Entry point ───────────────────────────────────────────────────────────
