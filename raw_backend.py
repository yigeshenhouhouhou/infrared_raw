"""Scene-linear infrared development. Camera channels are creative false colours.

The optional visible-light camera matrix is only an experimental baseline for
a modified camera and a 590 nm filter. False colours are assigned to sRGB.
"""
from functools import lru_cache
from pathlib import Path
import math

import numpy as np

ROOT = Path(__file__).resolve().parent


def positive(value, name="multiplier"):
    value = float(value)
    if not math.isfinite(value) or not 0.0001 <= value <= 10000:
        raise ValueError(f"{name} 必须是 0.0001–10000 的有限数")
    return value


def _native(path, half_size):
    import rawpy
    with rawpy.imread(str(path)) as raw:
        if raw.num_colors != 3 or raw.color_desc != b"RGBG":
            raise ValueError("目前支持三色 RGB Bayer / X-Trans RAW，不支持特殊四色传感器")
        black = np.asarray(raw.black_level_per_channel, np.float32)
        white = np.asarray(raw.camera_white_level_per_channel or
                           [raw.white_level] * 4, np.float32)
        white = np.where(white > black, white, raw.white_level)
        # LibRaw subtracts black but leaves native DN when auto scale is disabled.
        # Crucially, WB and colour conversion cannot clip in its uint16 pipeline.
        rgb = raw.postprocess(
            user_wb=[1, 1, 1, 1], use_camera_wb=False, use_auto_wb=False,
            no_auto_scale=True, no_auto_bright=True, adjust_maximum_thr=0,
            output_color=rawpy.ColorSpace.raw, output_bps=16, gamma=(1, 1),
            highlight_mode=rawpy.HighlightMode.Ignore, half_size=half_size,
            demosaic_algorithm=rawpy.DemosaicAlgorithm.LINEAR if half_size
            else rawpy.DemosaicAlgorithm.AHD,
        )
        # One common DN scale keeps CFA-derived neutral WB valid when channel
        # saturation limits differ. Per-channel whites are diagnostic thresholds,
        # not independent colour gains. G1/G2 jointly anchor the exposure scale.
        green_range = max(float(np.mean((white - black)[[1, 3]])), 1)
    return rgb.astype(np.float32) / np.float32(green_range)


@lru_cache(maxsize=2)
def _preview_native(path, mtime_ns, size):
    a = _native(path, True)
    # Box filter without trimming the sensor edges: keep ROI mapping consistent.
    factor = max(1, int(np.ceil(max(a.shape[:2]) / 1400)))
    if factor > 1:
        h, w = a.shape[:2]
        pad = ((0, (-h) % factor), (0, (-w) % factor), (0, 0))
        a = np.pad(a, pad, mode="edge")
        a = a.reshape(a.shape[0] // factor, factor,
                      a.shape[1] // factor, factor, 3).mean((1, 3))
    a.setflags(write=False)
    return a


def decode_linear(path, mr=1, mb=1, half_size=True):
    mr, mb = positive(mr, "R"), positive(mb, "B")
    source = Path(path).resolve()
    st = source.stat()
    native = (_preview_native(str(source), st.st_mtime_ns, st.st_size)
              if half_size else _native(source, False))
    # G=1 anchors exposure; WB is never normalized by the brightest channel.
    # Unlike the old Rec709 normalization, this preserves actual G-channel DN.
    return native * np.array([mr, 1, mb], np.float32)


def srgb_encode(a):
    a = np.maximum(a, 0)
    return np.where(a <= 0.0031308, 12.92 * a,
                    1.055 * np.power(a, 1 / 2.4) - 0.055).astype(np.float32)


# Match the Android renderer's D65 camera matrix, Bradford adaptation and
# ProPhoto mixer. This visible-light baseline is not an IR characterization.
_XYZ50_TO_PROPHOTO = np.array([[1.3459433, -.2556075, -.0511118],
    [-.5445989, 1.5081673, .0205351], [0, 0, 1.2118128]])
_D65_TO_D50 = np.array([[1.0478112, .0228866, -.0501270],
    [.0295424, .9904844, -.0170491], [-.0092345, .0150436, .7521316]])
_PROPHOTO_TO_XYZ50 = np.array([[.7976749, .1351917, .0313534],
    [.2880402, .7118741, .0000857], [0, 0, .8252100]])
_XYZ65_TO_SRGB = np.array([[3.2404542, -1.5371385, -.4985314],
    [-.9692660, 1.8760108, .0415560], [.0556434, -.2040259, 1.0572252]])
_PROPHOTO_TO_SRGB = _XYZ65_TO_SRGB @ np.linalg.inv(_D65_TO_D50) @ _PROPHOTO_TO_XYZ50
_SRGB_TO_PROPHOTO = np.linalg.inv(_PROPHOTO_TO_SRGB)
# Adapted from anxix/rt_ir_presets, ir590-yellow-trees.pp3, CC BY-SA 4.0.
_MIXER = np.array([[-.11, .05, 1.], [-.40, .83, .46], [.97, .24, -.46]])
_MIXER = _MIXER / _MIXER.sum(axis=1, keepdims=True)

# Channel-mixer stages adapted from anxix/rt_ir_presets (CC BY-SA 4.0).
# Rows are normalized to keep an input neutral neutral. RawTherapee's WB,
# tone curves and other modules are not reproduced by these matrices.
_PROFILE_MIXERS = {
    'reference590': _MIXER,
    'pastel_red': np.array([[-11, 5, 100], [-9, 83, 3], [97, 1, -7]], dtype=float),
    'red_foliage': np.array([[-9, 13, 114], [-13, 80, 25], [106, 1, -13]], dtype=float),
    'orange_foliage': np.array([[-17, 10, 106], [-27, 85, 25], [93, 10, -47]], dtype=float),
}
for _name in ('pastel_red', 'red_foliage', 'orange_foliage'):
    _PROFILE_MIXERS[_name] /= _PROFILE_MIXERS[_name].sum(axis=1, keepdims=True)

CAMERA_COLOR_MODES = frozenset((*_PROFILE_MIXERS, 'camera'))
COLOR_MODES = CAMERA_COLOR_MODES | frozenset(('classic', 'bluegold', 'linear_rb', 'linear_brg'))


@lru_cache(maxsize=16)
def _camera_to_prophoto(path, mtime_ns, size):
    import rawpy
    with rawpy.imread(path) as raw:
        cm = np.asarray(raw.rgb_xyz_matrix[:3, :3], dtype=np.float64)
    try:
        if not np.isfinite(cm).all() or abs(np.linalg.det(cm)) < 1e-9:
            raise ValueError("no usable camera matrix")
        response = cm @ np.array([.95047, 1, 1.08883])
        matrix = _XYZ50_TO_PROPHOTO @ _D65_TO_D50 @ (np.linalg.inv(cm) * response)
        return matrix.astype(np.float32)
    except (ValueError, np.linalg.LinAlgError):
        return _SRGB_TO_PROPHOTO.astype(np.float32)


def camera_to_prophoto(path):
    source = Path(path).resolve()
    st = source.stat()
    return _camera_to_prophoto(str(source), st.st_mtime_ns, st.st_size)


def profile_color(a, mode, camera_matrix=None):
    if mode == 'linear_rb':
        return a[..., [2, 1, 0]]
    if mode == 'linear_brg':
        return a[..., [2, 0, 1]]
    if mode not in CAMERA_COLOR_MODES:
        return a
    matrix = _SRGB_TO_PROPHOTO if camera_matrix is None else camera_matrix
    transform = _PROPHOTO_TO_SRGB @ (_PROFILE_MIXERS[mode] @ matrix if mode in _PROFILE_MIXERS else matrix)
    out = np.asarray(a @ transform.astype(np.float32).T, np.float32)
    y = np.maximum(out @ np.array([.2126, .7152, .0722], np.float32), 0)
    t = np.ones_like(y)
    for c in range(3):
        t = np.minimum(t, np.where(out[..., c] < 0,
                       y / np.maximum(y - out[..., c], 1e-12), 1))
    return np.maximum(y[..., None] + t[..., None] * (out - y[..., None]), 0)


def prepare_color(a, p):
    """Creative 590 nm mixing and chroma separation in scene-linear light.

    This is a look, not a measured camera colour calibration. A neutral stays
    neutral. The power transform expands channel ratios without negative RGB;
    common gain restores the input luminance before the display shoulder.
    """
    if p.get("color_mode", "classic") != "bluegold":
        return a
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mixed = np.stack((.05*r + .15*g + .80*b,
                      .05*r + .90*g + .05*b,
                      .85*r + .10*g + .05*b), axis=-1)
    peak = mixed.max(axis=-1)
    chroma = (peak - mixed.min(axis=-1)) / np.maximum(peak, 1e-12)
    signal = np.clip(peak / .015, 0, 1)
    exponent = 1 + .025*p.get("color_sep", 60)*chroma/(chroma+.02)*signal
    ratios = mixed / np.maximum(peak[..., None], 1e-12)
    separated = np.power(np.maximum(ratios, 0), exponent[..., None])
    weights = np.array([.2126, .7152, .0722], np.float32)
    lum = (a * weights).sum(axis=-1)
    new_lum = np.maximum((separated * weights).sum(axis=-1), 1e-12)
    out = separated * (lum/new_lum)[..., None]
    # Avoid numerical tint / noise expansion in truly achromatic pixels.
    neutral = (a.max(axis=-1)-a.min(axis=-1)) <= 1e-6*np.maximum(a.max(axis=-1),1e-12)
    return np.where(neutral[..., None], a, out).astype(np.float32)


def tone_map(lin, p, camera_matrix=None):
    """Exposure/brightness/shadows/contrast, then hue-preserving shoulder.

    A logarithmic shoulder stays strictly increasing well above white. All colour
    channels share one gain, avoiding the old per-channel bleaching of skies.
    """
    a = np.maximum(np.asarray(lin, np.float32), 0) * np.float32(2 ** p["exposure"])
    a = profile_color(a, p.get("color_mode", "classic"), camera_matrix)
    a = prepare_color(a, p)
    peak = a.max(axis=-1)
    v = peak.copy()
    brightness = p.get("brightness", 0) / 100
    if brightness:
        v *= np.exp2(2 * brightness * (0.18 / (v + 0.18)))
    shadows = p["shadows"] / 100
    if shadows:
        # Multiplicative lift keeps sensor black at zero and leaves highlights.
        v *= 1 + 4 * shadows * np.exp(-v / 0.12)
    contrast = p["contrast"] / 100
    if contrast:
        v = 0.18 * np.power(v / 0.18, 1 + 0.55 * contrast)
    knee = 0.8 - 0.5 * p["highlights"] / 100
    over = np.maximum(v - knee, 0)
    span = 1 - knee
    compressed = np.log1p(over / span)
    mapped = np.where(v > knee, knee + span * compressed / (1 + compressed), v)
    chroma = np.divide(a, peak[..., None], out=np.zeros_like(a), where=peak[..., None] > 0)
    display = srgb_encode(chroma * mapped[..., None])
    black = p.get("black", 0) / 100 * 0.22
    if black > 0:
        display = np.clip((display - black) / (1 - black), 0, 1)
    return display.astype(np.float32, copy=False)


def auto_scale(lin):
    # Meter the body of the image, not just its brightest colour channel.
    # Retain the old bright-scene floor; the floating shoulder handles superwhite
    # highlights rather than forcing the entire scene down to protect them.
    sample = np.maximum(lin[::2, ::2], 0)
    lum = sample @ np.array([.2126, .7152, .0722], np.float32)
    valid = lum[np.isfinite(lum) & (lum > 1e-6)]
    if not valid.size:
        return 1.0
    p50, p75 = np.percentile(valid, [50, 75])
    key = .4*float(p50) + .6*float(p75)
    peak = sample.max(axis=-1)
    peak = peak[np.isfinite(peak)]
    bright_floor = .65/max(float(np.percentile(peak,99.5)),1e-6)
    return float(np.clip(max(bright_floor, .28/max(key,1e-6)),1/256,256))


def sensor_diagnostics(path):
    import rawpy
    result = {}
    with rawpy.imread(str(path)) as raw:
        data = raw.raw_image_visible
        colors = raw.raw_colors_visible
        white = raw.camera_white_level_per_channel or [raw.white_level] * 4
        for c in range(4):
            v = data[colors == c]
            if not v.size:
                continue
            threshold = white[c] if white[c] > raw.black_level_per_channel[c] else raw.white_level
            result[str(c)] = {"samples": int(v.size), "white": int(threshold),
                              "saturated_percent": float(100 * np.mean(v >= threshold))}
    return result


def srgb_icc():
    from PIL import ImageCms
    return ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
