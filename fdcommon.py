#!/usr/bin/env python3
"""
fdcommon.py — 共享底层：进程发现、游戏目录解析、内存读写、已实测偏移。

所有工具（camfind / fov_unlock / fov_remap / fd_cam_panel / memdump）都从这里取，
不再各自硬编码绝对路径。换机器、换安装位置时只需要：

    1) 设置环境变量  FDMOD_GAME_DIR=<游戏根目录>
    2) 或者在 kit 根目录放 config.json:  {"game_dir": "<游戏根目录>"}
    3) 或者给每个工具传 --game-dir

三者都没有时，会尝试从正在运行的游戏进程反查 exe 路径。

已实测偏移（UE 5.7.4-0+UE5 / Fallen Doll Paralogue 0.9.x，Win64 Shipping 单体包）：
    UCameraComponent::FieldOfView        +0x250   float
    UCameraComponent::AspectRatio        +0x274   float (16:9 = 1.777778)
    APlayerCameraManager::DefaultFOV     +0x2C8   float (90.0)
    APlayerCameraManager::DefaultOrthoW. +0x2D0   float (512.0)
    APlayerCameraManager::DefaultAspect  +0x2D8   float (1.333333)
    APlayerCameraManager::ViewTarget     +0x340   AActor* (FViewTarget.Target)
    APlayerCameraManager::POV.FOV        +0x380   float (渲染侧实际值)

这些偏移是结构性偏移（编译器按类布局排出来的），同一版本游戏+同一编译器下稳定；
游戏大版本更新后需要重新校验（见 docs/技术文档.md 的「偏移怎么重测」）。
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess

GAME_EXE = "Paralogue-Win64-Shipping.exe"
BIN_SUBDIR = os.path.join("Paralogue", "Binaries", "Win64")
ENV_GAME_DIR = "FDMOD_GAME_DIR"
KIT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# ---- 偏移表（唯一权威来源；其他文件一律引用这里的常量，不写字面量）
OFF_CLASS = 0x10            # UObjectBase::ClassPrivate
OFF_OUTER = 0x20            # UObjectBase::OuterPrivate
OFF_FOV = 0x250
OFF_ASPECT = 0x274
OFF_PCM_DEFAULT_FOV = 0x2C8
OFF_PCM_ORTHO = 0x2D0
OFF_PCM_ASPECT = 0x2D8
OFF_PCM_VIEWTARGET = 0x340
OFF_PCM_POV_FOV = 0x380

FOV_MIN_PLAUSIBLE, FOV_MAX_PLAUSIBLE = 0.5, 179.0

# ---- Win32
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.OpenProcess.restype = ctypes.c_void_p
k32.ReadProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = ctypes.c_bool
k32.WriteProcessMemory.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.restype = ctypes.c_bool

PROC_QUERY_INFORMATION = 0x0400
PROC_VM_READ = 0x0010
PROC_VM_WRITE = 0x0020
PROC_VM_OPERATION = 0x0008
PROC_QUERY_LIMITED_INFORMATION = 0x1000

HEAP_LO, HEAP_HI = 0x10000, 0x7FFFFFFFFFFF


def _iter_processes():
    """纯 ctypes 枚举进程（不调 tasklist，不会弹控制台窗口）。"""
    import ctypes.wintypes as wt
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wt.DWORD),
            ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
            ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
            ("szExeFile", ctypes.c_char * 260),
        ]

    k32i = ctypes.WinDLL("kernel32", use_last_error=True)
    k32i.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32i.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32i.Process32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32)]
    k32i.Process32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32)]
    k32i.CloseHandle.argtypes = [ctypes.c_void_p]

    snap = k32i.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == ctypes.c_void_p(-1).value:
        return
    try:
        pe = PROCESSENTRY32()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32i.Process32First(snap, ctypes.byref(pe))
        while ok:
            yield pe.th32ProcessID, pe.szExeFile.decode("mbcs", "replace")
            ok = k32i.Process32Next(snap, ctypes.byref(pe))
    finally:
        k32i.CloseHandle(snap)


def find_pid(exe_name=GAME_EXE):
    """按进程名找 pid；不用 tasklist，不会弹控制台窗口。"""
    want = exe_name.lower()
    for pid, name in _iter_processes():
        if name.lower() == want:
            return pid
    return None


def open_proc(pid, write=False):
    """VirtualQueryEx 需要 PROCESS_QUERY_INFORMATION，缺了会 ERROR_ACCESS_DENIED(5)。"""
    access = PROC_QUERY_INFORMATION | PROC_VM_READ
    if write:
        access |= PROC_VM_WRITE | PROC_VM_OPERATION
    h = k32.OpenProcess(access, False, pid)
    return h or None


def read(h, addr, size):
    buf = (ctypes.c_char * size)()
    n = ctypes.c_size_t(0)
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
        return None
    return bytes(buf[: n.value])


def write_f32(h, addr, value):
    buf = ctypes.create_string_buffer(struct.pack("<f", float(value)), 4)
    n = ctypes.c_size_t(0)
    return bool(k32.WriteProcessMemory(h, ctypes.c_void_p(addr), buf, 4, ctypes.byref(n)))


def rd_f32(h, addr):
    b = read(h, addr, 4)
    return struct.unpack("<f", b)[0] if b else None


def rd_u64(h, addr):
    b = read(h, addr, 8)
    return struct.unpack("<Q", b)[0] if b else None


def plausible_ptr(p):
    return isinstance(p, int) and HEAP_LO <= p <= HEAP_HI


def process_exe_path(pid):
    """QueryFullProcessImageNameW，不依赖 WMI。"""
    h = k32.OpenProcess(PROC_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        size = wt.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        k32.CloseHandle(h)


def load_config():
    try:
        with open(KIT_CONFIG, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg):
    tmp = KIT_CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, KIT_CONFIG)


def resolve_game_dir(cli=None, verbose=False):
    """按 cli -> 环境变量 -> config.json -> 运行中的进程 的顺序找游戏根目录。"""
    def norm(p):
        if not p:
            return None
        p = os.path.abspath(os.path.expanduser(p))
        # 允许直接给到 Binaries\Win64 或 exe
        if p.lower().endswith(".exe"):
            p = os.path.dirname(p)
        if os.path.basename(p).lower() == "win64":
            p = os.path.dirname(os.path.dirname(os.path.dirname(p)))
        elif os.path.basename(p).lower() == "binaries":
            p = os.path.dirname(os.path.dirname(p))
        return p if os.path.isdir(p) else None

    if cli:
        got = norm(cli)
        if got:
            return got
        if verbose:
            print(f"[fdcommon] --game-dir 无效：{cli}", flush=True)

    env = os.environ.get(ENV_GAME_DIR)
    if env:
        got = norm(env)
        if got:
            return got
        if verbose:
            print(f"[fdcommon] 环境变量 {ENV_GAME_DIR} 无效：{env}", flush=True)

    cfg = load_config()
    if cfg.get("game_dir"):
        got = norm(cfg["game_dir"])
        if got:
            return got
        if verbose:
            print(f"[fdcommon] config.json 里的 game_dir 无效：{cfg['game_dir']}", flush=True)

    pid = find_pid()
    if pid:
        exe = process_exe_path(pid)
        if exe:
            got = norm(exe)
            if got:
                if verbose:
                    print(f"[fdcommon] 从进程反查到游戏目录：{got}", flush=True)
                return got
    if verbose:
        print("[fdcommon] 无法确定游戏目录，请给 --game-dir 或设置 " + ENV_GAME_DIR, flush=True)
    return None


def objects_path(game_dir):
    """UE4SS F6 manifest 的默认位置（可选路径，不是必需）。"""
    if not game_dir:
        return None
    return os.path.join(game_dir, BIN_SUBDIR, "FDCamDump", "objects.txt")


def exe_path(game_dir):
    return os.path.join(game_dir, BIN_SUBDIR, GAME_EXE) if game_dir else None
