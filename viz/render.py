"""Render one timeline trace to an ASCII-streak chart video: python viz/render.py trace.json out.mov [--still]."""
import json, math, random, subprocess, sys
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, FPS = 1920, 1080, 30
FX0, FX1, FY0, FY1 = 170, 1780, 120, 720   # trace field
BY0, BAX, BY1 = 790, 905, 1050               # token bars: top, axis, bottom
T_IN, T_SWEEP, T_HOLD = 0.6, 10.0, 3.0
BG = np.array([4, 5, 8], np.float32)
INK = np.array([228, 232, 240], np.float32)
MONO = "/System/Library/Fonts/Menlo.ttc"
LANES = [  # name, colour, matcher (top to bottom)
    ("OUT", (245, 245, 245), lambda p: p.startswith("/app/out")),
    ("WORK", (120, 124, 136), lambda p: p.startswith(("/app/work", "/app/scripts", "/tmp"))),
    ("FILED", (110, 215, 150), lambda p: "/tax/filed" in p or "/UStVA" in p or "/UVA" in p or "/ZM_" in p),
    ("ZMIG", (238, 64, 46), lambda p: "ZMIG" in p),
    ("S/4", (240, 172, 64), lambda p: "/sap/s4/" in p),
    ("ECC", (84, 204, 232), lambda p: "/sap/ecc/" in p),
    ("MASTER", (160, 140, 235), lambda p: p.startswith("/app/sap") or p.startswith("/app/tax")),
    ("RULES", (150, 150, 110), lambda p: True),
]
TOOL_COL = {"bash": (98, 128, 212), "python": (140, 162, 226), "write": (140, 210, 160)}
SCORE_KEYS = ["register", "recon", "correction", "tool", "memo", "traps", "format"]


def font(sz):
    return ImageFont.truetype(MONO, sz)


def lane_of(path):
    """Index of the first lane whose matcher accepts path."""
    return next(i for i, (_, _, m) in enumerate(LANES) if m(path))


def lane_y(i):
    return FY0 + 30 + i * (FY1 - FY0 - 60) / (len(LANES) - 1)


def layout(tr):
    """Per-event x, coverage-curve y and lane hits."""
    ev, wall = tr["events"], tr["wall_s"]
    xs = [FX0 + (FX1 - FX0) * e["t"] / wall for e in ev]
    allf = {p for e in ev for p in e["reads"] + e["writes"]}
    seen, ys = set(), []
    for e in ev:
        seen.update(e["reads"] + e["writes"])
        ys.append(FY1 - (FY1 - FY0) * 0.92 * len(seen) / max(1, len(allf)))
    hits = [(i, lane_of(p), p in e["writes"]) for i, e in enumerate(ev) for p in e["reads"] + e["writes"]]
    return xs, ys, hits


def streak_layer(tr, xs, ys):
    """Static luminance layer: each call is a band of vertical glyph columns made of its own command text."""
    rnd, ev = random.Random(1), tr["events"]
    img = Image.new("L", (W, H), 0)
    d, f = ImageDraw.Draw(img), font(9)
    mx = math.log(max(e["out_bytes"] for e in ev) + 1)
    for i, e in enumerate(ev):
        x0, x1 = xs[i], (xs[i + 1] if i + 1 < len(ev) else FX1)
        b = math.log(e["out_bytes"] + 1) / mx
        chars = "".join(c for c in e["cmd"] if not c.isspace()) or "."
        cy = ys[i]
        for cx in np.arange(x0, max(x0 + 7, x1), 7):
            top = FY1 - (FY1 - FY0) * b * rnd.uniform(.55, 1.0)
            k = rnd.randrange(len(chars))
            for y in range(int(FY1 - 10), int(top), -10):
                lit = y > cy
                a = (40 + 200 * b) if lit else (12 + 70 * b)
                a *= rnd.uniform(.45, 1.0)
                d.text((cx, y), chars[k % len(chars)], fill=int(a), font=f)
                k += 1
        for _ in range(int(min(60, e["out_bytes"] / 900))):  # granular dots under the curve
            r = rnd.choice([1, 1, 1, 2, 2, 3, 4])
            x, y = rnd.uniform(x0, max(x0 + 4, x1)), rnd.uniform(cy, FY1)
            d.ellipse([x - r, y - r, x + r, y + r], fill=int(160 + 90 * b))
    return np.asarray(img, np.float32) / 255.0


def bars_layer(tr):
    """Static RGBA mirrored token bars per turn: context up, output down; returns layer and per-bar right edge."""
    ev = tr["events"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pin, pout = max(e["in_tok"] for e in ev), max(e["out_tok"] for e in ev)
    bw, ends = (FX1 - FX0) / len(ev), []
    for i, e in enumerate(ev):
        x0, x1 = FX0 + i * bw, FX0 + (i + 1) * bw - max(1, bw * .18)
        col = (235, 80, 60) if e["error"] else TOOL_COL["write"] if e["writes"] and "/app/out" in e["writes"][0] else TOOL_COL.get(e["tool"], TOOL_COL["bash"])
        hu = (BAX - 6 - BY0) * e["in_tok"] / pin
        hd = (BY1 - BAX - 6) * e["out_tok"] / pout
        d.rectangle([x0, BAX - 6 - hu, x1, BAX - 6], fill=col + (235,))
        d.rectangle([x0, BAX + 6, x1, BAX + 6 + hd], fill=tuple(int(c * .45) for c in col) + (200,))
        ends.append(x1)
    d.line([FX0, BAX, FX1, BAX], fill=(60, 64, 78, 255), width=1)
    for x in range(FX0, FX1, 9):  # dotted peak line
        d.line([x, BY0, x + 3, BY0], fill=(110, 112, 124, 255))
    lab = f"peak ctx {pin / 1000:.0f}k"
    d.text((FX1 - d.textlength(lab, font=font(15)), BY0 - 22), lab, fill=(170, 172, 182, 255), font=font(15))
    d.text((FX0 + 4, BY1 - 4), f"output tok · peak {pout / 1000:.1f}k", fill=(100, 102, 114, 255), font=font(12))
    return np.asarray(img, np.float32) / 255.0, ends


def overlay(tr, xs, ys, hits, t, k, px, fonts):
    """Dynamic RGBA layer: lanes, hit dots, curve, header, ticker, scores."""
    ev = tr["events"]
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f12, f15, f22, f64 = fonts
    for li, (name, col, _) in enumerate(LANES):
        y = lane_y(li)
        d.line([FX0, y, FX1, y], fill=col + (38,))
        d.line([FX0, y, px, y], fill=col + (200,), width=2 if name in ("ZMIG", "ECC", "S/4") else 1)
        lab = Image.new("RGBA", (90, 18), (0, 0, 0, 0))
        ImageDraw.Draw(lab).text((0, 0), name, fill=col + (230,), font=f12)
        lab = lab.rotate(90, expand=True)
        img.alpha_composite(lab, (FX0 - 40, int(y - 90)))
    now = ev[k]["t"] if k >= 0 else 0
    for i, li, wr in hits:
        if i > k:
            continue
        age = now - ev[i]["t"]
        r = 2 + 1.8 * math.log1p(ev[i]["out_bytes"] / 2000) + (1.5 if wr else 0)
        r *= 1 + 1.8 * math.exp(-age / 6)  # pop on arrival
        col = LANES[li][1]
        x, y = xs[i], lane_y(li)
        d.ellipse([x - r, y - r, x + r, y + r], fill=col + (255,))
    mo = max(e["out_tok"] for e in ev) or 1
    for i in range(max(0, k + 1)):  # coverage curve as dots + line
        if i:
            d.line([xs[i - 1], ys[i - 1], xs[i], ys[i - 1], xs[i], ys[i]], fill=(255, 255, 255, 230), width=2)
        r = 1.5 + 5 * math.sqrt(ev[i]["out_tok"] / mo)
        d.ellipse([xs[i] - r, ys[i] - r, xs[i] + r, ys[i] + r], fill=(255, 255, 255, 255))
        if ev[i]["error"]:
            d.line([xs[i], FY0 - 14, xs[i], FY0 - 4], fill=(238, 64, 46, 255), width=2)
    if px < FX1:
        d.line([px, FY0 - 20, px, FY1 + 4], fill=(255, 255, 255, 90))
    # header
    cost = tr["cost_usd"] * (k + 1) / len(ev)
    tok = sum(e["in_tok"] + e["out_tok"] for e in ev[:k + 1])
    d.text((FX0, 40), f"{tr['harness']}", fill=(245, 245, 245, 255), font=f22)
    d.text((FX0 + 20 + d.textlength(tr["harness"], font=f22), 46), f"{tr['model']} · {tr['task']}", fill=(120, 122, 134, 255), font=f15)
    hms = f"{int(now // 3600):02d}:{int(now % 3600 // 60):02d}:{int(now % 60):02d}"
    stats = f"t {hms}   calls {k + 1:>3}   tok {tok / 1e6:4.2f}M   ${cost:5.3f}"
    d.text((FX1 - d.textlength(stats, font=f15), 46), stats, fill=(200, 202, 212, 255), font=f15)
    # ticker: the current call's code, the text underlay of the trace
    if k >= 0:
        e = ev[k]
        lines = e["cmd"].splitlines()[:3]
        li = lane_of((e["writes"] + e["reads"] + ["/app/x"])[0])
        d.text((FX0, FY1 + 18), "›", fill=LANES[li][1] + (255,), font=f15)
        for j, ln in enumerate(lines):
            d.text((FX0 + 18, FY1 + 18 + j * 17), ln[:150], fill=(200, 202, 212, 235 - j * 70), font=f12 if j else f15)
    # end scores: row of dots sized by component score
    st = t - T_IN - T_SWEEP
    if st > 0:  # dim the field, then a centred score card
        d.rectangle([FX0 - 60, FY0 - 24, FX1 + 10, FY1 + 6], fill=(4, 5, 8, int(170 * min(1, st / .6))))
        for j, key in enumerate(SCORE_KEYS):
            a = min(1, max(0, (st - j * .18) / .4))
            v = tr["score"].get(key, 0)
            cx, cy = (FX0 + FX1) / 2 + 130 * (j - (len(SCORE_KEYS) - 1) / 2), FY0 + 380
            r = 4 + 26 * v
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, int(255 * a)))
            val = f"{v:.2f}"
            d.text((cx - d.textlength(val, font=f15) / 2, cy + 38), val, fill=(230, 232, 240, int(255 * a)), font=f15)
            d.text((cx - d.textlength(key, font=f12) / 2, cy + 58), key, fill=(150, 152, 164, int(255 * a)), font=f12)
        a = min(1, max(0, (st - 1.4) / .5))
        tot = f"{tr['total']:.2f}"
        d.text(((FX0 + FX1) / 2 - d.textlength(tot, font=f64) / 2, FY0 + 200), tot, fill=(255, 255, 255, int(255 * a)), font=f64)
    return np.asarray(img, np.float32) / 255.0


def frames(tr, pick=None):
    """Yield uint8 RGB frames (only indices in pick, if given)."""
    xs, ys, hits = layout(tr)
    lum = streak_layer(tr, xs, ys)
    bars, ends = bars_layer(tr)
    fonts = (font(12), font(15), font(22), font(64))
    grain = [np.random.default_rng(s).normal(0, 4.5, (H, W, 1)).astype(np.float32) for s in range(6)]
    colx = np.arange(W, dtype=np.float32)[None, :]
    n = int((T_IN + T_SWEEP + T_HOLD) * FPS)
    ev = tr["events"]
    for fi in (pick if pick is not None else range(n)):
        t = fi / FPS
        p = min(1, max(0, (t - T_IN) / T_SWEEP))
        p = p * p * (3 - 2 * p) * .15 + p * .85  # slight ease
        px = FX0 + (FX1 - FX0) * p
        k = max([i for i, x in enumerate(xs) if x <= px], default=-1)
        m = np.clip((px - colx) / 3 + 1, 0, 1)                    # reveal mask, soft edge
        edge = np.exp(-np.clip(px - colx, 0, None) / 40) * (colx <= px)  # glow behind playhead
        L = lum * m * (1 + 1.6 * edge)
        fade = min(1, t / T_IN)
        img = BG + (INK - BG) * L[..., None] * fade
        bm = np.zeros((1, W), np.float32)
        if k >= 0:
            bm[0, :int(ends[k]) + 1] = 1
        a = bars[..., 3:] * bm[..., None]
        img = img * (1 - a) + bars[..., :3] * 255 * a
        ov = overlay(tr, xs, ys, hits, t, k, px, fonts)
        a = ov[..., 3:] * fade
        img = img * (1 - a) + ov[..., :3] * 255 * a
        small = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((W // 4, H // 4), Image.BILINEAR)
        bloom = np.asarray(small.filter(ImageFilter.GaussianBlur(5)).resize((W, H), Image.BILINEAR), np.float32)
        img = img + .55 * bloom + grain[fi % 6]
        yield np.clip(img, 0, 255).astype(np.uint8)


def main(src, dst, still=False):
    tr = json.load(open(src))
    if still:  # final frame only
        n = int((T_IN + T_SWEEP + T_HOLD) * FPS)
        Image.fromarray(next(frames(tr, [n - 1]))).save(dst)
        return
    ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                           "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-crf", "15", "-preset", "slow",
                           "-pix_fmt", "yuv420p", dst], stdin=subprocess.PIPE)
    for fr in frames(tr):
        ff.stdin.write(fr.tobytes())
    ff.stdin.close()
    ff.wait()


if __name__ == "__main__":
    for a in sys.argv[3:]:
        if a.startswith("--sweep="):
            T_SWEEP = float(a.split("=")[1])
    main(sys.argv[1], sys.argv[2], "--still" in sys.argv)
