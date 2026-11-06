---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 71d6a37e1f48b09cf117b4eaf1cc8472_8973e72ccee811f1b37d525400248c00
    ReservedCode1: /gq3ulJKsS3TZl4IgeA+xPIdOBzqJGyrEXZFdLcojWsf8viaNFMYGM0H6hUnIqVMlJ4RBa9avYOZbLTGQ71gk5aYppR2eCR5GMjQolyAFD3mJUF9FeS7JebL41i2xLi4VdqVuOnnKpp015tgc17wsfQCTXmegboPQ+2ug1rKM6kBjpXcGwTRA17Uvpw=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 71d6a37e1f48b09cf117b4eaf1cc8472_8973e72ccee811f1b37d525400248c00
    ReservedCode2: /gq3ulJKsS3TZl4IgeA+xPIdOBzqJGyrEXZFdLcojWsf8viaNFMYGM0H6hUnIqVMlJ4RBa9avYOZbLTGQ71gk5aYppR2eCR5GMjQolyAFD3mJUF9FeS7JebL41i2xLi4VdqVuOnnKpp015tgc17wsfQCTXmegboPQ+2ug1rKM6kBjpXcGwTRA17Uvpw=
---

# FGO 一键抓包（导入 Chaldea）

两个 Quicker 动作，各自对应一个**开机脚本**：都能自动启动模拟器、清掉上一次残留的代理，然后启动 FGO。区别只在中间要不要抓包。

**关机完全自由**：模拟器想怎么关就怎么关。守护检测到设备离线会立即停 mitmdump 并结束 CMD 窗口；上次没清干净的代理，由下次开机时无条件兜底，不需要任何收工动作。

## 两个开机入口

| 动作 | 入口 | 流程 | 不做的事 |
|---|---|---|---|
| **A 抓包** | `tools/一键抓包.bat` | 启动模拟器 → 检测并清除残留代理 → 启 mitmdump + 装 CA → 设代理 → 冷启动 FGO → 自动点登录 → 抓 `toplogin` → 复制剪贴板 | — |
| **B 直连** | `tools/一键开机-不抓包.bat` | 启动模拟器 → 检测并清除残留代理 → **停掉残留 mitmdump** → 直接冷启动 FGO | 不设代理、不启 mitmdump、不抓包、不写剪贴板 |

两个入口共享 `boot_common.py` 的前置逻辑、同一个清理守护、同一个单实例互斥体（不会同时运行），且都保留 **CMD 窗口**、**关窗即全收**与**离线即收工**。

### 开机兜底（两个脚本一模一样）

```
读三条 key：http_proxy / global_http_proxy_host / global_http_proxy_port
任一非空且非 null → 打印「检测到残留代理 …」
无条件 delete 三条 → 逐条读回，必须全为 null（最多重试 3 轮）
仍失败 → 报错退出，绝不启动 FGO
```

清代理发生在**模拟器在线时**、任何「下一步」之前。这是本项目的正确性基础：即使上次是直接关模拟器、崩溃或断电，残留代理也活不过一次开机。

### 收工行为

抓包/开机成功后 CMD 窗口会一直挂着（父进程存活 = 守护不动作）。三种收工路径：

| 你的动作 | 守护行为 | CMD 窗口 |
|---|---|---|
| FGO 自己退出（不关窗） | 清代理（读回校验）+ 停 mitmdump，**模拟器保留** | 自动关闭 |
| **关掉 CMD 窗口** | 「关窗即全收」：≤3 秒检测到父进程死亡 → 清代理 → 停 mitmdump → `ldconsole quit` 关闭整个模拟器 | 已关 |
| **直接关掉模拟器** | 「离线即收工」：检测到设备离线 → 停 mitmdump → 立即退出；清代理物理上不可能（adb 通道已断），残留交下次开机兜底 | 立即关闭 |

顺序不可颠倒：清代理必须在模拟器还活着时执行，关掉就清不进去了。

## 文件职责一览

### 用户入口（Quicker / 双击）

| 文件 | 职责 |
|---|---|
| `tools/一键抓包.bat` | 入口 A：调用 `auto_capture.py`（含抓包） |
| `tools/一键开机-不抓包.bat` | 入口 B：调用 `start_plain.py`（不含抓包） |
| `tools/一键抓包-恢复.bat` | 手动恢复：清代理 + 停 mitmdump（`restore.py`），异常中断时使用 |
| `tools/一键抓包-自检.bat` | 环境自检：`doctor.py`，全程只读 |
| `tools/find_python.cmd` | 四个 bat 共用的 Python 查找段（跳过商店桩、校验 3.11+） |

### Python 模块

| 文件 | 职责 | 被谁调用 |
|---|---|---|
| `tools/auto_capture.py` | **入口 A 主流程**：抓包、设代理、装 CA、冷启动 FGO、剪贴板；同时提供全项目复用的底层 helper（adb / 代理读写 / 端口 / CA） | `一键抓包.bat`；其余模块 `import auto_capture as ac` |
| `tools/start_plain.py` | **入口 B 主流程**：清残留、停 mitmdump、启动 FGO、挂窗口 | `一键开机-不抓包.bat` |
| `tools/boot_common.py` | **两个入口共用**：启动模拟器 `ldconsole launch` / 等 boot / 检测清残留代理（读回校验）/ 冷启动 FGO / 拉起守护并挂住窗口 | A、B 局部导入 |
| `tools/_cleanup_daemon.py` | **清理守护**（detached 后台）：等 FGO 退出还原环境；父进程死亡则全收（含关模拟器）；设备离线则立即收工 | `boot_common.spawn_daemon_and_wait` |
| `tools/restore.py` | 环境恢复：清代理 + 停 mitmdump，可选卸载 CA / 强停 FGO | `一键抓包-恢复.bat` |
| `tools/doctor.py` | 环境自检：只读，输出阻塞项，`--json` 供脚本消费 | `一键抓包-自检.bat` |
| `tools/fgoaddon.py` | mitmproxy 插件：抓 `/login/top` 响应并落盘 `toplogin` JSON（源自 Chaldea） | mitmdump 启动时加载 |
| `tools/wait_popup_win32.py` | Win32 进度弹窗（独立进程，靠 `PhaseFile` / `SignalFile` 通信） | `auto_capture.py` |

### 配置与运行时产物

| 文件 | 职责 |
|---|---|
| `tools/config.ini.example` | 配置模板，复制为 `config.ini` 生效 |
| `tools/config.ini` | 本地配置（可选，不存在时全自动探测；被 `.gitignore` 忽略） |
| `tools/requirements.txt` | Python 依赖（mitmproxy） |
| `tools/toplogin/` | 抓包输出（只保留最新一次；**含账号登录信息，切勿外传**） |
| `tools/mitmdump.log` | 全量流量日志，"抓不到"先看它 |
| `tools/run.log` | 统一异常日志 + `[wait]` / `[daemon]` 生命周期 |
| `tools/.wait_phase.tmp` / `.wait_signal.tmp` | 进度弹窗状态 / 取消信号 |
| 根目录 `README.md` / `LICENSE` / `.gitignore` | 说明 / MIT 许可 / 忽略规则 |

运行时产物均被 `.gitignore` 排除，无需手动管理。

## 特性

- **全自动**：从触发到抓包完成无需键盘鼠标；登录按钮由 ADB 自动点击（`config.ini` 可关）
- **证书自动安装**：模拟器未信任 mitmproxy CA 时 root 写入系统库（Android 9 legacy，Android 10+ APEX + legacy 双库）
- **代理自检 + 守门**：设置后读回校验（3 轮），全败则报错退出，绝不带失效代理启动 FGO
- **单实例防重入**：命名互斥体，两个入口共用一个锁
- **模拟器自动适配**：按盘符扫 `LDPlayer*/adb.exe` + 硬编码路径 + PATH 三级兜底
- **FGO 零打扰**：抓包成功后不删代理、不杀进程、不重启 FGO，由守护等 FGO 退出后还原（避免切断长连接）
- **守护状态机**：区分「FGO 已退出」与「设备不可达」；后者绝不执行清代理（命令发不进去会假成功），改为立即停 mitmdump 并收工，残留交下次开机兜底
- **坏数据不落盘**：JSON 合法性校验（含 `response` 字段）+ 原子落盘；`toplogin/` 只留最新一个

## 工作原理

```
FGO (模拟器内) ──HTTPS──→ Android 系统代理 ──→ mitmdump (本机:18080)
                                                  │ 解密，匹配 /login/top
                                                  ▼
                        toplogin_<区服>_YYYYMMDD.json ──→ Chaldea 导入
```

- FGO（Unity）读老 API `settings global http_proxy`，值必须是 `ip:port`；脚本同时写新 API 兼容新版雷电
- `/login/top` 只在**冷启动后走完整登录流程**时发一次（「菜单→设置→重新登录」不会触发）
- 因此用 `force-stop` + `am start` 保证触发；抓完后不再动 FGO
- FGO 是 Unity 渲染，`uiautomator` 无效 → ADB 盲点屏幕中央（`wm size` 自适应）

## 环境要求

| 依赖 | 要求 |
|---|---|
| Windows | 10 / 11 |
| 模拟器 | 雷电 9 / 12 / 14（按 `LDPlayer*` 通配扫描），**必须开启 root** |
| FGO | 国服 360 渠道服 `com.bilibili.fgo.qihoo`（其他渠道见配置） |
| Python | 3.11+（安装时勾选 **Add to PATH**） |
| mitmproxy | `pip install -r tools/requirements.txt` |
| Chaldea | 用于导入抓包数据 |

> **CA 证书必需**：360 渠道服 SDK 严格校验证书，未装 CA 会表现为「连接失败」。脚本自动安装，前提是模拟器已开 root。

## 安装

### 1. Python + mitmproxy

```bash
pip install -r tools/requirements.txt
```

> **Windows 商店桩坑**：裸敲 `python` 可能弹 Microsoft Store。`find_python.cmd` 会自动跳过商店桩（已知安装位置优先 → PATH 跳过 WindowsApps → 候选校验 3.11+），用 bat 触发无需手动关别名。手动在终端跑脚本时建议 `where python` 确认第一行是真实路径。

### 2. 模拟器开启 ROOT（二选一）

**GUI**：多开管理器 → 设置 → 勾选「ROOT 权限」→ 重启。

**命令行**：

```bat
ldconsole modify --index 0 --root 1
ldconsole reboot  --index 0
```

### 3. 装好 FGO，下载本仓库到任意目录

文件清单见上方「文件职责一览」。

## 重装系统 / 新机速查清单

| # | 项目 | 操作 | 验证 |
|---|---|---|---|
| 1 | 真实 Python | 安装包勾 **Add to PATH**；关闭应用执行别名 | `where python` 第一行非 WindowsApps |
| 2 | mitmproxy | `pip install -r tools/requirements.txt` | `python -c "from mitmproxy.tools.main import mitmdump"` 无报错 |
| 3 | 雷电模拟器 | 安装位置任意（脚本按盘符自动扫） | `where adb` 命中模拟器目录 |
| 4 | ROOT 权限 | `ldconsole modify --index 0 --root 1` + `reboot` | `adb shell su -c id` 返回 `uid=0(root)` |
| 5 | 重开终端 | PATH 是进程启动时读取的 | — |

> CA 首次运行自动装，无需手动操作（需 root）。

## 使用

**动作 A（要抓包）**

1. Quicker 触发 `tools\一键抓包.bat`（参数留空，勾「失败后停止」）——**不需要先开模拟器**，脚本会自动启动并等待就绪
2. 弹窗显示「✓ 抓包环境已就绪」→ FGO 自动启动 → 登录按钮被自动点击
3. 公告页加载完成 = 抓包成功：剪贴板已有数据，文件在 `tools/toplogin/toplogin_<区服>_YYYYMMDD.json`
4. Chaldea → 导入 → Https抓包 → 选账号 → **从剪贴板**（若提示复制失败，改「从文件」）
5. CMD 窗口保持；FGO 退出、直接关模拟器、或**想收工直接关窗**（= 自动清代理 + 停 mitmdump + 关模拟器），窗口都会自动关闭，详见「收工行为」

**动作 B（只玩，不抓包）**

1. Quicker 触发 `tools\一键开机-不抓包.bat`
2. 脚本启动模拟器 → 清残留代理 → 停残留 mitmdump → 直接启动 FGO
3. CMD 窗口保持；关窗触发全收，直接关模拟器则离线即收工

> ⚠️ 工程放在网络映射盘（NAS）上时：Quicker 以管理员身份运行，`net use X:` 建的盘符在提权会话里不存在 → 找不到 bat、静默无反应。见「常见问题」最后一条。

## 配置

包名自动探测（列含 `fate`/`fgo` 的包 → 候选表 → 自动查 launcher），一般不用管。需手动指定时把 `tools/config.ini.example` 复制为 `tools/config.ini`：

```ini
package=com.bilibili.fgo.qihoo
```

| 区服 | 包名 |
|---|---|
| 国服 360 渠道服（默认） | `com.bilibili.fgo.qihoo` |
| 国服 B 服 | `com.bilibili.fatego` |
| 国服其他渠道 | `com.bilibili.fategrandorder` |
| 日服 / 台服 | `com.aniplex.fategrandorder` |

| 键 | 含义 | 默认 |
|---|---|---|
| `package` | FGO 包名 | 自动探测 |
| `auto_tap` | 自动点击登录按钮（1/0） | 1 |
| `tap_interval` | 点击间隔（秒） | 1.5 |
| `tap_timeout` | 自动点击窗口时长（秒） | 90 |
| `tap_x` / `tap_y` | 盲点坐标（留空按 `wm size` 取屏幕中央） | 自动 |
| `status_popup` | Win32 进度弹窗（1/0） | 1 |

> 布尔项支持 `1/0/true/false/yes/no/on/off`，写错回退默认值。

## 常见问题

| 问题 | 原因 / 解决 |
|---|---|
| 一直抓不到 | 看 `tools/mitmdump.log`：无 FGO 流量 = 代理未生效；有流量无 `login/top` = 没走登录流程（需冷启动） |
| FGO 报「连接失败」 | 模拟器未信任 CA（360 SDK 校验）→ 确认 root 已开后重跑脚本自动补装 |
| 模拟器上不了网 | 代理残留：**下次开机任一脚本会自动清**；也可立刻双击 `tools/一键抓包-恢复.bat`。手动命令为 `adb shell settings delete global http_proxy` + 删除 `global_http_proxy_host` / `global_http_proxy_port`。**严禁 `settings put global http_proxy :0`**（空值令 FGO 直连 443） |
| CMD 窗口一直不关 | 只有 FGO 还在运行且模拟器在线时才保持（窗口在 = 父进程活 = 守护不动作）。FGO 退出、关掉模拟器、或关掉窗口，都会让它自动关闭 |
| 先关了模拟器，代理没清 | 模拟器关掉后 PC 侧进不去，无法清。**这是设计预期**：守护会立即停 mitmdump 并关闭 CMD 窗口，代理残留由下次开机脚本无条件清除 + 读回校验 |
| 弹「已有抓包流程正在运行中」 | 单实例互斥体（A/B 共用）：确认没别的在跑就等几秒；进程被杀后锁自动释放 |
| 提示「剪贴板复制失败」 | 8MB 大文本偶发失败：改用 Chaldea「从文件」导入 |
| 弹「未找到模拟器 adb」 | 模拟器未装或不在常见路径；加入 PATH 后**重开终端** |
| 弹「抓包服务未能监听端口 18080：[WinError 4551]」 | Smart App Control / WDAC 拦无签名 `mitmdump.exe`；脚本已改用有签名的 `python.exe -c` 拉起。仍被拦则检查系统 Smart App Control |
| 异常后行为怪异 | 看 `tools/run.log`：异常 traceback + `[wait]` / `[daemon]` 生命周期，贴末尾给维护者 |
| **bat 刷屏「'xxx' 不是内部或外部命令」** | 批处理被存成了 **LF**（cmd.exe 只认 CRLF）。改回 CRLF；**改动任何 `.bat` / `.cmd` 后务必确认仍是 CRLF** |
| **Quicker 触发毫无反应** | 工程在映射盘而 Quicker 以管理员运行：提权会话看不到 `X:`。两条路：① 放本地启动器 `FGOcap_launch.cmd`（先 `net use` 再 `call` 真 bat）；② 导入 `EnableLinkedConnections=1`（`HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System`，DWORD）后重启 |

## 风险提示

- 抓包属于中间人攻击，仅在专用模拟器上操作
- FGO 官方协议不允许模拟器/抓包，**有封号风险，后果自负**
- `toplogin/*.json` 含账号登录信息，**切勿分享他人**

## 致谢 / License

- [Chaldea](https://github.com/chaldea-center/chaldea) —— 数据导入工具，`fgoaddon.py` 源自其官方工具包
- [mitmproxy](https://mitmproxy.org/) —— HTTPS 抓包核心（MIT License）

本仓库脚本（`auto_capture.py` / `start_plain.py` / `boot_common.py` / `_cleanup_daemon.py` / `doctor.py` / `restore.py` / `wait_popup_win32.py` / 四个 `.bat` / `find_python.cmd`）采用 MIT License；`fgoaddon.py` 版权归 Chaldea 项目所有。

*（内容由AI生成，仅供参考）*
