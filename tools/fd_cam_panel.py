#!/usr/bin/env python3
"""
fd_cam_panel.py — Fallen Doll 相机叠层控制面板（进程外，不注入 DLL）

为什么是进程外：UE4SS 在这套 UE 5.7.4 单体 exe 上任何 FProperty 访问都会崩
（DumpAllObjects / DumpJMAP / ForEachProperty / obj[prop] 全实测崩），
所以 in-process 的 Lua/C++ mod 走不通。指针仍由 UE4SS 提供（F6 manifest），
属性读写全部走进程外 ReadProcessMemory / WriteProcessMemory。

功能
    1. 自带滑条 60~150°，直接钉 FOV（代替游戏那条只有 20~90 的滑条）
    2. 「跟随游戏滑条」：拖游戏滑条，松手 0.35s 后把 20~90 重映射成 60~150
       （拖拽中不介入，避免和游戏插值互推）
    3. 全局热键（面板不需要焦点）：
         Ctrl+F7  -5°      Ctrl+F8  +5°
         Ctrl+F9  切 跟随/手动      Ctrl+F10 显示/隐藏
    4. 地址失效自检：换关卡/重启游戏后提示「按 F6」

线程模型（踩过的坑）
    tkinter 变量不是线程安全的。后台线程只读写普通 Python 属性，
    所有 tk 控件操作都在主线程的 tick() 里做。

用法
    游戏内先按一次 F6 生成
    <GAME_DIR>\\Paralogue\\Binaries\\Win64\\FDCamDump\\objects.txt
    然后：python fd_cam_panel.py
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import tkinter as tk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fdcommon as fd           # noqa: E402  共享底层：进程/内存/路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import camfind          # 本地自解析扫描器（不依赖 UE4SS）
except Exception:           # 找不到就退化成只用 manifest
    camfind = None

FOV_OFFSET = fd.OFF_FOV
GAME_EXE = fd.GAME_EXE
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
WM_HOTKEY = 0x0312
MOD_CTRL = 0x0002


# ---------------------------------------------------------------- 进程 / 内存

def find_pid():
    return fd.find_pid(GAME_EXE)


def open_proc(pid):
    return fd.open_proc(pid, write=True)


def rd_float(h, addr):
    return fd.rd_f32(h, addr)


def wr_float(h, addr, val):
    return fd.write_f32(h, addr, val)


def parse_objects(path, filters):
    out = []
    if not os.path.exists(path):
        return out
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


# ---------------------------------------------------------------- 热键线程

class Hotkeys(threading.Thread):
    """RegisterHotKey 绑定调用线程的消息队列；tkinter mainloop 会先把 WM_HOTKEY
    取走丢弃，所以这里用独立线程注册并自己跑 GetMessage。"""

    def __init__(self, mapping):
        super().__init__(daemon=True)
        self.mapping = mapping
        self.queue = queue.Queue()
        self.ready = threading.Event()
        self.registered = []
        self.failed = []

    def run(self):
        for i, (mod, vk, name) in enumerate(self.mapping, start=1):
            if u32.RegisterHotKey(None, i, mod, vk):
                self.registered.append(name)
            else:
                self.failed.append((name, ctypes.get_last_error()))
        self.ready.set()
        msg = wt.MSG()
        while u32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message == WM_HOTKEY:
                idx = int(msg.wParam)
                if 1 <= idx <= len(self.mapping):
                    self.queue.put(self.mapping[idx - 1][2])

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out


# ---------------------------------------------------------------- 面板

class Panel:
    def __init__(self, args):
        self.args = args
        self.objects = args.objects
        self.filters = args.filter or ["A_FreeCameraPawn"]

        # ---- 线程之间只共享这几个普通属性
        self.pid = None
        self.h = None
        self.cams = []
        self.mode = "pin"            # "pin" | "follow"
        self.target = float(args.fov)
        self.status_msg = "启动中…"
        self.ui_fov = None           # 后台想让 UI 显示的 FOV
        self.settle = {}
        self.running = True
        self.hk_reported = False
        self.locating = False
        self.pin_targets = {}       # 所有发现过的相机地址（多相机同时压值）
        self.broadcast = []         # 扫描到的所有“玩家相机”（FOV>=60），全部一起压值
        self.all_cams = []          # 全量扫描得到的候选相机（快速重定位用）
        self.pcm = None             # 活跃 PlayerCameraManager
        self.last_cam = None        # 上次定位到的相机地址（快速重扫的锚点）
        self.pcms = []              # 缓存的 PlayerCameraManager 列表（快路径用）
        self.last_relocate = 0.0
        self.last_pid_check = 0.0
        self.relocate_misses = 0

        self.root = tk.Tk()          # 先建 root，tk 变量才有 master
        self.pid = find_pid()
        self.h = open_proc(self.pid) if self.pid else None
        self.load_targets()
        self.refresh_cams()

        self.build_ui()

        self.hotkeys = Hotkeys([
            (MOD_CTRL, 0x76, "fov_down"),   # Ctrl+F7
            (MOD_CTRL, 0x77, "fov_up"),     # Ctrl+F8
            (MOD_CTRL, 0x78, "toggle"),     # Ctrl+F9
            (MOD_CTRL, 0x79, "hide"),       # Ctrl+F10
            (MOD_CTRL, 0x7A, "relocate"),   # Ctrl+F11
        ])
        self.hotkeys.start()

        self.apply(self.target)
        threading.Thread(target=self.worker, daemon=True).start()
        self.root.after(60, self.tick)

    # ---- UI（全部在主线程执行）
    def build_ui(self):
        self.root.title("FallenDoll 相机 FOV")
        self.root.geometry(f"{self.args.width}x160+40+40")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.93)
        self.root.configure(bg="#1b1b1b")

        frm = tk.Frame(self.root, bg="#1b1b1b")
        frm.pack(fill="both", expand=True, padx=10, pady=8)

        top = tk.Frame(frm, bg="#1b1b1b")
        top.pack(fill="x")
        tk.Label(top, text="FOV", fg="#ddd", bg="#1b1b1b").pack(side="left")
        self.val_lbl = tk.Label(top, text="%.0f°" % self.target, fg="#6cf", bg="#1b1b1b",
                                font=("Consolas", 12, "bold"))
        self.val_lbl.pack(side="left", padx=6)
        self.mode_lbl = tk.Label(top, text="手动", fg="#fa6", bg="#1b1b1b")
        self.mode_lbl.pack(side="right")

        self.scale = tk.Scale(frm, from_=self.args.out_min, to=self.args.out_max,
                              orient="horizontal", showvalue=False, resolution=1,
                              bg="#1b1b1b", fg="#ddd", troughcolor="#333",
                              highlightthickness=0, command=self.on_scale)
        self.scale.set(self.target)
        self.scale.pack(fill="x", pady=6)

        btn = tk.Frame(frm, bg="#1b1b1b")
        btn.pack(fill="x")
        tk.Button(btn, text="跟随游戏滑条", command=self.set_follow, width=12).pack(side="left")
        tk.Button(btn, text="手动", command=self.set_pin, width=6).pack(side="left", padx=4)
        tk.Button(btn, text="自动扫描", command=lambda: self.auto_locate(False), width=9).pack(side="left", padx=4)
        tk.Button(btn, text="F6清单", command=self.refresh_cams, width=8).pack(side="left", padx=4)

        self.status_lbl = tk.Label(frm, text=self.status_msg, fg="#9c9", bg="#1b1b1b",
                                   anchor="w", justify="left", wraplength=self.args.width - 30)
        self.status_lbl.pack(fill="x", pady=(6, 0))

    def on_scale(self, value):
        self.target = float(value)
        self.val_lbl.config(text="%.0f°" % self.target)
        if self.mode == "pin":
            self.apply(self.target)

    def set_follow(self):
        self.mode = "follow"
        self.mode_lbl.config(text="跟随游戏")
        self.status_msg = ("拖游戏滑条，松手 0.35s 后生效（%.0f~%.0f → %.0f~%.0f）"
                           % (self.args.in_min, self.args.in_max,
                              self.args.out_min, self.args.out_max))

    def set_pin(self):
        self.mode = "pin"
        self.mode_lbl.config(text="手动")
        self.status_msg = "面板滑条直接决定 FOV（持续压住）"

    # ---- 相机列表持久化（同一游戏进程内有效）
    def targets_file(self):
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "pin_targets.json")

    def load_targets(self):
        try:
            with open(self.targets_file(), "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if not self.pid or d.get("pid") != self.pid:
                return
            for a in d.get("addrs", []):
                self.pin_targets[int(a)] = True
            pcms = d.get("pcms") or []
            if pcms:
                self.pcms = [{"addr": int(a), "viewtarget": 0} for a in pcms]
            if self.pin_targets:
                self.cams = [("camera(saved)", a) for a in self.pin_targets]
                self.status_msg = "已恢复 %d 个相机（同一游戏进程）" % len(self.pin_targets)
        except Exception:
            pass

    def save_targets(self):
        try:
            with open(self.targets_file(), "w", encoding="utf-8") as fh:
                json.dump({"pid": self.pid,
                           "addrs": sorted(self.pin_targets),
                           "pcms": [p["addr"] for p in self.pcms]}, fh)
        except Exception:
            pass

    # ---- 数据（主线程调用）
    def refresh_cams(self):
        self.pid = find_pid()
        if not self.pid:
            self.status_msg = "找不到游戏进程"
            return
        if not self.h:
            self.h = open_proc(self.pid)
        self.cams = parse_objects(self.objects, self.filters)
        if not self.cams:
            self.status_msg = "没有 manifest，改走自动定位…"
            self.auto_locate()
            return
        good = 0
        for _f, a in self.cams:
            v = rd_float(self.h, a + FOV_OFFSET) if self.h else None
            if v is not None and 0.5 <= v <= 179.0:
                good += 1
        if good == 0:
            self.status_msg = "manifest 里的地址过期，改走自动定位…"
            self.auto_locate()
        else:
            self.status_msg = "已连接 %d 个相机（来自 objects.txt）" % len(self.cams)

    def auto_locate(self, force_full=False):
        """不用 UE4SS：扫 PlayerCameraManager 签名 -> ViewTarget -> 它的相机组件。
        force_full=True 时忽略“上次相机附近”的快速路径，直接全量扫。"""
        if self.locating:
            self.status_msg = "自动扫描中（约 30 秒，游戏会卡顿一下）…"
            return
        if camfind is None:
            self.status_msg = "自动扫描不可用：找不到 camfind 扫描模块"
            return
        # 先在按钮回调所在的主线程更新，避免后台线程尚未获得调度时
        # 用户看不到扫描已经开始。
        self.status_msg = "自动扫描中（约 30 秒，游戏会卡顿一下）…"
        self.force_full = force_full
        self.locating = True
        threading.Thread(target=self._locate_worker, daemon=True).start()

    def _use_camera(self, active, how):
        self.last_cam = active["addr"]
        self.pin_targets[active["addr"]] = True
        self.cams = [("camera(auto:%s)" % how, a) for a in self.pin_targets]
        self.save_targets()
        self.status_msg = ("自动定位成功 0x%X  FOV=%.2f  方式=%s"
                           % (active["addr"], active["fov"], how))
        self.apply(self.target)

    def _locate_worker(self):
        try:
            pid = find_pid()
            if not pid:
                self.status_msg = "找不到游戏进程"
                return
            if not self.h:
                self.h = open_proc(pid)
            force_full = getattr(self, "force_full", False)
            # 1) 快路径：缓存 PCM + 当前 ViewTarget，直接找相机（毫秒级，不扫堆）
            if self.pcms and not force_full:
                fast = camfind.cameras_from_viewtarget(self.h, self.pcms)
                pcm0 = self.pcm or self.pcms[0]
                if fast:
                    strict = [c for c in fast if c.get("outer") == c.get("vt")]
                    if strict:
                        self._use_camera(strict[0], "viewtarget-owner")
                        return
                    active, pov, how = camfind.pick_active(self.h, fast, pcm0)
                    if active:
                        self._use_camera(active, how)
                        return
                # ViewTarget 全为 0 = 不在游戏画面里（标题/菜单），不要扫 9GB
                vts = []
                for p in self.pcms:
                    raw = fd.read(self.h, p["addr"] + camfind.VT_OFF, 8)
                    vts.append(camfind.q64(raw, 0) if raw else 0)
                if not any(camfind.plausible_ptr(v) for v in vts):
                    self.status_msg = ("当前没有活动的视图目标（在标题/菜单里？）"
                                       "进游戏画面后再点「自动扫描」")
                    return
            # 2) 窄扫（上次相机附近）或全量
            lo = hi = None
            if self.last_cam and not force_full:
                lo = max(camfind.HEAP_LO, self.last_cam - 0x10000000)
                hi = min(camfind.HEAP_HI, self.last_cam + 0x10000000)
            cams, pcms = camfind.scan(self.h, lo=lo, hi=hi, throttle_ms=2, stop_on_pcm=True)
            if lo is not None and not cams:
                self.status_msg = "附近没找到，全量扫描中（已限速，不会卡死游戏）…"
                cams, pcms = camfind.scan(self.h, throttle_ms=3, stop_on_pcm=True)
            self.pcms = pcms
            self.all_cams = cams
            self.save_targets()
            pcm = next((p for p in pcms if camfind.plausible_ptr(p["viewtarget"])), None)
            self.pcm = pcm
            if pcm:
                fast = camfind.cameras_from_viewtarget(self.h, [pcm])
                if fast:
                    known = {c["addr"] for c in cams}
                    cams = fast + [c for c in cams if c["addr"] not in known]
            active, pov, how = camfind.pick_active(self.h, cams, pcm)
            if active:
                self._use_camera(active, how)
            else:
                self.status_msg = "自动定位失败：请先进入自由相机/摄影界面再点『自动扫描』"
        except Exception as e:
            self.status_msg = "自动定位异常：%s" % e
        finally:
            self.locating = False


    def fast_relocate(self):
        """用上次全量扫描缓存的候选相机做快速重定位（不重新扫 8GB 堆）。"""
        if not self.h or not self.all_cams or camfind is None:
            return False
        try:
            active, pov, how = camfind.pick_active(self.h, self.all_cams, self.pcm)
        except Exception as e:
            self.status_msg = "快速重定位异常：%s" % e
            return False
        if active:
            self.pin_targets[active["addr"]] = True
            self.cams = [("camera(auto:%s)" % how, a) for a in self.pin_targets]
            self.save_targets()
            self.status_msg = "已重定位相机 0x%X（%s）FOV=%.1f" % (active["addr"], how, active["fov"])
            self.apply(self.target)
            return True
        return False

    def apply(self, value):
        """只写“确认过”的相机；写前校验对象仍然像 CameraComponent，值没变就不写。"""
        if not self.h:
            return 0
        n = 0
        for _f, a in list(self.cams):
            head = fd.read(self.h, a, fd.OFF_ASPECT + 4)
            if not head or len(head) < fd.OFF_ASPECT + 4:
                continue
            fov = struct.unpack_from("<f", head, FOV_OFFSET)[0]
            ar = struct.unpack_from("<f", head, fd.OFF_ASPECT)[0]
            if not (5.0 <= fov <= 179.0):
                continue
            if not (1.60 <= ar <= 2.00):
                continue
            if abs(fov - value) < 0.01:
                n += 1
                continue
            if wr_float(self.h, a + FOV_OFFSET, value):
                n += 1
        return n

    def worker(self):
        span_in = max(1e-6, self.args.in_max - self.args.in_min)
        span_out = self.args.out_max - self.args.out_min
        while self.running:
            try:
                # 游戏进程变了（重启/崩溃后重开）：旧句柄和旧地址全部作废
                tnow = time.monotonic()
                if tnow - self.last_pid_check > 2.0:
                    self.last_pid_check = tnow
                    cur_pid = find_pid()
                    if cur_pid and cur_pid != self.pid:
                        self.pid = cur_pid
                        self.h = open_proc(cur_pid)
                        self.pin_targets.clear()
                        self.cams = []
                        self.pcms = []
                        self.pcm = None
                        self.broadcast = []
                        self.last_cam = None
                        self.status_msg = "游戏进程已变化（pid %d），重新定位…" % cur_pid
                        if not self.locating:
                            self.auto_locate()
                        time.sleep(0.5)
                        continue
                if not self.h or not self.pid:
                    pid = find_pid()
                    if pid:
                        self.pid = pid
                        self.h = open_proc(pid)
                    time.sleep(0.5)
                    continue
                if self.mode == "pin":
                    self.apply(self.target)
                    # 剔除已失效的相机（读不到或值离谱）
                    alive = []
                    for _f, a in list(self.cams):
                        v = rd_float(self.h, a + FOV_OFFSET)
                        if v is not None and 0.5 <= v <= 179.0:
                            alive.append((_f, a))
                        else:
                            self.pin_targets.pop(a, None)
                            self.save_targets()
                    if len(alive) != len(self.cams):
                        self.cams = alive
                    alive_b = []
                    for a in self.broadcast:
                        v = rd_float(self.h, a + FOV_OFFSET)
                        if v is not None and 0.5 <= v <= 179.0:
                            alive_b.append(a)
                    self.broadcast = alive_b
                    # 渲染侧 POV 校验：当前生效的相机没被覆盖 -> 自动重定位
                    pov = None
                    if self.pcm:
                        pov = rd_float(self.h, self.pcm["addr"] + fd.OFF_PCM_POV_FOV)
                    covered = (pov is None) or (abs(pov - self.target) <= 1.0)   # 读不到 POV 时不要反复重扫
                    now = time.monotonic()
                    if not covered:
                        self.relocate_misses += 1
                    else:
                        self.relocate_misses = 0
                    if self.relocate_misses >= 2 and now - self.last_relocate > 3.0:
                        self.last_relocate = now
                        self.relocate_misses = 0
                        if not self.fast_relocate():
                            if not self.locating:
                                self.status_msg = "相机对象已切换，正在全量重扫（约30秒，游戏会卡一下）…"
                                self.auto_locate()
                else:
                    now = time.monotonic()
                    for _f, a in self.cams:
                        cur = rd_float(self.h, a + FOV_OFFSET)
                        if cur is None:
                            continue
                        s = self.settle.setdefault(a, {"last": None, "written": None,
                                                       "pending": None, "since": 0.0})
                        if s["written"] is not None and abs(cur - s["written"]) <= 1e-3:
                            s["last"] = cur
                            s["pending"] = None
                            continue
                        if s["last"] is None or abs(cur - s["last"]) > 1e-3:
                            s["last"] = cur
                            s["pending"] = cur
                            s["since"] = now
                        elif s["pending"] is not None and (now - s["since"]) * 1000 >= 350:
                            t = min(1.0, max(0.0, (s["pending"] - self.args.in_min) / span_in))
                            out = self.args.out_min + t * span_out
                            if wr_float(self.h, a + FOV_OFFSET, out):
                                s["written"] = out
                                self.target = out
                                self.ui_fov = out
                            s["pending"] = None
            except Exception as e:
                self.status_msg = "worker 异常：%s" % e
            # 10ms 一次：游戏那边每帧都在插值/回写 FOV，50ms 太慢会被压回去
            time.sleep(0.01)

    # ---- 主线程 tick：刷新 UI + 分发热键
    def tick(self):
        if self.hotkeys.ready.is_set() and not self.hk_reported:
            self.hk_reported = True
            if self.locating:
                # 扫描提示比一次性的热键初始化提示更重要，不能覆盖它。
                pass
            elif self.hotkeys.registered:
                self.status_msg = "热键就绪：" + " / ".join(self.hotkeys.registered)
            else:
                self.status_msg = "热键注册失败（可能被占用）：%s" % self.hotkeys.failed

        for ev in self.hotkeys.poll():
            if ev == "fov_down":
                self.set_pin()
                self.scale.set(max(self.args.out_min, self.target - 5))
                self.on_scale(self.scale.get())
            elif ev == "fov_up":
                self.set_pin()
                self.scale.set(min(self.args.out_max, self.target + 5))
                self.on_scale(self.scale.get())
            elif ev == "toggle":
                self.set_follow() if self.mode == "pin" else self.set_pin()
            elif ev == "relocate":
                self.auto_locate()
            elif ev == "hide":
                if self.root.state() == "normal":
                    self.root.withdraw()
                else:
                    self.root.deiconify()

        if self.ui_fov is not None:
            self.scale.set(round(self.ui_fov))
            self.val_lbl.config(text="%.0f°" % self.ui_fov)
            self.ui_fov = None

        self.status_lbl.config(text=self.status_msg)
        self.root.after(60, self.tick)

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self.running = False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game-dir", dest="game_dir",
                    help="游戏根目录；不给则按 环境变量 FDMOD_GAME_DIR -> config.json -> 进程反查")
    ap.add_argument("--objects", default=None,
                    help="UE4SS F6 manifest；默认 <game-dir>/Paralogue/Binaries/Win64/FDCamDump/objects.txt")
    ap.add_argument("--filter", action="append", default=[])
    ap.add_argument("--fov", type=float, default=110.0)
    ap.add_argument("--in-min", type=float, default=20.0, dest="in_min")
    ap.add_argument("--in-max", type=float, default=90.0, dest="in_max")
    ap.add_argument("--out-min", type=float, default=60.0, dest="out_min")
    ap.add_argument("--out-max", type=float, default=150.0, dest="out_max")
    ap.add_argument("--width", type=int, default=380)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    game_dir = fd.resolve_game_dir(args.game_dir, verbose=True)
    if args.objects is None:
        args.objects = fd.objects_path(game_dir) or ""

    if args.selftest:
        pid = find_pid()
        if not pid:
            print("FAIL: 游戏没运行")
            return 1
        h = open_proc(pid)
        cams = parse_objects(args.objects, args.filter or ["A_FreeCameraPawn"])
        if not cams:
            if not camfind:
                print("FAIL: objects.txt 无相机，且 camfind 不可用")
                return 1
            print("objects.txt 无相机，走自解析…")
            found, pcms = camfind.scan(h, verbose=True)
            pcm = next((p for p in pcms if fd.plausible_ptr(p["viewtarget"])), None)
            act, pov, how = camfind.pick_active(h, found, pcm)
            if not act:
                print("FAIL: 自解析也没找到相机")
                return 1
            cams = [(f"auto:{how}", act["addr"])]
        print(f"pid={pid} cams={len(cams)}")
        ok = 0
        for v in (args.out_min, args.out_max, 110.0):
            for _f, a in cams:
                wr_float(h, a + FOV_OFFSET, v)
            time.sleep(0.3)
            got = [rd_float(h, a + FOV_OFFSET) for _f, a in cams]
            good = all(g is not None and abs(g - v) < 0.01 for g in got)
            print(f"  write {v} -> {got} {'OK' if good else 'MISMATCH'}")
            ok += 1 if good else 0
        return 0 if ok == 3 else 2

    Panel(args).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
