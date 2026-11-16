# Fallen Doll — 相机 FOV / 垂直视角解锁工具包

给 *Fallen Doll* 用的进程外相机控制工具：
解除摄影/自由/第一人称相机那条「垂直视角」滑条 **20–90°** 的硬上限，并提供一个自带
60–150° 滑条的常驻控制面板。

**不注入 DLL，不修改游戏文件，不改 pak。** 全部通过 `ReadProcessMemory` /
`WriteProcessMemory` 在进程外完成，关掉工具立刻恢复原状。

---

## 快速开始

```powershell
# 0) 复制配置（或者不改，直接给命令加 --game-dir）
copy config.example.json config.json    # 然后编辑里面的 game_dir

# 1) 起游戏，进到自由相机 / 摄影界面
# 2) 起面板
python tools\fd_cam_panel.py
#    没有配置也没关系：工具会自动从正在运行的游戏进程反查安装目录
```

面板：自带 60–150° 滑条、「跟随游戏滑条」模式、四个全局热键。

| 热键 | 作用 |
|---|---|
| `Ctrl+F7` / `Ctrl+F8` | FOV −5° / +5° |
| `Ctrl+F9` | 切换 跟随游戏滑条 / 手动 |
| `Ctrl+F10` | 显示 / 隐藏面板 |

只想要「游戏滑条本身变成 60–150」的话，用 `tools/fov_remap.py`（见下文）。

---

## 路径怎么给

优先级：`--game-dir` / `-GameRoot` → 环境变量 `FDMOD_GAME_DIR` → `config.json` → **从正在运行的游戏进程反查**。

`*.ps1` / `*.bat` 如果本身就放在游戏根目录（与 `Paralogue\` 同级），会自动认这个目录，不用传参；
放在别处就显式给 `-GameRoot` 或设 `FDMOD_GAME_DIR`。`*.ps1` 另外可用 `-PythonExe` 指定解释器。

## 目录结构

```
fdcommon.py               共享底层：进程发现、游戏目录解析、内存读写、偏移表
tools/
  fd_cam_panel.py         常驻控制面板（tkinter 置顶小窗 + 全局热键）
  fov_remap.py            把游戏滑条的 20–90 重映射成任意区间（默认 60–150）
  fov_unlock.py           固定值模式：把 FOV 钉在指定值，支持目标值文件热调
  camfind.py              不依赖 UE4SS 的自解析定位器（扫结构签名找相机）
  memdump.py              通用内存工具：dump/floats/find/ptrs/write/snap/diff/scan
  scan_names.py           离线扫 exe 里的 FName 池，找相机相关符号
mod/FDCamDump/            UE4SS Lua mod：只取对象指针与类名（不碰属性，不崩）
bats/                     UE4SS 开关脚本（可选路径用）
启动面板.bat              Windows 双击启动面板（pythonw，无控制台）
使用说明.txt              面向玩家的使用说明（热键、扫描行为、文件清单）
tools/pin_targets.json    定位缓存（运行时生成，同一进程内复用；不入库）
Install-FDCamDump.ps1     安装 UE4SS + FDCamDump mod（可选，只有想走 F6 快路径才需要）
Uninstall-FDCamDump.ps1   移除 UE4SS
Test-Launch.ps1           注入存活实验脚本（逐个开关 mod / hook）
Send-GameKey.ps1          向游戏窗口投真实按键（UE4SS 热键只认真实输入）
Run-Sweep.ps1             重启游戏并在其抢到前台时投键
docs/技术文档.md          开发思路、偏移推导过程、踩坑清单、二次开发指南
config.example.json       配置样例（game_dir）
```

---

## 两种「找相机」的方式

| 方式 | 依赖 | 耗时 | 说明 |
|---|---|---|---|
| **UE4SS manifest** | 需要装 UE4SS，游戏内按一次 `F6` | 0 s | `objects.txt` 里直接给地址，最省事 |
| **自解析 `camfind`** | 不需要任何外部工具 | ~30 s | 扫私有堆找 `PlayerCameraManager` 签名 → `ViewTarget` → 相机组件 |

manifest 里的堆地址**只对当前进程有效**：重启游戏或换关卡后必须重新 `F6`，
否则工具写的是别人的内存。工具会自动检测这种情况并提示；没有 manifest 时
自动退化成 `camfind`。

---

## 用法示例

```powershell
# 面板（推荐日常用）
python tools\fd_cam_panel.py --game-dir "<GAME_DIR>" --fov 110

# 让游戏自己的滑条变成 60–150（松手 0.35s 后生效）
python tools\fov_remap.py --out-min 60 --out-max 150

# 固定 150°，用文件热调：改 tools\fov_target.txt 里的数字即可
python tools\fov_unlock.py --fov 150

# 只读：打印当前 PCM、活跃相机、渲染侧 POV FOV
python tools\camfind.py --all

# 通用内存工具
python tools\memdump.py floats --pid <PID> --addr 0x1234ABCD0 --size 0x400 --lo 5 --hi 180
python tools\memdump.py write  --pid <PID> --addr 0x1234ABCF0 --value 120   # 写后自动回读校验
python tools\memdump.py snap   --pid <PID> --addr 0x1234ABCD0 --size 0x400 --out before.bin
python tools\memdump.py diff   --a before.bin --b after.bin
```

---

## 硬性限制（不是免责声明，是实测踩出来的）

- **必须先进入某个游戏画面**（自由相机 / 摄影 / 第一人称都行）。标题画面和菜单里 `ViewTarget` 为 0，工具会拒绝扫堆并提示。
- 自解析要扫约 8 GB 私有堆，**30 秒左右**，期间游戏会明显卡顿。UE4SS manifest 路径是 0 秒。
- 兜底匹配依赖相机 `+0x274` 的 16:9 AspectRatio 签名；改成非 16:9 分辨率会漏，主路径（ViewTarget）不受影响。
- **大幅改值有风险**：把 SpringArm Length 从 200 直接写到 500 触发过断言崩溃（58.6 MB dmp）。FOV 90→150 实测安全，但仍建议小步长。
- 偏移是 UE 5.7.4 Shipping 的参数，**游戏大版本更新后必须重测**（流程见技术文档第 8 节）。

---

## 致谢

- **第一人称相机（FPV）的切换支持**：来自群友 **@Oxgen** 的贡献 —— 定位不再按名字过滤，
  识别的相机会累积进 `pin_targets` 同时压值，配合 `PCM+0x380` 的 POV 闭环，
  从自由相机切到第一人称后会自动跟上。
- UE4SS 的 5.7 兼容性验证依赖 `v4.0.0-9-gb2d936b9` 这个 experimental 构建。


---
