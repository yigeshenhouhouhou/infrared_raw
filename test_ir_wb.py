#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对 ir_wb.cfa_channel_means / solve_multipliers 的合成样本验证。

用已知通道真值（R:G:B = 1000:2000:500）构造 Bayer RGGB 合成图，
检查求解出的倍率能否精确复原 R=G=B。不依赖任何真实 RAW 文件。
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ir_wb import cfa_channel_means, solve_multipliers  # noqa: E402

SEED = 20260808
BLACK = 512
WHITE = 65535
PATTERN = np.array([[0, 1], [1, 2]], dtype=np.uint8)  # RGGB
COLOR_DESC = b"RGBG"

fails = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (("   " + detail) if detail else ""))
    if not cond:
        fails.append(name)


def make_bayer(h, w, r, g, b, black=BLACK, noise=0.0, rng=None):
    colors = np.tile(PATTERN, (h // 2, w // 2))
    data = np.zeros((h, w), dtype=np.float64)
    data[colors == 0] = r
    data[colors == 1] = g
    data[colors == 2] = b
    if noise > 0.0:
        data = data + rng.normal(0.0, noise, size=data.shape)
    data = data + black
    return np.clip(data, 0, WHITE).astype(np.uint16), colors


def main():
    rng = np.random.default_rng(SEED)
    print("合成样本验证 (seed=%d)" % SEED)
    print("")

    print("[1] 无噪声: 真值 R:G:B = 1000:2000:500")
    data, colors = make_bayer(64, 64, 1000, 2000, 500)
    means, stats = cfa_channel_means(data, colors, [BLACK] * 4, WHITE, COLOR_DESC)
    check("R 均值 = 1000", abs(means["R"] - 1000) < 1e-6, "got %.3f" % means["R"])
    check("G 均值 = 2000", abs(means["G"] - 2000) < 1e-6, "got %.3f" % means["G"])
    check("B 均值 = 500", abs(means["B"] - 500) < 1e-6, "got %.3f" % means["B"])
    mul = solve_multipliers(means)
    check("m_R = 2.0", abs(mul["R"] - 2.0) < 1e-9, "got %.6f" % mul["R"])
    check("m_G = 1.0", abs(mul["G"] - 1.0) < 1e-12)
    check("m_B = 4.0", abs(mul["B"] - 4.0) < 1e-9, "got %.6f" % mul["B"])
    check("归中后三通道相等",
          abs(means["R"] * mul["R"] - means["B"] * mul["B"]) < 1e-6,
          "R'=%.2f B'=%.2f" % (means["R"] * mul["R"], means["B"] * mul["B"]))

    print("")
    print("[2] 含噪声 (sigma=40): 倍率偏差应 < 2%")
    data, colors = make_bayer(256, 256, 1000, 2000, 500, noise=40.0, rng=rng)
    means, _ = cfa_channel_means(data, colors, [BLACK] * 4, WHITE, COLOR_DESC)
    mul = solve_multipliers(means)
    check("m_R ≈ 2.0", abs(mul["R"] - 2.0) / 2.0 < 0.02, "got %.4f" % mul["R"])
    check("m_B ≈ 4.0", abs(mul["B"] - 4.0) / 4.0 < 0.02, "got %.4f" % mul["B"])

    print("")
    print("[3] 混入 5% 饱和像素: 不应拉高均值")
    data, colors = make_bayer(64, 64, 1000, 2000, 500)
    flat = data.reshape(-1)
    idx = rng.choice(flat.size, size=flat.size // 20, replace=False)
    flat[idx] = WHITE
    data = flat.reshape(data.shape)
    means, _ = cfa_channel_means(data, colors, [BLACK] * 4, WHITE, COLOR_DESC)
    check("R 均值仍 ≈ 1000", abs(means["R"] - 1000) / 1000 < 0.02, "got %.2f" % means["R"])

    print("")
    print("[4] region 限定: 左半 R=1000, 右半 R=5000")
    data, colors = make_bayer(64, 64, 1000, 2000, 500)
    data[:, 32:] = np.where(colors[:, 32:] == 0, 5000 + BLACK, data[:, 32:])
    m_left, _ = cfa_channel_means(data, colors, [BLACK] * 4, WHITE, COLOR_DESC,
                                  region=(0.0, 0.0, 0.4, 1.0))
    m_right, _ = cfa_channel_means(data, colors, [BLACK] * 4, WHITE, COLOR_DESC,
                                   region=(0.6, 0.0, 1.0, 1.0))
    check("左半 R ≈ 1000", abs(m_left["R"] - 1000) < 1.0, "got %.1f" % m_left["R"])
    check("右半 R ≈ 5000", abs(m_right["R"] - 5000) < 1.0, "got %.1f" % m_right["R"])

    print("")
    if fails:
        print("FAILED: %d 项 %s" % (len(fails), fails))
        return 1
    print("ALL PASS — 求解器在合成样本上可精确复原已知倍率")
    return 0


if __name__ == "__main__":
    sys.exit(main())
