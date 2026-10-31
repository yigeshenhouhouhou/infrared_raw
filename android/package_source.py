"""Package corresponding source, omitting photos, tool binaries and private keys."""
from pathlib import Path
import zipfile
ROOT=Path(__file__).resolve().parent
version='0.4.0'
out=ROOT/'dist'/('InfraredRAW-'+version+'-source.zip')
skip={'runtime','dist','signing','tools','__pycache__'}
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in ROOT.rglob('*'):
        if not p.is_file():continue
        rel=p.relative_to(ROOT)
        if any(x in skip for x in rel.parts) or p.name.endswith(('.zip','.tar.gz')):continue
        z.write(p,'infrared_raw/android/'+rel.as_posix())
    for name in ['raw_backend.py','ir_wb_gui.py','ir_wb.py','requirements.txt',
                 'tests/test_backend.py','tests/test_color.py',
                 'research/590nm/LICENSE.txt','research/590nm/sources.json',
                 'research/590nm/ir590-yellow-trees.pp3',
                 'research/590nm/ir590-yellow-trees-blue-sky-dramatic.pp3',
                 'research/590nm/ir590-blue-sky-white-leaves.pp3',
                 'research/590nm/ir590-pastel-red-trees.pp3',
                 'research/590nm/ir590-red-trees.pp3',
                 'research/590nm/ir590-orange-trees.pp3']:
        z.write(ROOT.parent/name,'infrared_raw/'+name)
with zipfile.ZipFile(out) as z:
    assert z.testzip() is None
    assert not any('/signing/' in n for n in z.namelist())
print(out.name,out.stat().st_size)
