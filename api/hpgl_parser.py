"""HPGL/PLT plotter file parser - extracts piece geometries as clean polygons with labels."""

import re, os
from typing import List, Tuple, Dict, Optional


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
        """Extract (size, piece_name) from a plotter label such as
        '42 PID COUL 9MIJA 4555 A' -> ('42', 'PID COUL') or
        'S DOV 9MIJA A' -> ('S', 'DOV'). Size is the leading token; the name is
        the descriptive words before the model code (a token with digits or
        'MIJA'). Keeps multi-word names; drops the marker index letter."""
        toks = lbl.split()
        if not toks:
            return ('', lbl)
        size = ''
        if re.fullmatch(r'\d{1,3}|[SMLX]{1,4}', toks[0]):
            size = toks[0]
            toks = toks[1:]
        name_toks = []
        for t in toks:
            if re.search(r'\d', t) or 'MIJA' in t.upper():
                break
            name_toks.append(t)
        name = ' '.join(name_toks) if name_toks else (toks[0] if toks else lbl)
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


if __name__ == '__main__':
    path = r'C:\Users\HP\Desktop\cao\HPGL\LADIES-BLOUSE1.PLT'
    parser = HpglParser(path)
    parser.parse()

    print(f'File: {os.path.basename(path)}')
    print(f'Real pieces: {len(parser.pieces)}')
    print(f'Info: {parser.info}')

    piece_ids = parser.get_piece_ids()
    print(f'Piece IDs: {piece_ids}')

    by_size = {}
    for p in parser.pieces:
        sz = p['size']
        by_size.setdefault(sz, []).append(p)
    print(f'\nBy size:')
    for sz in sorted(by_size.keys()):
        print(f'  Size {sz}: {len(by_size[sz])} pieces')
        for p in by_size[sz]:
            print(f'    {p["piece_id"]:6s} | {len(p["polygon"]):4d} pts')

    out_dir = os.path.join(os.path.dirname(__file__), 'output')
    os.makedirs(out_dir, exist_ok=True)

    svg = parser.pieces_to_svg()
    with open(os.path.join(out_dir, 'LADIES-BLOUSE1.svg'), 'w') as f:
        f.write(svg)
    print(f'\nSVG: output/LADIES-BLOUSE1.svg')

    dxf = parser.all_to_dxf('mm')
    with open(os.path.join(out_dir, 'LADIES-BLOUSE1.dxf'), 'w') as f:
        f.write(dxf)
    print(f'DXF: output/LADIES-BLOUSE1.dxf')
