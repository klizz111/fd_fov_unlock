#!/usr/bin/env python3
"""
fov_remap.py — 把摄影模式那条「垂直视角」滑条变成 60°~150° 的可调范围。

为什么不是「实时重映射」：
    游戏在拖滑块时是插值写入 CameraComponent+0x250 的，而且会读回当前值再插值。
    实时重映射会和它互相推着走 —— 实测表现是「拖到一半直接跳到 150 卡死」。
    所以改成 settle（静默后再改写）：
        拖拽中   -> 我们完全不写，让游戏自己插值到它自己的 20~90
        停手 350ms 后 -> 按最终值做一次线性映射并写一次
    这样两端和中间都准，且不会互相推动。实测写进去的值游戏不会逐帧拉回
    （3 秒 10 次采样全部 HOLDS），所以写完就稳定。

用法：
    python fov_remap.py                       # 20~90 -> 60~150
    python fov_remap.py --out-min 40 --out-max 170
    python fov_remap.py --pin 120             # 固定值，忽略滑条
    python fov_remap.py --list                # 只看状态

前提：游戏内按过一次 F6（生成 objects.txt 提供指针）。
"""
import argparse
import ctypes
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fdcommon as fd            # noqa: E402

FOV_OFFSET = fd.OFF_FOV
k32 = ctypes.WinDLL("kernel32", use_last_error=True)


def open_proc(pid):
    h = fd.open_proc(pid, write=True)
    if not h:
        raise SystemExit(f"OpenProcess({pid}) 失败 err={ctypes.get_last_error()}")
    return h


def rd_float(h, addr):
    return fd.rd_f32(h, addr)


def wr_float(h, addr, val):
    return fd.write_f32(h, addr, val)


def find_pid():
    return fd.find_pid()


def parse_objects(path, filters):
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("#") or "\t" not in line:
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 5 or "CameraComponent" not in p[0]:
                continue
            full, addr = p[4], int(p[1], 16)
            if ".Camera" not in full:
                continue
            if filters and not any(f.lower() in full.lower() for f in filters):
                continue
            out.append((full, addr))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--game-dir", dest="game_dir",
                    help="游戏根目录；不给则按 环境变量 FDMOD_GAME_DIR -> config.json -> 进程反查")
    ap.add_argument("--auto", action="store_true",
                    help="不给 manifest，改用 camfind 自解析相机（约 30 秒）")
    ap.add_argument("--objects", default=None)
    ap.add_argument("--filter", action="append", default=[])
    ap.add_argument("--in-min", type=float, default=20.0, dest="in_min")
    ap.add_argument("--in-max", type=float, default=90.0, dest="in_max")
    ap.add_argument("--out-min", type=float, default=60.0, dest="out_min")
    ap.add_argument("--out-max", type=float, default=150.0, dest="out_max")
    ap.add_argument("--pin", type=float)
    ap.add_argument("--settle-ms", type=int, default=350, dest="settle")
    ap.add_argument("--interval", type=int, default=40)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    filters = args.filter or ["A_FreeCameraPawn"]
    pid = args.pid or find_pid()
    if not pid:
        raise SystemExit("找不到 Paralogue-Win64-Shipping.exe 进程")

    game_dir = fd.resolve_game_dir(args.game_dir, verbose=True)
    if args.objects is None:
        args.objects = fd.objects_path(game_dir)

    cams = []
    if args.objects and os.path.exists(args.objects):
        cams = parse_objects(args.objects, filters)

    if args.auto or not cams:
        print("没有可用的 manifest，改用自解析定位（约 30 秒）…")
        import camfind
        h0 = fd.open_proc(pid)
        found, pcms = camfind.scan(h0, verbose=True)
        pcm = next((p for p in pcms if fd.plausible_ptr(p["viewtarget"])), None)
        active, pov, how = camfind.pick_active(h0, found, pcm)
        if not active:
            raise SystemExit("自解析也没找到相机：请先进自由相机/摄影界面再试")
        cams = [(f"camera(auto:{how})", active["addr"])]
        print(f"自解析命中 0x{active['addr']:X}  FOV={active['fov']:.2f}  (POV={pov})")

    if not cams:
        raise SystemExit("objects.txt 里没有匹配的 CameraComponent；按 F6 刷新")

    h = open_proc(pid)
    span_in = max(1e-6, args.in_max - args.in_min)
    span_out = args.out_max - args.out_min

    def remap(v):
        t = min(1.0, max(0.0, (v - args.in_min) / span_in))
        return args.out_min + t * span_out

    print(f"pid={pid}  相机 {len(cams)} 个   映射 {args.in_min:g}~{args.in_max:g} -> "
          f"{args.out_min:g}~{args.out_max:g}   静默阈值 {args.settle}ms")
    stale = 0
    for full, addr in cams:
        cur = rd_float(h, addr + FOV_OFFSET)
        print(f"  0x{addr:X}  当前={cur}  {full}")
        if cur is None or not (0.5 <= cur <= 179.0):
            stale += 1
    if stale:
        print("\n[!] 有相机读不到合理的 FOV —— objects.txt 里的地址很可能在换关卡/重开后过期了。")
        print("    请回到游戏按一次 F6（重新生成 objects.txt），再重跑本脚本。")
    if args.list:
        return

    st = {}
    for _f, addr in cams:
        st[addr] = {"last": None, "written": None, "pending": None, "since": 0.0}

    print("\n拖完滑条松手约 0.35 秒后生效。Ctrl+C 停止。\n")
    try:
        while True:
            now = time.monotonic()
            for _full, addr in cams:
                cur = rd_float(h, addr + FOV_OFFSET)
                if cur is None:
                    continue
                s = st[addr]
                if args.pin is not None:
                    if wr_float(h, addr + FOV_OFFSET, args.pin):
                        if s["written"] != args.pin:
                            print(f"[{time.strftime('%H:%M:%S')}] pin => {args.pin}°")
                            s["written"] = args.pin
                    continue
                # 是我们自己刚写的值 => 不算「游戏写入」，直接略过
                if s["written"] is not None and abs(cur - s["written"]) <= 1e-3:
                    s["last"] = cur
                    s["pending"] = None
                    continue
                # 值变了 => 游戏在写（拖拽中）。只记录，不介入。
                if s["last"] is None or abs(cur - s["last"]) > 1e-3:
                    s["last"] = cur
                    s["pending"] = cur
                    s["since"] = now
                else:
                    # 静默足够久且存在待处理值 => 做一次映射
                    if s["pending"] is not None and (now - s["since"]) * 1000 >= args.settle:
                        out = remap(s["pending"])
                        if abs(out - cur) > 1e-3:
                            if wr_float(h, addr + FOV_OFFSET, out):
                                print(f"[{time.strftime('%H:%M:%S')}] 滑条 {s['pending']:.2f}° -> {out:.2f}°")
                        s["written"] = out
                        s["pending"] = None
            time.sleep(args.interval / 1000.0)
    except KeyboardInterrupt:
        print("\n停止。")


if __name__ == "__main__":
    main()
