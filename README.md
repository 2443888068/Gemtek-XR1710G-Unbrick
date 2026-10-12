# Gemtek XR1710G / Airoha AN7581 — Unbrick & Factory Data Recovery

[![SoC](https://img.shields.io/badge/SoC-Airoha%20AN7581-1f6feb)](#)
[![Flash](https://img.shields.io/badge/NAND-512%20MiB%20SPI-7c3aed)](#)
[![Tool](https://img.shields.io/badge/tool-single%20HTML%20file-2ea043)](#)
[![License](https://img.shields.io/badge/license-MIT-2ea043)](#license)

Bootloader 恢复 + 出厂数据（DSD / uenv）重建工具，**不需要任何原厂备份**。

适用于 Gemtek 代工的 Brightspeed **XR1710G**（Airoha AN7581，四核 A53，512 MiB SPI NAND）。
同一 SoC 的 XG2010G 等机型分区布局相同，方法可通用。

> ⚠️ 刷写有风险。本项目由社区逆向整理，**非官方**，请先完整阅读再动手。

---

## 这个项目解决什么

XR1710G 常见两种砖：

| 情况 | 现象 | 本项目的解法 |
|---|---|---|
| **bootloader 坏了** | 完全不开机、串口无输出 | X 模式两阶段镜像（已含 BL1/BL2/BL31/U-Boot + 全套证书） |
| **BL31 坏了** | 开机设备报错: ERROR:BL2: Failed to load image id 3 (-80) |
| **dsd 分区炸了** | 能开机但 MAC 全 0 / 序列号丢失 / WiFi 起不来 | 单文件 HTML 生成器，含同型号校准模板 |

两种都不需要你事先备份过。

---

## 快速开始

### 情况一：bootloader 坏了（X 模式两阶段） 或 ERROR:BL2: Failed to load image id 3 (-80)

```
1. 断电，按住 RESET 上电，串口 115200
2. 按 X（或 x），终端出现 CCCCC
3. XMODEM 发送 bin/stage1_preloader.bin          → 设备自动重启
4. 再次断电，按住 RESET 上电
5. 提示 "Press x to load BL31 + U-Boot FIP" 时按 x
6. XMODEM 发送 bin/stage2_bl31_uboot.bin         → 设备自动重启
```

完成后 bootloader 区段（flash `0x0-0x200000`）恢复完好，可以进 U-Boot 命令行。

### 情况二：dsd 炸了（生成出厂数据）

双击打开 以下链接 直接在浏览器里打开（无需下载）：

- [DSD-Generator-CAL.html](https://2443888068.github.io/Gemtek-XR1710G-Unbrick/docs/DSD-Generator-CAL.html) — 推荐
- [DSD-Generator-NoBackup.html](https://2443888068.github.io/Gemtek-XR1710G-Unbrick/docs/DSD-Generator-NoBackup.html)
- [DSD-Generator-Full.html](https://2443888068.github.io/Gemtek-XR1710G-Unbrick/docs/DSD-Generator-Full.html)




1. 默认什么都不用改，直接点 **生成并下载**
2. 会下载 `dsd_XXXX.bin` 和 `uenv_XXXX.bin`
3. 传到设备（TFTP 或串口）后写入：

```bash
# DSD -> flash 0x400000
flash erase 0x400000 0x8000
flash write 0x400000 0x8000 0x81800000

# uenv -> flash 0x200000
flash erase 0x200000 0x400
flash write 0x200000 0x400 0x81800000

reset
```

> 已在 ImmortalWrt 下：`mtd write dsd_XXXX.bin /dev/mtdX`（先 `cat /proc/mtd` 确认分区号）

---

## Flash 布局

原厂（`ECNT> mtd`）与 ImmortalWrt 的 **三个关键分区偏移完全相同**：

| 分区 | 起始 | 大小 | 内容 |
|---|---|---|---|
| `bootloader` | `0x000000` | 2 MiB | BL1 / BL2 / BL31 / U-Boot + 证书 |
| `uenv` | `0x200000` | 2 MiB | 环境变量（有效数据仅 1 KiB） |
| `dsd` | `0x400000` | 2 MiB | 出厂数据：MAC / 序列号 / WiFi |
| `tclinux` | `0x600000` | 64 MiB | 主系统镜像 |
| `tclinux_slave` | `0x04600000` | 64 MiB | 备用系统（原厂 A/B 双备份） |
| `system` | `0x08600000` | 312 MiB | 数据分区 |

**写入禁区**：`0x0-0x200000` 之外就是 uenv，超过会踩坏；`0x1BE00000` 之后是坏块/预留区。

详见 [`docs/FLASH-LAYOUT.md`](docs/FLASH-LAYOUT.md)。

---

## 工具

| 工具 | 用途 |
|---|---|
| `tools/DSD-Generator-CAL.html` | **推荐**。内嵌同型号校准模板，无需备份，生成即完整 |
| `tools/DSD-Generator-NoBackup.html` | 最小版本，不含校准段 |
| `tools/DSD-Generator-Full.html` | 完整版。可「载入现有 DSD / uenv / mtd0 备份」当模板，适合有备份或同型号样机时 |
| `tools/dsd_tool.py` | 命令行版，适合批量/脚本化 |

三个 HTML 都是**单文件**，双击即用，不联网、不上传任何数据。

命令行示例：

```bash
# 从备份里抽回 DSD / uenv
python tools/dsd_tool.py restore --mtd0 mtd0_vendor.bin --out ./out

# 批量生成 5 台（MAC 自动连号）
python tools/dsd_tool.py gen --out ./out --count 5 --uenv-template ./out/uenv_restored.bin

# 校验
python tools/dsd_tool.py verify --dsd ./out/dsd_XXXX.bin
```

---

## 能恢复什么 / 不能恢复什么

| 项目 | 状态 |
|---|---|
| Bootloader（BL1/BL2/BL31/U-Boot） | ✅ X 模式两阶段镜像已包含 |
| 网口 MAC | ✅ 按 OUI 生成，单播 + 全局唯一 + wan/lan 连号 |
| 序列号 | ✅ 生成（格式 `XR1710G` + 10 位数字） |
| SSID / WiFi 密码 / WPS PIN | ✅ 生成 |
| uenv 环境变量 | ✅ 内置默认集合 |
| **WiFi 射频校准** | ⚠️ 提供同型号模板，**通用性需实测一次**（见下） |
| DSD `0x1000` 加密段 | ❌ 原厂加密，无口令，留空 |

### 关于 WiFi 校准

`bin/dsd_template_cal_only.bin` 是从一台正常 XR1710G 提取、**已脱敏**的模板
（无 MAC、无序列号、无密码），保留了二进制校准段。

同一型号硬件一致，校准**大概率通用**，但若原厂在校准里做了设备绑定则可能失效。
第一次给别的机器用时建议花 2 分钟确认：

```bash
iw dev              # WiFi 接口 MAC 是否正确
iw phy              # 发射功率是否正常（不该是 0 或异常小）
iw dev wlan0 scan   # 能否扫到 AP
```

正常 → 可以放心用；报错或扫不到 → 该校准绑定了设备，只能走原厂产线工具。

---

## 逆向记录（省得别人再踩一遍）

这些是摸索过程中确认下来的事实，写出来给后来人省时间：

**1. mtd0 不是平铺的 BL1/BL2/U-Boot，而是一个 FIP 容器**

```
mtd0 0x000000  厂商前缀（16 条 ARM NOP + BL1 引导代码）
mtd0 0x000800  FIP 头  magic 0xAA640001 / serial 0x12345678（小端）
mtd0 0x000810  镜像表 slot0..slot8 + 终止项
mtd0 0x004420  BL2 记录 A   LZMA → 94,824 B
mtd0 0x00FE08  BL2 记录 B   LZMA → 95,049 B
mtd0 0x01BC00  签名块 + Tb-FW 证书（签 BL2）
mtd0 0x01F800  BL31 记录    LZMA → 95,787 B
mtd0 0x029C00  U-Boot 记录  LZMA → 488,328 B
```

**2. 证书的覆盖范围可以逐字节验证**

内容证书里的 SHA-512 正好等于对应 LZMA 记录的摘要：

```
证书 @0x1E800 → sha512(0x1F800..0x29938)  一致 ✅  (BL31)
证书 @0x1F000 → sha512(0x29C00..0x53473)  一致 ✅  (U-Boot)
```

**3. 别在代码字面量池里找 magic**

`0xAA640001` 在 `0x334` 处也出现一次 —— 那是 BL1 代码里的比较常量，不是 FIP 头。
按它定位会整体错位 0x400。

**4. preloader 的 FIP 起点是 `0x800`，不是 `0x0`**

X 模式第一阶段发的文件应从 `0x800` 开始（长度 126,972 字节），
第一个字节就是 `AA640001`。带上前面 0x800 前缀会静默失败。

**5. DSD `0x1000` 段是加密数据，不是签名**

```
PKCS#7 ContentInfo
 └─ SEQUENCE
    ├─ encryptedData ×3
    │    PBES2 → PBKDF2(salt 8B + HMAC-SHA256) → AES-128-GCM
```

三块 salt/IV 各不相同。它不参与校验、不影响开机。
详见 [`docs/DSD-ENCRYPTED-BLOB.md`](docs/DSD-ENCRYPTED-BLOB.md)。

---

## 校验

```bash
sha256sum -c SHA256SUMS
```

已发布文件的 SHA-256：

```
ce6fd734facaa4936eb146af311c62b95b17aba2b23181b3660d9271b6619aea  bin/dsd_template_cal_only.bin
a0d896b938a9313ee4d2afc5a3e0ae5566bb684e1992232835a7c8ed3d34dc83  bin/stage1_preloader.bin
f82bfcdd6a43d05591b95a2494b0835b96a5355112a1545416c5d7fdf2b49c2f  bin/stage2_bl31_uboot.bin
```

---

## 隐私说明

- `bin/dsd_template_cal_only.bin` **已脱敏**：文本段（MAC/序列号/WiFi 密码）与
  加密段全部覆写为 `0xFF`，仅保留二进制校准段与结构标记
- 三个 HTML 工具**完全离线**运行，不发起任何网络请求，不收集数据
- 本仓库**不包含**任何完整的分区备份（那种文件含设备唯一信息，请勿上传分享）

## 免责声明

固件镜像来自对零售设备的合法提取，版权归原厂及其上游项目
（ARM Trusted Firmware、U-Boot 等）所有，此处仅用于设备维修与互操作性研究。
按 BSD-3-Clause / GPL-2.0+ 等各自许可使用。

刷写操作有变砖风险，请自行评估。作者不对任何设备损坏负责。

## License

工具与文档：MIT
`bin/` 下的固件镜像：遵循各自上游许可
