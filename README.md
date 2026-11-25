# linux-use

在 Linux 桌面上用 **AT-SPI2 无障碍树**（而不是视觉模型）操作 GUI 的智能体。逻辑受到 [Jeomon/Windows-Use](https://github.com/Jeomon/Windows-Use) 的启发（读无障碍树 → LLM 决定点击/输入/滚动 → 循环直到完成），但代码是单独实现的，**没有克隆或 pip 安装那个仓库**。

先满足本机自用，同时提供干净的 CLI / Python API / JSON-RPC，方便其他 Agent 调用，并预留了 Windows / macOS 后端接口（目前是 stubs）。

---

## 安装

系统依赖（Debian/Ubuntu）：

```bash
sudo apt-get install -y at-spi2-core gir1.2-atspi-2.0 python3-pyatspi python3-dbus libatk-adaptor python3-gi
# 输入与窗口：xdotool、wmctrl（本机已有）
# 可选：mousepad（冒烟测试编辑器）、scrot（截图）
```

克隆后在项目目录里可编辑安装。必须用 `--system-site-packages`，否则虚拟环境读不到系统里的 `gi` / `pyatspi`：

```bash
git clone https://github.com/mumu-2025/linux-use.git
cd linux-use
python3 -m venv --system-site-packages venv
./venv/bin/pip install -e .
export PATH="$PWD/venv/bin:$PATH"
linux-use --version
```

会话里需要有 `DISPLAY` 和 D-Bus session。程序会自动启用 AT-SPI 总线，并用 `GTK_A11Y=atspi`、`GTK_MODULES=atk-bridge` 拉起被控应用。在 X11 上用 xdotool/wmctrl 做输入与窗口管理。

---

## 用法

```bash
# 无 LLM：把当前无障碍树打出来（inspect 模式）
linux-use inspect
linux-use inspect --json --out /tmp/tree.json
linux-use inspect --full          # 强制全量扫描

# 无 LLM：按脚本执行动作（用缓存里的元素 id 或坐标）
linux-use act app --action launch --command mousepad --path /tmp/a.txt
linux-use act click --id e12
linux-use act type --id e12 --text "hello" --clear
linux-use act shortcut --keys ctrl+s
linux-use act inspect

# 受控冒烟测试（会打开/关闭一个临时 Mousepad，并测量时延）
linux-use smoke

# 有 LLM 时：用自然语言跑任务
export LINUX_USE_API_KEY="your-key"      # 或 OPENAI_API_KEY；不要把密钥写进代码或仓库
export LINUX_USE_BASE_URL=https://api.openai.com/v1   # 可选，任意 OpenAI 兼容端点
export LINUX_USE_MODEL=gpt-4o-mini
linux-use run "Open Mousepad and type hello"
```

等价入口：`python -m linux_use ...`

### Python API

```python
from linux_use import inspect_desktop, act, Agent, OpenAICompatLLM

snap = inspect_desktop()          # 第一次全量，之后增量/缓存
print(snap.observation())
print(snap.observe_ms, snap.refresh_mode, snap.focused_id)

act("click", id="e12")
act("type", id="e12", text="hello", clear=True)

llm = OpenAICompatLLM()           # 读环境变量里的 key，不写死密钥
agent = Agent(llm=llm, max_steps=15)
result = agent.invoke("在文本编辑器里写一行 hello")
print(result.answer, result.observe_ms_total, result.act_ms_total)
```

### JSON-RPC（给其他 Agent）

```bash
linux-use serve --stdio                 # 每行一个 JSON-RPC 2.0 请求
# 或
linux-use serve --host 127.0.0.1 --port 8765
```

方法：`inspect`、`act`、`windows`、`run`、`ping`。

```json
{"jsonrpc":"2.0","id":1,"method":"inspect","params":{"force":false}}
{"jsonrpc":"2.0","id":2,"method":"act","params":{"tool":"click","id":"e12"}}
```

---

## 速度设计（核心）

主痛点是「每一步都全量重建 UI 树」。本实现默认走短循环 + 缓存：

1. **稳定元素 id**：节点按无障碍路径分配 `e1, e2, ...`，全量/增量扫描时尽量复用同一 id。动作优先用缓存的 id 和包围盒，不必先重扫整树。
2. **AT-SPI 事件失效**：后台 GLib 循环监听 `children-changed`、`window:create/destroy/activate`、`text-changed`、`bounds-changed`、`state-changed:focused` 等。同类事件会合并，避免打字时被 caret/bounds 事件淹没。
3. **分级刷新**
   - 第一次，或出现结构事件 / 廉价校验失败：全量扫描（但跳过关闭菜单、越界隐藏节点）。
   - 只有文本/状态/位置变化：只刷新被事件点到的节点和当前焦点子树（incremental）。
   - 无事件且应用数、顶层窗口标题没变：直接返回缓存（cache hit），只做 O(应用数) 的廉价检查。
4. **观察可跳过**：Agent 循环在 cache hit 时只告诉模型「UI unchanged + 当前焦点」，不把整棵树再送一遍。需要时再调用 `inspect`（可 `--full`）。
5. **输入走快捷路径**：对支持 Text 接口的控件优先 `set_text` / `do_action` / `grab_focus`，不行再退回 xdotool 坐标点击和键入。

冒烟测试会分别打印 **observe_ms**（冷扫描 / 热缓存 / 键入后）和 **act_ms**（点击、键入、快捷键）。文字日志在 `smoke-logs/`（树快照），说明见 `docs/SMOKE.md`。截图 PNG 体积较大且通过当前发布通道不便上传，因此不进入仓库；复测时会在本地重新生成。

在一台 X11 1920×1200 的 Linux 云桌面（Mousepad、90 个节点）上测到的结果（Asia/Shanghai，2026-11-25 23:09 左右）：

| 步骤 | 模式 | 时延 | 走过的节点 |
|--|--|--|--|
| 冷观察（全量） | full | **39.2 ms** | 90 |
| 热观察（无事件） | cache | **5.6 ms** | 0 |
| 点击文本框 | act | 110.1 ms | — |
| AT-SPI `set_text` | act | **3.9 ms** | — |
| 键入后观察 | incremental | **1.0 ms** | 1 |
| `ctrl+s` | act | 35.1 ms | — |
| 保存后再观察 | incremental | 2.4 ms | 1 |

相对全量扫描：缓存命中大约快 7 倍，键入后的增量刷新大约快 40 倍。动作本身走缓存 id / 包围盒，不必先重扫树。

---

## 架构

```
linux_use/
  a11y.py              启动并启用 AT-SPI 总线
  tree.py              快照、delta、给 LLM 的紧凑文本
  agent.py             观察 → 工具调用 → 再观察
  tools.py             click/type/scroll/move/shortcut/app/shell/file/wait/done
  llm.py               OpenAI 兼容 Chat Completions（密钥只来自环境变量）
  cli.py / server.py   命令行和 JSON-RPC
  platform/
    base.py            Accessibility / Desktop / Input 接口
    linux_a11y.py      AT-SPI 增量缓存（速度关键路径）
    linux_desktop.py   wmctrl / 启动应用
    linux_input.py     xdotool（本机是 X11）
    windows.py / macos.py   仅 stubs，供以后接 UIA / AX
```

---

## 与 Windows-Use 的差异和限制

| | Windows-Use | linux-use（本包） |
|--|--|--|
| 无障碍 API | Windows UI Automation | Linux AT-SPI2 |
| 输入 | UIA + 系统输入 | 优先 AT-SPI action/text，其次 xdotool（X11） |
| 视觉模型 | 可选 | 有截图工具，但不是主路径 |
| 浏览器树 | 可从浏览器无障碍树抓取 | Chrome 需带 `--force-renderer-accessibility` 启动才较完整，尚未做专用 scrape |
| 虚拟桌面 | 有 Windows VDM | 未做（可后接 wmctrl / XFCE 工作区） |
| 语音 STT/TTS | 有 | 无 |
| 多供应商 LLM | 很多封装 | 一个 OpenAI 兼容客户端（改 `BASE_URL` 即可接其他家） |
| 平台 | 仅 Windows | Linux 已实现；Win/mac 仅接口 stubs |

其它限制：

- 本机验证环境是 **X11 + XFCE/xfwm4**。Wayland 需要另接 `ydotool`/`wtype`，目前未做。
- 没有导出无障碍树的程序（部分 Electron、游戏、纯自绘界面）只能靠坐标，效果会差。
- 在 AT-SPI 启动之前就已经打开的应用，可能不会出现在树上，需要重启该应用。
- `shell` / `file` 有基本限制（危险命令拒绝；文件默认只允许家目录、`/tmp`、当前目录，以及可选的 `LINUX_USE_FILE_ROOTS`）。这不是沙箱，请勿在不可信任务上放开。
- 不要对别人的桌面会话乱点。冒烟测试只操作临时 Mousepad 窗口。

---

## 环境变量

| 变量 | 作用 |
|--|--|
| `LINUX_USE_API_KEY` 或 `OPENAI_API_KEY` | LLM 密钥，代码里不写死 |
| `LINUX_USE_BASE_URL` 或 `OPENAI_BASE_URL` | OpenAI 兼容接口，默认 `https://api.openai.com/v1` |
| `LINUX_USE_MODEL` 或 `OPENAI_MODEL` | 默认 `gpt-4o-mini` |
| `DISPLAY` | X11 显示，未设置时回退到 `:0` |
| `LINUX_USE_LOG_DIR` | 冒烟/截图日志目录，默认 `smoke-logs` |
| `LINUX_USE_FILE_ROOTS` | 额外允许 `file` 工具访问的目录，用 `os.pathsep` 分隔 |

---

## License

MIT。仓库：https://github.com/mumu-2025/linux-use

---

## English summary

**linux-use** is a Linux desktop GUI agent. It reads the screen through **AT-SPI2**, not a vision model. An optional OpenAI-compatible LLM then chooses click / type / scroll / shortcut / shell actions in a short loop. If no API key is set, `inspect`, `act`, and `smoke` still work.

The tree is **cached and event-invalidated**: full walks happen only on the first snapshot or after structural AT-SPI events. Text/bounds/focus changes refresh the focused subtree; if nothing changed, a cheap O(apps) check returns the cached tree. Tools act on stable element ids and cached bounding boxes so a click does not rescan the UI.

The reference environment used for the smoke numbers above is X11 (Xvfb + xfwm4). Wayland input, a dedicated browser scraper, and real Windows/macOS backends are not implemented yet (stubs only). This package is a separate implementation inspired by the Windows-Use architecture; it does not vendor or install that repository.
