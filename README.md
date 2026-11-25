# linux-use

跨平台桌面 GUI 智能体：用系统无障碍树（而不是视觉模型）读界面，默认 **HYBRID** 策略按动作在缓存树和分类坐标图之间自动选择，并优先走无障碍原语（`do_action` / `set_text` / `grab_focus`）。逻辑受到 [Jeomon/Windows-Use](https://github.com/Jeomon/Windows-Use) 的启发（读无障碍树 → LLM 决定点击/输入/滚动 → 循环直到完成），但代码是单独实现的，**没有克隆或 pip 安装那个仓库**。

仓库名仍是 [`mumu-2025/linux-use`](https://github.com/mumu-2025/linux-use)（没有另开仓库）。同一套接口自动检测操作系统：

| 系统 | 无障碍 / 输入 | 本仓库验证状态 |
|--|--|--|
| **Linux**（X11） | AT-SPI2 + xdotool/wmctrl | **已在本机实测**（inspect / act / smoke / 四模式基准） |
| **Windows** | UI Automation（comtypes）+ SendInput | **已实现完整代码路径，未在真实 Windows 硬件上跑过**；有 mock 单元测试。不要声称「已经能用」。`pip install 'linux-use[windows]'` |
| **macOS** | Accessibility AX（pyobjc）+ CGEvent | **已实现完整代码路径，未在真实 Mac 硬件上跑过**；有 mock 单元测试。需要把宿主进程加入「辅助功能」权限。不要声称「已经能用」。`pip install 'linux-use[macos]'` |

默认入口仍是 `linux-use`；`desktop-use` 是同一个 CLI 的别名。对外还有 CLI、Python API、JSON-RPC，以及 `linux-use mcp`（MCP stdio，给 Claude / Cursor 等）。

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

Windows / macOS **尚未在真实机器上验证**。若要在那些系统上尝试（自担风险）：

```bash
pip install -e '.[windows]'   # comtypes；输入走 SendInput，不依赖 pywinauto
pip install -e '.[macos]'     # pyobjc ApplicationServices / Cocoa / Quartz
```

默认策略是 `HYBRID`（可用环境变量 `LINUX_USE_POLICY=hybrid|cache|locmap|original` 覆盖）。

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
linux-use act click --name "File" --role menu      # 走分类坐标图，热路径不再扫树
linux-use act type --id e12 --text "hello" --clear
linux-use act shortcut --keys ctrl+s
linux-use inspect
linux-use map                     # 按 role/region 列出缓存坐标
linux-use map --json

# 受控冒烟测试（会打开/关闭一个临时 Mousepad，并测量时延）
linux-use smoke

# 同一脚本任务对比 original / cache / locmap / hybrid
linux-use bench --runs 5

# MCP stdio（Claude / Cursor 等）。细节见 docs/INTEGRATIONS.md
linux-use mcp

# 有 LLM 时：用自然语言跑任务
export LINUX_USE_API_KEY="your-key"      # 或 OPENAI_API_KEY；不要把密钥写进代码或仓库
export LINUX_USE_BASE_URL=https://api.openai.com/v1   # 可选，任意 OpenAI 兼容端点
export LINUX_USE_MODEL=gpt-4o-mini
linux-use run "Open Mousepad and type hello"
```

等价入口：`python -m linux_use ...`，以及别名 `desktop-use`。

### Python API

```python
from linux_use import inspect_desktop, act, Agent, OpenAICompatLLM

snap = inspect_desktop()          # 第一次全量，之后增量/缓存
print(snap.observation())
print(snap.observe_ms, snap.refresh_mode, snap.focused_id)

act("click", id="e12")
act("click", name="File", role="menu")   # 分类坐标图
act("type", id="e12", text="hello", clear=True)

from linux_use import get_location_map
loc = get_location_map(refresh=True)
print(loc.dump_text())

llm = OpenAICompatLLM()           # 读环境变量里的 key，不写死密钥
agent = Agent(llm=llm, max_steps=15)
result = agent.invoke("在文本编辑器里写一行 hello")
print(result.answer, result.observe_ms_total, result.act_ms_total)
```

点击 / 键入默认走 **HYBRID**：有无障碍原语就用原语；缓存树仍可信时在内存里定位（比坐标图的窗口几何校验更快）；静态控件且没有新鲜快照时才走 locmap；动作后做廉价校验，失败则增量刷新并重试一次，全量重扫是最后手段。每一步会记录走了哪条路径（`res.data["path"]`）。

### JSON-RPC（给其他 Agent）

```bash
linux-use serve --stdio                 # 每行一个 JSON-RPC 2.0 请求
# 或
linux-use serve --host 127.0.0.1 --port 8765
```

方法：`inspect`、`act`、`map`、`windows`、`run`、`ping`。MCP 模式见下方与 `docs/INTEGRATIONS.md`。

```json
{"jsonrpc":"2.0","id":1,"method":"inspect","params":{"force":false}}
{"jsonrpc":"2.0","id":2,"method":"act","params":{"tool":"click","name":"File","role":"menu"}}
{"jsonrpc":"2.0","id":3,"method":"map","params":{"refresh":false}}
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
6. **分类坐标图**：把屏幕上的按钮、输入框、链接、菜单项、标签页等按 role/region 编成 `name -> (cx,cy)` 查找表。`linux-use act click --name "保存"` 在热路径上只做查找 + 窗口几何廉价校验，然后直接点缓存坐标，不再走树。AT-SPI 的 bounds/children/窗口移动或滚动只会作废受影响的条目；校验失败则退回增量刷新。

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

分类坐标图（点击 Mousepad 的 File 菜单，Asia/Shanghai 2026-11-25 23:35 左右）：

| 路径 | 意图到点击完成 | 其中定位 | 走过节点 |
|--|--|--|--|
| 无坐标图（先强制全量扫描再点） | 163.2 ms | **57.6 ms** | 90 |
| 分类坐标图（查找 + 窗口几何校验 + 点击） | 108.9 ms | **5.3 ms** | 0 |
| 再点一次（热路径） | 112.1 ms | **7.8 ms** | 0 |

定位阶段大约快 **10.9 倍**（57.6 ms → 5.3 ms；查找 0.01 ms + 校验 5.24 ms）。整段意图到点击大约快 1.5 倍，剩余时间主要花在 xdotool 点击本身（约 104–106 ms）。建表只要 0.12 ms（8 个可见交互条目）。

### 四种模式对照（同一脚本，无 LLM，默认策略据此选定）

日期：2026-11-26 00:00（Asia/Shanghai）。命令：`linux-use bench --runs 5`。20 次全部成功（每种模式 5/5），每次都把标记 `LINUX_USE_BENCH_OK_20261125` 写进了临时文件。启动 Mousepad 的等待不计入任务时延。任务：观察并点击文本区 → 写入标记 → 点 File 菜单 → Escape → `ctrl+s` → 核对文件。

| 模式 | 任务总时延 median (min–max) ms | 观察+定位 median (min–max) ms | 动作 median (min–max) ms | 走过节点 | 相对 original 总时延 | 相对 original 观察+定位 | 成功率 |
|--|--|--|--|--|--|--|--|
| original | 758.9 (750.7–15277.0) | 473.2 (464.5–14990.8) | 263.5 (261.1–266.0) | 992 | 1.00× | 1.00× | 5/5 |
| cache | 337.2 (320.6–360.2) | 62.5 (53.1–64.8) | 261.9 (256.3–279.4) | 94 (94–95) | 2.25× | 7.57× | 5/5 |
| locmap | 390.7 (379.7–401.3) | 73.4 (68.8–78.5) | 259.4 (257.2–263.8) | 94 (94–95) | 1.94× | 6.45× | 5/5 |
| **hybrid（默认）** | **145.2 (132.9–149.6)** | 81.4 (76.1–89.6) | **53.4 (49.5–57.8)** | 90 | **5.23×** | 5.82× | 5/5 |

说明：

- original 有一次观察阶段碰到约 15 s 的 AT-SPI 停顿（max 15277 ms）；其余 4 次在 750.7–790.8 ms。公平对比请看 **median**。
- cache / locmap 的动作时间仍约 260 ms，主要是 xdotool 点击。hybrid 在本任务里对文本框走 `grab_focus`、对 File 菜单走 AT-SPI `do_action`、对正文走 `set_text`（日志路径：`cache` / `atspi`），动作 median 降到 53.4 ms，所以端到端约快 **5.23×**。
- 本任务在初次观察后缓存树一直可信，hybrid 因此走 cache 而不是 locmap（内存查找约 0.02 ms，比窗口几何校验的 5–12 ms 更便宜）。locmap 会在「没有新鲜快照、目标是静态控件、几何未变」时被选中。
- hybrid 的观察+定位略高于 cache（81 vs 62 ms），是因为动作后有廉价校验（File 菜单约 27 ms）。动作节省远大于这笔开销。
- 完整路径日志和更细的注释见 `docs/SMOKE.md`。

---

## 架构

```
linux_use/
  a11y.py              启动并启用 AT-SPI 总线
  tree.py              快照、delta、给 LLM 的紧凑文本
  locmap.py            分类元素坐标图（name/role/region -> 屏幕坐标）
  hybrid.py            默认 HYBRID 策略（按动作在 cache / locmap / AT-SPI·UIA·AX 之间选择）
  agent.py             观察 → 工具调用 → 再观察
  tools.py             click/type/scroll/move/shortcut/app/shell/file/wait/done
  llm.py               OpenAI 兼容 Chat Completions（密钥只来自环境变量）
  cli.py / server.py   命令行和 JSON-RPC
  mcp_server.py        MCP stdio（inspect / map / act / run / windows）
  bench.py             四模式对照基准
  platform/
    base.py            Accessibility / Desktop / Input 接口
    linux_*.py         AT-SPI + wmctrl + xdotool（已在本机实测）
    windows_*.py       UIA + SendInput（已实现，未在真实 Windows 上验证）
    macos_*.py         AX + CGEvent（已实现，未在真实 macOS 上验证）
```

---

## 与 Windows-Use 的差异和限制

| | Windows-Use | linux-use（本包） |
|--|--|--|
| 无障碍 API | Windows UI Automation | Linux AT-SPI2（已测）；Windows UIA / macOS AX（已实现，未在真实硬件验证） |
| 输入 | UIA + 系统输入 | 优先无障碍原语，其次 Linux xdotool / Windows SendInput / macOS CGEvent |
| 视觉模型 | 可选 | 有截图工具，但不是主路径 |
| 浏览器树 | 可从浏览器无障碍树抓取 | Chrome 需带 `--force-renderer-accessibility` 启动才较完整，尚未做专用 scrape |
| 虚拟桌面 | 有 Windows VDM | 未做（可后接 wmctrl / XFCE 工作区） |
| 语音 STT/TTS | 有 | 无 |
| 多供应商 LLM | 很多封装 | 一个 OpenAI 兼容客户端（改 `BASE_URL` 即可接其他家） |
| 平台 | 仅 Windows | Linux 已实测；Win/mac 有完整后端代码 + mock 测试，**未在真实硬件上跑过，不声称可用** |

其它限制：

- 本机验证环境是 **X11 + XFCE/xfwm4**。Wayland 需要另接 `ydotool`/`wtype`，目前未做。
- Windows / macOS 后端不要当作已经交付的功能：代码是完整路径（不是空 stubs），只在 Linux 上用 mock 测过。
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
| `LINUX_USE_POLICY` | 动作策略：`hybrid`（默认）/ `cache` / `locmap` / `original` |

---

## License

MIT。仓库：https://github.com/mumu-2025/linux-use

---

## English summary

**linux-use** is a desktop GUI agent (repo name kept). It reads the screen through the OS accessibility tree, not a vision model. The default **HYBRID** policy picks AT-SPI/UIA/AX primitives, a cached tree, or a classified coordinate map per action, then does a cheap post-action verify. An optional OpenAI-compatible LLM chooses click / type / scroll / shortcut / shell in a short loop. If no API key is set, `inspect`, `act`, `smoke`, `bench`, and `mcp` still work.

**Tested vs untested:** Linux (X11 + AT-SPI) is hardware-tested on this project’s reference box. Windows (UI Automation + SendInput) and macOS (AX + CGEvent) are real implementations with mock unit tests, but they have **not** been run on real Windows or Mac hardware — do not claim they work. Install optional extras with `pip install 'linux-use[windows]'` or `pip install 'linux-use[macos]'`.

The tree is **cached and event-invalidated**. Integrations: CLI (`linux-use` / alias `desktop-use`), Python API, JSON-RPC (`linux-use serve`), and MCP stdio (`linux-use mcp`). See `docs/INTEGRATIONS.md`. This package is a separate implementation inspired by the Windows-Use architecture; it does not vendor or install that repository.
