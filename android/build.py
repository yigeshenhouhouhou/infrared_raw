"""Reproducible local APK build; SDK dependencies and all outputs stay on E:."""
from pathlib import Path
import os
import subprocess
import sys
import zipfile
import json
import shutil
import secrets
import hashlib

sys.stdout.reconfigure(encoding='utf-8',errors='replace')

ROOT=Path(__file__).resolve().parent
WORK=ROOT/'runtime'
PROJECT=ROOT.parent
# Single source of truth for the release name; keep AndroidManifest in step.
VERSION='0.4.0'
TOOLS=Path(os.environ.get('IR_ANDROID_LEGACY_TOOLS',PROJECT.parent/'agent_work'/'sony-a7r2-k80'/'tools'))
JAVA=Path(os.environ.get('IR_ANDROID_JAVA',TOOLS/'jadx'/'jre'/'bin'/'java.exe'))
NDK=Path(os.environ.get('IR_ANDROID_NDK',TOOLS/'android-ndk-r19c'))
os.environ.update(TEMP=str(WORK/'tmp'),TMP=str(WORK/'tmp'),
    JAVA_TOOL_OPTIONS='-Djava.io.tmpdir='+str(WORK/'tmp')+' -Duser.home='+str(WORK/'java-home'),
    PYTHONDONTWRITEBYTECODE='1')
for p in [WORK/'tmp',WORK/'java-home',WORK/'logs',ROOT/'dist',ROOT/'signing',ROOT/'app'/'assets']:
    p.mkdir(parents=True,exist_ok=True)

def run(name,args,cwd=ROOT):
    args=list(map(str,args));print(name,flush=True)
    result=subprocess.run(args,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    (WORK/'logs'/f'{name}.log').write_bytes(result.stdout)
    if result.returncode:
        print(result.stdout.decode('utf-8','replace')[-16000:]);raise RuntimeError(f'{name} failed: {result.returncode}')

def native(abi):
    out=WORK/('native-'+abi)
    jpeg=WORK/('jpeg-'+abi)
    common=['-G','Ninja','-DCMAKE_BUILD_TYPE=Release',
      '-DCMAKE_TOOLCHAIN_FILE='+str(NDK/'build'/'cmake'/'android.toolchain.cmake'),
      '-DANDROID_ABI='+abi,'-DANDROID_PLATFORM=android-26','-DANDROID_STL=c++_static']
    run('configure-jpeg-'+abi,['cmake','-S',ROOT/'vendor'/'libjpeg-turbo-3.2.0','-B',jpeg,*common,
        '-DENABLE_SHARED=OFF','-DENABLE_STATIC=ON','-DWITH_TURBOJPEG=OFF','-DWITH_SIMD=OFF'])
    run('compile-jpeg-'+abi,['cmake','--build',jpeg,'--target','jpeg-static','-j','6'])
    run('configure-'+abi,['cmake','-S',ROOT,'-B',out,'-G','Ninja',
      '-DCMAKE_BUILD_TYPE=Release','-DCMAKE_TOOLCHAIN_FILE='+str(NDK/'build'/'cmake'/'android.toolchain.cmake'),
      '-DANDROID_ABI='+abi,'-DANDROID_PLATFORM=android-26','-DANDROID_STL=c++_static',
      '-DJPEG_BINARY_DIR='+str(jpeg),'-DJPEG_LIBRARY='+str(jpeg/'libjpeg.a')])
    run('compile-'+abi,['cmake','--build',out,'-j','6'])
    return out/'libinfrared.so'

def main():
    assets=ROOT/'app'/'assets'
    sys.path.insert(0,str(PROJECT))
    import raw_backend
    (assets/'sRGB.icc').write_bytes(raw_backend.srgb_icc())
    notice='''红外 RAW @VERSION@ · 离线安卓测试版

本应用在手机上使用 LibRaw 解码，不连接电脑，不申请网络权限。
导入：系统文件选择器。导出：JPG，质量 95，内嵌 sRGB 色彩配置。
相机通道假色，与相机 JPEG / 可见光色彩标定不同。
全尺寸导出最多 3600 万像素；更大的照片请选半尺寸。
预览是缩小的去马赛克结果，细节可能与全尺寸稍有不同。
降噪与锐化为基础滤波，移动版滤波半径与桌面版可能略有差异。
热点补偿为中心亮度调整，无法准确消除带偏色的反光圆环。
特殊四色传感器不支持，实际 RAW 兼容性以 LibRaw 为准。

开源组件：LibRaw 0.22.3（LGPL 2.1 或 CDDL 1.0，本包选择 LGPL 2.1）。
JPEG 解码：libjpeg-turbo 3.2.0（IJG / BSD / zlib，详见许可文件）。
应用源代码及第三方内容的许可详见源码包。构建源码与安装包一并提供。
项目源码包内含对应第三方源码、完整许可证与重建脚本。
实验色彩矩阵改编自 anxix/rt_ir_presets（CC BY-SA 4.0）：
https://github.com/anxix/rt_ir_presets ，已按行归一化，非完整预设复现。
许可全文见应用资源 CC-BY-SA-4.0.txt。
'''.replace('@VERSION@',VERSION)
    (assets/'NOTICE.txt').write_text(notice,'utf-8')
    shutil.copyfile(PROJECT/'research'/'590nm'/'LICENSE.txt',assets/'CC-BY-SA-4.0.txt')
    for name in ['LICENSE.LGPL','LICENSE.CDDL','COPYRIGHT']:
        src=ROOT/'vendor'/'LibRaw-0.22.3'/name
        if src.exists():shutil.copyfile(src,assets/('LibRaw-'+name+'.txt'))
    for name in ['LICENSE.md','README.ijg']:
        src=ROOT/'vendor'/'libjpeg-turbo-3.2.0'/name
        if src.exists():shutil.copyfile(src,assets/('libjpeg-turbo-'+name+'.txt'))
    aapt=ROOT/'tools'/'aapt2.exe'
    compiled=WORK/'resources.zip'
    run('resources',[aapt,'compile','--dir','app/res','-o','runtime/resources.zip'])
    unsigned=WORK/'unsigned.apk'
    run('package',[aapt,'link','-o','runtime/unsigned.apk','--manifest','app/AndroidManifest.xml',
      '-I','tools/android-framework.jar','-A','app/assets','runtime/resources.zip'])
    classes=WORK/'classes';classes.mkdir(exist_ok=True)
    sources=list((ROOT/'app'/'src').rglob('*.java'))
    run('java',[JAVA,'-jar',TOOLS/'ecj-legacy.jar','-encoding','UTF-8','-source','1.8','-target','1.8',
      '-bootclasspath',ROOT/'tools'/'android-35.jar','-d',classes,*sources])
    dex=WORK/'dex';dex.mkdir(exist_ok=True)
    d8=next((ROOT/'tools'/'build-tools').rglob('d8.jar'))
    run('dex',[JAVA,'-cp',d8,'com.android.tools.r8.D8','--release','--min-api','26',
      '--lib',ROOT/'tools'/'android-35.jar','--output',dex,*classes.rglob('*.class')])
    libraries={abi:native(abi) for abi in ['arm64-v8a','x86_64']}
    # The NDK toolchain emits debug information by default. Strip the copies that
    # go into the APK (the build tree keeps its symbols) so the download does not
    # carry several megabytes of .debug_* that no user can use.
    strip=NDK/'toolchains'/'llvm'/'prebuilt'/'windows-x86_64'/'bin'/'llvm-strip.exe'
    with zipfile.ZipFile(unsigned,'a',zipfile.ZIP_DEFLATED) as z:
        z.write(dex/'classes.dex','classes.dex')
        for abi,path in libraries.items():
            staged=WORK/('strip-'+abi+'.so')
            shutil.copyfile(path,staged)
            run('strip-'+abi,[strip,'--strip-all',staged])
            z.write(staged,'lib/'+abi+'/libinfrared.so')
    credentials=ROOT/'signing'/'local.json'
    if not credentials.exists():
        password=secrets.token_urlsafe(24)
        os.environ['IR_SIGN_PASSWORD']=password
        openssl=Path(os.environ.get('IR_ANDROID_OPENSSL',r'E:\Git\usr\bin\openssl.exe'))
        run('create-signing-key',[openssl,'req','-x509','-newkey','rsa:3072','-nodes',
          '-keyout',ROOT/'signing'/'key.pem','-out',ROOT/'signing'/'cert.pem',
          '-days','10000','-subj','/CN=Infrared RAW Local'])
        run('create-keystore',[openssl,'pkcs12','-export','-inkey',ROOT/'signing'/'key.pem',
          '-in',ROOT/'signing'/'cert.pem','-name','infrared','-out',ROOT/'signing'/'infrared.p12',
          '-passout','env:IR_SIGN_PASSWORD'])
        credentials.write_text(json.dumps({'password':password}),'utf-8')
    password=json.loads(credentials.read_text('utf-8'))['password']
    os.environ['IR_SIGN_PASSWORD']=password
    bt=next((ROOT/'tools'/'build-tools').glob('*/zipalign.exe')).parent
    run('align',[bt/'zipalign.exe','-f','-P','16','4','runtime/unsigned.apk','runtime/aligned.apk'])
    apk=ROOT/'dist'/('InfraredRAW-'+VERSION+'-android8-arm64.apk')
    run('sign',[JAVA,'-jar',bt/'lib'/'apksigner.jar','sign',
      '--ks',ROOT/'signing'/'infrared.p12','--ks-key-alias','infrared',
      '--ks-pass','env:IR_SIGN_PASSWORD','--key-pass','env:IR_SIGN_PASSWORD','--out',apk,'runtime/aligned.apk'])
    run('verify-signature',[JAVA,'-jar',bt/'lib'/'apksigner.jar','verify','--verbose','--print-certs',apk])
    digest=hashlib.sha256(apk.read_bytes()).hexdigest()
    (ROOT/'dist'/'SHA256.txt').write_text(digest+'  '+apk.name+'\n','utf-8')
    print('APK:',apk,'SHA256:',digest,flush=True)

if __name__=='__main__':main()
