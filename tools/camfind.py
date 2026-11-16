#!/usr/bin/env python3
"""
camfind.py — 不依赖 UE4SS 自行定位相机对象（纯进程外内存扫描）。

为什么需要它：UE4SS 的 F6 manifest 只给出「这一次进程」的堆地址，重启即失效，
而 UE4SS 本身在 UE 5.7.4 上属性访问会崩。这里改成扫结构化签名，自己找：

    CameraComponent        +0x250 = FieldOfView (float, 5~179)
                           +0x274 = AspectRatio (1.777778 for 16:9)
                           +0x010 = ClassPrivate (堆指针)
                           +0x020 = OuterPrivate (heap ptr, 通常是 Pawn)

    PlayerCameraManager    +0x2C8 = DefaultFOV      (90.0)
                           +0x2D0 = DefaultOrthoWidth (512.0)
                           +0x2D8 = DefaultAspectRatio (1.333333)
                           +0x340 = ViewTarget.Target  (当前被看见的 Actor)

用法：
    python camfind.py                 # 打印 PCM + 当前活跃相机
    python camfind.py --all           # 列出所有相机候选
    python camfind.py --json          # 机器可读，供面板调用
"""
import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fdcommon as fd            # noqa: E402  共享偏移/内存原语

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = ctypes.c_void_p
HEAP_LO, HEAP_HI = fd.HEAP_LO, fd.HEAP_HI
FOV_OFF, AR_OFF = fd.OFF_FOV, fd.OFF_ASPECT
PCM_FOV_OFF = fd.OFF_PCM_DEFAULT_FOV
PCM_VIEWTARGET_OFF = fd.OFF_PCM_VIEWTARGET
HEAD_LEN = fd.OFF_ASPECT + 4     # 读多少字节才够覆盖 FOV / AspectRatio / Class / Outer


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD), ("__a1", wt.DWORD),
                ("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                ("Protect", wt.DWORD), ("Type", wt.DWORD), ("__a2", wt.DWORD)]


# 必须显式声明：不写 argtypes 的话 64 位地址会被按 C int 截断，VirtualQueryEx 直接失败
k32.VirtualQueryEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = ctypes.c_bool


MEM_COMMIT, PAGE_GUARD, PAGE_NOACCESS = 0x1000, 0x100, 0x01
MEM_PRIVATE = 0x20000
WRITABLE = {0x04, 0x08, 0x40, 0x80}
PAT_ASPECT = struct.pack("<f", 16.0 / 9.0)     # 1.7777778  -> 39 8E E3 3F
PAT_FOV90 = struct.pack("<f", 90.0)            # 00 00 B4 42


def find_pid():
    return fd.find_pid()


def open_proc(pid, write=False):
    # VirtualQueryEx 需要 PROCESS_QUERY_INFORMATION，只给 VM_READ 会 ERROR_ACCESS_DENIED(5)
    h = fd.open_proc(pid, write=write)
    if not h:
        raise SystemExit(f"OpenProcess({pid}) 失败 err={ctypes.get_last_error()}")
    return h


def read(h, addr, size):
    buf = (ctypes.c_char * size)()
    n = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
        return None
    return bytes(buf)


def f32(b, off):
    return struct.unpack_from("<f", b, off)[0]


def q64(b, off):
    return struct.unpack_from("<Q", b, off)[0]


def plausible_ptr(p):
    return HEAP_LO <= p <= HEAP_HI


def is_private(h, addr):
    """地址必须落在私有堆的可写页里（排除 exe/dll 的模块映像段）。"""
    mbi = MBI()
    if not k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi),
                              ctypes.sizeof(mbi)):
        return False
    if mbi.State != MEM_COMMIT or mbi.Type != MEM_PRIVATE:
        return False
    return (mbi.Protect & 0xFF) in WRITABLE


def regions(h):
    addr, mbi = 0, MBI()
    while k32.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
        base, size = mbi.BaseAddress or 0, mbi.RegionSize
        if size == 0:
            break
        if (mbi.State == MEM_COMMIT and not (mbi.Protect & PAGE_GUARD)
                and mbi.Protect != PAGE_NOACCESS and (mbi.Protect & 0xFF) in WRITABLE
                and mbi.Type == MEM_PRIVATE):        # 只扫进程私有堆，跳过映射/镜像段
            yield base, size
        addr = base + size
        if addr > HEAP_HI:
            break


def validate_camera(h, p):
    """按结构确认 p 是一个 CameraComponent。"""
    if not plausible_ptr(p) or not is_private(h, p):
        return None
    head = read(h, p, HEAD_LEN)
    if not head or len(head) < HEAD_LEN:
        return None
    fov = f32(head, FOV_OFF)
    ar = f32(head, AR_OFF)
    if not (5.0 <= fov <= 179.0):
        return None
    if abs(ar - 1.777778) > 0.02:
        return None
    cls = q64(head, fd.OFF_CLASS)
    outer = q64(head, fd.OFF_OUTER)
    if not plausible_ptr(cls):
        return None
    return {"addr": p, "fov": fov, "aspect": ar, "class": cls, "outer": outer}


def _iter_chunks(h, lo, hi, CHUNK=32 * 1024 * 1024, OVERLAP=0x400):
    for base, size in regions(h):
        if lo is not None and base + size < lo:
            continue
        if hi is not None and base > hi:
            break
        start = 0
        while start < size:
            yield base + start, min(CHUNK + OVERLAP, size - start)
            start += CHUNK


def scan(h, verbose=False, lo=None, hi=None, throttle_ms=0, workers=8, stop_on_pcm=False):
    """扫私有堆找 CameraComponent / PlayerCameraManager。
    workers: 并行读内存的线程数（ctypes 调用会释放 GIL，能明显加速）。
    stop_on_pcm: 一旦找到带合法 ViewTarget 的 PCM 就立刻停（之后可用 ViewTarget 快路径）。
    """
    cams, pcms = [], []
    lock = threading.Lock()
    stop = threading.Event()
    scanned = [0]

    def work(item):
        base, n = item
        if stop.is_set():
            return
        data = read(h, base, n)
        if not data:
            return
        lc, lp = [], []
        _scan_buf(h, base, data, lc, lp)
        if throttle_ms:
            time.sleep(throttle_ms / 1000.0)
        if lc or lp:
            with lock:
                cams.extend(lc)
                pcms.extend(lp)
                scanned[0] += len(data)
            if stop_on_pcm and any(plausible_ptr(p["viewtarget"]) for p in lp):
                stop.set()

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(work, _iter_chunks(h, lo, hi)))

    if verbose:
        print(f"# scanned {scanned[0]/1024/1024:.0f} MB, cams={len(cams)} pcms={len(pcms)}",
              file=sys.stderr)

    def uniq(rows, key="addr"):
        seen, out = set(), []
        for r in rows:
            if r[key] in seen:
                continue
            seen.add(r[key])
            out.append(r)
        return out

    return uniq(cams), uniq(pcms)


def _scan_buf(h, base, data, cams, pcms):
    """用 bytes.find（C 速度）定位签名，再逐个结构校验。"""
    # --- CameraComponent: 命中 16:9 的 AspectRatio，再回看 FOV
    i = data.find(PAT_ASPECT)
    while i >= 0:
        off = i - AR_OFF
        if off >= 0:
            v = validate_camera(h, base + off)
            if v:
                cams.append(v)
        i = data.find(PAT_ASPECT, i + 1)

    # --- PlayerCameraManager: 命中 DefaultFOV=90，再查 +8=512 / +16≈1.3333
    i = data.find(PAT_FOV90)
    while i >= 0:
        off = i - PCM_FOV_OFF
        if off >= 0 and off + 0x360 <= len(data):
            ortho = f32(data, i + 8)
            ar2 = f32(data, i + 16)
            if abs(ortho - 512.0) < 0.5 and abs(ar2 - 1.333333) < 0.01:
                head = read(h, base + off, 0x360)
                if head:
                    pcms.append({"addr": base + off, "default_fov": f32(data, i),
                                 "viewtarget": q64(head, PCM_VIEWTARGET_OFF)})
        i = data.find(PAT_FOV90, i + 1)


def probe_active(hw, cams, pcm, probe_value=31.7):
    """逐个候选写入特征值，谁让 PCM 的 POV FOV 变了，谁就是活跃相机。
    这是最硬的判定：不依赖 outer/pawn 结构假设。写完立刻还原。"""
    if not pcm:
        return None, None
    pov_addr = pcm["addr"] + fd.OFF_PCM_POV_FOV
    for c in cams:
        old = read(hw, c["addr"] + FOV_OFF, 4)
        if not old:
            continue
        orig = f32(old, 0)
        wr = read and None
        # 写特征值
        buf = ctypes.create_string_buffer(struct.pack("<f", probe_value), 4)
        n = ctypes.c_size_t(0)
        if not k32.WriteProcessMemory(hw, ctypes.c_void_p(c["addr"] + FOV_OFF), buf, 4, ctypes.byref(n)):
            continue
        time.sleep(0.09)
        pov = read(hw, pov_addr, 4)
        got = f32(pov, 0) if pov else None
        # 还原
        buf2 = ctypes.create_string_buffer(struct.pack("<f", orig), 4)
        k32.WriteProcessMemory(hw, ctypes.c_void_p(c["addr"] + FOV_OFF), buf2, 4, ctypes.byref(n))
        if got is not None and abs(got - probe_value) < 0.05:
            return c, got
    return None, None


def components_of(h, actor, size=0x2000):
    """在 Actor 的内存块里找它的组件：指针可读、+0x10 是类指针、+0x250 是合理 FOV。"""
    body = read(h, actor, size)
    if not body:
        return []
    out, seen = [], set()
    for off in range(0, len(body) - 8, 8):
        p = q64(body, off)
        if not plausible_ptr(p) or p in seen:
            continue
        head = read(h, p, HEAD_LEN)
        if not head:
            continue
        fov = f32(head, FOV_OFF)
        cls = q64(head, fd.OFF_CLASS)
        if 5.0 <= fov <= 179.0 and plausible_ptr(cls):
            seen.add(p)
            out.append({"addr": p, "fov": fov, "aspect": f32(head, AR_OFF),
                        "class": cls, "outer": q64(head, fd.OFF_OUTER), "via": f"actor+0x{off:X}"})
    return out


def pick_active(h, cams, pcm):
    """优先走 ViewTarget：PCM+0x340 = 当前被看见的 Actor，其组件里 FOV 与渲染侧一致的
    就是活跃相机。这条链是实测校准过的（87.93 与 PCM+0x380 完全一致）。"""
    if not pcm:
        return (cams[0] if cams else None), None, "no-pcm"
    pov = read(h, pcm["addr"] + fd.OFF_PCM_POV_FOV, 4)
    pov_fov = f32(pov, 0) if pov else None
    pawn = pcm["viewtarget"]
    if plausible_ptr(pawn):
        comps = components_of(h, pawn)
        if pov_fov is not None and comps:
            comps.sort(key=lambda c: abs(c["fov"] - pov_fov))
            if abs(comps[0]["fov"] - pov_fov) < 0.05:
                return comps[0], pov_fov, "viewtarget-component"
        if comps:
            return comps[0], pov_fov, "viewtarget-only"
        # 兜底：只按名字外的结构比较
        for c in cams:
            if pov_fov is not None and abs(c["fov"] - pov_fov) < 0.05:
                return c, pov_fov, "pov-fov-match"
    return (cams[0] if cams else None), pov_fov, "fallback"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--probe", action="store_true", default=True,
                    help="用写入特征值的方式判定活跃相机（默认开）")
    ap.add_argument("--no-probe", dest="probe", action="store_false")
    args = ap.parse_args()

    pid = args.pid or find_pid()
    if not pid:
        raise SystemExit("找不到游戏进程")

    h = open_proc(pid)
    cams, pcms = scan(h, verbose=True)
    real_pcms = [p for p in pcms if plausible_ptr(p["viewtarget"])]
    pcm = real_pcms[0] if real_pcms else None

    active, pov_fov, how = pick_active(h, cams, pcm)
    if args.probe:
        hw = open_proc(pid, write=True)
        pc, pf = probe_active(hw, cams, pcm)
        if pc:
            active, pov_fov, how = pc, pf, "probe-write"

    if args.json:
        print(json.dumps({
            "pid": pid,
            "pcm": pcms,
            "cameras": cams,
            "active": active,
            "pov_fov": pov_fov,
            "how": how,
        }))
        return 0

    print(f"pid={pid}")
    for p in pcms:
        print(f"PCM    0x{p['addr']:X}  DefaultFOV={p['default_fov']:.1f}  ViewTarget=0x{p['viewtarget']:X}")
    if pov_fov is not None:
        print(f"POV FOV（渲染侧）= {pov_fov:.2f}")
    if args.all:
        for c in cams:
            tag = "  <== 活跃" if active and c["addr"] == active["addr"] else ""
            print(f"cam    0x{c['addr']:X}  FOV={c['fov']:.2f}  outer=0x{c['outer']:X}{tag}")
    if active:
        print(f"活跃相机 0x{active['addr']:X}  FOV={active['fov']:.2f}  (匹配方式: {how})")
        print(f"写入地址 0x{active['addr'] + FOV_OFF:X}  (camera+0x{FOV_OFF:X})")
    else:
        print("没找到相机 —— 确认游戏在自由相机/摄影界面")
    return 0


if __name__ == "__main__":
    sys.exit(main())


# ---------------------------------------------------------------- 快路径
VT_OFF = fd.OFF_PCM_VIEWTARGET   # 别名：面板侧有引用（fd_cam_panel 用 camfind.VT_OFF）


def cameras_from_viewtarget(h, pcms, size=0x6000):
    """从 PCM 的 ViewTarget 角色对象里直接找相机组件，不扫整个堆（毫秒级）。"""
    out = []
    seen = set()
    for pcm in pcms or []:
        raw = read(h, pcm["addr"] + VT_OFF, 8)
        vt = q64(raw, 0) if raw else 0
        if not plausible_ptr(vt) or vt in seen:
            continue
        seen.add(vt)
        data = read(h, vt, size)
        if not data:
            continue
        for off in range(0, len(data) - 8, 8):
            p = q64(data, off)
            if not plausible_ptr(p) or p in seen:
                continue
            if not is_private(h, p):
                continue
            seen.add(p)
            head = read(h, p, HEAD_LEN)
            if not head:
                continue
            fov = f32(head, FOV_OFF)
            ar = f32(head, AR_OFF)
            if 5.0 <= fov <= 179.0 and 0.5 <= ar <= 3.0:
                out.append({"addr": p, "fov": fov, "aspect": ar,
                            "class": q64(head, fd.OFF_CLASS), "outer": q64(head, fd.OFF_OUTER),
                            "vt": vt})
    return out
