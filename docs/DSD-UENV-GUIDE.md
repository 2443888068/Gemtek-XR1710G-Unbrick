XR1710G — DSD / uenv 分区炸了怎么办（含 MAC 生成工具）
========================================================================

一、先搞清楚这两个分区里存的是什么
------------------------------------------------------------------------
  DSD  (flash 0x400000, 你的 mtd0 里 0x400000 起)：
      文本键值段 0x000-0x158，出厂唯一数据
        wan_mac / lan_mac      网口 MAC（原机 00:58:28:XX:XX:XX / :60，连号）
        serial_number          整机序列号  XR1710G0000000000
        manufacturer / fan_id  Gemtek / sunon_XR1710G
        wifi_ssid / wifi_passphrase / wifi_guest_passphrase / wifi_wps_pin
        admin_username / admin_password
      二进制段 0x158-0x6C66
        WiFi 校准数据 + 厂商数据（设备唯一，无法凭空生成）

  uenv (flash 0x200000)：
      4 字节头 + 51 个环境变量
        ethaddr / ipaddr / bootcmd / uboot_filename / soc / invaild_env ...
      注意：uenv 里的 ethaddr 原机是占位值 00:AA:BB:01:23:40，
            真正生效的 MAC 在 DSD 里。

  bootloader 区段 0x80000 处还有一份工厂默认 uenv（同格式），
  它不属于上面两个分区，刷 mtd0 不会动到它。

二、炸了会怎样
------------------------------------------------------------------------
  DSD 丢  -> MAC 变随机/全 0，序列号丢失，WiFi 校准没了（无线可能不起或功率异常）
  uenv 丢 -> 环境变量回默认，bootcmd/ipaddr 等丢失，一般还能进 U-Boot

三、恢复优先级
------------------------------------------------------------------------
  1) 有 mtd0 / dsd 备份  ->  直接写回，最安全（用你自己机器的原始值）
  2) 没有备份，但记得 MAC/序列号  ->  用工具生成，把值填回去
  3) 什么都没有  ->  只能新生成 MAC/序列号；WiFi 校准段无法恢复

四、工具：xr1710g_dsd_tool.py
------------------------------------------------------------------------
  需要 Python 3（无第三方依赖）。

  (1) 从备份恢复
      python xr1710g_dsd_tool.py restore --mtd0 mtd0_vendor.bin --out ./out
        产出 dsd_restored.bin / uenv_restored.bin / uenv_bootloader_copy.bin
        并把里面的 MAC、序列号、关键环境变量打印出来核对

  (2) 生成新的 DSD / uenv（MAC 自动生成）
      python xr1710g_dsd_tool.py gen --out ./out --count 3 \
             --uenv-template ./out/uenv_restored.bin
        · MAC 前缀默认沿用本机 OUI 00:58:28
        · wan/lan 自动连号，多台设备依次占用地址（每台 2 个）
        · 自动生成序列号、SSID、WPA/WPS 口令
        · --mac 00:58:28:AA:BB:CC   指定起始 MAC
        · --serial XR1710G0000000000 指定序列号
        · --dsd-size 0x8000         输出文件大小（默认 32 KiB，分区是 2 MiB，
                                    只写前面这段即可）

  (3) 校验
      python xr1710g_dsd_tool.py verify --dsd ./out/dsd_xxxx.bin

五、写回命令
------------------------------------------------------------------------
  U-Boot 下（先把文件传到 0x81800000）：
      # DSD（32 KiB）
      flash erase 0x400000 0x8000
      flash write 0x400000 0x8000 0x81800000
      # uenv（1 KiB）
      flash erase 0x200000 0x400
      flash write 0x200000 0x400 0x81800000

  ImmortalWrt 下：
      mtd write dsd_xxxx.bin /dev/mtdX      # X = dsd 分区号，先 cat /proc/mtd 确认
      mtd write uenv_xxxx.bin /dev/mtdY

六、注意
------------------------------------------------------------------------
  * WiFi 校准二进制段是设备唯一的，工具生成的 DSD 里该段为空（0xFF）。
    能用，但无线性能不保证；要恢复原状必须有原机 DSD 备份。
  * uenv 头部 4 字节是厂商自定义标记（实测不是标准 crc32）。
    用 --uenv-template 传一份备份，工具会原样沿用它的头。
  * 写 flash 前先确认偏移：dsd = 0x400000，uenv = 0x200000，
    不要越界写到 ubi（0x600000 起）。
