#!/usr/bin/env python3
"""
tests/test_paths.py — 不依赖游戏、不依赖 Windows API 的纯逻辑测试。

验证「换机器能用」这件事：--game-dir / 环境变量 / exe 路径 / Win64 路径
都要能归一化成同一个游戏根目录。

    python tests/test_paths.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import fdcommon as fd      # noqa: E402


def main() -> int:
    root = tempfile.mkdtemp(prefix="fdtest_")
    bin64 = os.path.join(root, "Paralogue", "Binaries", "Win64")
    os.makedirs(bin64)
    exe = os.path.join(bin64, fd.GAME_EXE)
    open(exe, "w").close()

    ok = True

    def check(name, got, want):
        nonlocal ok
        good = os.path.normcase(got or "") == os.path.normcase(want)
        ok &= good
        print(f"  {name:<14} -> {got}\n{'':<17}   {'OK' if good else 'MISMATCH (want %s)' % want}")

    print("[*] 各种输入都要归一化到游戏根目录")
    check("根目录", fd.resolve_game_dir(root), root)
    check("Win64 目录", fd.resolve_game_dir(bin64), root)
    check("exe 路径", fd.resolve_game_dir(exe), root)
    check("尾部斜杠", fd.resolve_game_dir(root + os.sep), root)

    print("[*] 环境变量优先级")
    os.environ[fd.ENV_GAME_DIR] = root
    check("FDMOD_GAME_DIR", fd.resolve_game_dir(None), root)
    os.environ.pop(fd.ENV_GAME_DIR, None)

    print("[*] objects 路径生成")
    op = fd.objects_path(root)
    want_suffix = os.path.join("Paralogue", "Binaries", "Win64", "FDCamDump", "objects.txt")
    good = op.endswith(want_suffix)
    ok &= good
    print(f"  objects        -> {op}\n{'':<17}   {'OK' if good else 'MISMATCH'}")

    print("[*] 偏移表自洽性")
    for name in ("OFF_FOV", "OFF_ASPECT", "OFF_PCM_DEFAULT_FOV", "OFF_PCM_VIEWTARGET",
                 "OFF_PCM_POV_FOV"):
        v = getattr(fd, name)
        good = isinstance(v, int) and v > 0
        ok &= good
        print(f"  {name:<22} = 0x{v:X}  {'OK' if good else 'BAD'}")

    print("\nRESULT:", "ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
