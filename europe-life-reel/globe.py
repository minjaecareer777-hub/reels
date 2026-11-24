"""Globe hook renderer: an orthographic Earth that turns from East Asia to Europe.

Textures are NASA Blue Marble / Black Marble (public domain), see fetch_assets.py.
"""
import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1080, 1920
SS = 2  # supersampling factor for the globe disc


def ease_in_out(x):
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (6 * x - 15) + 10)


def ease_out(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _basis(lat0, lon0):
    la, lo = math.radians(lat0), math.radians(lon0)
    f = np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])
    e = np.array([-math.sin(lo), math.cos(lo), 0.0])
    n = np.array([-math.sin(la) * math.cos(lo), -math.sin(la) * math.sin(lo), math.cos(la)])
    return e, n, f


def _unit(lat, lon, alt=1.0):
    la, lo = math.radians(lat), math.radians(lon)
    return alt * np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


def great_circle(p, q, steps=160, lift=0.07):
    """Points along the great circle p->q (lat, lon), lifted off the surface."""
    a, b = _unit(*p), _unit(*q)
    omega = math.acos(float(np.clip(a @ b, -1, 1)))
    out = []
    for i in range(steps + 1):
        s = i / steps
        v = (math.sin((1 - s) * omega) * a + math.sin(s * omega) * b) / math.sin(omega)
        out.append(v * (1.0 + lift * math.sin(math.pi * s)))
    return np.array(out)


class Globe:
    def __init__(self, day_path, night_path, seed=7):
        Image.MAX_IMAGE_PIXELS = None
        self.day = np.asarray(Image.open(day_path).convert("RGB"), dtype=np.float32) / 255.0
        night = np.asarray(Image.open(night_path).convert("RGB"), dtype=np.float32) / 255.0
        if night.shape != self.day.shape:
            night = cv2.resize(night, (self.day.shape[1], self.day.shape[0]), interpolation=cv2.INTER_AREA)
        self.night = night
        rng = np.random.default_rng(seed)
        n = 520
        self.stars = np.stack([rng.uniform(0, W, n), rng.uniform(0, H, n),
                               rng.uniform(0.15, 1.0, n) ** 2.2, rng.uniform(0.6, 1.9, n)], axis=1)
        self.sun = np.array([-0.52, 0.38, 0.76])
        self.sun /= np.linalg.norm(self.sun)

    # ---- projection helpers -------------------------------------------------
    def project(self, pts, lat0, lon0, cx, cy, R):
        e, n, f = _basis(lat0, lon0)
        x = pts @ e
        y = pts @ n
        z = pts @ f
        return cx + x * R, cy - y * R, z

    def _space(self, t):
        img = Image.new("RGB", (W, H), (2, 3, 8))
        d = ImageDraw.Draw(img)
        for x, y, b, r in self.stars:
            x = (x - 9 * t) % W
            tw = 0.82 + 0.18 * math.sin(t * 3.1 + x * 0.05)
            c = int(255 * min(1.0, b * tw))
            d.ellipse([x - r, y - r, x + r, y + r], fill=(c, c, min(255, c + 12)))
        return np.asarray(img.filter(ImageFilter.GaussianBlur(0.6)), dtype=np.float32) / 255.0

    def render(self, t, lat0, lon0, cx, cy, R):
        """Return float32 RGB frame (H, W, 3) in 0..1."""
        frame = self._space(t)
        r_out = int(R * 1.16) + 4
        # only shade pixels that land inside the frame (matters for the punch-in zoom)
        x0, x1 = max(0, int(cx - r_out)), min(W, int(cx + r_out))
        y0, y1 = max(0, int(cy - r_out)), min(H, int(cy + r_out))
        if x1 <= x0 or y1 <= y0:
            return frame
        bw, bh = (x1 - x0) * SS, (y1 - y0) * SS
        xs = (np.arange(bw, dtype=np.float32) + 0.5) / SS + x0
        ys = (np.arange(bh, dtype=np.float32) + 0.5) / SS + y0
        u = (xs[None, :] - cx) / R
        v = (cy - ys[:, None]) / R
        rr2 = u * u + v * v
        rr = np.sqrt(rr2)
        inside = rr2 < 1.0
        z = np.sqrt(np.clip(1.0 - rr2, 0.0, 1.0))
        e, n, f = _basis(lat0, lon0)
        px = u * e[0] + v * n[0] + z * f[0]
        py = u * e[1] + v * n[1] + z * f[1]
        pz = u * e[2] + v * n[2] + z * f[2]
        lat = np.arcsin(np.clip(pz, -1, 1))
        lon = np.arctan2(py, px)
        th, tw = self.day.shape[:2]
        mx = ((lon + np.pi) / (2 * np.pi) * tw - 0.5).astype(np.float32)
        my = ((np.pi / 2 - lat) / np.pi * th - 0.5).astype(np.float32)
        day = cv2.remap(self.day, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
        night = cv2.remap(self.night, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)

        s = self.sun
        dotl = u * s[0] + v * s[1] + z * s[2]
        lit = np.clip((dotl + 0.10) / 0.36, 0, 1)
        lit = lit * lit * (3 - 2 * lit)
        diffuse = 0.20 + 0.98 * np.clip(dotl, 0, 1) ** 0.75
        # richer, slightly deeper colour for the day side
        dayc = np.clip(day, 0, 1) ** 1.06
        lum = dayc.mean(axis=2, keepdims=True)
        dayc = np.clip(lum + (dayc - lum) * 1.22, 0, 1)
        col = dayc * diffuse[..., None] * lit[..., None]
        lights = np.clip(night * 2.6, 0, 1) ** 1.25 * np.array([1.0, 0.86, 0.62], dtype=np.float32)
        col += lights * (1 - lit[..., None]) + dayc * 0.035 * (1 - lit[..., None])
        # specular glint on water (dark blue pixels)
        ocean = np.clip((dayc[..., 2] - dayc[..., 0]) * 3.0, 0, 1)
        hvec = s + np.array([0, 0, 1.0])
        hvec /= np.linalg.norm(hvec)
        spec = np.clip(u * hvec[0] + v * hvec[1] + z * hvec[2], 0, 1) ** 60
        col += (spec * ocean * 0.35)[..., None]
        # atmosphere: fresnel rim inside the disc
        rim = (1 - z) ** 2.6
        atm = np.array([0.30, 0.56, 1.0], dtype=np.float32)
        col += atm * (rim * (0.25 + 0.95 * lit))[..., None]
        col = np.where(inside[..., None], col, 0.0)
        # anti-aliased edge + outer glow
        edge = np.clip((1.0 - rr) * R * SS / 1.5 + 0.5, 0, 1)
        halo = np.exp(-np.clip(rr - 1.0, 0, None) / 0.035) * (rr >= 1.0)
        halo_l = np.clip((u * s[0] + v * s[1]) / np.maximum(rr, 1e-3) * 0.75 + 0.5, 0.05, 1)
        glow = atm * (halo * halo_l * 0.85)[..., None]

        col = cv2.resize(col, (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
        edge = cv2.resize(edge.astype(np.float32), (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
        glow = cv2.resize(glow.astype(np.float32), (x1 - x0, y1 - y0), interpolation=cv2.INTER_AREA)
        # paste (clipped to the frame)
        fx0, fy0 = max(0, x0), max(0, y0)
        fx1, fy1 = min(W, x1), min(H, y1)
        sx0, sy0 = fx0 - x0, fy0 - y0
        sx1, sy1 = sx0 + (fx1 - fx0), sy0 + (fy1 - fy0)
        dst = frame[fy0:fy1, fx0:fx1]
        a = edge[sy0:sy1, sx0:sx1, None]
        dst[:] = dst * (1 - a) + col[sy0:sy1, sx0:sx1] * a + glow[sy0:sy1, sx0:sx1]
        return np.clip(frame, 0, 1)
