# FGO 一键抓包（导入 Chaldea）

双击 bat 或 Quicker 触发 → 自动完成**代理设置 + 证书安装 + mitmdump 抓包 + FGO 冷启动 + 自动点登录** → `toplogin` JSON 自动进剪贴板 → 导入 Chaldea。全程只在登录页出现时自动点击，无需其他任何交互。

## 特性

- **全自动**：从触发到抓包完成，无需键盘鼠标操作（登录按钮由 ADB 自动点击）
- **证书自动安装**：检测到模拟器未信任 mitmproxy CA 时，自动 root 写入系统信任库——Android 9 写 legacy 库，Android 10+ 写 APEX + legacy 双库（前提：模拟器已开 root，见下方「安装」）
- **代理自检 + 守门**：设置代理后读回校验（最多 3 轮），全部失败则报错退出**绝不带着失效代理启动 FGO**
- **模拟器自动适配**：按盘符扫描 `<盘>:\leidian\LDPlayer*\adb.exe` 与 `<盘>:\software\leidian\LDPlayer*\adb.exe`（重装系统换盘符也不怕），再加硬编码路径与 PATH 三级兜底
- **FGO 零打扰**：抓包成功后**不删代理、不杀进程、不重启 FGO**，由后台守护等你退出 FGO 后自动还原环境（避免游戏连接被切断）
- 抓到数据自动复制剪贴板 + 打开 `toplogin/` 文件夹

## 工作原理

```
FGO (模拟器内) ──HTTPS──→ Android 系统代理 ──→ mitmdump (本机:18080)
                                                  │ 解密，匹配 /login/top
                                                  ▼
                                        toplogin_*.json ──→ Chaldea 导入
```

- FGO（Unity）读 Android 老 API `settings global http_proxy`，值必须是 `ip:port`
- `/login/top` 只在 **冷启动后走完整登录流程**时发送一次（「菜单→设置→重新登录」不会触发）
- 脚本因此用 `force-stop` + `am start` 保证触发；抓完后**不再动 FGO**
- FGO 是 Unity 渲染，`uiautomator` 对游戏内按钮无效 → 用 ADB 盲点屏幕中央（自动读取 `wm size` 适配分辨率）

## 环境要求

| 依赖 | 要求 |
|---|---|
| Windows | 10 / 11 |
| 模拟器 | 雷电 9 / 12 / 14（已验证），**必须开启 root** |
| FGO | 国服 360 渠道服 `com.bilibili.fgo.qihoo`（其他渠道见配置） |
| Python | 3.10+（安装时务必勾选 **Add to PATH**） |
| mitmproxy | `pip install mitmproxy` |
| Chaldea | 用于导入抓包数据 |

> **CA 证书是必需的**：国服 360 渠道服的 SDK 会严格校验证书，不装 CA 时 HTTPS 握手会失败并表现为「连接失败」。脚本会自动安装——**前提是模拟器已开 root**。

## 安装

### 1. 装 Python + mitmproxy

```bash
pip install mitmproxy
```

> ⚠️ **Windows 商店桩坑**：重装系统后裸敲 `python` 可能弹出 Microsoft Store——这是「应用执行别名」把 `python` 指向了商店桩。修复：安装真实 Python 时勾选 *Add to PATH*，再到 设置 → 应用 → 高级应用设置 → 应用执行别名，把 `python.exe` / `python3.exe` 的开关关掉。验证：新开终端跑 `where python`，第一行应是真实 Python 路径。

### 2. 模拟器开启 ROOT 权限（二选一）

**GUI**：多开管理器 → 对应模拟器 → 设置 → 勾选「ROOT 权限」→ 重启模拟器。

**命令行**（可脚本化，推荐）：

```bat
ldconsole modify --index 0 --root 1
ldconsole reboot  --index 0
```

> `--index` 是多开编号，主实例为 0。多开环境下每个实例都要单独开 root。

### 3. 装好 FGO，下载本仓库到任意目录

```
FGOcap/
├── tools/
│   ├── 一键抓包.bat        ← 唯一入口
│   ├── auto_capture.py     ← 主脚本
│   ├── _cleanup_daemon.py  ← 清理守护（自动拉起）
│   ├── fgoaddon.py         ← mitmproxy 插件（来自 Chaldea）
│   └── wait_popup_win32.py ← 进度弹窗
└── README.md / LICENSE
```

## 重装系统 / 新机速查清单

重装 Windows 或换机后，按顺序补齐这 5 项即可恢复抓包能力（每一项都是踩过坑验证过的）：

| # | 项目 | 操作 | 验证 |
|---|---|---|---|
| 1 | 真实 Python | python.org 安装包，勾 **Add to PATH**；关闭应用执行别名的 python 桩 | `where python` 第一行非 WindowsApps |
| 2 | mitmproxy | `pip install mitmproxy` | `mitmdump --version` 有输出 |
| 3 | 雷电模拟器 | 安装位置任意（脚本按盘符自动扫）；建议把安装目录加入 PATH | `where adb` 命中模拟器目录 |
| 4 | ROOT 权限 | `ldconsole modify --index 0 --root 1` + `ldconsole reboot --index 0` | `adb shell su -c id` 返回 `uid=0(root)` |
| 5 | 重开终端 | PATH 是进程启动时读取的，改完 PATH 必须**重开终端**再跑脚本 | — |

> CA 证书无需手动装：首次运行脚本时会自动检测并写入模拟器（需 root）。

## 使用

1. 启动模拟器（等完全开机，**不要先开 FGO**）
2. 双击 `一键抓包.bat`
3. 弹窗显示「✓ 抓包环境已就绪」→ FGO 被自动启动 → 登录按钮被 ADB 自动点击
4. 公告页加载完成 = 抓包成功 → 弹窗自动关、剪贴板已有数据、文件夹已打开
5. Chaldea → 导入 → Https抓包 → 选账号 → **从剪贴板**

**Quicker**：「运行或打开」步骤直接指向 `tools\一键抓包.bat`（参数留空，勾「失败后停止」）。
脚本内部会等待模拟器就绪（最多 2 分钟），可与「启动模拟器」步骤并行。

> ⚠️ **工程放在网络映射盘（NAS）上时**：Quicker 以管理员身份运行的话，`net use X:` 建的盘符在**提权会话里不存在** → 找不到 bat，Quicker 静默什么都不做。见「常见问题」里那两条。

## 配置

包名**自动探测**（列出含 `fate`/`fgo` 的包 → 按候选表挑 → 自动查 launcher activity），一般不用管。

需要手动指定时，把 `tools/config.ini.example` 复制为 `tools/config.ini`：

```ini
package=com.bilibili.fgo.qihoo
```

| 区服 | 包名 |
|---|---|
| 国服 360 渠道服（默认） | `com.bilibili.fgo.qihoo` |
| 国服 B 服 | `com.bilibili.fatego` |
| 日服 / 台服 | `com.aniplex.fategrandorder` |

其他参数在 `tools/auto_capture.py` 顶部：`PORT = 18080`（抓包端口）。

## 常见问题

| 问题 | 原因 / 解决 |
|---|---|
| 一直抓不到 | 看 `tools/mitmdump.log`：无 FGO 流量 = 代理未生效；有流量无 `login/top` = FGO 没走登录流程（需冷启动） |
| FGO 报「连接失败」 | 模拟器未信任 CA（360 SDK 校验）→ 确认 root 已开（验证命令见速查清单 #4），重跑脚本会自动补装 |
| 弹「CA 写入模拟器失败」但 root 已开 | 老版本脚本的 `su -c` 引号 bug（2026-09-13 已修复），更新 `auto_capture.py` 即可；或手动装：`adb push %USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem /data/local/tmp/c8750f0d.0` 后 `adb shell "su -c 'cp /data/local/tmp/c8750f0d.0 /system/etc/security/cacerts/'"` + `chmod 644` + 重启模拟器 |
| 弹「未找到模拟器Adb」 | 模拟器未装或不在常见路径：确认已装雷电并开机；或把安装目录加入 PATH 后**重开终端** |
| 弹「未找到 mitmdump 可执行文件」 | 未装 mitmproxy：`pip install mitmproxy`；装完重开终端 |
| 模拟器上不了网 | 代理残留：执行 `adb shell settings delete global http_proxy` + 删除 `global_http_proxy_host` / `global_http_proxy_port`。**严禁用 `settings put global http_proxy :0`**（空值会让 FGO 直连 443，抓不到包） |
| mitmdump 启动失败 | `pip install mitmproxy`，确认 `mitmdump --version` 可运行 |
| **窗口刷屏「'xxx' 不是内部或外部命令，也不是可运行的程序或批处理文件」** | `一键抓包.bat` 被编辑器存成了 **LF 换行**（Unix）。cmd.exe 的批处理解析器只认 **CRLF**，LF 会把行切错、`goto` / `for` 全部失效，于是每一行都报这个错。修复：换行符改回 CRLF——VS Code 右下角把 `LF` 切成 `CRLF`，或 `python -c "p=r'tools/一键抓包.bat';d=open(p,'rb').read().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n');open(p,'wb').write(d)"`。**改动 `tools/一键抓包.bat` 后务必确认它仍是 CRLF** |
| **Quicker 触发毫无反应（连 cmd 窗口都不出现）** | 工程在映射盘（如 `X:\`）上、而 Quicker 以**管理员身份**运行时：`net use` 建的盘符只属于非提权会话，提权上下文里 `X:` 根本不存在，ShellExecute 找不到文件 → 静默失败、Quicker 也不报错。两条路：① 放一个**本地启动器** `FGOcap_launch.cmd`（放在本地盘，Quicker 第 1 步指向它）：先 `if not exist` 判断目标，缺失就自己 `net use X: \\nas\share`，再 `call` 真正的 bat，并把每次运行写进日志，便于事后定位；② 导入 `EnableLinkedConnections=1`（`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`，DWORD）后**重启**，让映射盘对提权进程可见，之后 Quicker 可直连 `X:\...` |

## 风险提示

- 抓包属于中间人攻击，仅在专用模拟器上操作
- FGO 官方协议不允许模拟器/抓包，**有封号风险，后果自负**
- `toplogin/*.json` 含账号登录信息，**切勿分享他人**

## 致谢 / License

- [Chaldea](https://github.com/chaldea-center/chaldea) —— 数据导入工具，`fgoaddon.py` 源自其官方工具包
- [mitmproxy](https://mitmproxy.org/) —— HTTPS 抓包核心（MIT License）

本仓库脚本（`auto_capture.py` / `_cleanup_daemon.py` / `wait_popup_win32.py` / `一键抓包.bat`）采用 MIT License；`fgoaddon.py` 版权归 Chaldea 项目所有。
