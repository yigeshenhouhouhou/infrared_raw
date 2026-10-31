#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ir_wb_gui.py — 红外 RAW 白平衡 + 后期一体化界面（本地网页）

启动: 双击同目录 "启动红外白平衡.bat"
输出: 环境变量 IR_WB_OUT，否则项目内 output（必须在 E 盘）

处理顺序（预览与导出严格共用 process_array）:
    RAW 相机通道 → 浮点白平衡/曝光 → 高光映射 → sRGB → 创意调色
"""

import io
import html
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ir_wb  # noqa: E402
import raw_backend as backend
import math
from pathlib import Path

os.environ["TEMP"] = os.path.join(HERE, "runtime", "tmp")
os.environ["TMP"] = os.environ["TEMP"]
os.makedirs(os.environ["TEMP"], exist_ok=True)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def pick_outdir():
    path = os.path.abspath(os.environ.get("IR_WB_OUT") or os.path.join(HERE, "output"))
    if os.path.splitdrive(path)[0].upper() != "E:":
        raise ValueError("输出目录必须位于 E 盘（IR_WB_OUT）")
    return path


OUTDIR = pick_outdir()
CACHE_DIR = os.path.join(OUTDIR, ".cache")
os.makedirs(OUTDIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

ENTRIES = {}
LOCK = threading.Lock()
PORT = int(os.environ.get("IR_WB_PORT", "8765"))

DEFAULTS = {
    "color_mode": "reference590",
    "color_sep": 0.0,
    "gold": 0.0,
    "blue": 0.0,
    "vibrance": 0.0,
    "warm_sat": 0.0,
    "cool_sat": 0.0,
    "brightness": 0.0,
    "black": 0.0,       # 保留用户新增的显示黑场调整
    "exposure": 0.0,    # 曝光，线性域，单位 EV
    "highlights": 0.0,  # 高光压缩 0-100（软膝，不是硬裁）
    "shadows": 0.0,     # 阴影提亮 0-100
    "contrast": 0.0,    # 对比度 -100..100（以 0.18 中灰为支点的 S 曲线）
    "swap": 0.0,        # 通道互换强度 0-100
    "swap_mode": "none",  # none|rb|rg|gb|rot|rot_rev
    "swap_hl": 0.0,     # 互换的高光保护 0-100
    "sat": 100.0,       # 全局饱和度 0-200
    "sky_light": 0.0,   # 冷色（天空）明度 -100..100
    "veg_light": 0.0,   # 暖色（植被）明度 -100..100
    "denoise": 0.0,     # 降噪 0-100
    "sharpen": 0.0,     # 锐化 0-150
    "hotspot": 0.0,     # 中心热点补偿 0-100
}

EV_MIN, EV_MAX = -8.0, 8.0
SWAP_MODES = ("none", "rb", "rg", "gb", "rot", "rot_rev")


def parse_params(qs):
    p = dict(DEFAULTS)
    for k, dv in DEFAULTS.items():
        if k not in qs:
            continue
        if isinstance(dv, str):
            p[k] = qs[k][0]
        else:
            try:
                p[k] = float(qs[k][0])
            except (TypeError, ValueError):
                pass

    def cl(key, lo, hi):
        try:
            value = float(p[key])
            if not math.isfinite(value):
                raise ValueError("参数必须是有限数")
            p[key] = min(max(value, lo), hi)
        except (TypeError, ValueError):
            p[key] = DEFAULTS[key]

    cl("brightness", -100.0, 100.0)
    cl("color_sep", -100.0, 100.0)
    cl("gold", -100.0, 100.0)
    cl("blue", -100.0, 100.0)
    cl("vibrance", -100.0, 100.0)
    cl("warm_sat", -100.0, 100.0)
    cl("cool_sat", -100.0, 100.0)
    if p["color_mode"] not in backend.COLOR_MODES:
        p["color_mode"] = DEFAULTS["color_mode"]
    if p['color_mode'] == 'bluegold':
        for key in ('color_sep', 'gold', 'blue', 'vibrance'):
            p[key] = max(0.0, p[key])
    cl("black", 0.0, 100.0)
    cl("exposure", EV_MIN, EV_MAX)
    cl("highlights", 0.0, 100.0)
    cl("shadows", 0.0, 100.0)
    cl("contrast", -100.0, 100.0)
    cl("swap", 0.0, 100.0)
    cl("swap_hl", 0.0, 100.0)
    cl("sat", 0.0, 200.0)
    cl("sky_light", -100.0, 100.0)
    cl("veg_light", -100.0, 100.0)
    cl("denoise", 0.0, 100.0)
    cl("sharpen", 0.0, 150.0)
    cl("hotspot", 0.0, 100.0)
    if p["swap_mode"] not in SWAP_MODES:
        p["swap_mode"] = "rb"
    return p


# ---------------------------------------------------------------- 图像处理

def _downscale(a, max_side):
    """线性域 box 平均降采样。在线性域平均才对（gamma 域平均会偏暗）。"""
    h, w = a.shape[:2]
    if max(h, w) <= max_side:
        return a
    f = int(np.ceil(max(h, w) / float(max_side)))
    if f < 2:
        return a
    h2, w2 = (h // f) * f, (w // f) * f
    if h2 < 1 or w2 < 1:
        return a
    a = a[:h2, :w2]
    return a.reshape(h2 // f, f, w2 // f, f, a.shape[2]).mean(axis=(1, 3))


tone_map = backend.tone_map


_SWAP_TARGETS = {
    "none": np.eye(3, dtype=np.float32),
    "rb": np.array([[0, 0, 1], [0, 1, 0], [1, 0, 0]], dtype=np.float32),
    "rg": np.array([[0, 1, 0], [1, 0, 0], [0, 0, 1]], dtype=np.float32),
    "gb": np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0]], dtype=np.float32),
    "rot": np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
    "rot_rev": np.array([[0, 1, 0], [0, 0, 1], [1, 0, 0]], dtype=np.float32),
}


def swap_matrix(mode, t):
    """互换矩阵 M，满足 RGB' = M · RGB。t=0 是单位阵，t=1 是纯置换。"""
    T = _SWAP_TARGETS.get(mode, _SWAP_TARGETS["rb"])
    I = np.eye(3, dtype=np.float32)
    return (1.0 - float(t)) * I + float(t) * T


def _blur3(a):
    """Separable Gaussian with replicated edges; no wraparound halos."""
    b = np.pad(a, ((1, 1), (0, 0), (0, 0)), mode="edge")
    b = (b[:-2] + 2 * b[1:-1] + b[2:]) * 0.25
    c = np.pad(b, ((0, 0), (1, 1), (0, 0)), mode="edge")
    return (c[:, :-2] + 2 * c[:, 1:-1] + c[:, 2:]) * 0.25


def _blur_n(a, n):
    for _ in range(max(1, int(n))):
        a = _blur3(a)
    return a


def _rgb_to_hsv(a):
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mx = a.max(axis=-1)
    mn = a.min(axis=-1)
    d = mx - mn
    safe = np.maximum(d, 1e-8)
    h = np.zeros_like(mx)
    mr = d > 1e-8

    m = mr & (mx == r)
    h[m] = ((g - b)[m] / safe[m]) % 6.0
    m = mr & (mx == g) & ~(mx == r)
    h[m] = (b - r)[m] / safe[m] + 2.0
    m = mr & (mx == b) & ~(mx == r) & ~(mx == g)
    h[m] = (r - g)[m] / safe[m] + 4.0

    h = h * 60.0
    s = np.where(mx > 1e-8, d / np.maximum(mx, 1e-8), 0.0)
    return h, s, mx


def _hsv_to_rgb(h, s, v):
    h = (h % 360.0) / 60.0
    c = v * s
    x = c * (1.0 - np.abs((h % 2.0) - 1.0))
    m = v - c
    z = np.zeros_like(c)

    conds = [h < 1, h < 2, h < 3, h < 4, h < 5, h < 6]
    rs = [c, x, z, z, x, c]
    gs = [x, c, c, x, z, z]
    bs = [z, z, x, c, c, x]

    r = np.select(conds, rs, default=c)
    g = np.select(conds, gs, default=x)
    b = np.select(conds, bs, default=z)
    return np.stack([r + m, g + m, b + m], axis=-1)


def _hue_weight(hdeg, center, width):
    d = np.abs((hdeg - center + 180.0) % 360.0 - 180.0)
    return np.clip(1.0 - d / width, 0.0, 1.0)


def process_array(a, p, blur_repeat=1):
    """a: float32 (h,w,3) 0..1，sRGB 已 gamma 编码。返回同形状 float32。"""
    a = a.astype(np.float32, copy=True)
    h, w = a.shape[:2]

    if p.get('color_mode') == 'coral_canopy':
        # A creative grade guided by a reference image, not object detection.
        hue, sat, val = _rgb_to_hsv(np.clip(a, 0, 1))
        gate = np.clip((sat - .025) / .16, 0, 1)
        gate = gate * gate * (3 - 2 * gate) * np.clip((val - .025) / .15, 0, 1)
        warm = _hue_weight(hue, 12, 70) * gate
        cool = _hue_weight(hue, 190, 85) * gate
        shift = (354 - hue + 180) % 360 - 180
        hue = (hue + .72 * warm * shift - 12 * cool) % 360
        sat = np.clip(sat * (1 + .48 * warm - .3 * cool), 0, 1)
        val = val * (.88 + .12 * np.clip(val / .6, 0, 1))
        val = np.clip(val + .30 * warm * val * (1 - val), 0, 1)
        val *= 1 - .14 * cool
        a = _hsv_to_rgb(hue, sat, val)

    if p.get("color_mode", "classic") == "bluegold":
        hue, sat, val = _rgb_to_hsv(a)
        def smooth(x):
            x = np.clip(x, 0, 1)
            return x*x*(3-2*x)
        warm = smooth(_hue_weight(hue, 55, 100)/.4)
        cool = smooth(_hue_weight(hue, 220, 100)/.4)
        gate = smooth((sat-.015)/.12) * smooth((val-.02)/.12)
        gw = warm * gate * p.get("gold",85)/100
        bw = cool * gate * p.get("blue",85)/100
        dh_gold = (54-hue+180)%360-180
        dh_blue = (216-hue+180)%360-180
        hue = (hue + gw*dh_gold + bw*dh_blue)%360
        amount = .9*gw + .65*bw + p.get("vibrance",35)/100
        sat = np.clip(sat + amount*sat*(1-sat),0,1)
        a = _hsv_to_rgb(hue,sat,val)

    if p["hotspot"] > 0:
        yy = np.linspace(-1, 1, h, dtype=np.float32)[:, None]
        xx = np.linspace(-1, 1, w, dtype=np.float32)[None, :]
        r2 = (yy * yy + xx * xx) / 2.0
        k = p["hotspot"] / 100.0
        m = 1.0 - 0.55 * k * np.exp(-r2 / 0.12)
        a *= m[..., None]

    if p["denoise"] > 0:
        k = p["denoise"] / 100.0 * 0.85
        a = a * (1.0 - k) + _blur_n(a, blur_repeat) * k

    if p["swap"] > 0:
        t = p["swap"] / 100.0
        T = _SWAP_TARGETS.get(p["swap_mode"], _SWAP_TARGETS["rb"])
        if p["swap_hl"] > 0:
            lum = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
            g = np.clip((lum - 0.55) / 0.35, 0.0, 1.0)
            tp = t * (1.0 - p["swap_hl"] / 100.0 * g)
        else:
            tp = np.asarray(t, dtype=np.float32)
        perm = a @ T.T
        a = a * (1.0 - tp[..., None]) + perm * tp[..., None]

    modern = p.get('color_mode') != 'bluegold'
    need_hsl = (abs(p["sat"] - 100.0) > 1e-3 or abs(p["sky_light"]) > 1e-3
                or abs(p["veg_light"]) > 1e-3 or p.get('warm_sat',0) or p.get('cool_sat',0)
                or (modern and any(p.get(k,0) for k in ('color_sep','gold','blue','vibrance'))))
    if need_hsl:
        a = np.clip(a, 0, 1)
        hue, sat, val = _rgb_to_hsv(a)

        warm = np.maximum(_hue_weight(hue, 45, 75), _hue_weight(hue, 15, 45))
        cool = _hue_weight(hue, 210, 90)
        sat = np.clip(sat * np.maximum(0, 1 + warm*p.get('warm_sat',0)/100
                                        + cool*p.get('cool_sat',0)/100), 0, 1)
        if modern:
            sat = np.clip(sat * (1 + p.get('color_sep',0)/100), 0, 1)
            sat = np.clip(sat + p.get('vibrance',0)/100 * sat*(1-sat), 0, 1)
            hue = (hue + 30*warm*p.get('gold',0)/100
                       + 30*cool*p.get('blue',0)/100) % 360

        if abs(p["sat"] - 100.0) > 1e-3:
            sat = np.clip(sat * (p["sat"] / 100.0), 0.0, 1.0)

        if abs(p["sky_light"]) > 1e-3:
            wgt = _hue_weight(hue, 215.0, 70.0)
            f = 1.0 + p["sky_light"] / 100.0 * 0.6 * wgt
            val = np.clip(val * f, 0.0, 1.0)

        if abs(p["veg_light"]) > 1e-3:
            wgt = _hue_weight(hue, 95.0, 75.0)
            f = 1.0 + p["veg_light"] / 100.0 * 0.6 * wgt
            val = np.clip(val * f, 0.0, 1.0)

        a = _hsv_to_rgb(hue, sat, val)

    if p["sharpen"] > 0:
        amt = p["sharpen"] / 100.0 * 1.8
        a = a + (a - _blur_n(a, blur_repeat)) * amt

    return np.clip(a, 0.0, 1.0)


decode_linear = backend.decode_linear


def render_jpeg(path, mr, mb, p, scale, max_side=1400, quality=86):
    from PIL import Image

    lin = decode_linear(path, mr, mb, half_size=True)
    lin = _downscale(lin, max_side)
    matrix = backend.camera_to_prophoto(path) if p['color_mode'] in backend.CAMERA_COLOR_MODES else None
    a = tone_map(lin * np.float32(scale), p, matrix)
    a = process_array(a, p, blur_repeat=1)

    buf = io.BytesIO()
    Image.fromarray((a * 255.0 + 0.5).astype(np.uint8)).save(
        buf, "JPEG", quality=quality, optimize=True, icc_profile=backend.srgb_icc())
    return buf.getvalue()


def export_tiff(path, mr, mb, p, scale, name, mode="developed", outdir=None):
    import tifffile
    if mode not in ("developed", "linear", "jpeg"):
        raise ValueError("未知导出类型")
    lin = decode_linear(path, mr, mb, half_size=False)
    recipe = {"version": 3, "source": name, "white_balance": [mr, 1, mb],
              "scale": scale, "parameters": p, "export": mode,
              "working_space": "camera RGB; selected display color mode; G=1 exposure anchor"}
    base = os.path.splitext(os.path.basename(name))[0]
    ext = ".jpg" if mode == "jpeg" else ".tif"
    destination = OUTDIR if outdir is None else validate_export_dir(outdir)
    out = os.path.join(destination, base + "_" + mode + "_" + secrets.token_hex(4) + ext)
    if mode == "linear":
        # Archive WB + exposure without display curve, ICC, channel swaps or clipping.
        lin *= np.float32(scale * 2 ** p["exposure"])
        tifffile.imwrite(out, lin, photometric="rgb", metadata=None,
                         description=json.dumps(recipe, ensure_ascii=False))
    else:
        matrix = backend.camera_to_prophoto(path) if p['color_mode'] in backend.CAMERA_COLOR_MODES else None
        a = tone_map(lin * np.float32(scale), p, matrix)
        del lin
        rep = max(1, int(round(max(a.shape[:2]) / 1400.0)))
        a = process_array(a, p, blur_repeat=rep)
        if mode == "jpeg":
            from PIL import Image
            Image.fromarray((a * 255.0 + 0.5).astype(np.uint8)).save(
                out, "JPEG", quality=95, subsampling=0,
                icc_profile=backend.srgb_icc())
        else:
            tifffile.imwrite(out, (a * 65535.0 + 0.5).astype(np.uint16),
                             photometric="rgb", metadata=None,
                             iccprofile=backend.srgb_icc())
    Path(out + ".json").write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def validate_export_dir(path):
    folder = Path(path).expanduser().resolve()
    if os.path.splitdrive(str(folder))[0].upper() != 'E:':
        raise ValueError('保存目录必须位于 E 盘')
    if not folder.is_dir():
        raise ValueError('保存目录不存在，请填写已有的 E 盘文件夹')
    return str(folder)


def analyze_bytes(data, filename):
    ext = os.path.splitext(filename)[1].lower() or ".raw"
    fid = secrets.token_hex(6)
    path = os.path.join(CACHE_DIR, fid + ext)
    with open(path, "wb") as f:
        f.write(data)

    try:
        means, stats, meta = ir_wb.read_cfa_stats(path)
        mul = ir_wb.solve_multipliers(means)
        lin = decode_linear(path, mul["R"], mul["B"], half_size=True)
        scale = backend.auto_scale(lin)
        diagnostics = backend.sensor_diagnostics(path)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise

    entry = {
        "fid": fid,
        "name": filename,
        "path": path,
        "bytes": len(data),
        "means": means,
        "mul": mul,
        "scale": float(scale),
        "meta": {
            "size": list(meta["size"]),
            "cfa": meta["color_desc"],
            "black": list(meta["black_level_per_channel"]),
            "white": meta["white_level"],
            "sensor_channels": diagnostics,
            "camera_wb": [round(float(x), 4) for x in meta["camera_wb"]],
        },
    }
    with LOCK:
        ENTRIES[fid] = entry
    return entry


def probe_region(path, region):
    """在归一化矩形区域上重算通道均值与倍率。"""
    import rawpy

    with rawpy.imread(path) as raw:
        data = raw.raw_image_visible
        colors = raw.raw_colors_visible
        bl = list(raw.black_level_per_channel)
        white = float(raw.white_level)
        cd = raw.color_desc
        if not isinstance(cd, bytes):
            cd = str(cd).encode()
        x0, y0, x1, y1 = region
        if raw.sizes.flip == 3:
            region = (1-x1, 1-y1, 1-x0, 1-y0)
        elif raw.sizes.flip == 5:
            region = (1-y1, x0, 1-y0, x1)
        elif raw.sizes.flip == 6:
            region = (y0, 1-x1, y1, 1-x0)
        white = raw.camera_white_level_per_channel or white
        means, stats = ir_wb.cfa_channel_means(data, colors, bl, white, cd, region)
    mul = ir_wb.solve_multipliers(means)
    return means, mul


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>红外白平衡</title>
<style>
:root{
  --bg:#f6f6f3; --card:#fff; --line:#e3e3de; --txt:#1b1b19;
  --muted:#75756c; --accent:#0f6e56; --accent-soft:#e1f5ee; --danger:#a32d2d;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  -webkit-font-smoothing:antialiased}
header{padding:14px 22px;border-bottom:1px solid var(--line);background:var(--card);
  display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
header h1{font-size:15px;font-weight:500;margin:0}
header .sub{font-size:12px;color:var(--muted)}
header .out{margin-left:auto;font-size:12px;color:var(--muted);
  font-family:ui-monospace,Consolas,monospace}
main{display:flex;gap:16px;padding:16px 22px 48px;align-items:flex-start}
.left{width:300px;flex:none}
.right{flex:1;min-width:0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px}

#drop{border:1.5px dashed #cfcfc7;border-radius:10px;padding:22px 14px;
  text-align:center;cursor:pointer;background:var(--card);transition:border-color .15s,background .15s}
#drop:hover{border-color:#a8a89e}
#drop.over{border-color:var(--accent);background:var(--accent-soft)}
#drop .t{font-size:14px;font-weight:500;margin:0 0 4px}
#drop .s{font-size:12px;color:var(--muted);margin:0}

ul#list{list-style:none;margin:10px 0 0;padding:0;display:flex;flex-direction:column;gap:7px}
li.item{background:var(--card);border:1px solid var(--line);border-radius:8px;
  padding:8px 10px;cursor:pointer;transition:border-color .12s}
li.item:hover{border-color:#c9c9c1}
li.item.sel{border-color:var(--accent);box-shadow:inset 0 0 0 1px var(--accent)}
li.item.err{border-color:#e8b4b4;background:#fdf4f4}
.nm{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.li2{font-size:11.5px;color:var(--muted);display:flex;gap:8px;margin-top:2px}
li.item .bad{color:var(--danger)}

.view{position:relative;background:#17171a;border-radius:10px;overflow:hidden;
  min-height:300px;display:flex;align-items:center;justify-content:center;padding:6px}
.stage{position:relative;line-height:0;display:none}
.stage.on{display:block}
.stage img{max-width:100%;max-height:56vh;display:block;border-radius:4px}
.stage.picking img{cursor:crosshair}
#sel{position:absolute;border:1.5px solid #fff;background:rgba(255,255,255,.16);
  box-shadow:0 0 0 1px rgba(0,0,0,.45);display:none;pointer-events:none}
.view .hint{color:#8b8b93;font-size:13px}
.spin{position:absolute;top:8px;right:8px;font-size:11px;color:#fff;
  background:rgba(0,0,0,.6);padding:2px 8px;border-radius:20px;display:none;z-index:3}
.spin.on{display:block}

.bar{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
button{font:13px/1 inherit;padding:7px 12px;border-radius:7px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--txt);
  transition:border-color .12s,background .12s}
button:hover{border-color:#c2c2b9}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{background:#0b5745}
button.on{background:var(--accent-soft);border-color:var(--accent);color:var(--accent)}
button:disabled{opacity:.45;cursor:default}

.panel{margin-top:10px;padding:12px 14px}
.pgrid{display:grid;grid-template-columns:1fr 1fr;gap:0 22px}
@media(max-width:980px){.pgrid{grid-template-columns:1fr}}
.grp{margin-bottom:6px}
.gh{font-size:11px;color:var(--muted);letter-spacing:.04em;margin:8px 0 4px}
.row{display:flex;align-items:center;gap:9px;height:26px}
.row label{width:56px;font-size:12px;color:var(--muted);flex:none}
.row input[type=range]{flex:1;accent-color:var(--accent);min-width:0;height:16px}
.row .val{width:76px;text-align:right;flex:none;
  font:11.5px/1 ui-monospace,Consolas,monospace;color:var(--txt)}
.row select{flex:1;min-width:0;font:12px/1.2 inherit;padding:3px 5px;
  border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--txt)}
.meta{margin-top:10px;padding-top:9px;border-top:1px solid var(--line);
  font-size:11.5px;color:var(--muted);line-height:1.85;
  font-family:ui-monospace,Consolas,monospace;word-break:break-all}
.meta b{font-weight:500;color:var(--txt);
  font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.note{font-size:11.5px;color:var(--muted);margin-top:8px;line-height:1.7}
.histogram{margin-top:10px;padding:8px 12px;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.histogram canvas{width:256px;height:100px;max-width:100%;background:#17171a;border-radius:5px}
.histogram .note{margin:0}
.export-dir{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}
.export-dir input{flex:1;min-width:240px;padding:6px;border:1px solid var(--line);border-radius:6px}
.warn{color:var(--danger)}
.toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);
  background:#1b1b19;color:#fff;font-size:13px;padding:9px 16px;border-radius:8px;
  opacity:0;pointer-events:none;transition:opacity .18s;max-width:82vw;z-index:20}
.toast.on{opacity:1}
</style>
</head>
<body>
<header>
  <h1>红外白平衡</h1>
  <span class="sub">全波段改机 · 590 nm</span>
  <span class="out" id="outdir">__OUTDIR__</span>
</header>

<main>
  <section class="left">
    <div id="drop">
      <p class="t">把 RAW 拖到这里</p>
      <p class="s">或点击选择，可一次多张</p>
    </div>
    <input type="file" id="file" multiple hidden>
    <ul id="list"></ul>
    <div class="note" id="tip">自动白平衡和曝光仅作起点。请用吸管框选本应中性的白墙或灰路，再比较色彩方案。公开矩阵使用可见光相机矩阵作为近似，并非红外标定。</div>
  </section>

  <section class="right">
    <div class="view card" id="view">
      <span class="hint" id="hint">拖入一张 RAW 开始</span>
      <div class="stage" id="stage">
        <img id="img" alt="">
        <div id="sel"></div>
      </div>
      <span class="spin" id="spin">渲染中</span>
    </div>

    <div class="card histogram"><canvas id="hist" width="256" height="100" aria-label="预览亮度直方图"></canvas>
      <div class="note" id="histInfo">预览直方图：左侧为暗部，右侧为高光。<br>导入照片后显示。</div></div>

    <div class="bar">
      <button id="btnPick" disabled>吸管取白</button>
      <button id="btnAuto" disabled>回自动倍率</button>
      <button id="btnAutoExp" disabled>自动曝光</button>
      <button id="btnRaw" disabled>看未调色</button>
      <button id="btnReset">全部归零</button>
      <select id="exportMode"><option value="developed">16-bit 成片 TIFF</option><option value="linear">32-bit 浮点底片 TIFF</option><option value="jpeg">JPG 成片</option></select>
      <button id="btnOne" class="primary" disabled>导出这张</button>
      <button id="btnJpg" disabled>导出 JPG</button>
      <button id="btnAll" disabled>全部导出</button>
    </div>
    <div class="export-dir"><label for="exportDir">保存文件夹</label><input id="exportDir" type="text" aria-label="保存文件夹" placeholder="输入已有的 E 盘文件夹路径"><button id="btnFolder">选择文件夹</button><span class="note">选择 E 盘已有文件夹；JPG 和 TIFF 共用</span></div>

    <div class="card panel">
      <div class="pgrid" id="pgrid"></div>
      <div class="meta" id="meta"></div>
      <div class="note">白平衡 → 线性曝光 / 中间调 → 高光压缩 → sRGB → 创意调色。16-bit 成片含 sRGB 色彩配置；32-bit 浮点底片仅含白平衡及曝光，保留超白数据。</div>
    </div>
  </section>
</main>

<div class="toast" id="toast"></div>

<script>
const $ = s => document.querySelector(s);
const imgEl=$('#img'), stage=$('#stage'), selEl=$('#sel'), spinEl=$('#spin');
const listEl=$('#list'), hintEl=$('#hint');

const PARAMS=[
 {k:'color_mode',g:'590 nm 色彩',lb:'色彩方案',type:'sel',opts:[['reference590','金色森林'],['coral_canopy','珊瑚粉影'],['linear_brg','梦幻粉紫'],['linear_rb','经典红蓝互换'],['camera','相机色彩'],['classic','原始通道']]},
 {k:'color_sep',g:'590 nm 色彩',lb:'色彩分离',min:-100,max:100,step:1,dec:0},
 {k:'gold',g:'590 nm 色彩',lb:'暖色色相',min:-100,max:100,step:1,dec:0},
 {k:'blue',g:'590 nm 色彩',lb:'冷色色相',min:-100,max:100,step:1,dec:0},
 {k:'vibrance',g:'590 nm 色彩',lb:'自然饱和',min:-100,max:100,step:1,dec:0},
 {k:'exposure',g:'亮度',lb:'曝光',min:-8,max:8,step:0.05,dec:2,unit:' EV'},
 {k:'brightness',g:'亮度',lb:'中间调',min:-100,max:100,step:1,dec:0},
 {k:'black',g:'亮度',lb:'黑场',min:0,max:100,step:1,dec:0},
 {k:'highlights',g:'亮度',lb:'高光',min:0,max:100,step:1,dec:0},
 {k:'shadows',g:'亮度',lb:'阴影',min:0,max:100,step:1,dec:0},
 {k:'contrast',g:'亮度',lb:'对比度',min:-100,max:100,step:1,dec:0},
 {k:'mr',g:'白平衡',lb:'R',min:0.0001,max:10000,step:0.000001,dec:5},
 {k:'mb',g:'白平衡',lb:'B',min:0.0001,max:10000,step:0.000001,dec:5},
 {k:'swap_mode',g:'附加通道互换',lb:'模式',type:'sel',opts:[
   ['none','不互换'],['rb','R ↔ B'],['rg','R ↔ G'],['gb','G ↔ B'],
   ['rot','R→B→G→R'],['rot_rev','R→G→B→R']]},
 {k:'swap',g:'附加通道互换',lb:'强度',min:0,max:100,step:1,dec:0,unit:'%'},
 {k:'swap_hl',g:'附加通道互换',lb:'保护高光',min:0,max:100,step:1,dec:0,unit:'%'},
 {k:'sat',g:'色调',lb:'饱和度',min:0,max:200,step:1,dec:0,unit:'%'},
 {k:'warm_sat',g:'色调',lb:'暖色饱和',min:-100,max:100,step:1,dec:0,unit:'%'},
 {k:'cool_sat',g:'色调',lb:'冷色饱和',min:-100,max:100,step:1,dec:0,unit:'%'},
 {k:'sky_light',g:'色调',lb:'冷色明度',min:-100,max:100,step:1,dec:0},
 {k:'veg_light',g:'色调',lb:'暖色明度',min:-100,max:100,step:1,dec:0},
 {k:'denoise',g:'细节',lb:'降噪',min:0,max:100,step:1,dec:0},
 {k:'sharpen',g:'细节',lb:'锐化',min:0,max:150,step:1,dec:0},
 {k:'hotspot',g:'细节',lb:'热点补偿',min:0,max:100,step:1,dec:0},
];
const DEF={color_mode:'reference590',color_sep:0,gold:0,blue:0,vibrance:0,brightness:0,black:0,exposure:0,highlights:0,shadows:0,contrast:0,swap_mode:'none',
  swap:0,swap_hl:0,sat:100,warm_sat:0,cool_sat:0,sky_light:0,veg_light:0,denoise:0,sharpen:0,hotspot:0};
const pv=Object.assign({},DEF,{mr:1,mb:1});

let renderVersion=0;
let items=[], cur=null, timer=null, picking=false, busy=false, showRaw=false, drag=null;

function buildPanel(){
  const groups={};
  PARAMS.forEach(p=>{ (groups[p.g]=groups[p.g]||[]).push(p); });
  let html='';
  for(const g in groups){
    html+='<div class="grp"><div class="gh">'+g+'</div>';
    groups[g].forEach(p=>{
      if(p.type==='sel'){
        html+='<div class="row"><label>'+p.lb+'</label>'+
          '<select id="s_'+p.k+'">'+
          p.opts.map(o=>'<option value="'+o[0]+'">'+o[1]+'</option>').join('')+
          '</select></div>';
      }else{
        html+='<div class="row"><label>'+p.lb+'</label>'+
          '<input type="range" id="s_'+p.k+'" min="'+p.min+'" max="'+p.max+'" step="'+p.step+'">'+
          ((p.k==='mr'||p.k==='mb')
            ? '<input class="val" type="number" min="0.0001" max="10000" step="0.0001" id="v_'+p.k+'">'
            : '<span class="val" id="v_'+p.k+'"></span>')+'</div>';
      }
    });
    html+='</div>';
  }
  $('#pgrid').innerHTML=html;
  PARAMS.forEach(p=>{
    const el=$('#s_'+p.k);
    if(!el) return;
    if(p.type==='sel'){
      el.addEventListener('change',()=>{ pv[p.k]=el.value; if(p.k==='color_mode'){pv.swap_mode='none';pv.swap=0;Object.assign(pv,el.value==='bluegold'?{color_sep:60,gold:85,blue:85,vibrance:35}:{color_sep:0,gold:0,blue:0,vibrance:0});syncAll();} schedule(); });
    }else{
      el.addEventListener('input',()=>{ pv[p.k]=parseFloat(el.value); syncLabel(p); schedule(); });
      if(p.k==='mr'||p.k==='mb') $('#v_'+p.k).addEventListener('change',e=>{
        const v=Number(e.target.value);
        if(!Number.isFinite(v)||v<0.0001||v>10000){ syncLabel(p); return; }
        pv[p.k]=v; syncAll(); schedule();
      });
    }
  });
}
function syncLabel(p){
  const el=$('#v_'+p.k);
  if(el){
    if(el.tagName==='INPUT') el.value=pv[p.k];
    else el.textContent=pv[p.k].toFixed(p.dec)+(p.unit||'');
  }
}
function syncAll(){
  PARAMS.forEach(p=>{
    const el=$('#s_'+p.k); if(!el) return;
    if(p.k==='mr'||p.k==='mb'){
      const v=pv[p.k];el.min=Math.max(.0001,v*.5);el.max=Math.min(10000,v*1.5);el.step=Math.max(.0000001,(el.max-el.min)/2000);
    }
    if(['color_sep','gold','blue','vibrance'].includes(p.k)) el.min=pv.color_mode==='bluegold'?0:-100;
    el.value=pv[p.k];
    if(p.type!=='sel') syncLabel(p);
  });
}

function toast(m,ms){ const t=$('#toast'); t.textContent=m; t.classList.add('on');
  clearTimeout(t._h); t._h=setTimeout(()=>t.classList.remove('on'),ms||2600); }
function esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

async function upload(file){
  const idx=items.length;
  const li=document.createElement('li');
  li.className='item';
  li.innerHTML='<div class="nm">'+esc(file.name)+'</div><div class="li2">读取中</div>';
  listEl.appendChild(li);
  items.push({name:file.name,el:li,data:null});
  li.onclick=()=>select(idx);
  try{
    const res=await fetch('/api/analyze',{method:'POST',
      headers:{'X-Filename':encodeURIComponent(file.name)},body:file});
    const j=await res.json();
    if(!res.ok) throw new Error(j.error||('HTTP '+res.status));
    items[idx].data=j;
    li.querySelector('.li2').innerHTML=esc(j.meta.cfa+' '+j.meta.size[0]+'x'+j.meta.size[1])+
      ' · <b>R '+j.mul.R.toFixed(2)+' B '+j.mul.B.toFixed(2)+'</b>';
    if(cur===null||cur===idx) select(idx);
  }catch(e){
    li.classList.add('err');
    li.querySelector('.li2').innerHTML='<span class="bad">'+esc(e.message)+'</span>';
  }
}

function saveCurrent(){
  if(cur!==null && items[cur].data) items[cur].settings={...pv};
}
function select(i){
  saveCurrent();
  cur=i;
  items.forEach((it,k)=>it.el.classList.toggle('sel',k===i));
  const d=items[i].data; if(!d) return;
  pv.mr=d.mul.R; pv.mb=d.mul.B;
  Object.assign(pv,DEF,{mr:d.mul.R,mb:d.mul.B},items[i].settings||{});
  syncAll();
  showRaw=false; $('#btnRaw').textContent='看未调色';
  ['#btnPick','#btnAuto','#btnAutoExp','#btnRaw','#btnOne','#btnJpg'].forEach(s=>$(s).disabled=false);
  $('#btnAll').disabled=!items.some(x=>x.data);
  stage.classList.add('on'); hintEl.style.display='none';
  setPicking(false); render(true);
  const m=d.meta;
  $('#meta').innerHTML='<b>'+esc(d.name)+'</b><br>'+
    '通道均值 R '+d.means.R.toFixed(1)+'  G '+d.means.G.toFixed(1)+'  B '+d.means.B.toFixed(1)+'<br>'+
    '白电平 '+m.white+'   黑电平 '+m.black.join('/')+'<br>'+
    '相机记录 WB '+m.camera_wb.join(' / ')+'<br>传感器已饱和（各 CFA 通道）：'+Object.entries(m.sensor_channels||{}).map(([c,v])=>c+' = '+v.saturated_percent.toFixed(3)+'%').join(' / ');
}

function query(){
  return PARAMS.map(p=>p.k+'='+encodeURIComponent(pv[p.k])).join('&');
}

function render(force){
  const d=cur===null?null:items[cur].data; if(!d) return;
  const q = showRaw ? 'mr=1&mb=1&color_mode=classic' : query();
  spinEl.classList.add('on');
  const version=++renderVersion;
  const tmp=new Image();
  tmp.onload=()=>{ if(version!==renderVersion) return; imgEl.src=tmp.src; drawHistogram(tmp); spinEl.classList.remove('on'); };
  tmp.onerror=()=>{ if(version!==renderVersion) return; spinEl.classList.remove('on'); toast('渲染失败'); };
  tmp.src='/api/render?fid='+d.fid+'&'+q+'&t='+Date.now();
}
function drawHistogram(img){
  const sample=document.createElement('canvas'); sample.width=256;sample.height=Math.max(1,Math.round(256*img.naturalHeight/img.naturalWidth));
  const s=sample.getContext('2d',{willReadFrequently:true});s.drawImage(img,0,0,sample.width,sample.height);
  const pixels=s.getImageData(0,0,sample.width,sample.height).data, bins=new Uint32Array(256);let dark=0,bright=0;
  for(let i=0;i<pixels.length;i+=4){const y=Math.round(.2126*pixels[i]+.7152*pixels[i+1]+.0722*pixels[i+2]);bins[y]++;if(y<=5)dark++;if(y>=250)bright++;}
  const c=$('#hist'),ctx=c.getContext('2d'),max=Math.max(1,...bins);ctx.clearRect(0,0,256,100);ctx.fillStyle='#c9d8dd';
  for(let x=0;x<256;x++){const h=95*Math.log1p(bins[x])/Math.log1p(max);ctx.fillRect(x,100-h,1,h);}
  const n=pixels.length/4;$('#histInfo').innerHTML='预览亮度直方图：左暗右亮（对数显示）<br>接近黑色 '+(100*dark/n).toFixed(1)+'% · 接近白色 '+(100*bright/n).toFixed(1)+'%<br>仅反映处理后预览，传感器饱和请看下方数据。';
}
function schedule(){ saveCurrent(); clearTimeout(timer); timer=setTimeout(()=>render(false),170); }

function setPicking(on){
  picking=on;
  stage.classList.toggle('picking',on);
  $('#btnPick').classList.toggle('on',on);
  $('#btnPick').textContent=on?'吸管已开 · 框选白区':'吸管取白';
}

$('#btnPick').onclick=()=>setPicking(!picking);
$('#btnAuto').onclick=()=>{
  const d=items[cur].data;
  pv.mr=d.mul.R; pv.mb=d.mul.B; showRaw=false;
  syncAll(); render(true);
  toast('已回到整幅平均倍率');
};
$('#btnRaw').onclick=()=>{
  showRaw=!showRaw;
  $('#btnRaw').textContent=showRaw?'看处理后':'看未调色';
  render(true);
};
$('#btnAutoExp').onclick=async()=>{
  const d=items[cur].data;
  spinEl.classList.add('on');
  try{
    const r=await fetch('/api/autoscale?fid='+d.fid+'&mr='+pv.mr+'&mb='+pv.mb);
    const j=await r.json();
    if(!r.ok) throw new Error(j.error||('HTTP '+r.status));
    pv.exposure=0; syncAll(); render(true);
    toast('曝光基准已按当前白平衡重算');
  }catch(e){ toast('失败: '+e.message,4200); }
  finally{ spinEl.classList.remove('on'); }
};

$('#btnReset').onclick=()=>{
  Object.keys(DEF).forEach(k=>pv[k]=DEF[k]);
  const d=cur===null?null:items[cur].data;
  if(d){ pv.mr=d.mul.R; pv.mb=d.mul.B; }
  syncAll(); render(true);
};

function rectOf(e){
  const r=imgEl.getBoundingClientRect();
  return {x:Math.min(Math.max((e.clientX-r.left)/r.width,0),1),
          y:Math.min(Math.max((e.clientY-r.top)/r.height,0),1)};
}
function showSel(a,b,e){
  const r=imgEl.getBoundingClientRect(), host=stage.getBoundingClientRect();
  const x0=Math.min(a.x,b.x)*r.width, x1=Math.max(a.x,b.x)*r.width;
  const y0=Math.min(a.y,b.y)*r.height, y1=Math.max(a.y,b.y)*r.height;
  selEl.style.display='block';
  selEl.style.left=(r.left-host.left+x0)+'px';
  selEl.style.top=(r.top-host.top+y0)+'px';
  selEl.style.width=Math.max(2,x1-x0)+'px';
  selEl.style.height=Math.max(2,y1-y0)+'px';
}

stage.addEventListener('mousedown',e=>{
  if(!picking) return;
  e.preventDefault();
  drag=rectOf(e); showSel(drag,drag,e);
});
window.addEventListener('mousemove',e=>{
  if(!drag||!picking) return;
  showSel(drag,rectOf(e),e);
});
window.addEventListener('mouseup',async e=>{
  if(!drag||!picking) return;
  const b=rectOf(e), a=drag; drag=null;
  selEl.style.display='none';
  const x0=Math.min(a.x,b.x), x1=Math.max(a.x,b.x);
  const y0=Math.min(a.y,b.y), y1=Math.max(a.y,b.y);
  if((x1-x0)*imgEl.clientWidth<4 || (y1-y0)*imgEl.clientHeight<4){
    toast('框太小，请拖出一个更大的区域'); return;
  }
  const d=items[cur].data;
  spinEl.classList.add('on');
  try{
    const res=await fetch('/api/probe?fid='+d.fid+'&x0='+x0+'&y0='+y0+'&x1='+x1+'&y1='+y1);
    const j=await res.json();
    if(!res.ok) throw new Error(j.error||('HTTP '+res.status));
    pv.mr=j.mul.R; pv.mb=j.mul.B;
    syncAll(); render(true);
    const lv=j.level;
    let msg='已取白：R '+j.mul.R.toFixed(3)+'  B '+j.mul.B.toFixed(3);
    if(lv>0.92) toast(msg+'   但该区接近过曝，建议换个稍暗的白区',5200);
    else if(lv<0.04) toast(msg+'   但该区过暗，噪声会主导，建议换个更亮的中性区',5200);
    else toast(msg,3600);
  }catch(err){ toast('取白失败: '+err.message,4200); }
  finally{ spinEl.classList.remove('on'); }
});

function exportQuery(){
  const d=items[cur].data;
  return PARAMS.map(p=>p.k+'='+encodeURIComponent(pv[p.k])).join('&');
}
async function exportOne(i, mode=$('#exportMode').value){
  saveCurrent();
  const d=items[i].data;
  const settings=items[i].settings||{...DEF,mr:d.mul.R,mb:d.mul.B};
  const q=new URLSearchParams(settings).toString();
  const res=await fetch('/api/export?fid='+d.fid+'&'+q+'&mode='+encodeURIComponent(mode)+'&outdir='+encodeURIComponent($('#exportDir').value.trim()),{method:'POST'});
  const j=await res.json();
  if(!res.ok) throw new Error(j.error||('HTTP '+res.status));
  return j.path;
}

$('#btnJpg').onclick=async()=>{
  if(busy||cur===null||!items[cur].data) return;
  busy=true;
  const b=$('#btnJpg'); b.disabled=true; b.textContent='导出中';
  try{ toast('已保存 '+await exportOne(cur,'jpeg'),5200); }
  catch(e){ toast('JPG 导出失败: '+e.message,4400); }
  finally{ b.disabled=false; b.textContent='导出 JPG'; busy=false; }
};

$('#btnOne').onclick=async()=>{
  if(busy) return; busy=true;
  const b=$('#btnOne'); b.disabled=true; b.textContent='导出中';
  try{ toast('已保存 '+await exportOne(cur),4400); }
  catch(e){ toast('导出失败: '+e.message,4400); }
  b.disabled=false; b.textContent='导出这张'; busy=false;
};
$('#btnAll').onclick=async()=>{
  if(busy) return; busy=true;
  const b=$('#btnAll'); b.disabled=true;
  const todo=items.map((it,i)=>[it,i]).filter(([it])=>it.data);
  let ok=0;
  for(let k=0;k<todo.length;k++){
    b.textContent='导出 '+(k+1)+'/'+todo.length;
    try{ await exportOne(todo[k][1]); ok++; }catch(e){}
  }
  b.textContent='全部导出'; b.disabled=false; busy=false;
  toast('完成 '+ok+'/'+todo.length,4400);
};

const drop=$('#drop');
drop.onclick=()=>$('#file').click();
$('#file').onchange=e=>{ [...e.target.files].forEach(upload); e.target.value=''; };
['dragenter','dragover'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault(); e.stopPropagation(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(ev=>drop.addEventListener(ev,e=>{
  e.preventDefault(); e.stopPropagation(); drop.classList.remove('over'); }));
drop.addEventListener('drop',e=>{ [...e.dataTransfer.files].forEach(upload); });
window.addEventListener('dragover',e=>e.preventDefault());
window.addEventListener('drop',e=>{
  e.preventDefault();
  if(e.target.closest&&e.target.closest('#drop')) return;
  [...e.dataTransfer.files].forEach(upload);
});

$('#exportDir').value=localStorage.getItem('irExportDir')||$('#outdir').textContent;
$('#exportDir').addEventListener('change',()=>localStorage.setItem('irExportDir',$('#exportDir').value.trim()));
$('#btnFolder').onclick=async()=>{
  const b=$('#btnFolder');b.disabled=true;
  try{const r=await fetch('/api/folder',{method:'POST'});const j=await r.json();if(!r.ok)throw new Error(j.error);if(j.path){$('#exportDir').value=j.path;localStorage.setItem('irExportDir',j.path);}}
  catch(e){toast('选择文件夹失败：'+e.message,4500);}finally{b.disabled=false;}
};
buildPanel(); syncAll();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IRWB/3.0"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _qs(self):
        u = urllib.parse.urlparse(self.path)
        return urllib.parse.parse_qs(u.query), u.path

    def _entry(self, qs):
        fid = (qs.get("fid") or [""])[0]
        return ENTRIES.get(fid)

    def _fail(self, exc):
        traceback.print_exc()
        lines = [ln for ln in str(exc).strip().splitlines() if ln.strip()]
        short = lines[-1] if lines else type(exc).__name__
        self._json({"error": short[:300]}, 400 if isinstance(exc, (ValueError, KeyError)) else 500)

    def _handle_autoscale(self, qs):
        """按未裁剪的线性通道峰值计算曝光起点。"""
        e = self._entry(qs)
        if not e:
            self._json({"error": "文件不存在或已失效"}, 404)
            return
        mr = backend.positive((qs.get("mr") or ["1"])[0], "R")
        mb = backend.positive((qs.get("mb") or ["1"])[0], "B")
        lin = decode_linear(e["path"], mr, mb, half_size=True)
        e["scale"] = backend.auto_scale(lin)
        self._json({"scale": e["scale"]})

    def _handle_export(self, qs):
        e = self._entry(qs)
        if not e:
            self._json({"error": "文件不存在或已失效"}, 404)
            return
        mr = backend.positive((qs.get("mr") or ["1"])[0], "R")
        mb = backend.positive((qs.get("mb") or ["1"])[0], "B")
        p = parse_params(qs)
        outdir = (qs.get('outdir') or [None])[0]
        out = export_tiff(e["path"], mr, mb, p, e["scale"], e["name"], (qs.get("mode") or ["developed"])[0], outdir)
        self._json({"ok": True, "path": out})

    def _handle_probe(self, qs):
        e = self._entry(qs)
        if not e:
            self._json({"error": "文件不存在或已失效"}, 404)
            return
        try:
            x0 = float((qs.get("x0") or ["0"])[0])
            y0 = float((qs.get("y0") or ["0"])[0])
            x1 = float((qs.get("x1") or ["1"])[0])
            y1 = float((qs.get("y1") or ["1"])[0])
        except ValueError:
            self._json({"error": "坐标格式错误"}, 400)
            return
        means, mul = probe_region(e["path"], (x0, y0, x1, y1))
        g = float(means.get("G", 0.0))
        white = float(e["meta"]["white"] or 65535)
        lvl = g / white if white > 0 else 0.0
        self._json({
            "means": {k: round(float(v), 1) for k, v in means.items()},
            "mul": {k: round(float(v), 5) for k, v in mul.items()},
            "level": round(lvl, 4),
        })

    def do_GET(self):
        qs, path = self._qs()
        try:
            if path in ("/", "/index.html"):
                ui_file = os.path.join(HERE, "ui", "index.html")
                if os.path.exists(ui_file):
                    with open(ui_file, "r", encoding="utf-8") as f:
                        content = f.read()
                else:
                    content = PAGE
                self._send(200, content.replace("__OUTDIR__", html.escape(OUTDIR)),
                           "text/html; charset=utf-8")
                return
            if path == "/api/render":
                e = self._entry(qs)
                if not e:
                    self._json({"error": "文件不存在或已失效"}, 404)
                    return
                mr = backend.positive((qs.get("mr") or ["1"])[0], "R")
                mb = backend.positive((qs.get("mb") or ["1"])[0], "B")
                jpg = render_jpeg(e["path"], mr, mb, parse_params(qs), e["scale"])
                self._send(200, jpg, "image/jpeg")
                return
            if path == "/api/autoscale":
                self._handle_autoscale(qs)
                return
            if path == "/api/probe":
                self._handle_probe(qs)
                return
            if path == "/api/open":
                try:
                    os.startfile(OUTDIR)
                    self._json({"ok": True, "dir": OUTDIR})
                except Exception as exc:
                    self._json({"error": str(exc)}, 500)
                return
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._fail(exc)

    def do_POST(self):
        qs, path = self._qs()
        try:
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                self._json({"error": "拒绝跨站请求"}, 403)
                return
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 512 * 1024 * 1024:
                self._json({"error": "单文件上限 512 MB"}, 413)
                self.close_connection = True
                return
            if path == "/api/analyze":
                name = urllib.parse.unquote(self.headers.get("X-Filename") or "unknown.raw")
                data = self.rfile.read(n) if n else b""
                if not data:
                    self._json({"error": "空文件"}, 400)
                    return
                t0 = time.time()
                e = analyze_bytes(data, name)
                self._json({
                    "fid": e["fid"], "name": e["name"], "bytes": e["bytes"],
                    "means": {k: round(float(v), 1) for k, v in e["means"].items()},
                    "mul": {k: round(float(v), 5) for k, v in e["mul"].items()},
                    "meta": e["meta"],
                    "ms": int((time.time() - t0) * 1000),
                })
                return
            if n:
                self.rfile.read(n)
            if path == "/api/export":
                self._handle_export(qs)
                return
            if path == "/api/folder":
                helper = Path(HERE) / 'choose_export_folder.py'
                result = subprocess.run([sys.executable, '-B', str(helper)],
                                        capture_output=True, text=True, encoding='utf-8',
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if result.returncode:
                    raise ValueError('无法打开文件夹选择窗口，请直接输入路径')
                selected = result.stdout.strip()
                self._json({'path': validate_export_dir(selected) if selected else ''})
                return
            self._json({"error": "not found"}, 404)
        except Exception as exc:
            self._fail(exc)


def main():
    global PORT
    srv = None
    for p in [PORT] + list(range(PORT + 1, PORT + 12)):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            PORT = p
            break
        except OSError:
            continue
    if srv is None:
        print("无法绑定本地端口，请关闭占用 8765 附近的程序后重试")
        return 1

    url = "http://127.0.0.1:%d/" % PORT
    print("")
    print("  红外白平衡  已启动")
    print("  地址      : %s" % url)
    print("  输出目录  : %s" % OUTDIR)
    print("  关闭窗口即退出")
    print("")
    if not os.environ.get("IR_WB_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        backend._preview_native.cache_clear()
        for e in list(ENTRIES.values()):
            try:
                os.remove(e["path"])
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

