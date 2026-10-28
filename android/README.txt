红外 RAW 0.2.1 · 安卓离线测试版

安装包：dist\InfraredRAW-0.2.1-android8-arm64.apk
适用：Android 8.0 及以上、64 位 ARM 手机；安装包也包含 x86_64 构建。
没有 32 位 ARM 版本。安装包自签名，可直接侧载安装，不是应用商店发行版。

色彩与曝光更新详见 RELEASE-0.2.1.txt。新版默认采用可调的 590 nm 蓝金风景假色方案。

使用
  1. 将 APK 传到手机，点击安装。按系统提示允许该来源安装应用。
  2. 点“导入 RAW”，从手机文件或 USB 读卡器选择文件。
  3. 用自动白平衡作为起点，或开启吸管并点击中性区域；可手动输入 R/B 倍率。
  4. 调整中间调、曝光、高光、黑场以及假色互换等参数。
  5. 点“导出 JPG”，在系统文件选择器中指定保存位置。
  6. 原图像素超过 3600 万时，请选择“半尺寸”；宽高各减半。

功能
  原生 LibRaw 0.22.3，在手机上解码，不依赖电脑、服务器或网络。
  没有申请网络权限。导入和导出由系统文件选择器授权，无需读取整个相册。
  处理未缩放的线性相机 RGB，在浮点中白平衡和曝光，使用与桌面版相同的
  中间调、高光肩部、黑场、sRGB 传递函数和通道互换 / 分色公式。
  可调降噪、锐化、中心热点补偿。细节滤波顺序与半径为移动端简化实现，
  效果可能与桌面版略有不同，不属于复杂 AI 降噪。
  JPG 质量 95，嵌入 sRGB ICC。颜色采用红外相机通道假色。
  自动保留最近一张原片副本及参数，下次打开时恢复；导入新片成功后删除旧副本。

当前边界
  这是独立移植的第一版，而非将电脑版网页套壳。
  当前只有 JPG 导出；TIFF、32-bit 浮点底片、批量队列及参数 JSON 导出尚未移植。
  自动白平衡从缩小的去马赛克图统计，和桌面直接统计 CFA 的结果可能稍有差别。
  首版支持三色 RGB Bayer / X-Trans；特殊四色传感器不支持。
  RAW 支持范围取决于 LibRaw；超大文件（超过 512 MB）和超过 1 亿像素的输入被拒绝。
  全尺寸导出最多 3600 万像素，内存不足时请用半尺寸，并在处理期间保持应用打开。
  半尺寸也需要读取整个 RAW，不能保证每台低内存手机都能处理高像素文件。
  真实传感器饱和的细节不能恢复；中心热点补偿不能准确消除偏色反光圆环。

已验证
  安卓 ARM64、x86_64 原生库编译成功；Java 界面和 DEX 编译成功。
  APK v2 / v3 签名、压缩包完整性、资源及启动入口、16 KB ELF 段对齐通过。
  同一份 C++ 核心在 Windows 上解码真实 Sony NEX-5 ARW：
    预览 1152×768、半尺寸 2304×1536、全尺寸 4608×3072。
  合成 DNG 解码及 180 个浮点样本与桌面算法对照通过，最大绝对差约 8.3e-7。
  尚未在安卓真机或模拟器上运行验证；手机端文件选择、保存、内存和机型兼容性
  仍需首次安装测试。本机算法验证不能替代安卓运行测试。
  详细记录：dist\validation.json 与 runtime\logs\verify-signature.log。

构建 / 开源
  app\src：原生安卓 Java 界面；native：JNI 桥及可移植图像处理核心。
  vendor\LibRaw-0.22.3：对应完整 LibRaw 源码，选择 LGPL 2.1。
  vendor\libjpeg-turbo-3.2.0：对应 JPEG 解码源码，保留原有许可证。
  应用自行编写的代码采用 MIT；完整许可文件位于 vendor 及 app\assets。
  随附 source.zip 排除私有签名密钥、照片、缓存、运行输出和开发工具二进制。
  可修改原生库 / 应用源码，并用自己的密钥重建安装包。
  签名密钥保存在本机 signing 中，不在源码下载包中。后续官方更新需保留该目录。

  build.py 是当前 Windows 工作区可直接复现的构建脚本：
    ..\.venv\Scripts\python.exe -B build.py
  需要 CMake / Ninja、NDK r19c、Java 运行时（本机为 25）、ECJ 3.12.1、
  Android SDK 35 的 android.jar、Build Tools 35.0.1 的 D8 / apksigner / zipalign、
  aapt2 与 Android 资源框架、OpenSSL，以及桌面目录的 raw_backend.py / Python 依赖。
  tools 文件夹中的下载缓存不会随 source.zip 分发，请在重建前自行准备工具。
  第一次构建会生成个人签名密钥，签名密码只保存在本机 signing/local.json。
  可用环境变量 IR_ANDROID_LEGACY_TOOLS、IR_ANDROID_NDK、IR_ANDROID_JAVA、
  IR_ANDROID_OPENSSL 覆盖本机工具位置。工具目录中的 ecj-legacy.jar 为 ECJ 3.12.1。
  tools/android-35.jar 是 Google SDK 平台 jar；tools/android-framework.jar 为 aapt2
  可用的框架资源包；tools/build-tools/<版本目录> 包含官方构建工具。
  所有构建缓存、临时文件和开发产物默认留在 E 盘本项目内。

  下载来源：
    https://www.libraw.org/download
    https://github.com/libjpeg-turbo/libjpeg-turbo
    https://dl.google.com/android/repository/repository2-1.xml
