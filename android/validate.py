"""Validate mobile native code against desktop formulas and real LibRaw input.

This runs the same C++ core on Windows. It is not an Android device/emulator test.
"""
from pathlib import Path
import subprocess
import sys
import os
import json
import zipfile
import struct
import hashlib
import numpy as np
from PIL import Image

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
sys.path.insert(0,str(ROOT.parent/'tests'))
import ir_wb_gui as desktop
import test_backend
os.environ['TEMP']=str(ROOT/'runtime/tmp')
os.environ['TMP']=os.environ['TEMP']
os.environ['PYTHONDONTWRITEBYTECODE']='1'
EXE=ROOT/'runtime/native-host/native_check.exe'
report={'device_tested':False,'checks':[]}

def run(*args):
    r=subprocess.run([str(EXE),*map(str,args)],cwd=ROOT,capture_output=True,text=True)
    if r.returncode:raise RuntimeError(r.stderr)
    return r.stdout

keys=['exposure','brightness','highlights','shadows','contrast','black','mr','mb',
      'swap_mode','swap','sat','hotspot','sky_light','veg_light','denoise','sharpen','swap_hl',
      'color_mode','color_sep','gold','blue','vibrance']
cases=[{}, {'exposure':8,'contrast':100,'highlights':100},
       {'exposure':-8,'contrast':-100}, {'brightness':100,'shadows':100,'black':35},
       {'brightness':-100,'shadows':40,'contrast':40,'mr':.1,'mb':10},
       {'sat':0}, {'sat':180,'sky_light':80,'veg_light':-60}, {'hotspot':85}]
for mode in range(6):cases.append({'swap_mode':mode,'swap':100,'swap_hl':75})
cases += [{'color_mode':'classic'}, {'color_sep':100,'gold':100,'blue':100,'vibrance':100},
          {'color_sep':0,'gold':0,'blue':0,'vibrance':0}, {'color_mode':'classic','swap_mode':1,'swap':100}]
maximum=0
for case in cases:
    d={**desktop.DEFAULTS,'color_mode':'bluegold','mr':1.,'mb':1.,'swap_mode':0,**case}
    values=[(1 if d[k]=='bluegold' else 0) if k=='color_mode' else d[k] for k in keys]
    actual=np.array([[float(x) for x in ln.split()] for ln in run('--pixels',*values).splitlines()])
    expected=[]
    for v in [0,.0001,.01,.05,.18,.5,1,2,8,100]:
        a=np.broadcast_to(np.array([v*d['mr'],v*.6,v*.2*d['mb']],np.float32),(3,3,3))
        p={**desktop.DEFAULTS,'color_mode':'bluegold',**case,'swap_mode':desktop.SWAP_MODES[int(d['swap_mode'])]}
        b=desktop.process_array(desktop.tone_map(a,p),p)
        expected.append(b[1,1])
    error=float(np.abs(actual-np.array(expected)).max());maximum=max(maximum,error)
    assert error<4e-6,(case,error)
report['checks'].append({'name':'native float pixel parity','cases':len(cases),'samples':len(cases)*10,'max_abs_error':maximum})

new_cases=[('reference590',3,{}),('coral_canopy',8,{}),('linear_brg',7,{}),
           ('linear_rb',6,{}),('camera',2,{}),('classic',0,{}),
           ('coral_canopy',8,{'warm_sat':-75,'cool_sat':40,'gold':30,'blue':-20,'color_sep':20,'vibrance':20}),
           ('linear_brg',7,{'sat':150,'warm_sat':40,'cool_sat':-60,'sky_light':35,'veg_light':-25})]
new_max=0
for name,mode,case in new_cases:
    p={**desktop.DEFAULTS,'color_mode':name,**case}
    values=[p.get(k,1 if k in ('mr','mb') else 0) if k!='swap_mode' and k!='color_mode' else (mode if k=='color_mode' else 0) for k in keys]
    values += [0,100,50,0,100,0,0,p.get('warm_sat',0),p.get('cool_sat',0)]
    actual=np.array([[float(x) for x in ln.split()] for ln in run('--pixels',*values).splitlines()])
    expected=[]
    for v in [0,.0001,.01,.05,.18,.5,1,2,8,100]:
        a=np.broadcast_to(np.array([v,v*.6,v*.2],np.float32),(3,3,3))
        expected.append(desktop.process_array(desktop.tone_map(a,p),p)[1,1])
    error=float(np.abs(actual-np.array(expected)).max());new_max=max(new_max,error)
    assert error<5e-6,(name,case,error)
report['checks'].append({'name':'six desktop color schemes and selective controls','cases':len(new_cases),'max_abs_error':new_max})

# These alternative looks must leave a balanced neutral grey neutral. The
# reference mixer has been adapted to this pipeline; this is not PP3 parity.
for mode in (2,3,6):
    values=[0,0,0,0,0,0,1,1,0,0,100,0,0,0,0,0,0,mode,0,0,0,0,0,100,50,0,100,0,0]
    neutral=np.array([[float(x) for x in ln.split()] for ln in run('--neutral',*values).splitlines()])
    assert np.isfinite(neutral).all() and np.ptp(neutral,axis=1).max()<2e-5,(mode,neutral)
values[17]=6
swapped=np.array([[float(x) for x in ln.split()] for ln in run('--pixels',*values).splitlines()])
for i,v in enumerate([0,.0001,.01,.05,.18,.5,1,2,8,100]):
    expected=desktop.tone_map(np.array([[[v*.2,v*.6,v]]],np.float32),{**desktop.DEFAULTS,'color_mode':'classic'})[0,0]
    np.testing.assert_allclose(swapped[i],expected,atol=2e-6)
report['checks'].append({'name':'alternative modes preserve neutral and linear red-blue swap','result':'passed'})

fixture=ROOT/'runtime/ramp.dng';test_backend.make_dng(fixture)
run('runtime/ramp.dng','runtime/ramp.ppm')
with Image.open(ROOT/'runtime/ramp.ppm') as im:
    actual=np.asarray(im).astype(np.float32)/255
    lin=desktop.decode_linear(fixture,1,1,True)
    expected=desktop.tone_map(lin*desktop.backend.auto_scale(lin),{**desktop.DEFAULTS,'color_mode':'bluegold'})
    assert im.size==(64,64)
    assert np.abs(actual-expected).max() <= 1.01/255
report['checks'].append({'name':'synthetic DNG through LibRaw','dimensions':[64,64],'result':'passed'})

raw=ROOT.parent/'output/.cache/197c35c8bedb.arw'
if raw.is_file():
    for mode,dims in [('',(1152,768)),('--half',(2304,1536)),('--full',(4608,3072))]:
        name='real'+(mode or 'preview')+'.ppm'
        args=[str(raw.relative_to(ROOT.parent.parent))]  # run with an ASCII relative path
        args=['../output/.cache/'+raw.name,'runtime/'+name]
        if mode:args.insert(0,mode)
        meta=run(*args).strip()
        with Image.open(ROOT/'runtime'/name) as im:assert im.size==dims
        report['checks'].append({'name':'real Sony ARW '+(mode or 'preview'),'dimensions':list(dims),'metadata':meta})

apk=ROOT/'dist/InfraredRAW-0.4.0-android8-arm64.apk'
with zipfile.ZipFile(apk) as z:
    assert z.testzip() is None
    assert z.read('classes.dex').startswith(b'dex\n')
    assert z.read('assets/sRGB.icc')[36:40]==b'acsp'
    for abi,machine in [('arm64-v8a',183),('x86_64',62)]:
        elf=z.read('lib/'+abi+'/libinfrared.so');assert elf[:4]==b'\x7fELF'
        assert struct.unpack_from('<H',elf,18)[0]==machine
        offset=struct.unpack_from('<Q',elf,32)[0];entrySize,count=struct.unpack_from('<HH',elf,54)
        loads=[]
        for i in range(count):
            at=offset+i*entrySize
            if struct.unpack_from('<I',elf,at)[0]==1:loads.append(struct.unpack_from('<Q',elf,at+48)[0])
        assert loads and min(loads)>=16384,(abi,loads)
report['checks'].append({'name':'APK integrity, DEX, ICC and 16 KB ELF alignment','result':'passed'})
report['apk_sha256']=hashlib.sha256(apk.read_bytes()).hexdigest()
report['apk_bytes']=apk.stat().st_size
(ROOT/'dist/validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
