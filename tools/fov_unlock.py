#!/usr/bin/env python3
"""
fov_unlock.py — 常驻解除 Fallen Doll 的垂直视角（FOV）上限。不注入 DLL，纯进程外读写。

已实测的映射关系（Room_SinglePCMain / A_FreeCameraPawn）：
    CameraComponent + 0x250 = FieldOfView (float)
    游戏滑块 0.00~1.00  ->  FOV 20.4~90.0   （滑块 1.00 = 90° 硬上限）
    写入后 PlayerCameraManager + 0x380/+0x388 立即镜像，渲染生效
    实测 150° 可视，游戏自身不会把它改回去（只有拖动滑块时才会覆盖）

用法：
    1) 游戏内按一次 F6（生成 objects.txt，给出指针）
    2) python fov_unlock.py                 # 默认钉到 120°
       python fov_unlock.py --fov 150       # 更激进
       python fov_unlock.py --list          # 只列出解析到的相机和当前值

    运行中随时改目标值：编辑同目录下的 fov_target.txt（写一个数字即可），
    下一拍就生效，不用重启脚本。

    停止：Ctrl+C。停止瞬间游戏就回到它自己的值。
"""
import argparse
import ctypes
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fdcommon as fd            # noqa: E402  共享底层：进程/内存/路径

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
            if not full.endswith(".Camera") and ".Camera" not in full:
                continue
            if filters and not any(f.lower() in full.lower() for f in filters):
                continue
            out.append((full, addr))
    return out


def read_target(path, default):
    try:
        with open(path, "r") as fh:
            v = float(fh.read().strip())
        if 1.0 <= v <= 179.0:
            return v
    except Exception:
        pass
    return default


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--game-dir", dest="game_dir",
                    help="游戏根目录；不给则按 环境变量 FDMOD_GAME_DIR -> config.json -> 进程反查")
    ap.add_argument("--objects", default=None,
                    help="UE4SS F6 manifest 路径；默认 <game-dir>/Paralogue/Binaries/Win64/FDCamDump/objects.txt")
    ap.add_argument("--auto", action="store_true",
                    help="不给 manifest，改用 camfind 自解析相机（约 30 秒）")
    ap.add_argument("--filter", action="append", default=[],
                    help="只改名字含该串的相机，可多次给；默认 A_FreeCameraPawn")
    ap.add_argument("--fov", type=float, default=120.0)
    ap.add_argument("--interval", type=int, default=200)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    filters = args.filter or ["A_FreeCameraPawn"]
    target_file = os.path.join(here, "fov_target.txt")

    game_dir = fd.resolve_game_dir(args.game_dir, verbose=True)
    if args.objects is None:
        args.objects = fd.objects_path(game_dir)

    pid = args.pid or find_pid()
    if not pid:
        raise SystemExit("找不到 Paralogue-Win64-Shipping.exe 进程")

    cams = []
    if args.objects and os.path.exists(args.objects):
        cams = parse_objects(args.objects, filters)

    use_auto = args.auto or not cams
    if use_auto:
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
        raise SystemExit(f"objects.txt 里没有匹配 {filters} 的 CameraComponent；按 F6 刷新后再试")

    h = open_proc(pid)
    print(f"pid={pid}  目标相机 {len(cams)} 个（offset +0x{FOV_OFFSET:X}）")
    for full, addr in cams:
        print(f"  0x{addr:X}  FOV={rd_float(h, addr + FOV_OFFSET)}  {full}")
    if args.list:
        return

    if not os.path.exists(target_file):
        with open(target_file, "w") as fh:
            fh.write(str(args.fov))
    cur = read_target(target_file, args.fov)
    print(f"\n目标 FOV 文件: {target_file}  (当前 {cur})")
    print("改这一个数字即可实时调整；Ctrl+C 停止。\n")

    last = None
    try:
        while True:
            cur = read_target(target_file, cur)
            ok = 0
            for _f, addr in cams:
                if wr_float(h, addr + FOV_OFFSET, cur):
                    ok += 1
            if cur != last:
                print(f"[{time.strftime('%H:%M:%S')}] FOV -> {cur}  ({ok}/{len(cams)} 个相机写入成功)")
                last = cur
            time.sleep(args.interval / 1000.0)
    except KeyboardInterrupt:
        print("\n停止。")


if __name__ == "__main__":
    main()
