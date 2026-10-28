#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ir_wb.py — 红外 RAW 白平衡倍率求解 / 线性导出

依赖: rawpy (LibRaw >= 0.20, 支持 CR3), numpy, tifffile

子命令
  info   <raw>                         打印元信息与各 CFA 通道稳健均值
  solve  <raw> [--region x0,y0,x1,y1]  求解白平衡倍率 (归一化到 G=1)
  apply  <raw> --mul R,G,B [--out F]   按倍率导出 16-bit TIFF

倍率定义
  m_R = mean(G) / mean(R),  m_G = 1,  m_B = mean(G) / mean(B)
  乘完之后中性参考区域满足 R = G = B。

统计口径
  先在 CFA 原始数据上按通道分组（不经去马赛克，避免插值污染），
  减黑电平、剔除饱和像素，再做 2.5 sigma 迭代截尾后取均值。

举例
  python ir_wb.py info  DSC01234.CR3
  python ir_wb.py solve DSC01234.CR3 --region 0.30,0.55,0.45,0.78
  python ir_wb.py apply DSC01234.CR3 --mul 0.62,1,1.85 --out out.tif
"""

import argparse
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def read_cfa_stats(path, region=None, sat_ratio=0.98):
    """返回 (means, stats, meta)。means 形如 {'R': float, 'G': float, 'B': float}。"""
    import rawpy

    with rawpy.imread(path) as raw:
        data = raw.raw_image_visible.astype(np.float32)
        colors = raw.raw_colors_visible.copy()
        bl = list(raw.black_level_per_channel)
        white = list(raw.camera_white_level_per_channel or [raw.white_level] * 4)
        white = [v if v > bl[c] else raw.white_level for c, v in enumerate(white)]
        color_desc = raw.color_desc
        if isinstance(color_desc, bytes):
            pass
        else:
            color_desc = str(color_desc).encode()

        meta = {
            "size": (raw.sizes.width, raw.sizes.height),
            "visible": (data.shape[1], data.shape[0]),
            "black_level_per_channel": bl,
            "white_level": float(raw.white_level),
            "white_level_per_channel": white,
            "color_desc": color_desc.decode(errors="replace"),
            "raw_pattern": np.asarray(raw.raw_pattern).tolist(),
            "camera_wb": list(raw.camera_whitebalance),
        }

    means, stats = cfa_channel_means(data, colors, bl, white, color_desc, region, sat_ratio)
    return means, stats, meta


def cfa_channel_means(data, colors, bl, white, color_desc, region=None, sat_ratio=0.98):
    """在 CFA 原始数据上按通道分组求稳健均值。data/colors 为同形状二维数组。"""
    h, w = data.shape
    if isinstance(color_desc, str):
        color_desc = color_desc.encode()
    if data.shape != colors.shape:
        raise ValueError("CFA 数据与颜色索引尺寸不匹配")
    if not 0 < sat_ratio <= 1:
        raise ValueError("饱和阈值必须在 (0,1]")
    if region is not None:
        if len(region) != 4 or not all(np.isfinite(x) and 0 <= x <= 1 for x in region):
            raise ValueError("取白坐标必须在 [0,1]")
        x0, y0, x1, y1 = region
        xa, xb = sorted((int(x0 * w), int(x1 * w)))
        ya, yb = sorted((int(y0 * h), int(y1 * h)))
        sub, subc = data[ya:yb, xa:xb], colors[ya:yb, xa:xb]
        if sub.size == 0:
            raise ValueError("region 落在图像之外: %r" % (region,))
    else:
        sub, subc = data, colors

    stats = {}
    for c in range(int(subc.max()) + 1):
        mask = subc == c
        n_raw = int(mask.sum())
        if n_raw < 256:
            continue
        bl_c = float(bl[c]) if c < len(bl) else float(bl[0])
        white_c = float(white if np.isscalar(white) else white[c])
        v = sub[mask] - bl_c
        v = v[v > 0]
        v = v[v < (white_c - bl_c) * sat_ratio]
        if v.size < 256:
            continue
        for _ in range(3):
            mu, sd = float(v.mean()), float(v.std())
            if sd <= 0:
                break
            keep = np.abs(v - mu) < 2.5 * sd
            if keep.all() or keep.sum() < 256:
                break
            v = v[keep]
        stats[c] = (float(v.mean()), int(v.size), white_c)

    acc = {"R": [], "G": [], "B": []}
    for c, (mu, n, _) in stats.items():
        if c < len(color_desc):
            ch = chr(color_desc[c])
            if ch in acc:
                acc[ch].append((mu, n))

    means = {}
    for ch, lst in acc.items():
        if not lst:
            continue
        tot = sum(n for _, n in lst)
        means[ch] = sum(mu * n for mu, n in lst) / tot
    return means, stats


def solve_multipliers(means):
    if "G" not in means:
        raise ValueError("找不到绿色通道，CFA 解析失败")
    g = means["G"]
    if not np.isfinite(g) or g <= 0:
        raise ValueError("绿色通道均值为 0，图像可能全黑")
    out = {"G": 1.0}
    for ch in ("R", "B"):
        if ch not in means or not np.isfinite(means[ch]) or means[ch] <= 0:
            raise ValueError("%s 通道无有效信号" % ch)
        out[ch] = g / means[ch]
    return out


def apply_multipliers(path, mul, out_path, linear=False, colorspace="sRGB"):
    import raw_backend as backend
    import tifffile
    from pathlib import Path
    if colorspace not in ("sRGB", "raw"):
        raise ValueError("红外后端支持 sRGB 成片或 raw 浮点底片；可见光 Adobe/ProPhoto 相机标定不适用于本模式")
    out = Path(out_path).resolve()
    if out.drive.upper() != "E:":
        raise ValueError("输出必须位于 E 盘")
    out.parent.mkdir(parents=True, exist_ok=True)
    mr, mg, mb = [backend.positive(x) for x in mul]
    rgb = backend.decode_linear(path, mr/mg, mb/mg, half_size=False)
    if linear or colorspace == "raw":
        tifffile.imwrite(out, rgb, photometric="rgb", metadata=None,
                         description="Scene-linear camera false-colour RGB; no ICC; G=1")
    else:
        p = dict(exposure=0, highlights=0, shadows=0, contrast=0)
        a = backend.tone_map(rgb, p)
        tifffile.imwrite(out, (a*65535+0.5).astype(np.uint16), photometric="rgb",
                         iccprofile=backend.srgb_icc(), metadata=None)
    return str(out), rgb.shape


def parse_region(s):
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region 需要 4 个归一化坐标: x0,y0,x1,y1")
    if not all(0.0 <= p <= 1.0 for p in parts):
        raise argparse.ArgumentTypeError("region 坐标必须在 [0,1] 区间")
    return tuple(parts)


def parse_mul(s):
    parts = [float(x) for x in s.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("mul 需要 3 个数: R,G,B")
    return tuple(parts)


def main(argv=None):
    ap = argparse.ArgumentParser(description="红外 RAW 白平衡倍率求解与导出")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="打印元信息与通道均值")
    p.add_argument("raw")
    p.add_argument("--region", type=parse_region, default=None)

    p = sub.add_parser("solve", help="求解白平衡倍率")
    p.add_argument("raw")
    p.add_argument("--region", type=parse_region, default=None)

    p = sub.add_parser("apply", help="按倍率导出 TIFF")
    p.add_argument("raw")
    p.add_argument("--mul", type=parse_mul, required=True)
    p.add_argument("--out", default=None)
    p.add_argument("--linear", action="store_true", help="输出 32-bit 浮点线性底片（不裁超白数据）")
    p.add_argument("--colorspace", default="sRGB",
                   choices=["sRGB", "raw"],
                   help="sRGB：16-bit 成片含 ICC；raw：32-bit 浮点相机假色底片")

    a = ap.parse_args(argv)

    try:
        means, stats, meta = read_cfa_stats(a.raw, getattr(a, "region", None))
    except Exception as exc:
        print("错误: 无法解码 %s" % a.raw, file=sys.stderr)
        print("  %s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        print("  常见原因: 路径写错 / 该格式未被 LibRaw 支持 / 文件不完整", file=sys.stderr)
        return 2

    if a.cmd == "info":
        print("文件        : %s" % a.raw)
        print("传感器尺寸  : %d x %d  (可见区 %d x %d)"
              % (meta["size"][0], meta["size"][1], meta["visible"][0], meta["visible"][1]))
        print("CFA 描述    : %s" % meta["color_desc"])
        print("CFA pattern : %s" % meta["raw_pattern"])
        print("黑电平/通道 : %s" % meta["black_level_per_channel"])
        print("白电平      : %g" % meta["white_level"])
        print("相机记录 WB : %s" % [round(x, 3) for x in meta["camera_wb"]])
        print("")
        print("各通道稳健均值 (减黑电平后, 16-bit 标度):")
        for ch in ("R", "G", "B"):
            if ch in means:
                print("  %s = %10.1f" % (ch, means[ch]))
        print("")
        order = sorted(((v, k) for k, v in means.items()), reverse=True)
        print("亮度排序    : %s" % " > ".join(k for _, k in order))
        return 0

    mul = solve_multipliers(means)

    if a.cmd == "solve":
        print("通道均值: R=%.1f  G=%.1f  B=%.1f" % (means["R"], means["G"], means["B"]))
        print("")
        print("白平衡倍率 (归一化到 G=1):")
        print("  R = %.4f" % mul["R"])
        print("  G = %.4f" % mul["G"])
        print("  B = %.4f" % mul["B"])
        print("")
        print("直接拷贝到 RawTherapee > Color > White Balance > Custom 三个输入框。")
        print("或用本脚本导出:")
        print("  python ir_wb.py apply \"%s\" --mul %.4f,1,%.4f --out out.tif"
              % (a.raw, mul["R"], mul["B"]))
        return 0

    if a.cmd == "apply":
        from pathlib import Path
        out = a.out or str(Path(__file__).resolve().parent / "output" / "ir_wb_out.tif")
        p_, shape = apply_multipliers(a.raw, a.mul, out, a.linear, a.colorspace)
        tag = "32-bit 浮点线性" if a.linear or a.colorspace == "raw" else "16-bit sRGB 成片（含 ICC）"
        print("已导出: %s  (%d x %d, %s, %s)"
              % (p_, shape[1], shape[0], a.colorspace, tag))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
