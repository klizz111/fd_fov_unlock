#!/usr/bin/env python3
"""
memdump.py — 进程外读游戏内存（ReadProcessMemory），不注入、不碰 UObject 反射。

为什么用它：UE4SS 在这套 UE 5.7.4 exe 上任何 FProperty 访问都会崩，
但 UObject 指针是好的。所以地址来自 UE4SS（F6 manifest），
属性值全部在进程外读。

用法：
  # 列出进程
  python memdump.py ps

  # 转储一段内存（offset / qword / float / int 混排）
  python memdump.py dump --pid 1234 --addr 0x1A2B3C4D0 --size 0x800

  # 只挑出「像 float」的槽位（用于找 FOV / 高度 / 偏移量）
  python memdump.py floats --pid 1234 --addr 0x1A2B3C4D0 --size 0x600

  # 在区间内按数值搜索
  python memdump.py find --pid 1234 --addr 0x1A2B3C4D0 --size 0x2000 --value 90.0 --tol 0.01

  # 多个对象一次读
  python memdump.py dump --pid 1234 --label Camera:0x1A2B3C4D0:0x800 --label SpringArm:0x1A2B3D4E0:0x600
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READWRITE = PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION


def list_processes():
    TH32CS_SNAPPROCESS = 0x00000002
    class PE32(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        name = e.szExeFile.decode("mbcs", "ignore")
        if "paralogue" in name.lower():
            print(f"{e.th32ProcessID}\t{name}")
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)


def open_proc(pid, write=False):
    access = PROCESS_VM_READWRITE if write else (PROCESS_QUERY_INFORMATION | PROCESS_VM_READ)
    h = k32.OpenProcess(access, False, pid)
    if not h:
        raise SystemExit(f"OpenProcess({pid}) failed, err={ctypes.get_last_error()}")
    return h


def read_mem(h, addr, size):
    buf = (ctypes.c_char * size)()
    n = ctypes.c_size_t(0)
    ok = k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
    if not ok:
        err = ctypes.get_last_error()
        raise SystemExit(f"ReadProcessMemory(0x{addr:X}, {size}) failed, err={err}")
    return bytes(buf[: n.value])


def read_mem_safe(h, addr, size):
    """按页读，跨到未提交页时返回已读到的一部分并标注空洞（err=299 很常见）。"""
    data = bytearray(size)
    got = bytearray(size)          # 1 = 真的读到了
    off = 0
    while off < size:
        page_end = ((addr + off) & ~0xFFF) + 0x1000
        chunk = min(page_end - (addr + off), size - off)
        buf = (ctypes.c_char * chunk)()
        n = ctypes.c_size_t(0)
        ok = k32.ReadProcessMemory(h, ctypes.c_void_p(addr + off), buf, chunk, ctypes.byref(n))
        if ok:
            data[off: off + chunk] = bytes(buf[: chunk])
            for i in range(off, off + chunk):
                got[i] = 1
        else:
            # 退到更小的粒度，逐 64 字节试探
            for sub in range(0, chunk, 64):
                s2 = min(64, chunk - sub)
                b2 = (ctypes.c_char * s2)()
                n2 = ctypes.c_size_t(0)
                if k32.ReadProcessMemory(h, ctypes.c_void_p(addr + off + sub), b2, s2, ctypes.byref(n2)):
                    data[off + sub: off + sub + s2] = bytes(b2[: s2])
                    for i in range(off + sub, off + sub + s2):
                        got[i] = 1
        off += chunk
    return bytes(data), bytes(got)


def write_mem(h, addr, data):
    buf = ctypes.create_string_buffer(data, len(data))
    n = ctypes.c_size_t(0)
    ok = k32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, len(data), ctypes.byref(n))
    if not ok:
        raise SystemExit(f"WriteProcessMemory(0x{addr:X}) failed, err={ctypes.get_last_error()}")
    return n.value


def is_plausible_float(f):
    if f != f:          # NaN
        return False
    if abs(f) == float("inf"):
        return False
    if f == 0.0:
        return True
    return 1e-6 <= abs(f) <= 1e7


def cmd_dump(args):
    h = open_proc(args.pid)
    for spec in args.label:
        parts = spec.split(":")
        label, addr, size = parts[0], int(parts[1], 16), int(parts[2], 16)
        print(f"\n===== {label}  0x{addr:X}  size=0x{size:X} =====")
        data, got = read_mem_safe(h, addr, size)
        print(f"{'off':>6} {'qword':>20} {'int32':>12} {'float':>16}")
        for off in range(0, len(data) - 7, 8):
            if not got[off]:
                print(f"0x{off:04X}  <unreadable>")
                continue
            q = struct.unpack_from("<Q", data, off)[0]
            i2 = struct.unpack_from("<i", data, off)[0]
            f1 = struct.unpack_from("<f", data, off)[0]
            fs = f"{f1:16.6g}" if is_plausible_float(f1) else " " * 16
            print(f"0x{off:04X} 0x{q:016X} {i2:12d} {fs}")


def cmd_floats(args):
    h = open_proc(args.pid)
    data, got = read_mem_safe(h, args.addr, args.size)
    seen = 0
    print(f"# plausible floats in 0x{args.addr:X} .. +0x{args.size:X}")
    for off in range(0, len(data) - 3, 4):
        if not got[off]:
            continue
        f = struct.unpack_from("<f", data, off)[0]
        if not is_plausible_float(f):
            continue
        if f == 0.0 and not args.keep_zero:
            continue
        if args.lo is not None and f < args.lo:
            continue
        if args.hi is not None and f > args.hi:
            continue
        print(f"0x{off:04X}  {f:.6g}")
        seen += 1
    print(f"# {seen} hits")


def cmd_find(args):
    h = open_proc(args.pid)
    data, got = read_mem_safe(h, args.addr, args.size)
    hits = 0
    for off in range(0, len(data) - 3, 4):
        if not got[off]:
            continue
        f = struct.unpack_from("<f", data, off)[0]
        if not is_plausible_float(f):
            continue
        if abs(f - args.value) <= args.tol:
            print(f"0x{off:04X}  0x{args.addr + off:X}  {f:.6g}")
            hits += 1
    print(f"# {hits} hits for {args.value} +-{args.tol}")


def cmd_scan(args):
    """跨整个进程扫字节模式（只扫已提交的可写内存），用于找 float 常量对。"""
    h = open_proc(args.pid)
    pat = bytes.fromhex(args.pattern.replace(" ", ""))
    print(f"# pattern {pat.hex()}  ({len(pat)} bytes)  {'writable only' if not args.all else 'all committed'}")

    class MBI(ctypes.Structure):
        _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                    ("AllocationProtect", wt.DWORD), ("__a1", wt.DWORD),
                    ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                    ("Protect", wt.DWORD), ("Type", wt.DWORD), ("__a2", wt.DWORD)]

    MEM_COMMIT = 0x1000
    PAGE_GUARD = 0x100
    PAGE_NOACCESS = 0x01
    WRITABLE = {0x04, 0x08, 0x40, 0x80}  # READWRITE, WRITECOPY, EXECUTE_READWRITE, EXECUTE_WRITECOPY

    addr = 0
    mbi = MBI()
    hits = 0
    scanned = 0
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base = mbi.BaseAddress or 0
        size = mbi.RegionSize
        if size == 0:
            break
        ok = (mbi.State == MEM_COMMIT) and not (mbi.Protect & PAGE_GUARD) and mbi.Protect != PAGE_NOACCESS
        if ok and (args.all or (mbi.Protect & 0xFF) in WRITABLE):
            data, got = read_mem_safe(h, base, size)
            scanned += size
            start = 0
            while True:
                i = data.find(pat, start)
                if i < 0:
                    break
                print(f"0x{base + i:X}")
                hits += 1
                start = i + 1
                if hits >= args.limit:
                    print(f"# 达到上限 {args.limit}")
                    print(f"# scanned {scanned/1024/1024:.0f} MB, {hits} hits")
                    return
        addr = base + size
        if addr > 0x7FFFFFFFFFFF:
            break
    print(f"# scanned {scanned/1024/1024:.0f} MB, {hits} hits")


def cmd_ptrs(args):
    """在一块内存里找 8 字节指针，看它指向哪个已知对象。"""
    h = open_proc(args.pid)
    data, got = read_mem_safe(h, args.addr, args.size)
    want = {}
    for spec in args.ptr:
        name, val = spec.split("=")
        want[int(val, 16)] = name
    hits = 0
    for off in range(0, len(data) - 7, 8):
        if not got[off]:
            continue
        q = struct.unpack_from("<Q", data, off)[0]
        if q in want:
            print(f"0x{off:04X}  0x{args.addr+off:X}  -> {want[q]}  (0x{q:X})")
            hits += 1
    print(f"# {hits} pointer hits")
    if args.list_all:
        print("# --- all plausible userspace pointers ---")
        for off in range(0, len(data) - 7, 8):
            if not got[off]:
                continue
            q = struct.unpack_from("<Q", data, off)[0]
            if 0x10000 <= q <= 0x7FFFFFFFFFFF:
                print(f"0x{off:04X}  0x{q:X}")


def cmd_write(args):
    """写 float32，并在写入后回读，看游戏有没有把它改回去（= 夹取是否生效）。"""
    h = open_proc(args.pid, write=True)
    before = struct.unpack("<f", read_mem_safe(h, args.addr, 4)[0][:4])[0]
    write_mem(h, args.addr, struct.pack("<f", args.value))
    print(f"[write] 0x{args.addr:X}  {before:.6g} -> {args.value:.6g}")
    for i in range(args.check):
        time.sleep(args.interval / 1000.0)
        cur = struct.unpack("<f", read_mem_safe(h, args.addr, 4)[0][:4])[0]
        verdict = "HOLDS" if abs(cur - args.value) < max(1e-4, abs(args.value) * 1e-4) else "RESET"
        print(f"  +{args.interval*(i+1):4d}ms  now={cur:.6g}  {verdict}")


def cmd_snap(args):
    h = open_proc(args.pid)
    data, got = read_mem_safe(h, args.addr, args.size)
    with open(args.out, "wb") as fh:
        fh.write(struct.pack("<QI", args.addr, args.size))
        fh.write(data)
        fh.write(got)
    print(f"# snapshot 0x{args.addr:X}+0x{args.size:X} -> {args.out}")


def cmd_diff(args):
    with open(args.a, "rb") as fh:
        a_addr, a_size = struct.unpack("<QI", fh.read(12))
        a_data = fh.read(a_size)
        a_got = fh.read(a_size)
    with open(args.b, "rb") as fh:
        b_addr, b_size = struct.unpack("<QI", fh.read(12))
        b_data = fh.read(b_size)
        b_got = fh.read(b_size)
    if a_addr != b_addr or a_size != b_size:
        raise SystemExit("两份快照的地址/长度不一致")
    n = 0
    print(f"# diff {args.a} -> {args.b}  base=0x{a_addr:X}")
    for off in range(0, a_size - 3, 4):
        if not (a_got[off] and b_got[off]):
            continue
        fa = struct.unpack_from("<f", a_data, off)[0]
        fb = struct.unpack_from("<f", b_data, off)[0]
        if fa == fb:
            continue
        if not (is_plausible_float(fa) and is_plausible_float(fb)):
            continue
        if args.min_delta is not None and abs(fb - fa) < args.min_delta:
            continue
        print(f"0x{off:04X}  0x{a_addr+off:X}  {fa:.6g} -> {fb:.6g}")
        n += 1
    print(f"# {n} changed floats")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ps")

    d = sub.add_parser("dump")
    d.add_argument("--pid", type=int, required=True)
    d.add_argument("--label", action="append", required=True, help="Name:0xADDR:0xSIZE")
    d.set_defaults(func=cmd_dump)

    fl = sub.add_parser("floats")
    fl.add_argument("--pid", type=int, required=True)
    fl.add_argument("--addr", type=lambda s: int(s, 16), required=True)
    fl.add_argument("--size", type=lambda s: int(s, 16), default=0x400)
    fl.add_argument("--lo", type=float)
    fl.add_argument("--hi", type=float)
    fl.add_argument("--keep-zero", action="store_true")
    fl.set_defaults(func=cmd_floats)

    fd = sub.add_parser("find")
    fd.add_argument("--pid", type=int, required=True)
    fd.add_argument("--addr", type=lambda s: int(s, 16), required=True)
    fd.add_argument("--size", type=lambda s: int(s, 16), default=0x400)
    fd.add_argument("--value", type=float, required=True)
    fd.add_argument("--tol", type=float, default=0.01)
    fd.set_defaults(func=cmd_find)

    pt = sub.add_parser("ptrs")
    pt.add_argument("--pid", type=int, required=True)
    pt.add_argument("--addr", type=lambda s: int(s, 16), required=True)
    pt.add_argument("--size", type=lambda s: int(s, 16), default=0x800)
    pt.add_argument("--ptr", action="append", default=[], help="name=0xADDR")
    pt.add_argument("--list-all", action="store_true")
    pt.set_defaults(func=cmd_ptrs)

    sc = sub.add_parser("scan")
    sc.add_argument("--pid", type=int, required=True)
    sc.add_argument("--pattern", required=True, help="hex bytes, e.g. 0000A0410000B442")
    sc.add_argument("--limit", type=int, default=40)
    sc.add_argument("--all", action="store_true", help="扫所有已提交内存（含只读代码段）")
    sc.set_defaults(func=cmd_scan)

    w = sub.add_parser("write")
    w.add_argument("--pid", type=int, required=True)
    w.add_argument("--addr", type=lambda s: int(s, 16), required=True)
    w.add_argument("--value", type=float, required=True)
    w.add_argument("--check", type=int, default=5, help="写入后回读次数")
    w.add_argument("--interval", type=int, default=150, help="回读间隔 ms")
    w.set_defaults(func=cmd_write)

    sn = sub.add_parser("snap")
    sn.add_argument("--pid", type=int, required=True)
    sn.add_argument("--addr", type=lambda s: int(s, 16), required=True)
    sn.add_argument("--size", type=lambda s: int(s, 16), default=0x1000)
    sn.add_argument("--out", required=True)
    sn.set_defaults(func=cmd_snap)

    df = sub.add_parser("diff")
    df.add_argument("--a", required=True)
    df.add_argument("--b", required=True)
    df.add_argument("--min-delta", type=float, default=0.0)
    df.set_defaults(func=cmd_diff)

    args = ap.parse_args()
    if args.cmd == "ps":
        list_processes()
        return
    args.func(args)


if __name__ == "__main__":
    main()
