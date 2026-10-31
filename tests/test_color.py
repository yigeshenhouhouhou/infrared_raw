import numpy as np
import pytest
import ir_wb_gui as gui
import raw_backend as backend


def develop(a,**kw):
    p={**gui.DEFAULTS,'color_mode':'bluegold','color_sep':60,'gold':85,'blue':85,'vibrance':35,**kw}
    return gui.process_array(backend.tone_map(a,p),p)


def test_blue_gold_has_directed_hues_and_more_chroma():
    # Camera-native warm/cool signatures, not semantic sky/foliage masks.
    a=np.array([[[.20,.12,.08]],[[.08,.14,.20]]],np.float32)
    old=develop(a,color_mode='classic',swap_mode='rb',swap=100,color_sep=0,gold=0,blue=0,vibrance=0)
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
    b=backend.prepare_color(a,{**gui.DEFAULTS,'color_mode':'bluegold','color_sep':100})
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


def test_new_modes_keep_neutral_and_linear_swap():
    ramp=np.repeat(np.geomspace(.00001,100,100)[None,:,None],3,-1).astype(np.float32)
    for mode in ('reference590','camera','linear_rb'):
        out=backend.tone_map(ramp,{**gui.DEFAULTS,'color_mode':mode})
        np.testing.assert_allclose(out[...,0],out[...,1],atol=2e-5)
        np.testing.assert_allclose(out[...,1],out[...,2],atol=2e-5)
    a=np.array([[[.1,.2,2.]]],np.float32)
    actual=backend.tone_map(a,{**gui.DEFAULTS,'color_mode':'linear_rb'})
    expected=backend.tone_map(a[...,::-1],{**gui.DEFAULTS,'color_mode':'classic'})
    np.testing.assert_allclose(actual,expected,atol=1e-7)


def test_selective_saturation_and_color_controls():
    colors=np.array([[[.8,.5,.25],[.25,.5,.8],[.5,.5,.5]]],np.float32)
    p={**gui.DEFAULTS,'color_mode':'reference590'}
    warm=gui.process_array(colors,{**p,'warm_sat':-100})
    cool=gui.process_array(colors,{**p,'cool_sat':-100})
    saturation=lambda x: gui._rgb_to_hsv(x)[1]
    assert saturation(warm)[0,0]<saturation(colors)[0,0]*.3
    assert saturation(cool)[0,1]<saturation(colors)[0,1]*.3
    np.testing.assert_allclose(warm[0,1],colors[0,1],atol=1e-6)
    np.testing.assert_allclose(cool[0,0],colors[0,0],atol=1e-6)
    for name in ('color_sep','gold','blue'):
        changed=gui.process_array(colors,{**p,name:100})
        assert np.max(np.abs(changed-colors))>.02, name
        np.testing.assert_allclose(changed[0,2],colors[0,2],atol=1e-6)


@pytest.mark.parametrize('mode', ['pastel_red', 'red_foliage', 'orange_foliage', 'linear_brg', 'coral_canopy'])
def test_added_profiles_preserve_neutral_and_change_chromatic_pixels(mode):
    values=np.geomspace(.0001,30,80).astype(np.float32)
    neutral=np.repeat(values[None,:,None],3,-1)
    p={**gui.DEFAULTS,'color_mode':mode}
    rendered=backend.tone_map(neutral,p)
    assert np.isfinite(rendered).all()
    np.testing.assert_allclose(rendered[...,0],rendered[...,1],atol=3e-5)
    np.testing.assert_allclose(rendered[...,1],rendered[...,2],atol=3e-5)
    a=np.array([[[.04,.12,.3],[.25,.1,.03]]],np.float32)
    assert np.max(np.abs(backend.tone_map(a,p)-backend.tone_map(a,{**p,'color_mode':'classic'})))>.02


def test_cyclic_channel_profile_order():
    a=np.array([[[.1,.2,.4]]],np.float32)
    np.testing.assert_array_equal(backend.profile_color(a,'linear_brg'),a[..., [2,0,1]])


def test_profile_names_are_accepted_by_server():
    for mode in ('pastel_red','red_foliage','orange_foliage','linear_brg','coral_canopy'):
        assert gui.parse_params({'color_mode':[mode]})['color_mode']==mode


def test_coral_grade_keeps_greys_neutral_and_softens_highlights():
    p={**gui.DEFAULTS,'color_mode':'coral_canopy'}
    values=np.linspace(0,1,200,dtype=np.float32)
    greys=np.repeat(values[None,:,None],3,axis=-1)
    result=gui.process_array(greys,p)
    np.testing.assert_allclose(result[...,0],result[...,1],atol=1e-6)
    np.testing.assert_allclose(result[...,1],result[...,2],atol=1e-6)
    assert np.all(np.diff(result[0,:,0])>0)
    assert result.min()>=0 and result.max()<=1
    patches=np.array([[[.75,.40,.38],[.20,.40,.43],[.5,.5,.5]]],np.float32)
    output=gui.process_array(patches,p)
    assert output[0,0,0]>output[0,0,2]>output[0,0,1]
    assert output[0,1].max()<patches[0,1].max()
