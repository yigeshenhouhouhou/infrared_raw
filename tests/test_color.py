import numpy as np
import pytest
import ir_wb_gui as gui
import raw_backend as backend


def develop(a,**kw):
    p={**gui.DEFAULTS,**kw}
    return gui.process_array(backend.tone_map(a,p),p)


def test_blue_gold_has_directed_hues_and_more_chroma():
    # Camera-native warm/cool signatures, not semantic sky/foliage masks.
    a=np.array([[[.20,.12,.08]],[[.08,.14,.20]]],np.float32)
    old=develop(a,color_mode='classic',swap_mode='rb',swap=100)
    new=develop(a)
    h,s,v=gui._rgb_to_hsv(new)
    old_s=gui._rgb_to_hsv(old)[1]
    assert 205<h[0,0]<225
    assert 44<h[1,0]<65
    assert np.all(s>old_s*1.35)
    assert np.isfinite(new).all() and new.min()>=0 and new.max()<=1


def test_neutral_ramp_is_not_painted_gold_or_blue():
    values=np.geomspace(.00001,100,200).astype(np.float32)
    a=np.repeat(values[None,:,None],3,-1)
    np.testing.assert_allclose(develop(a),develop(a,color_mode='classic'),atol=1e-6)


def test_linear_separation_preserves_luminance():
    a=np.random.default_rng(53).random((21,31,3)).astype(np.float32)*3
    b=backend.prepare_color(a,{**gui.DEFAULTS,'color_sep':100})
    w=np.array([.2126,.7152,.0722])
    np.testing.assert_allclose(a@w,b@w,rtol=3e-6,atol=1e-7)
    assert b.min()>=0


def test_auto_exposure_meters_subject_instead_of_bright_patch():
    a=np.full((100,100,3),.02,np.float32);a[:15]=.8
    old=.65/np.percentile(a[::2,::2].max(-1),99.5)
    new=backend.auto_scale(a)
    assert new>old*3
    mapped=develop(a*new,color_mode='classic')
    assert mapped[50,50,0]>.5
    assert mapped[0,0,0]<1
    assert np.isfinite(mapped).all()


@pytest.mark.parametrize('value',[.00001,.02,.18,.8,4])
def test_auto_exposure_does_not_darken_uniform_scenes(value):
    a=np.full((8,8,3),value,np.float32)
    old=np.clip(.65/value,1/256,256)
    assert backend.auto_scale(a)>=old*(1-1e-6)


def test_classic_permutation_preserves_hsv_saturation():
    a=np.random.default_rng(21).random((10,10,3)).astype(np.float32)
    p={**gui.DEFAULTS,'color_mode':'classic','swap_mode':'rb','swap':100}
    b=gui.process_array(a,p)
    np.testing.assert_allclose(gui._rgb_to_hsv(a)[1],gui._rgb_to_hsv(b)[1],atol=1e-7)
