<div align="center">
  <img src="assets/ico.svg" width="96" height="96" alt="mkr">

  # mkr

  **轻量、跨平台的鼠标键盘录制与回放工具**

  全局热键一键录制 · 毫秒级时序还原 · 原生悬浮胶囊 UI · MCP Server 原生集成

  [![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
  [![pynput](https://img.shields.io/badge/powered%20by-pynput%201.7%2B-brightgreen)](https://github.com/moses-palmer/pynput)
  [![PySide6](https://img.shields.io/badge/GUI-PySide6-41CD52?logo=qt&logoColor=white)](https://doc.qt.io/qtforpython/)
  [![MCP](https://img.shields.io/badge/MCP-Server%20Ready-8A2BE2)](https://modelcontextprotocol.io/)
  [![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

</div>

---

## ✨ 特性

| 能力 | 说明 |
|---|---|
| 🎙️ **全量录制** | 鼠标移动 / 点击 / 滚轮 + 键盘按下 / 抬起，完整保留修饰键状态 |
| 🫧 **胶囊 GUI** | 单窗口透明胶囊，五种状态、平滑动画、滚轮调节背景透明度 |
| 🧠 **键盘兜底** | Windows 下使用 `GetAsyncKeyState` 轮询，不依赖可能失效的 `WH_KEYBOARD_LL` 钩子 |
| 🎯 **像素级还原** | 回放前鼠标自动归位到录制起点；Windows Per-Monitor V2 DPI 声明，杜绝缩放偏移 |
| 🛡️ **输入接管保护** | 回放中检测到人为操作立即停止，避免失控注入 |
| 🧹 **安全退出** | 任意退出路径均主动释放全部修饰键，杜绝键盘映射错乱 |
| 🤖 **MCP Server** | 通过 stdio 暴露 9 个工具，本机 Agent 可直接驱动录制 / 回放 |
| ⌨️ **全局热键** | 连按 3 次 Ctrl 开始录制，无需切换窗口焦点 |

## 🖥️ 平台支持

| 平台 | 状态 | 说明 |
|---|:---:|---|
| Windows 10 / 11 | ✅ | 完整支持（Per-Monitor V2 DPI） |
| Linux (X11) | ✅ | 完整支持 |
| Linux (Wayland) | ⚠️ | pynput 仅支持 X11，请切换会话或启用 XWayland |
| macOS | ⚠️ | 需授予「辅助功能」权限 |

## 📦 获取

### 下载预编译版本

前往 [Releases](https://github.com/doubsumi/mkr/releases/latest) 下载 Windows 绿色版
`mkr-0.1.0-windows-x64.zip`，解压后双击 `mkr.exe` 即可运行，无需安装 Python。

> 未签名 exe 可能触发 SmartScreen 提示，选择「仍要运行」即可。详见 [注意事项](#-注意事项)。

### 从源码运行

```bash
git clone https://github.com/doubsumi/mkr.git
cd mkr
python -m venv .venv
.venv\Scripts\pip install -e .      # Windows
# source .venv/bin/activate && pip install -e .   # Linux / macOS
```

依赖：`pynput` + `PySide6-Essentials`（Python 3.9+）。

## 🚀 使用

### 悬浮胶囊 GUI

```bash
mkr          # 不带参数默认启动 GUI
mkr gui      # 显式启动 GUI
```

Windows 下也可直接使用仓库内的启动脚本，免去激活虚拟环境：

| 脚本 | 用途 |
|---|---|
| `run_gui.cmd` | 使用 `.venv` 的 `pythonw.exe` 无控制台启动 |
| `run_gui_admin.cmd` | 以管理员身份启动（目标应用为 Elevated 时必需） |
| `create_desktop_shortcut.cmd` | 在桌面创建带图标的快捷方式 |

### 全局热键

| 热键 | 功能 |
|---|---|
| `Ctrl` × 3（500ms 内） | 开始 / 停止录制 |
| `Ctrl + Alt + P` | 回放当前录制 |
| `Ctrl + Alt + S` | 停止回放 |
| `Ctrl + Alt + Q` | 退出程序 |

回放默认**循环执行**，直到点击停止、按下停止回放快捷键，或检测到人工接管。

### 命令行

```bash
mkr record -o demo.json          # 热键录制并保存为 JSON
mkr replay demo.json             # 回放（--speed 2.0 --loops 3 --no-countdown）
mkr-mcp                          # 启动 MCP stdio server
```

### 录制文件格式

JSON，包含版本号、创建时间、总时长与事件序列；事件类型为
`move` / `button` / `scroll` / `key`，时间戳为相对秒，精度 1μs。

## 🤖 MCP 集成

将以下配置加入 Agent 的 MCP 配置（如 Claude Desktop、Cursor）：

```json
{
  "mcpServers": {
    "mkr": { "command": "mkr-mcp", "args": [] }
  }
}
```

暴露工具：`start_record` · `stop_record` · `replay` · `replay_file` · `stop_replay` · `save` · `load` · `get_status` · `clear`

## 🏗️ 项目结构

```
mkr/
├── entry.py                  # PyInstaller 入口（含 DPI 声明）
├── mkr/
│   ├── recorder.py           # 录制 / 回放核心引擎
│   ├── app.py                # 应用层：热键、事件总线、生命周期
│   ├── keyboard_polling.py   # Windows 键盘轮询后端
│   ├── gui/                  # PySide6 透明胶囊窗口
│   ├── cli.py                # CLI 入口（record / replay / gui）
│   ├── mcp_server.py         # MCP stdio server（9 个工具）
│   └── storage.py            # JSON 录制文件读写
├── assets/ico.svg            # 图标资产
├── run_gui.cmd               # 源码启动（pythonw，无控制台）
├── run_gui_admin.cmd         # 管理员启动
├── create_desktop_shortcut.* # 桌面快捷方式
├── build_exe.cmd             # Windows 打包脚本
├── version_info.txt          # exe 版本资源
├── pyproject.toml            # 包定义与入口点
└── requirements.txt
```

## 🔨 从源码构建

### Windows（绿色免安装目录）

> Qt 类 GUI **必须使用 `--onedir`**：`--onefile` 下 Qt 插件路径失效，进程存活但窗口不显示。

```powershell
.venv\Scripts\pip install pyinstaller
.venv\Scripts\python -m PyInstaller --noconfirm --clean --windowed --onedir ^
  --name mkr --icon assets\ico.ico --version-file version_info.txt ^
  --collect-all mkr --collect-submodules pynput ^
  --hidden-import PySide6.QtCore --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebEngineQuick ^
  --exclude-module PySide6.QtQml --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.Qt3DCore --exclude-module PySide6.QtMultimedia ^
  --noupx ^
  entry.py
```

产物位于 `dist/mkr/`，压缩为 zip 即为绿色分发包；也可直接运行 `build_exe.cmd`。

### Linux

```bash
pyinstaller --noconsole --clean --onedir --name mkr --collect-all mkr \
  --collect-submodules pynput --noupx entry.py
tar -czf mkr-linux-x64.tar.gz -C dist mkr
```

## ⚠️ 注意事项

- **未签名 exe**：可能触发 SmartScreen「未知发布者」提示，选择「仍要运行」即可；
  对外分发建议进行代码签名（OV / EV / Azure Trusted Signing）。
- **管理员目标应用**：Windows UIPI 会阻止普通权限进程注入键鼠；录制/回放 Elevated
  窗口时，请通过 `run_gui_admin.cmd` 或管理员终端启动。
- **录制无输入事件**：确认目标窗口未被保护，且 mkr 与目标应用处于同一交互桌面。
- **Linux + Wayland**：pynput 依赖 X11，纯 Wayland 会话下不可用，请切换至 X11 或启用 XWayland。
- **macOS**：首次运行需在「系统设置 → 隐私与安全性 → 辅助功能」中授权终端 / 解释器。

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。

---

<div align="center">
  <sub>mkr —— 让重复操作只需演示一次。</sub>
</div>
