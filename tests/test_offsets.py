#!/usr/bin/env python3
"""
tests/test_offsets.py — 偏移「单一来源」回归测试。

背景：偏移曾经散落在 camfind.py 里（裸写 0x340/0x380），版本更新时会退化成
"改一处"→"改三处"。现在约定：**偏移只允许在 fdcommon.py 定义**，
其他文件一律引用 fd.OFF_*（camfind 保留 VT_OFF 等别名给面板用）。

本测试做三件事：
  1. 校验 fdcommon 的偏移值与实测值一致（数值被人改动就会失败）
  2. 校验 camfind 的别名与 fd 常量一致
  3. 静态扫描 tools/*.py 与 fdcommon.py，禁止出现偏移字面量（ast 级，不是字符串匹配，
     所以注释和 docstring 里写 0x380 不会误报）

    python tests/test_offsets.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import fdcommon as fd      # noqa: E402
import camfind as cf       # noqa: E402

# UE 5.7.4 Shipping 实测值 —— 改动这里之前先读 docs/技术文档.md §4.5 / §9.2
EXPECTED = {
    "OFF_CLASS": 0x10,
    "OFF_OUTER": 0x20,
    "OFF_FOV": 0x250,
    "OFF_ASPECT": 0x274,
    "OFF_PCM_DEFAULT_FOV": 0x2C8,
    "OFF_PCM_ORTHO": 0x2D0,
    "OFF_PCM_ASPECT": 0x2D8,
    "OFF_PCM_VIEWTARGET": 0x340,
    "OFF_PCM_POV_FOV": 0x380,
}

# 这些数值不允许以字面量形式出现在 fdcommon 以外的代码里
FORBIDDEN_LITERALS = {0x250, 0x274, 0x2C8, 0x2D0, 0x2D8, 0x340, 0x380}


def check_constants() -> bool:
    ok = True
    print("[*] fdcommon 偏移值")
    for name, want in EXPECTED.items():
        got = getattr(fd, name, None)
        good = got == want
        ok &= good
        print("  %-22s = %s  expect 0x%X  %s"
              % (name, ("0x%X" % got) if got is not None else None, want,
                 "OK" if good else "MISMATCH"))
    return ok


def check_aliases() -> bool:
    ok = True
    print("[*] camfind 别名与 fd 常量一致")
    pairs = [
        ("FOV_OFF", fd.OFF_FOV),
        ("AR_OFF", fd.OFF_ASPECT),
        ("PCM_FOV_OFF", fd.OFF_PCM_DEFAULT_FOV),
        ("PCM_VIEWTARGET_OFF", fd.OFF_PCM_VIEWTARGET),
        ("VT_OFF", fd.OFF_PCM_VIEWTARGET),
    ]
    for name, want in pairs:
        got = getattr(cf, name, None)
        good = got == want
        ok &= good
        print("  camfind.%-20s = %s  expect 0x%X  %s"
              % (name, ("0x%X" % got) if got is not None else None, want,
                 "OK" if good else "MISMATCH"))
    head_ok = cf.HEAD_LEN >= fd.OFF_ASPECT + 4
    ok &= head_ok
    print("  camfind.HEAD_LEN            = 0x%X  覆盖 AR_OFF+4  %s"
          % (cf.HEAD_LEN, "OK" if head_ok else "TOO SMALL"))
    return ok


def int_literals(path: str):
    """用 ast 找整数常量，跳过 docstring/注释（它们不是 ast.Constant 在语句里）。"""
    with open(path, "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int):
            yield node.value, getattr(node, "lineno", 0)


def check_no_literals() -> bool:
    ok = True
    targets = [os.path.join(ROOT, "tools", n)
               for n in ("camfind.py", "fd_cam_panel.py", "fov_remap.py",
                         "fov_unlock.py", "memdump.py")]
    print("[*] tools/*.py 不得出现偏移字面量")
    for path in targets:
        if not os.path.exists(path):
            continue
        hits = [(v, ln) for v, ln in int_literals(path) if v in FORBIDDEN_LITERALS]
        good = not hits
        ok &= good
        rel = os.path.relpath(path, ROOT)
        if good:
            print("  %-28s OK" % rel)
        else:
            print("  %-28s FAIL  字面量: %s"
                  % (rel, ", ".join("0x%X@L%d" % (v, ln) for v, ln in hits)))
    return ok


def main() -> int:
    ok = check_constants()
    ok &= check_aliases()
    ok &= check_no_literals()
    print("\nRESULT:", "ALL OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
