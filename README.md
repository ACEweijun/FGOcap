# FGO 一键抓包（导入 Chaldea）

> 双击 bat 或 Quicker 触发 → 自动完成 **模拟器代理设置 + mitmdump 抓包 + FGO 自动启动** → 你在登录页点一下【登录】→ `toplogin` JSON 自动保存到剪贴板并打开文件夹 → 导入 Chaldea。

## ✨ 效果

- **一键全自动**：从触发到抓包完成，你只需要在 FGO 登录页点一下【登录】按钮
- **原生弹窗实时进度**：弹窗显示「准备中… → 启动抓包… → 请登录 FGO → 已等待 N 秒」，抓到数据自动关闭
- **零配置抓包**：自动探测模拟器 adb、自动设置 Android 代理、自动重启 FGO 触发登录接口
- 抓包数据**自动复制到剪贴板** + 自动打开 `toplogin/` 文件夹，导入 Chaldea 只要两步

## 🔧 工作原理

```
FGO (模拟器内)
   │  HTTPS 请求
   ▼
Android 系统代理 (自动设置) ──→ mitmdump (本机 18080 端口)
                                   │  解密 HTTPS，匹配 /login/top
                                   ▼
                        toplogin_CN_qudao_*.json ──→ Chaldea 导入
```

- FGO（Unity 客户端）读取 Android 老 API `settings global http_proxy`，值必须是 `ip:port` 格式
- `/login/top` 接口只在 **APP 冷启动后走完整登录流程**时发送一次；「菜单→设置→重新登录」不会触发（客户端用本地缓存 session 重连）
- 脚本因此采用**无条件重启 FGO**（`force-stop` + `am start`）来保证触发登录接口
- 国服渠道服**无证书绑定**，无需安装 mitmproxy CA 即可解密（若你的区服/版本校验证书，见 FAQ）

## 📋 环境要求

| 依赖 | 要求 | 说明 |
|---|---|---|
| Windows | 10/11 | 需要能运行 Android 模拟器 |
| 模拟器 | LDPlayer9（推荐）/ MuMu / Nox，**已 root** | 自动探测常见安装路径 |
| FGO | 国服 360 渠道服 `com.bilibili.fgo.qihoo` | 其他渠道服需改 `FGO_PACKAGE` 常量 |
| Python | 3.10+ | 安装时勾选 **Add to PATH** |
| mitmproxy | `pip install mitmproxy` | 提供 mitmdump 命令 |
| Chaldea | 任意平台版本 | 用于导入抓包数据 |

## 🚀 安装（3 步）

### 1. 安装 Python + mitmproxy

```bash
# 安装 Python 3.10+（官网下载，勾选 Add to PATH）
# https://www.python.org/downloads/

# 然后安装 mitmproxy
pip install mitmproxy
```

### 2. 安装模拟器 + FGO

1. 安装 LDPlayer9（开启 Root 权限：设置 → 其他 → Root 权限）
2. 把 FGO 360 渠道服 APK 装进模拟器
3. 确认 adb 可用（模拟器默认开启 adb 调试）

### 3. 下载本仓库

把整个仓库解压到任意目录（例如 `D:\FGOcap`），目录结构：

```
FGOcap/
├── 一键抓包.bat        ← 双击运行（有弹窗、有进度）
├── 守护抓包.bat        ← 常驻监控模式（可选）
└── tools/
    ├── auto_capture.py        ← 主脚本
    ├── fgoaddon.py            ← mitmproxy addon（匹配 /login/top）
    ├── wait_popup_win32.py    ← 原生 Win32 弹窗
    ├── quicker_trigger.py     ← Quicker 触发入口（可选）
    └── watchdog.py            ← 守护模式（可选）
```

## ▶️ 使用

### 方式 1：双击 bat（最简单）

1. 先启动模拟器，等它完全开机（不启动 FGO）
2. 双击 `一键抓包.bat`
3. 弹窗显示「✓ 抓包环境已就绪」后，**模拟器里的 FGO 会被脚本自动启动**
4. 等 FGO 到登录页，点【登录】
5. 公告页加载完成 → 抓包成功 → 弹窗自动关 → 剪贴板已有数据、文件夹已打开

### 方式 2：Quicker 动作（推荐，可串联自己的工具流）

用 `pythonw.exe` 运行 `tools/quicker_trigger.py`（无控制台窗口，只有弹窗），
可以在 Quicker 里和你的其他工具（Chaldea、桌面小工具、游戏辅助等）**串联成一步**。

**最简配置**（「运行或打开」步骤）：

| 字段 | 值 |
|---|---|
| 路径或命令 | `C:\你的Python安装目录\pythonw.exe` |
| 参数 | `C:\你的FGOcap路径\tools\quicker_trigger.py` |
| 失败后停止 | ✅ 勾选 |
| 激活窗口快捷键 | 不勾 |

#### 进阶：串到你的工具流里

如果你的 Quicker 动作原本就是多步骤串联（启动数据导入工具、辅助工具、桌面快捷方式等），
**把 FGOcap 抓包作为其中一个步骤即可**。典型串联示例：

| 步骤 | 类型 | 目标 | 说明 |
|---|---|---|---|
| 1 | 运行/打开 | `Chaldea.exe` | 数据导入工具（待会要用它） |
| 2 | 运行/打开 | 你的辅助工具 `.cmd` / 快捷方式 | 如截图工具、桌面小工具等 |
| 3 | 等待时间 | `7000` ms | 给前面工具启动时间 |
| 4 | 运行/打开 | `pythonw.exe` + 参数 `quicker_trigger.py` | **启动 FGOcap 自动抓包** |

**完整时序**（步骤 4 触发后自动发生）：

```
弹窗立即出现（<2s），后台线程并行：
  [1/5] 探测模拟器
  [2/5] 等待模拟器完全启动
  [3/5] 清理残留代理
  [4/5] 启动 mitmdump + 设置 Android 代理
  [5/5] 脚本自动启动 FGO（force-stop + am start）

→ 你看到 FGO 在登录页 → 点【登录】 → toplogin 落到 toplogin/ → 弹窗自动关
→ 剪贴板已有 JSON、文件夹已打开 → 切回 Chaldea 一键导入
```

#### 为什么用 `pythonw.exe` 而不是 `python.exe`

| 选项 | 控制台 | 用途 |
|---|---|---|
| `pythonw.exe` | 无 | **抓包主脚本**（只需要弹窗，不要黑色窗口） |
| `python.exe` | 有 | 排错时手动跑，看实时 stdout |

**抓包成功的 3 个关键设置**（Quicker 步骤面板）：

- ✅ **失败后停止** — 抓包脚本异常时中断整个动作链
- ❌ **以管理员身份运行** — Quicker 已是管理员则不需要
- ❌ **激活窗口** — 弹窗自带 topmost，会自动抢焦点

#### 抓包失败排错

看 `tools/quicker_trigger.log` 末尾；和方式 1 一样，可对照 `tools/mitmdump.log` 确认 FGO 流量是否进代理。

### 导入 Chaldea

1. 打开 Chaldea → 导入 → Https抓包
2. 选账号 → 导入 → **从剪贴板**（或从文件选 `tools/toplogin/toplogin_CN_qudao_*.json`）

## ⚙️ 配置

打开 `tools/auto_capture.py` 顶部：

```python
PORT = 18080                          # mitmdump 监听端口
LDPLAYER_PATHS = [...]                # adb.exe 搜索路径（自动探测，一般不用改）
FGO_PACKAGE = "com.bilibili.fgo.qihoo"  # 目标渠道服包名
```

## ❓ 常见问题

| 问题 | 原因 / 解决 |
|---|---|
| 弹窗一直「检测中」抓不到 | 看 `tools/mitmdump.log`：若无 FGO 流量 = 代理未生效；若有流量无 `login/top` = FGO 没走登录（用「菜单→设置→重新登录」不会触发，需重启 APP） |
| 模拟器断网 / 上不了网 | 脚本退出时已还原代理；若手动中断过，执行：`adb shell settings put global http_proxy :0` + 删除 `global_http_proxy_host/port` |
| 提示证书错误 | 你的 FGO 版本校验证书：把 mitmproxy CA（`~/.mitmproxy/mitmproxy-ca-cert.pem`）装进模拟器系统信任库（需 root + 磁盘可写） |
| mitmdump 启动失败 | 先 `pip install mitmproxy`，确认命令行能跑 `mitmdump --version` |
| 想要其他区服 | 修改 `FGO_PACKAGE`，并确认 fgoaddon 的区服识别（`CN_qudao` 等） |

## ⚠️ 风险提示

- 抓包属于**中间人攻击**，仅在专用模拟器上操作
- FGO 官方协议不允许模拟器/抓包行为，**有封号风险，后果自负**
- `toplogin/*.json` 含账号登录信息，**切勿分享给他人**
- 抓包数据不含账密，仅游戏资源数据

## 🙏 致谢

- [Chaldea](https://github.com/chaldea-center/chaldea) —— 数据导入工具，`fgoaddon.py` 源自其官方工具包
- [mitmproxy](https://mitmproxy.org/) —— HTTPS 抓包核心（MIT License）

## 📄 License

本仓库脚本（`auto_capture.py` / `wait_popup_win32.py` / `quicker_trigger.py` / `watchdog.py`）采用 MIT License；
`fgoaddon.py` 版权归 Chaldea 项目所有。
