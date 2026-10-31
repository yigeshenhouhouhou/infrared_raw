# IR Studio Pro (红外 RAW 白平衡与创意调色工坊)

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![LibRaw](https://img.shields.io/badge/Backend-LibRaw%20%2F%20rawpy-brightgreen.svg)](https://letmaik.github.io/rawpy/)
[![License](https://img.shields.io/badge/License-CC%20BY--SA%204.0-orange.svg)](https://creativecommons.org/licenses/by-sa/4.0/)

> 面向改机红外摄影（尤其是 590nm 全波段）的高精度 RAW 白平衡与色彩后期处理系统。采用纯浮点线性物理白平衡管线，突破传统商用修图软件对红外极端色温倍率的硬截断限制，搭载全新现代暗黑工作室界面（Studio UI）。

---

## 🌟 核心特性与亮点

### 1. 极致精度的 32-bit 浮点色彩管线
* **突破色温极限**：支持 R/B 通道倍率在 `0.0001 ~ 10000` 自由设定（G 固定为 1），再也不受 Lightroom/Photoshop 色温滑块尽头的截断困扰。
* **线性域曝光与高光保留**：基于传感器黑/白电平物理归一化，在 Float32 空间完成白平衡、曝光补偿（-8 ~ +8 EV）及软膝对数高光压缩，保留真实高光动态细节。
* **真·红外 590nm 色彩矩阵**：
  * 内置 **公开 590nm 预设色彩矩阵**（基于行归一化标定）；
  * 线性红蓝互换 (Linear R-B)；
  * 相机原生色彩矩阵与经典原始通道；
  * 支持暖色色相、冷色色相、色彩分离及自然饱和独立微调。

### 2. IR Studio Pro 专业暗黑前端交互
* **影室暗黑风格**：减少高亮白光对色彩校准和人眼的视觉干扰，居中沉浸式画廊视口。
* **RGB 三通道重叠直方图**：实时显示 R/G/B 对数动态分布，高光溢出与暗部欠曝一目了然。
* **高效交互与快捷键系统**：
  * `\` 或 `|`：**按住即可快速切换到 RAW 原始未调色对比**，松开立刻切回调整后画面。
  * `W`：快速开启/关闭**区域吸管测白**模式。
  * `[` / `]` 或 `←` / `→`：多张照片胶卷无缝切换。
  * **双击快速归零**：双击任意滑块或参数标签，瞬间重置回初始默认值。
* **预设导入与导出**：一键导出当前调色方案为 `.json` 配置文件，随时跨工程加载。

### 3. 多格式导出支持
* **16-bit 成片 TIFF**：包含完整 sRGB 色彩配置与全部后期调色曲线。
* **32-bit 浮点底片 TIFF**：保留线性相机 RGB 与超白数据（>1.0），供专业科研或 HDR 软件二次处理。
* **快速 JPG 成片**：一键导出 95 质量、4:4:4 色度采样的原图分辨率成片。

---

## 🚀 快速上手

### 1. 环境准备
项目自带基于 `rawpy`、`numpy`、`Pillow` 的高性能环境：
```bash
# 安装环境（首次或更换目录时）
双击运行 "安装环境.bat"
```

### 2. 启动服务
```bash
# 启动本地工作室
双击运行 "启动红外白平衡.bat"
```
服务默认启动于 `http://127.0.0.1:8765/`，自动打开浏览器。所有 RAW 解码和浮点运算纯本地执行，数据安全不上传云端。

---

## ⌨️ 快捷操作指引

| 按键 | 功能说明 |
| :--- | :--- |
| **`\` / `|`** (按住) | 瞬时对比 RAW 原片 (未调色底片) |
| **`W`** | 开启 / 关闭吸管取白 (在画面拖拽中性灰/白区域) |
| **`←` / `→`** 或 **`[` / `]`** | 切换胶卷中的上一张 / 下一张 RAW 照片 |
| **双击滑块 / 标签** | 该项参数立刻复位为默认值 |
| **直接输入数值** | R/B 白平衡框或参数数值可点击直接高亮手动输入精确值 |

---

## 🔬 命令行使用示例

除了 Web 交互界面，工具亦支持 CLI 批处理模式：
```bash
# 1. 查看 RAW 传感器信息与 CFA 通道分布
.venv\Scripts\python.exe -B ir_wb.py info "E:\Photos\test.ARW"

# 2. 指定区域 (x0,y0,x1,y1) 吸取中性灰倍率
.venv\Scripts\python.exe -B ir_wb.py solve "E:\Photos\test.ARW" --region 0.2,0.3,0.5,0.6

# 3. 命令行应用白平衡并导出 16-bit 成片
.venv\Scripts\python.exe -B ir_wb.py apply "E:\Photos\test.ARW" --mul 0.4,1.0,2.3 --out "E:\Photos\result.tif"

# 4. 导出 32-bit 线性浮点底片
.venv\Scripts\python.exe -B ir_wb.py apply "E:\Photos\test.ARW" --mul 0.4,1.0,2.3 --linear --out "E:\Photos\linear.tif"
```

---

## 📄 开源许可与致谢
* 色彩混色算法参考并改编自 [anxix/rt_ir_presets](https://github.com/anxix/rt_ir_presets) (遵循 **CC BY-SA 4.0** 许可协议)。
* RAW 底层解算由 [LibRaw](https://www.libraw.org/) / [rawpy](https://github.com/letmaik/rawpy) 强力驱动。
