"""Regression tests: native RAW headroom, transfer curve, output and HTTP API."""
import io
import json
from pathlib import Path
import sys
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import numpy as np
import pytest
import tifffile
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ir_wb
import ir_wb_gui as gui
import raw_backend as backend


def make_dng(path):
    h = w = 128
    # Horizontal step ramp, including sensor-saturated patches.
    ramp = np.repeat(np.array([256, 512, 1024, 2048, 4096, 8192, 12000, 15871]), 16)
    rgb = np.broadcast_to(ramp[None, :, None], (h, w, 3))
    colors = np.tile(np.array([[0, 1], [1, 2]]), (h//2, w//2))
    image = np.take_along_axis(rgb, colors[..., None], axis=-1)[..., 0] + 512
    tags = [(50706, 'B', 4, (1,4,0,0), True),
            (50707, 'B', 4, (1,1,0,0), True),
            (50708, 's', 0, 'Infrared regression', True),
            (50714, 'H', 1, 512, True), (50717, 'H', 1, 16383, True),
            (50721, '2i', 9, (1,1,0,1,0,1,0,1,1,1,0,1,0,1,0,1,1,1), True),
            (50728, '2I', 3, (1,1,1,1,1,1), True), (50778,'H',1,21,True),
            (33421,'H',2,(2,2),True),(33422,'B',4,(0,1,1,2),True)]
    tifffile.imwrite(path, image.astype(np.uint16), photometric='cfa', extratags=tags)


@pytest.fixture
def dng(tmp_path):
    p = tmp_path/'ramp.dng'
    make_dng(p)
    return p


def params(**kw):
    return {**gui.DEFAULTS, **kw}


def test_decode_preserves_headroom_and_black(dng):
    a = backend.decode_linear(dng, 4, 2, False)
    # An actual LibRaw decode must retain a ramp above 1 after float WB.
    assert a.dtype == np.float32
    values = a[60, [8,24,40,56,72,88,104,120], 0]
    np.testing.assert_allclose(values, 4*np.array([256,512,1024,2048,4096,8192,12000,15871])/15871,
                               atol=5e-4)
    assert np.all(np.diff(values) > 0)
    assert values[-1] > 3.99
    b = backend.decode_linear(dng, 1, 1, False)
    np.testing.assert_allclose(a[..., 0], b[..., 0]*4, atol=1e-6)
    np.testing.assert_array_equal(a[..., 1], b[..., 1])


@pytest.mark.parametrize('ev', [-8, 0, 4, 8])
@pytest.mark.parametrize('contrast', [-100, 0, 100])
def test_highlight_ramp_does_not_flatten(ev, contrast):
    a = np.repeat(np.linspace(.05, 8, 256)[None,:,None], 3, -1).astype('float32')
    o = backend.tone_map(a, params(exposure=ev, contrast=contrast, highlights=80))
    assert np.isfinite(o).all()
    assert o.min() >= 0 and o.max() <= 1
    # Float precision may merge adjacent values at extreme contrast + exposure.
    assert np.all(np.diff(o[0,:,0]) >= 0)
    assert len(np.unique(o[0,:,0])) > 240


def test_chroma_is_preserved_before_srgb():
    a = np.array([[[8.,4.,2.]]], np.float32)
    o = backend.tone_map(a, params(highlights=100,color_mode='classic'))
    linear = np.where(o <= .04045, o/12.92, ((o+.055)/1.055)**2.4)
    np.testing.assert_allclose(linear[0,0]/linear[0,0,1], [2,1,.5], atol=1e-5)


def test_brightness_protects_highlights_and_black():
    a=np.repeat(np.array([0,.05,.18,.5,4],np.float32)[None,:,None],3,-1)
    base=backend.tone_map(a,params())
    mid=backend.tone_map(a,params(brightness=100))
    exp=backend.tone_map(a,params(exposure=2))
    assert mid[0,0,0] == 0
    assert mid[0,2,0] > base[0,2,0]
    assert mid[0,4,0]-base[0,4,0] < exp[0,4,0]-base[0,4,0]


def test_saturation_uses_black_corrected_channel_white():
    colors=np.tile(np.array([[0,1],[3,2]],np.uint8),(40,40))
    data=np.full(colors.shape,1100.)
    data[:, :20]=3999  # below global white 16383, saturated for this channel
    means, stats=ir_wb.cfa_channel_means(data,colors,[100]*4,[4000]*4,b'RGBG')
    assert means == {'R':1000.,'G':1000.,'B':1000.}
    assert all(v[1] == 1200 for v in stats.values())
    with pytest.raises(ValueError):
        ir_wb.cfa_channel_means(data,colors,[100]*4,4000,b'RGBG',(-.1,0,1,1))


def test_export_linear_and_icc(dng, tmp_path, monkeypatch):
    monkeypatch.setattr(gui,'OUTDIR',str(tmp_path))
    p=params(exposure=2)
    linear=gui.export_tiff(dng,4,2,p,1,'ramp.dng','linear')
    a=tifffile.imread(linear)
    assert a.dtype == np.float32 and a.max() > 15.9
    recipe=json.loads(Path(linear+'.json').read_text('utf-8'))
    assert recipe['white_balance'] == [4,1,2]
    developed=gui.export_tiff(dng,4,2,p,1,'ramp.dng')
    with tifffile.TiffFile(developed) as tif:
        assert tif.asarray().dtype == np.uint16
        assert tif.pages[0].tags[34675].value == backend.srgb_icc()
    jpg=gui.render_jpeg(dng,4,2,p,1)
    im=Image.open(io.BytesIO(jpg))
    assert im.info['icc_profile']
    jpg_path=gui.export_tiff(dng,4,2,params(exposure=2,black=35,swap=100,swap_mode='rb'),1,'ramp.dng','jpeg')
    with Image.open(jpg_path) as im:
        assert im.format == 'JPEG'
        assert im.size == (a.shape[1], a.shape[0])
        assert im.info['icc_profile'] == backend.srgb_icc()
        # Full-size JPG uses the same edited pixels as developed TIFF.
        expected=gui.process_array(gui.tone_map(gui.decode_linear(dng,4,2,False),
                    params(exposure=2,black=35,swap=100,swap_mode='rb'),backend.camera_to_prophoto(dng)),
                    params(exposure=2,black=35,swap=100,swap_mode='rb'))
        assert np.abs(np.asarray(im).astype(float)/255-expected).mean() < .015
    assert json.loads(Path(jpg_path+'.json').read_text('utf-8'))['parameters']['black'] == 35


def test_half_full_exposure_agreement(dng):
    full=backend.decode_linear(dng,3,2,False)
    preview=backend.decode_linear(dng,3,2,True)
    np.testing.assert_allclose(preview[30, [4,12,20,28,36,44,52,60]],
                               full[60, [8,24,40,56,72,88,104,120]], atol=5e-4)


def test_coral_export_uses_same_grade(dng, tmp_path, monkeypatch):
    monkeypatch.setattr(gui, 'OUTDIR', str(tmp_path))
    p=params(color_mode='coral_canopy')
    target=gui.export_tiff(dng,1,1,p,1,'ramp.dng','jpeg')
    expected=gui.process_array(backend.tone_map(backend.decode_linear(dng,1,1,False),p,
                                                 backend.camera_to_prophoto(dng)),p)
    with Image.open(target) as im:
        assert np.abs(np.asarray(im).astype(float)/255-expected).mean()<.015
    assert json.loads(Path(target+'.json').read_text('utf-8'))['parameters']['color_mode']=='coral_canopy'


def test_blur_does_not_wrap():
    a=np.zeros((9,9,3),np.float32); a[:,0]=1
    assert gui._blur3(a)[:,-1].max() == 0


def test_http_upload_preview_export(dng, tmp_path, monkeypatch):
    monkeypatch.setattr(gui,'CACHE_DIR',str(tmp_path))
    monkeypatch.setattr(gui,'OUTDIR',str(tmp_path))
    server=gui.ThreadingHTTPServer(('127.0.0.1',0),gui.Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(Request(url+'/api/analyze',data=dng.read_bytes(),headers={'X-Filename':'ramp.dng'})) as r:
            entry=json.load(r)
        fid=entry['fid']
        with urlopen(url+f'/api/render?fid={fid}&mr=4&mb=2&exposure=3') as r:
            assert r.headers['Content-Type']=='image/jpeg'
            assert Image.open(io.BytesIO(r.read())).size[0]>0
        with urlopen(url+f'/api/render?fid={fid}&mr=4&mb=2&color_mode=pastel_red') as r:
            assert Image.open(io.BytesIO(r.read())).size[0]>0
        with urlopen(Request(url+f'/api/export?fid={fid}&mr=4&mb=2&mode=linear',data=b'')) as r:
            assert Path(json.load(r)['path']).is_file()
        with urlopen(Request(url+f'/api/export?fid={fid}&mr=4&mb=2&mode=jpeg&black=35',data=b'')) as r:
            exported=Path(json.load(r)['path'])
            assert exported.suffix == '.jpg'
            with Image.open(exported) as im:
                assert im.size == (128,128)
        with urlopen(Request(url+f'/api/export?fid={fid}&mode=jpeg&color_mode=pastel_red',data=b'')) as r:
            exported=Path(json.load(r)['path'])
            assert json.loads(Path(str(exported)+'.json').read_text('utf-8'))['parameters']['color_mode']=='pastel_red'
        custom=tmp_path/'custom';custom.mkdir()
        from urllib.parse import quote
        with urlopen(Request(url+f'/api/export?fid={fid}&mode=jpeg&outdir='+quote(str(custom)),data=b'')) as r:
            exported=Path(json.load(r)['path'])
            assert exported.parent==custom and exported.is_file()
        with pytest.raises(HTTPError) as err:
            urlopen(Request(url+f'/api/export?fid={fid}&mode=jpeg&outdir=C%3A%5C',data=b''))
        assert err.value.code == 400
        with pytest.raises(HTTPError) as err:
            urlopen(Request(url+'/api/analyze',data=b'x',headers={'Origin':'https://invalid.example'}))
        assert err.value.code == 403
        with pytest.raises(HTTPError) as err:
            urlopen(url+f'/api/render?fid={fid}&mr=nan')
        assert err.value.code == 400
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('x', ['nan','inf','-1','0','10001'])
def test_bad_wb(x):
    with pytest.raises(ValueError): backend.positive(x)
