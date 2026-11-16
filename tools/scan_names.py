#!/usr/bin/env python3
"""
scan_names.py — 从 UE 单体 exe 的 FName 池里捞相机相关的符号名。

UE 反射需要 FName 池常驻，所以 Shipping 单体包里类名 / 属性名 / UFUNCTION 名
都还在 .rdata 里以 ASCII 明文出现。这一步不需要跑游戏，先把候选名单列出来，
再用 UE4SS 的 jmap 精确对齐（类 → 属性偏移 → CDO 默认值）。

用法：
    python scan_names.py "<GAME_DIR>\\Paralogue\\Binaries\\Win64\\Paralogue-Win64-Shipping.exe" -o names.txt
"""
import argparse
import re
import sys

KEYWORDS = [
    "fov", "fieldofview", "camera", "pitch", "yaw", "roll", "zoom",
    "springarm", "armlength", "boom", "socketoffset", "targetoffset",
    "photo", "freecam", "spectator", "orbit", "viewtarget", "cameraoffset",
    "clamp", "orbitdistance", "distancetofocus", "cinematic", "lens",
]

PAT = re.compile(rb"[\x20-\x7e]{4,120}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("binary")
    ap.add_argument("-o", "--out", default="names.txt")
    ap.add_argument("-k", "--keywords", default=",".join(KEYWORDS))
    args = ap.parse_args()

    kws = [k.strip().lower() for k in args.keywords.split(",") if k.strip()]

    with open(args.binary, "rb") as fh:
        data = fh.read()
    print(f"[*] {args.binary}  {len(data):,} bytes", file=sys.stderr)

    hits = {}
    for m in PAT.finditer(data):
        s = m.group().decode("ascii", "ignore")
        low = s.lower()
        for k in kws:
            if k in low:
                hits.setdefault(s, 0)
                hits[s] += 1
                break

    # 过滤明显是噪音的长句 / 代码片段
    def plausible(s: str) -> bool:
        if len(s) > 64:
            return False
        if s.count(" ") > 2 or "://" in s:
            return False
        return True

    rows = sorted(((s, c) for s, c in hits.items() if plausible(s)))
    with open(args.out, "w", encoding="utf-8") as out:
        for s, c in rows:
            out.write(f"{c}\t{s}\n")
    print(f"[*] {len(rows)} unique keyword-bearing names -> {args.out}", file=sys.stderr)

    # 控制台只打最像「属性 / 函数 / 类」的那批（含驼峰且不含路径分隔符）
    interesting = [s for s, _ in rows
                   if ("/" not in s and "_" in s or re.search(r"[a-z][A-Z]", s))
                   and len(s) <= 48]
    for s in interesting[:400]:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
