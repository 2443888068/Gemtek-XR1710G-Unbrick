#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XR1710G (Airoha AN7581 / Gemtek) — DSD & uenv 分区恢复 / 生成工具

用途：
  1) dsd restore   —— 从 mtd0 备份里抽回原来的 DSD / uenv（最安全）
  2) dsd gen       —— 当原厂 DSD 彻底丢失时，生成一份可用的 DSD / uenv
                      （自动生成 MAC、序列号、WiFi 口令等）

分区在整片 flash 中的位置：
    bootloader : 0x000000  2 MiB   （内含 BL1/BL2/BL31/U-Boot + 工厂默认 uenv@0x80000）
    uenv       : 0x200000  2 MiB   （运行时环境变量）
    dsd        : 0x400000  2 MiB   （出厂数据: MAC / 序列号 / WiFi 校准）

用法：
  python xr1710g_dsd_tool.py restore --mtd0 mtd0_vendor.bin --out ./out
  python xr1710g_dsd_tool.py gen --out ./out --serial XR1710G0000000000
  python xr1710g_dsd_tool.py gen --out ./out --mac 00:58:28:AA:BB:CC --count 4
  python xr1710g_dsd_tool.py verify --dsd ./out/dsd_new.bin
"""
import argparse
import hashlib
import os
import random
import re
import struct
import sys
import zlib

# ---------------- flash 布局 ----------------
FLASH_UENV_OFF = 0x200000
FLASH_DSD_OFF = 0x400000
DSD_MAX = 0x8000          # DSD 有效数据实测不超过 0x6C66，留足余量
UENV_BLOCK = 0x400        # 环境块单元

GEMTEK_OUI = '00:58:28'   # 本机实测 OUI（Gemtek）

# DSD 二进制段的历史标记（原机数据里出现两次，生成时沿用）
DSD_TAIL_MARK = bytes.fromhex('cfd4dbe6edf70100')


def log(msg):
    print(msg, flush=True)


def mac_from_str(s, allow_prefix=False):
    """接受 6 段完整 MAC，或（allow_prefix=True 时）3 段 OUI 前缀"""
    parts = re.split(r'[:\-]', s.strip())
    want = (3, 6) if allow_prefix else (6,)
    if len(parts) not in want:
        raise ValueError('MAC 格式错误: %s' % s)
    return bytes(int(p, 16) for p in parts)


def mac_to_str(b):
    return ':'.join('%02X' % x for x in b)


def gen_mac(oui=GEMTEK_OUI, rnd=None):
    """生成单播、全局唯一（U/L=0）的 MAC：OUI 固定，后 3 字节随机"""
    r = rnd or random.SystemRandom()
    ob = mac_from_str(oui, allow_prefix=True)
    if len(ob) < 6:
        ob = ob + bytes(r.randrange(256) for _ in range(6 - len(ob)))
    b = bytearray(ob[:6])
    b[0] &= 0xFE          # 清 I/G 位 -> 单播
    b[0] &= 0xFD          # 清 U/L 位 -> 全局唯一（沿用厂商 OUI 时必须如此）
    return bytes(b)


def gen_pair(base=None, oui=GEMTEK_OUI):
    """生成 WAN / LAN 一对连续 MAC（原机就是连号：...:5F / ...:60）"""
    if base is None:
        b = bytearray(gen_mac(oui))
        b[5] &= 0xFE
        base = bytes(b)
    wan = bytearray(base)
    lan = bytearray(base)
    lan[5] = (lan[5] + 1) & 0xFF
    if lan[5] == 0:
        lan[4] = (lan[4] + 1) & 0xFF
    return bytes(wan), bytes(lan)


def gen_serial(prefix='XR1710G', rnd=None):
    r = rnd or random.SystemRandom()
    return '%s%s' % (prefix, ''.join(str(r.randrange(10)) for _ in range(10)))


def gen_wifi_key(n=13, rnd=None):
    """原机口令形如 ExamplePass123：固定前缀 + 小写字母数字"""
    r = rnd or random.SystemRandom()
    alphabet = 'abcdefghijkmnpqrstuvwxyz23456789'
    return 'Bright' + ''.join(r.choice(alphabet) for _ in range(n))


# ---------------- DSD ----------------
DSD_ORDER = ['admin_password', 'admin_username', 'wan_mac', 'lan_mac', 'serial_number',
             'manufacturer', 'fan_id', 'wifi_guest_passphrase', 'wifi_passphrase',
             'wifi_ssid', 'wifi_wps_pin']


def build_dsd(kv, payload=None, total_len=DSD_MAX):
    """
    组装 DSD 分区：
      文本键值段（'\n' 结尾）+ 0xFF 填充 + 二进制段 + 尾部标记
    返回 total_len 字节；若 payload 为 None 则只填 0xFF/0x00 占位。
    """
    lines = []
    for k in DSD_ORDER:
        if k in kv:
            lines.append('%s=%s' % (k, kv[k]))
    for k, v in kv.items():
        if k not in DSD_ORDER:
            lines.append('%s=%s' % (k, v))
    text = ('\n'.join(lines) + '\n').encode('utf-8')

    buf = bytearray(b'\xff' * total_len)
    buf[0:len(text)] = text
    off = 0x1000
    if payload:
        buf[off:off + len(payload)] = payload
        off += len(payload)
    # 尾部标记（原机在数据末尾出现两次 12 字节标记）
    mark = DSD_TAIL_MARK
    tail = off + 0x10
    if tail + len(mark) * 2 <= total_len:
        buf[tail:tail + len(mark)] = mark
        buf[tail + 0x10:tail + 0x10 + len(mark)] = mark
    return bytes(buf[:total_len]), len(text)


def parse_dsd(data):
    """解析 DSD 的文本键值段"""
    kv = {}
    end = 0
    for m in re.finditer(rb'([A-Za-z0-9_]+)=([^\x00\n]*)\n', data[:0x400]):
        kv[m.group(1).decode()] = m.group(2).decode('utf-8', 'replace')
        end = m.end()
    return kv, end


# ---------------- uenv ----------------
def parse_env(data):
    """解析 uenv 块：前 4 字节头 + NUL 分隔的 key=value"""
    body = data[4:]
    end = body.find(b'\x00\x00')
    if end < 0:
        end = len(body.rstrip(b'\x00'))
    raw = body[:end]
    kv = []
    for item in raw.split(b'\x00'):
        if b'=' in item:
            k, _, v = item.partition(b'=')
            kv.append((k.decode('latin1'), v.decode('latin1')))
    return kv


def build_env(kv, header=None):
    """
    组装 uenv 块。头部 4 字节为厂商自定义校验/标记：
    实测不是标准 crc32，因此默认原样沿用备份里的头；没有备份时用 0。
    """
    raw = b'\x00'.join(('%s=%s' % (k, v)).encode('latin1') for k, v in kv) + b'\x00'
    blk = bytearray(b'\x00' * UENV_BLOCK)
    hdr = header if header is not None else b'\x00\x00\x00\x00'
    blk[0:4] = hdr
    if 4 + len(raw) > UENV_BLOCK:
        raise ValueError('环境变量超出 0x400 块大小 (%d)' % (4 + len(raw)))
    blk[4:4 + len(raw)] = raw
    return bytes(blk)


# ---------------- 子命令 ----------------
def cmd_restore(args):
    d = open(args.mtd0, 'rb').read()
    os.makedirs(args.out, exist_ok=True)

    dsd = d[FLASH_DSD_OFF:FLASH_DSD_OFF + DSD_MAX]
    # 裁掉尾部空白
    nz = max(i for i, b in enumerate(dsd) if b not in (0x00, 0xFF)) + 1
    dsd = dsd[:max(nz, 0x1000)]

    env = d[FLASH_UENV_OFF:FLASH_UENV_OFF + UENV_BLOCK]
    env_boot = d[0x80000:0x80000 + UENV_BLOCK]

    for name, data in (('dsd_restored.bin', dsd),
                       ('uenv_restored.bin', env),
                       ('uenv_bootloader_copy.bin', env_boot)):
        p = os.path.join(args.out, name)
        open(p, 'wb').write(data)
        log('  %-28s %6d B  sha256=%s' % (name, len(data), hashlib.sha256(data).hexdigest()[:24]))

    kv, _ = parse_dsd(dsd)
    log('')
    log('  DSD 键值:')
    for k, v in kv.items():
        log('    %s=%s' % (k, v))
    ekv = parse_env(env)
    log('')
    log('  uenv 变量 (%d 项)，关键项:' % len(ekv))
    for k, v in ekv:
        if k in ('ethaddr', 'ipaddr', 'bootcmd', 'invaild_env', 'uboot_filename', 'soc', 'product_name'):
            log('    %s=%s' % (k, v))
    log('')
    log('  两个分区的 MAC:')
    log('    DSD  wan_mac=%s  lan_mac=%s' % (kv.get('wan_mac'), kv.get('lan_mac')))
    em = dict(ekv)
    log('    uenv ethaddr=%s' % em.get('ethaddr'))
    return 0


def cmd_gen(args):
    os.makedirs(args.out, exist_ok=True)
    rnd = random.SystemRandom()

    # 生成 count 台设备的 MAC 序列（连号）
    if args.mac:
        base = mac_from_str(args.mac)
    else:
        base = gen_mac(args.oui)
        base = base[:5] + bytes([base[5] & 0xFE])
    log('OUI: %s   起始 MAC: %s' % (args.oui, mac_to_str(base)))
    log('')

    made = []
    for i in range(args.count):
        wan = bytearray(base)
        lan = bytearray(base)
        # 每台设备占 2 个地址
        add = i * 2
        v = int.from_bytes(wan, 'big') + add
        wan = v.to_bytes(6, 'big')
        lan = (v + 1).to_bytes(6, 'big')

        serial = args.serial if (args.serial and args.count == 1) else gen_serial(rnd=rnd)
        kv = {
            'admin_password': args.admin_password,
            'admin_username': args.admin_user,
            'wan_mac': mac_to_str(wan),
            'lan_mac': mac_to_str(lan),
            'serial_number': serial,
            'manufacturer': 'Gemtek',
            'fan_id': 'sunon_XR1710G',
            'wifi_guest_passphrase': gen_wifi_key(11, rnd),
            'wifi_passphrase': gen_wifi_key(11, rnd),
            'wifi_ssid': args.ssid or ('Brightspeed_%s' % ''.join(
                rnd.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(6))),
            'wifi_wps_pin': '%08d' % rnd.randrange(10 ** 8),
        }
        dsd, tlen = build_dsd(kv, payload=None, total_len=args.dsd_size)
        name = 'dsd_%s.bin' % mac_to_str(lan).replace(':', '')
        open(os.path.join(args.out, name), 'wb').write(dsd)
        made.append((name, kv, len(dsd)))
        log('%s  (DSD %d B, 文本段 %d B)' % (name, len(dsd), tlen))
        log('    wan_mac=%s  lan_mac=%s' % (kv['wan_mac'], kv['lan_mac']))
        log('    serial =%s' % kv['serial_number'])
        log('    ssid=%s  wpa=%s  guest=%s  wps=%s' % (
            kv['wifi_ssid'], kv['wifi_passphrase'], kv['wifi_guest_passphrase'], kv['wifi_wps_pin']))

    # uenv：优先沿用备份里的模板
    tmpl = None
    if args.uenv_template and os.path.exists(args.uenv_template):
        tmpl = open(args.uenv_template, 'rb').read()
    kv_env = []
    if tmpl:
        kv_env = parse_env(tmpl)
        log('')
        log('uenv 模板: %s (%d 项)' % (args.uenv_template, len(kv_env)))
    else:
        kv_env = [('baudrate', '115200'), ('bootcmd', 'flash read 0x600000 0x100000 $loadaddr; bootm'),
                  ('bootdelay', '4'), ('ipaddr', '192.168.1.1'), ('serverip', '192.168.1.10'),
                  ('stdin', 'serial'), ('stdout', 'serial'), ('stderr', 'serial'),
                  ('soc', 'an7581'), ('vendor', 'ecnt')]
        log('')
        log('uenv 模板: 内置最小集合')
    for i, (name, kv, _) in enumerate(made):
        env = []
        for k, v in kv_env:
            if k == 'ethaddr':
                continue
            env.append((k, v))
        env.append(('ethaddr', kv['lan_mac']))
        data = build_env(env, header=(tmpl[:4] if tmpl else None))
        fn = name.replace('dsd_', 'uenv_')
        open(os.path.join(args.out, fn), 'wb').write(data)
        log('  %s  ethaddr=%s' % (fn, kv['lan_mac']))
    log('')
    log('提示: DSD 里的 WiFi 校准二进制段无法凭空生成；')
    log('      若原机 DSD 已丢失，无线可能需要重新做校准/用原厂工具补回。')
    return 0


def cmd_verify(args):
    d = open(args.dsd, 'rb').read()
    kv, end = parse_dsd(d)
    log('文件: %s  (%d B)' % (args.dsd, len(d)))
    log('文本段结束于 0x%X' % end)
    log('键值:')
    for k, v in kv.items():
        log('  %-24s = %s' % (k, v))
    ok = True
    for k in ('wan_mac', 'lan_mac'):
        v = kv.get(k)
        if not v or not re.fullmatch(r'([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', v):
            log('  !! %s 缺失或格式错误' % k)
            ok = False
    if ok:
        w = mac_from_str(kv['wan_mac'])
        l = mac_from_str(kv['lan_mac'])
        if int.from_bytes(l, 'big') != int.from_bytes(w, 'big') + 1:
            log('  ! 提示: wan/lan 不是连号（原机是连号，非必须）')
        if w[0] & 1:
            log('  !! wan_mac 是组播地址，非法')
            ok = False
    log('结果: %s' % ('通过' if ok else '有问题'))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description='XR1710G DSD / uenv 恢复与生成工具')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('restore', help='从 mtd0 备份恢复 DSD / uenv')
    p.add_argument('--mtd0', required=True, help='mtd0 备份文件（6 MiB 或整片 flash 均可，脚本按 0x400000 取）')
    p.add_argument('--out', default='./out')
    p.set_defaults(func=cmd_restore)

    p = sub.add_parser('gen', help='生成新的 DSD / uenv（MAC 自动生成）')
    p.add_argument('--out', default='./out')
    p.add_argument('--count', type=int, default=1, help='生成几台设备')
    p.add_argument('--oui', default=GEMTEK_OUI, help='MAC 前缀，默认本机 Gemtek OUI')
    p.add_argument('--mac', help='指定起始 MAC，例如 00:58:28:AA:BB:CC')
    p.add_argument('--serial', help='指定序列号（count=1 时生效）')
    p.add_argument('--ssid')
    p.add_argument('--admin-user', default='admin')
    p.add_argument('--admin-password', default='admin')
    p.add_argument('--dsd-size', type=lambda x: int(x, 0), default=DSD_MAX)
    p.add_argument('--uenv-template', help='沿用某份 uenv 备份里的变量集合与头部')
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser('verify', help='校验生成的 DSD')
    p.add_argument('--dsd', required=True)
    p.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
