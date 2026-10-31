"""Generate README examples from a local RAW; the source is never copied."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ir_wb
import ir_wb_gui as gui
import raw_backend as backend

PROFILES = {
    'golden-forest.jpg': 'reference590',
    'coral-shadows.jpg': 'coral_canopy',
    'dreamy-purple.jpg': 'linear_brg',
    'classic-swap.jpg': 'linear_rb',
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('raw', type=Path, help='source RAW (read-only)')
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'examples')
    args = parser.parse_args()
    target = args.output.resolve()
    if target.drive.upper() != 'E:':
        raise ValueError('示例输出目录必须在 E 盘')
    target.mkdir(parents=True, exist_ok=True)
    means, _, _ = ir_wb.read_cfa_stats(str(args.raw))
    gains = ir_wb.solve_multipliers(means)
    linear = backend.decode_linear(args.raw, gains['R'], gains['B'], half_size=True)
    scale = backend.auto_scale(linear)
    for name, mode in PROFILES.items():
        p = {**gui.DEFAULTS, 'color_mode': mode}
        data = gui.render_jpeg(args.raw, gains['R'], gains['B'], p, scale,
                               max_side=1400, quality=90)
        (target / name).write_bytes(data)
        print(f'{mode}: {target / name}')
    print(f'全图自动白平衡 R={gains["R"]:.6f}, B={gains["B"]:.6f}; 曝光倍率={scale:.6f}')


if __name__ == '__main__':
    main()
