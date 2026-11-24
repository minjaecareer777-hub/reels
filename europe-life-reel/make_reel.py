#!/usr/bin/env python3
"""Render the Switzerland x Finland "one ordinary day" reel.

    python3 make_reel.py                 # full render -> out/europe_life_reel.mp4
    python3 make_reel.py --preview 0.5   # half-res quick preview
    python3 make_reel.py --stills        # only write check frames to out/stills/

Everything (cuts, copy, timing) lives in timeline.json. Assets are fetched by
fetch_assets.py into ./assets (not committed).
"""
import argparse
import json
import math
import os
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from globe import Globe, great_circle, ease_in_out, ease_out, W, H

ROOT = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(ROOT, "assets")
FONTS = os.path.join(ASSETS, "fonts")
FPS = 30

FONT_FILES = {
    "ko_serif": "NotoSerifKR_600SemiBold.ttf",
    "ko_serif_b": "NotoSerifKR_700Bold.ttf",
    "ja_serif": "NotoSerifJP_700Bold.ttf",
    "ja_serif_m": "NotoSerifJP_500Medium.ttf",
    "ko_sans": "Pretendard-SemiBold.otf",
    "ja_sans": "PretendardJP-SemiBold.otf",
    "lat_black": "Montserrat_800ExtraBold.ttf",
    "lat_semi": "Montserrat_600SemiBold.ttf",
    "emoji": "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
}
_font_cache = {}


def font(key, size):
    k = (key, size)
    if k not in _font_cache:
        f = FONT_FILES[key]
        path = f if os.path.isabs(f) else os.path.join(FONTS, f)
        _font_cache[k] = ImageFont.truetype(path, size)
    return _font_cache[k]


# --------------------------------------------------------------------------- text
def _is_emoji(ch):
    o = ord(ch)
    return (0x1F000 <= o <= 0x1FAFF) or (0x2600 <= o <= 0x27BF) or o in (0x2B50, 0x2728, 0xFE0F, 0x200D)


def _runs(text):
    runs, cur, cur_e = [], "", None
    for ch in text:
        e = _is_emoji(ch)
        if cur and e != cur_e:
            runs.append((cur, cur_e))
            cur = ""
        cur += ch
        cur_e = e
    if cur:
        runs.append((cur, cur_e))
    return runs


def _emoji_img(s, size):
    f = font("emoji", 109)
    s = s.replace("️", "")
    w = int(f.getlength(s)) + 8
    im = Image.new("RGBA", (max(w, 8), 140), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4, 8), s, font=f, embedded_color=True)
    bb = im.getbbox()
    if bb:
        im = im.crop(bb)
    sc = size / 118.0
    return im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)


def measure_line(text, fkey, size, tracking=0):
    w = 0
    for s, is_e in _runs(text):
        if is_e:
            w += _emoji_img(s, size).width + int(size * 0.08)
        else:
            w += font(fkey, size).getlength(s) + tracking * len(s)
    return int(math.ceil(w))


def draw_line(canvas, x, y, text, fkey, size, fill, tracking=0):
    """Draw one line with its top at y; returns width."""
    d = ImageDraw.Draw(canvas)
    f = font(fkey, size)
    asc, desc = f.getmetrics()
    cx = x
    for s, is_e in _runs(text):
        if is_e:
            em = _emoji_img(s, size)
            canvas.alpha_composite(em, (int(cx), int(y + (asc + desc - em.height) / 2 + size * 0.04)))
            cx += em.width + int(size * 0.08)
        elif tracking:
            for ch in s:
                d.text((cx, y), ch, font=f, fill=fill)
                cx += f.getlength(ch) + tracking
        else:
            d.text((cx, y), s, font=f, fill=fill)
            cx += f.getlength(s)
    return cx - x


class Layer:
    """Pre-rendered premultiplied RGBA patch positioned on the frame."""

    def __init__(self, rgba, x, y):
        a = np.asarray(rgba, dtype=np.float32) / 255.0
        self.alpha = a[..., 3:4]
        self.rgb = a[..., :3] * self.alpha
        self.x, self.y = int(x), int(y)

    def blit(self, frame, opacity=1.0, dx=0, dy=0):
        if opacity <= 0.003:
            return
        h, w = self.alpha.shape[:2]
        x0, y0 = self.x + int(round(dx)), self.y + int(round(dy))
        fx0, fy0, fx1, fy1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
        if fx1 <= fx0 or fy1 <= fy0:
            return
        sx0, sy0 = fx0 - x0, fy0 - y0
        a = self.alpha[sy0:sy0 + fy1 - fy0, sx0:sx0 + fx1 - fx0] * opacity
        c = self.rgb[sy0:sy0 + fy1 - fy0, sx0:sx0 + fx1 - fx0] * opacity
        dst = frame[fy0:fy1, fx0:fx1]
        dst *= (1 - a)
        dst += c


def text_block(lines, cx, y, gap=14, shadow=1.0, align="center"):
    """lines: list of dicts(text, font, size, fill, tracking). Returns Layer centred on cx with top at y."""
    pad = 60
    widths = [measure_line(l["text"], l["font"], l["size"], l.get("tracking", 0)) for l in lines]
    heights = []
    for l in lines:
        asc, desc = font(l["font"], l["size"]).getmetrics()
        heights.append(asc + desc)
    bw = max(widths) + 2 * pad
    bh = sum(heights) + sum(l.get("gap", gap) for l in lines[:-1]) + 2 * pad
    fg = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    yy = pad
    for l, w_, h_ in zip(lines, widths, heights):
        xx = pad + (max(widths) - w_) // 2 if align == "center" else pad
        draw_line(fg, xx, yy, l["text"], l["font"], l["size"], l.get("fill", (255, 255, 255, 255)), l.get("tracking", 0))
        yy += h_ + l.get("gap", gap)
    alpha = fg.getchannel("A")
    sh = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    if shadow > 0:
        soft = alpha.filter(ImageFilter.GaussianBlur(16)).point(lambda v: min(255, int(v * 1.9 * shadow)))
        tight = alpha.filter(ImageFilter.GaussianBlur(3)).point(lambda v: min(255, int(v * 1.25 * shadow)))
        off = Image.new("L", (bw, bh), 0)
        off.paste(tight, (0, 3))
        comb = Image.fromarray(np.clip(np.maximum(np.asarray(soft) * 0.62, np.asarray(off) * 0.78) * min(1.25, max(1.0, shadow)), 0, 255).astype(np.uint8))
        sh.putalpha(comb)
    out = Image.alpha_composite(sh, fg)
    return Layer(out, cx - bw // 2, y - pad)


def flag_img(country, h=34):
    w = int(h * 1.0) if country == "CH" else int(h * 1.55)
    s = 4
    im = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    if country == "CH":
        d.rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=int(h * s * 0.16), fill=(218, 41, 28, 255))
        t, l = h * s * 0.20, h * s * 0.62
        c = h * s / 2
        d.rectangle([c - t / 2, c - l / 2, c + t / 2, c + l / 2], fill="white")
        d.rectangle([c - l / 2, c - t / 2, c + l / 2, c + t / 2], fill="white")
    else:  # FI
        d.rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=int(h * s * 0.14), fill=(255, 255, 255, 255))
        t = h * s * 0.27
        d.rectangle([w * s * 0.28, 0, w * s * 0.28 + t, h * s], fill=(0, 47, 108, 255))
        d.rectangle([0, h * s / 2 - t / 2, w * s, h * s / 2 + t / 2], fill=(0, 47, 108, 255))
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * s - 1, h * s - 1], radius=int(h * s * 0.14), fill=255)
        im.putalpha(mask)
    return im.resize((w, h), Image.LANCZOS)


def stamp_layer(time_txt, place, country, y=300):
    """Clock time + flag + place, centred near the top."""
    pad = 60
    f_t, f_p = font("lat_black", 96), font("lat_semi", 34)
    tw = int(f_t.getlength(time_txt))
    track = 5
    pw = int(sum(f_p.getlength(c) + track for c in place))
    fl = flag_img(country, 32)
    row_w = fl.width + 14 + pw
    bw = max(tw, row_w) + 2 * pad
    bh = 92 + 18 + 44 + 2 * pad + 30
    fg = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)
    d.text(((bw - tw) // 2, pad), time_txt, font=f_t, fill="white")
    ry = pad + 92 + 30
    rx = (bw - row_w) // 2
    fg.alpha_composite(fl, (rx, ry + 3))
    x = rx + fl.width + 14
    for c in place:
        d.text((x, ry), c, font=f_p, fill=(255, 255, 255, 245))
        x += f_p.getlength(c) + track
    alpha = fg.getchannel("A")
    soft = alpha.filter(ImageFilter.GaussianBlur(14)).point(lambda v: min(255, int(v * 3.0)))
    tight = alpha.filter(ImageFilter.GaussianBlur(4)).point(lambda v: min(255, int(v * 1.6)))
    comb = Image.fromarray(np.maximum(np.asarray(soft) * 0.70, np.asarray(tight) * 0.90).astype(np.uint8))
    sh = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    sh.putalpha(comb)
    return Layer(Image.alpha_composite(sh, fg), W // 2 - bw // 2, y - pad)


def bilingual(ko, ja, y, ko_size=64, ja_size=48, gap=18, shadow=1.3):
    lines = []
    for i, part in enumerate(ko.split("\n")):
        lines.append(dict(text=part, font="ko_serif_b", size=ko_size, gap=6))
    lines[-1]["gap"] = gap
    for part in ja.split("\n"):
        lines.append(dict(text=part, font="ja_serif", size=ja_size, fill=(255, 255, 255, 246), gap=4, tracking=1))
    return text_block(lines, W // 2, y, shadow=shadow)


# --------------------------------------------------------------------------- media
def probe(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                   "stream=width,height,r_frame_rate:stream_side_data=rotation:format=duration",
                                   "-of", "json", path])
    j = json.loads(out)
    s = j["streams"][0]
    w, h = s["width"], s["height"]
    rot = 0
    for sd in s.get("side_data_list", []):
        rot = int(sd.get("rotation", 0))
    if abs(rot) in (90, 270):
        w, h = h, w
    num, den = s["r_frame_rate"].split("/")
    return w, h, float(num) / float(den or 1), float(j["format"].get("duration", 0) or 0)


def video_frames(path, start, n, cx=0.5, cy=0.5, speed=1.0, zoom=(1.0, 1.0), scale=1.0):
    ow, oh = int(W * scale), int(H * scale)
    z = max(zoom)
    # crop the largest 9:16 window centred on (cx, cy); leave zoom headroom for the push-in
    vf = (f"setpts=(PTS-STARTPTS)/{speed},fps={FPS},"
          f"crop='min(iw,ih*9/16)':'min(ih,iw*16/9)':'max(0,min(iw-ow,{cx}*iw-ow/2))':'max(0,min(ih-oh,{cy}*ih-oh/2))',"
          f"scale={int(ow * z / 2) * 2}:{int(oh * z / 2) * 2}:flags=lanczos")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", path, "-frames:v", str(n), "-vf", vf,
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout
    bw, bh = int(ow * z / 2) * 2, int(oh * z / 2) * 2
    got = len(raw) // (bw * bh * 3)
    if got < n:
        raise RuntimeError(f"{path}: only {got} of {n} frames available from {start}s; lower 'start'")
    arr = np.frombuffer(raw[:got * bw * bh * 3], dtype=np.uint8).reshape(got, bh, bw, 3)
    out = []
    for i in range(n):
        fr = arr[min(i, got - 1)]
        s = i / max(1, n - 1)
        zz = zoom[0] + (zoom[1] - zoom[0]) * s
        out.append(_zoom_crop(fr, 1.0 / zz, ow, oh))
    return out


def _zoom_crop(img, rel, ow, oh, px=0.0, py=0.0):
    """Take a centred window that is rel (<=1) of img and resample it to (ow, oh)."""
    h, w = img.shape[:2]
    cw, ch = w * (ow / w) / (ow / w) * rel, h * rel
    cw = w * rel
    x0 = (w - cw) / 2 + px * (w - cw) / 2
    y0 = (h - ch) / 2 + py * (h - ch) / 2
    M = np.array([[cw / ow, 0, x0], [0, ch / oh, y0]], dtype=np.float32)
    return cv2.warpAffine(img, M, (ow, oh), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)


def photo_frames(path, n, cx=0.5, cy=0.5, zoom=(1.06, 1.16), pan=(0.0, 0.0), scale=1.0, blur=None):
    """Ken Burns frames from a still. blur: a list of polygons, each [[x, y], ...] in fractions
    of the image, softened so small text in that area (a screen, a sign) cannot be read."""
    ow, oh = int(W * scale), int(H * scale)
    Image.MAX_IMAGE_PIXELS = None
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    iw, ih = im.size
    for poly in (blur or []):
        r = max(4, int(iw * 0.0045))
        soft = im.filter(ImageFilter.GaussianBlur(r))
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).polygon([(int(x * iw), int(y * ih)) for x, y in poly], fill=255)
        im = Image.composite(soft, im, mask.filter(ImageFilter.GaussianBlur(max(2, r // 3))))
    cw = min(iw, ih * 9 / 16)
    ch = min(ih, iw * 16 / 9)
    x0 = max(0, min(iw - cw, cx * iw - cw / 2))
    y0 = max(0, min(ih - ch, cy * ih - ch / 2))
    im = im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch)))
    base_w = int(ow * 1.3)
    im = im.resize((base_w, int(base_w * 16 / 9)), Image.LANCZOS)
    arr = np.asarray(im)
    zmax = max(zoom)
    out = []
    for i in range(n):
        s = i / max(1, n - 1)
        z = zoom[0] + (zoom[1] - zoom[0]) * s
        out.append(_zoom_crop(arr, 1.0 / z, ow, oh, px=pan[0] * (2 * s - 1), py=pan[1] * (2 * s - 1)))
    return out


_vig = {}


def vignette(shape, strength=0.22):
    k = (shape[0], shape[1], strength)
    if k not in _vig:
        h, w = shape[:2]
        y, x = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2) / math.sqrt(2)
        _vig[k] = (1 - strength * np.clip(r, 0, 1) ** 2.2)[..., None].astype(np.float32)
    return _vig[k]


def grade(f, exposure=1.0, contrast=1.06, sat=1.10, warmth=0.0, lift=0.0):
    """f: float32 RGB 0..1 (modified copy returned)."""
    f = f * exposure
    if warmth:
        f = f * np.array([1 + warmth, 1.0, 1 - warmth], dtype=np.float32)
    f = np.clip(f, 0, 1)
    # gentle S-curve
    f = f + contrast_amt(contrast) * (f - 0.5) * (1 - np.abs(2 * f - 1)) * 1.0
    lum = (f[..., 0:1] * 0.299 + f[..., 1:2] * 0.587 + f[..., 2:3] * 0.114)
    f = lum + (f - lum) * sat
    if lift:
        f = f * (1 - lift) + lift
    return np.clip(f * vignette(f.shape), 0, 1)


def contrast_amt(c):
    return (c - 1.0) * 2.0


_scrims = {}


def scrim(shape, top=0.30, mid=0.0, bottom=0.34):
    k = (shape[0], shape[1], top, mid, bottom)
    if k not in _scrims:
        h = shape[0]
        y = np.arange(h, dtype=np.float32) / h
        a = top * np.clip(1 - y / 0.34, 0, 1) ** 1.6
        a += bottom * np.clip((y - 0.52) / 0.48, 0, 1) ** 1.3
        a += mid * np.exp(-((y - 0.67) / 0.09) ** 2)
        _scrims[k] = (1 - np.clip(a, 0, 0.8))[:, None, None].astype(np.float32)
    return _scrims[k]


# --------------------------------------------------------------------------- reel
def _rss_mb():
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024


def fade(t, t0, t1, fin=0.2, fout=0.14):
    if t < t0 or t >= t1:
        return 0.0
    a = min(1.0, (t - t0) / fin) if fin > 0 else 1.0
    b = min(1.0, (t1 - t) / fout) if fout > 0 else 1.0
    return max(0.0, min(a, b))


def build(tl, scale=1.0, only_frames=None, log=lambda *a: print(*a, flush=True)):
    P = tl["cut_period"]
    hook_d = tl["hook"]["duration"]
    cuts = tl["cuts"]
    end_d = tl["ending"]["duration"]
    total = hook_d + P * len(cuts) + end_d
    nf_total = int(round(total * FPS))
    hook_n = int(round(hook_d * FPS))
    ow, oh = int(W * scale), int(H * scale)

    def want(i):
        return only_frames is None or i in only_frames

    # ---------- prepared overlays
    hk = tl["hook"]
    hook_layer = bilingual(hk["ko"], hk["ja"], hk.get("y", 250), ko_size=hk.get("ko_size", 60), ja_size=hk.get("ja_size", 42), gap=22)
    reply_layer = text_block([
        dict(text=hk["reply_ko"], font="ko_sans", size=58, gap=12),
        dict(text=hk["reply_ja"], font="ja_sans", size=44, fill=(255, 255, 255, 244)),
    ], W // 2, hk.get("reply_y", 500)) if hk.get("reply_ko") else None
    stamps = [stamp_layer(c["time"], c["place"].upper(), c["country"]) for c in cuts]
    subs = [(s, bilingual(s["ko"], s["ja"], s.get("y", tl.get("sub_y", 1255)))) for s in tl["subtitles"]]
    en = tl["ending"]
    end_layer = bilingual(en["ko"], en["ja"], en.get("y", 800), ko_size=en.get("ko_size", 70), ja_size=en.get("ja_size", 46), gap=22)
    cta_layer = text_block([
        dict(text=en["cta_ko"], font="ko_sans", size=54, gap=14),
        dict(text=en["cta_ja"], font="ja_sans", size=38, fill=(255, 255, 255, 244)),
    ], W // 2, en.get("cta_y", 1120))
    end_stamp = stamp_layer(en["time"], en["place"].upper(), en["country"]) if en.get("time") else None

    globe = Globe(os.path.join(ASSETS, "globe", "day.jpg"), os.path.join(ASSETS, "globe", "night.jpg"))
    g = hk["globe"]
    routes = [great_circle(tuple(r["from"]), tuple(r["to"])) for r in g["routes"]]

    def pin_layers():
        out = {}
        for p in g["pins"]:
            lines = [dict(text=p["label"], font="lat_semi", size=30, tracking=2)]
            out[p["label"]] = text_block(lines, 0, 0, shadow=1.2)
        return out

    pins = pin_layers()

    def hook_frame(i):
        t = i / FPS
        k = ease_in_out((t - g["t0"]) / (g["t1"] - g["t0"]))
        lat0 = g["from"][0] + (g["to"][0] - g["from"][0]) * k
        lon0 = g["from"][1] + (g["to"][1] - g["from"][1]) * k
        R = g["r0"] + (g["r1"] - g["r0"]) * ease_in_out(t / g["t1"])
        cx, cy = W / 2, g.get("cy", 1080)
        # punch-in at the very end
        tz = (t - (hook_d - g["zoom_d"])) / g["zoom_d"]
        if tz > 0:
            zz = tz ** 2.4
            R = R * (1 + 5.5 * zz)
            tgt = g["zoom_to"]
            lat0 += (tgt[0] - lat0) * min(1, tz * 1.6)
            lon0 += (tgt[1] - lon0) * min(1, tz * 1.6)
        fr = globe.render(t, lat0, lon0, cx, cy, R)
        # routes + pins drawn with PIL on an overlay
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(ov)
        prog = ease_in_out((t - g["route_t0"]) / (g["route_t1"] - g["route_t0"]))
        for route in routes:
            xs, ys, zs = globe.project(route, lat0, lon0, cx, cy, R)
            m = int(prog * (len(route) - 1))
            seg = []
            for j in range(m + 1):
                if zs[j] > 0.02:
                    seg.append((xs[j], ys[j]))
                else:
                    if len(seg) > 1:
                        d.line(seg, fill=(255, 226, 168, 235), width=5, joint="curve")
                    seg = []
            if len(seg) > 1:
                d.line(seg, fill=(255, 226, 168, 235), width=5, joint="curve")
            if 0 < prog < 1 and zs[m] > 0.02:
                d.ellipse([xs[m] - 9, ys[m] - 9, xs[m] + 9, ys[m] + 9], fill=(255, 255, 255, 255))
        glow = ov.filter(ImageFilter.GaussianBlur(9))
        ov = Image.alpha_composite(glow, ov)
        d = ImageDraw.Draw(ov)
        labels = []
        for p in g["pins"]:
            a = fade(t, p["t0"], p["t1"], 0.18, 0.25)
            if a <= 0:
                continue
            from globe import _unit
            x, y, z = globe.project(_unit(p["lat"], p["lon"])[None, :], lat0, lon0, cx, cy, R)
            if z[0] < 0.08:
                continue
            x, y = float(x[0]), float(y[0])
            pop = ease_out((t - p["t0"]) / 0.3)
            r = 9 * pop
            col = tuple(p.get("color", [255, 255, 255]))
            d.ellipse([x - r * 2.1, y - r * 2.1, x + r * 2.1, y + r * 2.1], outline=col + (int(170 * a),), width=3)
            d.ellipse([x - r, y - r, x + r, y + r], fill=col + (int(255 * a),))
            labels.append((p, x, y, a))
        o = np.asarray(ov, dtype=np.float32) / 255.0
        fr = fr * (1 - o[..., 3:4]) + o[..., :3] * o[..., 3:4]
        for p, x, y, a in labels:
            L = pins[p["label"]]
            h_, w_ = L.alpha.shape[:2]
            dx = p.get("dx", 22)
            L.x = int(x + dx - 60) if dx >= 0 else int(x + dx - w_ + 60)
            L.y = int(y - h_ / 2 + p.get("dy", 0))
            L.blit(fr, a)
        fr *= scrim(fr.shape, top=0.0, bottom=0.0)
        a = fade(t, hk.get("text_t0", 0.0), hook_d - g["zoom_d"], 0.3, 0.2)
        hook_layer.blit(fr, a, dy=(1 - min(1, a)) * 14)
        if reply_layer is not None:
            a2 = fade(t, hk.get("reply_t0", 1.5), hook_d - g["zoom_d"], 0.22, 0.2)
            reply_layer.blit(fr, a2, dy=(1 - min(1, a2)) * 12)
        if tz > 0.45:
            w_ = ((tz - 0.45) / 0.55) ** 1.6
            fr = fr * (1 - w_) + w_
        return np.clip(fr, 0, 1)

    frames_out = {}

    def emit(i, fr):
        nonlocal writer
        u8 = (np.clip(fr, 0, 1) * 255 + 0.5).astype(np.uint8)
        if scale != 1.0 and u8.shape[1] != ow:
            u8 = cv2.resize(u8, (ow, oh), interpolation=cv2.INTER_AREA)
        if only_frames is not None:
            if i in only_frames:
                frames_out[i] = u8
        else:
            writer.stdin.write(u8.tobytes())

    writer = None
    out_path = tl["_out"]
    if only_frames is None:
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        writer = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{ow}x{oh}",
                                   "-r", str(FPS), "-i", "-", "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "19", "-maxrate", "14M", "-bufsize", "28M",
                                   "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709",
                                   "-colorspace", "bt709", "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)

    # ---------- hook
    log(f"total {total:.2f}s = {nf_total} frames; hook {hook_n} frames  rss={_rss_mb()}MB")
    for i in range(hook_n):
        if want(i):
            emit(i, hook_frame(i))

    # ---------- montage
    def load(c, n):
        path = os.path.join(ASSETS, c["src"])
        gz = tuple(c.get("zoom", (1.0, 1.05) if c["kind"] == "video" else (1.05, 1.15)))
        if c["kind"] == "video":
            return video_frames(path, c.get("start", 0.0), n, c.get("cx", 0.5), c.get("cy", 0.5), c.get("speed", 1.0), gz)
        return photo_frames(path, n, c.get("cx", 0.5), c.get("cy", 0.5), gz, tuple(c.get("pan", (0.0, 0.0))), blur=c.get("blur"))

    bounds = [int(round((hook_d + P * k) * FPS)) for k in range(len(cuts) + 1)]
    for ci, c in enumerate(cuts):
        f0, f1 = bounds[ci], bounds[ci + 1]
        n = f1 - f0
        if only_frames is not None and not any(f0 <= k < f1 for k in only_frames):
            continue
        frames = load(c, n)
        gp = c.get("grade", {})
        for k in range(n):
            i = f0 + k
            if not want(i):
                continue
            t = i / FPS
            fr = grade(frames[k].astype(np.float32) / 255.0, **gp)
            fr = fr * scrim(fr.shape, top=c.get("scrim_top", 0.30), mid=c.get("scrim_mid", 0.16), bottom=0.30)
            if ci == 0 and k < 5:  # come out of the white flash
                w_ = (1 - k / 5) ** 1.5
                fr = fr * (1 - w_) + w_
            # time stamp pops with each cut
            pop = ease_out(k / 4)
            stamps[ci].blit(fr, min(1.0, 0.35 + pop), dy=(1 - pop) * 10)
            for s, L in subs:
                st0 = hook_d + P * s["from_cut"]
                st1 = hook_d + P * (s["to_cut"] + 1) - 0.06
                a = fade(t, st0 + 0.04, st1, 0.2, 0.14)
                L.blit(fr, a, dy=(1 - min(1, a)) * 12)
            emit(i, fr)
        log(f"cut {ci + 1}/{len(cuts)} {c['time']} {c['place']}  rss={_rss_mb()}MB")

    # ---------- ending
    f0 = bounds[-1]
    n = nf_total - f0
    if only_frames is None or any(k >= f0 for k in only_frames):
        ec = en["clip"]
        frames = load(ec, n)
        gp = ec.get("grade", {})
        for k in range(n):
            i = f0 + k
            if not want(i):
                continue
            te = k / FPS
            fr = grade(frames[k].astype(np.float32) / 255.0, **gp)
            dim = en.get("dim", 0.34) * ease_out(te / 0.9)
            fr = fr * (1 - dim)
            fr = fr * scrim(fr.shape, top=0.30, mid=0.0, bottom=0.30)
            if end_stamp is not None:
                a0 = fade(te, 0.0, en.get("stamp_out", 1.0), 0.12, 0.3)
                end_stamp.blit(fr, a0)
            a = fade(te, en.get("line_t0", 0.35), 99, 0.4, 0)
            end_layer.blit(fr, a, dy=(1 - a) * 16)
            a = fade(te, en.get("cta_t0", 1.45), 99, 0.4, 0)
            cta_layer.blit(fr, a, dy=(1 - a) * 12)
            fo = en.get("fade_out", 0.35)
            if te > end_d - fo:
                fr = fr * max(0.0, (end_d - te) / fo) ** 0.8
            emit(i, fr)
        log("ending done")

    if writer is not None:
        writer.stdin.close()
        if writer.wait() != 0:
            raise RuntimeError("ffmpeg encode failed")
    return total, frames_out


def whoosh(path, dur=0.9, sr=44100, seed=3):
    """A soft airy riser for the globe punch-in (pure synthesis, no samples)."""
    import wave
    rng = np.random.default_rng(seed)
    n = int(dur * sr)
    noise = rng.standard_normal(n)
    t = np.arange(n) / sr
    out = np.zeros(n)
    # band-pass sweep via overlapping FFT blocks
    blk = 2048
    hop = blk // 4
    win = np.hanning(blk)
    for s in range(0, n - blk, hop):
        k = s / n
        fc = 300 * (9000 / 300) ** k
        spec = np.fft.rfft(noise[s:s + blk] * win)
        fr = np.fft.rfftfreq(blk, 1 / sr)
        spec *= np.exp(-0.5 * (np.log2(np.maximum(fr, 1) / fc) / 0.55) ** 2)
        out[s:s + blk] += np.fft.irfft(spec) * win
    env = (t / dur) ** 2.6 * np.clip((dur - t) / 0.05, 0, 1)
    out = out / (np.abs(out).max() + 1e-9) * env * 0.5
    pcm = (np.stack([out, out], axis=1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def mux(tl, video, total, out):
    m = tl["music"]
    music = os.path.join(ASSETS, m["src"])
    wh = os.path.join(os.path.dirname(video), "_whoosh.wav")
    hook_d = tl["hook"]["duration"]
    whoosh(wh, 0.9)
    delay = int(max(0, hook_d - 0.9 + 0.04) * 1000)
    fo = m.get("fade_out", 1.2)
    af = (f"[1:a]atrim=start={m['start']}:duration={total},asetpts=PTS-STARTPTS,"
          f"afade=t=in:st=0:d={m.get('fade_in', 0.25)},afade=t=out:st={total - fo}:d={fo},volume={m.get('gain', 1.0)}[mu];"
          f"[2:a]adelay={delay}|{delay},volume={m.get('whoosh_gain', 0.5)}[wh];"
          f"[mu][wh]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000,asetpts=N/SR/TB[a]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", video, "-i", music, "-i", wh, "-filter_complex", af,
                    "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-t", f"{total:.3f}",
                    "-movflags", "+faststart", out], check=True)
    os.remove(wh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeline", default=os.path.join(ROOT, "timeline.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "europe_life_reel.mp4"))
    ap.add_argument("--preview", type=float, default=1.0, help="render scale, e.g. 0.5")
    ap.add_argument("--stills", action="store_true", help="write one check frame per scene instead of a video")
    args = ap.parse_args()
    tl = json.load(open(args.timeline, encoding="utf-8"))
    P, hook_d = tl["cut_period"], tl["hook"]["duration"]
    if args.stills:
        want = [int(FPS * t) for t in (0.4, 1.2, 2.0, 2.6, hook_d - 0.12)]
        want += [int(round((hook_d + P * (i + 0.6)) * FPS)) for i in range(len(tl["cuts"]))]
        end0 = hook_d + P * len(tl["cuts"])
        want += [int(round((end0 + x) * FPS)) for x in (0.25, 1.2, 2.4)]
        tl["_out"] = args.out
        total, frames = build(tl, 1.0, set(want))
        d = os.path.join(ROOT, "out", "stills")
        os.makedirs(d, exist_ok=True)
        for i, fr in sorted(frames.items()):
            Image.fromarray(fr).save(os.path.join(d, f"f{i:04d}.jpg"), quality=90)
        print("stills:", len(frames), "total", total)
        return
    tmp = args.out.replace(".mp4", "._video.mp4")
    tl["_out"] = tmp
    total, _ = build(tl, args.preview)
    mux(tl, tmp, total, args.out)
    os.remove(tmp)
    print("wrote", args.out, f"{total:.2f}s")


if __name__ == "__main__":
    main()
