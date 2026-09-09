# FGO 一键抓包（导入 Chaldea）

双击 bat 或 Quicker 触发 → 自动完成**代理设置 + 证书安装 + mitmdump 抓包 + FGO 冷启动** → 你在登录页点一下【登录】→ `toplogin` JSON 自动进剪贴板 → 导入 Chaldea。

## 特性

- **全自动**：从触发到抓包完成，你只需点一次【登录】
- **证书自动安装**：检测到模拟器未信任 mitmproxy CA 时，自动 root 写入系统信任库（支持 Android 9 / 14）
- **代理自检 + 守门**：设置代理后读回校验（最多 3 轮），全部失败则报错退出**绝不带着失效代理启动 FGO**
- **模拟器自动适配**：glob 匹配 `LDPlayer*/adb.exe`，雷电 9 / 12 / 14 免配置
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

## 环境要求

| 依赖 | 要求 |
|---|---|
| Windows | 10 / 11 |
| 模拟器 | 雷电 9 / 12 / 14（已验证），**需 root** |
| FGO | 国服 360 渠道服 `com.bilibili.fgo.qihoo`（其他渠道见配置） |
| Python | 3.10+（安装时勾选 Add to PATH） |
| mitmproxy | `pip install mitmproxy` |
| Chaldea | 用于导入抓包数据 |

> **CA 证书是必需的**：国服 360 渠道服的 SDK 会严格校验证书，不装 CA 时 HTTPS 握手会失败并表现为「连接失败」。
> 脚本会自动安装，无需手动操作——前提模拟器已 root。

## 安装

```bash
pip install mitmproxy          # 1. 装 mitmproxy（Python 3.10+ 装时勾 Add to PATH）
```

2. 模拟器：**多开管理器 → 设置 → 开启 ROOT 权限**；装好 FGO
3. 下载本仓库到任意目录：

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

## 使用

1. 启动模拟器（等完全开机，**不要先开 FGO**）
2. 双击 `一键抓包.bat`
3. 弹窗显示「✓ 抓包环境已就绪」→ FGO 被自动启动 → 到登录页点【登录】
4. 公告页加载完成 = 抓包成功 → 弹窗自动关、剪贴板已有数据、文件夹已打开
5. Chaldea → 导入 → Https抓包 → 选账号 → **从剪贴板**

**Quicker**：「运行或打开」步骤直接指向 `tools\一键抓包.bat`（参数留空，勾「失败后停止」）。
脚本内部会等待模拟器就绪（最多 2 分钟），可与「启动模拟器」步骤并行。

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
| FGO 报「连接失败」 | 模拟器未信任 CA（360 SDK 校验）→ 确认 root 已开，重跑脚本会自动补装 |
| 模拟器上不了网 | 代理残留：执行 `adb shell settings delete global http_proxy` + 删除 `global_http_proxy_host` / `global_http_proxy_port`。**严禁用 `settings put global http_proxy :0`**（空值会让 FGO 直连 443，抓不到包） |
| mitmdump 启动失败 | `pip install mitmproxy`，确认 `mitmdump --version` 可运行 |

## 风险提示

- 抓包属于中间人攻击，仅在专用模拟器上操作
- FGO 官方协议不允许模拟器/抓包，**有封号风险，后果自负**
- `toplogin/*.json` 含账号登录信息，**切勿分享他人**

## 致谢 / License

- [Chaldea](https://github.com/chaldea-center/chaldea) —— 数据导入工具，`fgoaddon.py` 源自其官方工具包
- [mitmproxy](https://mitmproxy.org/) —— HTTPS 抓包核心（MIT License）

本仓库脚本（`auto_capture.py` / `_cleanup_daemon.py` / `wait_popup_win32.py` / `一键抓包.bat`）采用 MIT License；`fgoaddon.py` 版权归 Chaldea 项目所有。
