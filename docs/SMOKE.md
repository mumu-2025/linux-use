# linux-use 冒烟测试记录

日期：2026-11-25，约 23:09（Asia/Shanghai）  
环境：Linux 云桌面，X11 `DISPLAY=:4`（Xvfb 1920×1200 + xfwm4 + xfce4-panel），AT-SPI2 会话总线  
命令：`linux-use smoke`（无 LLM，脚本化动作）  
原则：只操作临时 Mousepad 窗口，结束后关闭，桌面只留下面板。

## 步骤与结果

1. 截图桌面 → `smoke-logs/01-before.png`
2. 启动 `mousepad /tmp/linux-use-smoke-pad.txt`（约 402 ms，含 0.4 s 等待窗口）
3. 等到 AT-SPI 出现可编辑 `text` 节点后再测时延
4. 用 AT-SPI 点击该节点、写入标记 `LINUX_USE_SMOKE_OK_20261125`、`ctrl+s` 保存
5. 核对文件内容与无障碍树里的文本
6. 截图 → `smoke-logs/02-after.png`（窗口中可见该标记）
7. 关闭 Mousepad，桌面恢复为仅 xfce4-panel

## 时延（最近一次成功跑）

| 步骤 | 模式 | observe_ms / act_ms | walked |
|--|--|--|--|
| observe_cold_full | full | **39.2 ms** | 90 |
| observe_warm_cache | cache | **5.6 ms** | 0 |
| click_text_element (`e75`) | act | 110.1 ms | — |
| type_marker（AT-SPI `set_text`） | act | **3.9 ms** | — |
| observe_after_type | incremental | **1.0 ms** | 1 |
| shortcut_ctrl_s | act | 35.1 ms | — |
| verify_file | — | 文件内容等于标记 | — |
| observe_final | incremental | 2.4 ms | 1 |

增量观察的 delta：`* [e75] text ''->'LINUX_USE_SMOKE_OK_20261125'`。  
相对全量扫描：缓存命中约快 7 倍，键入后增量刷新约快 40 倍。

## 日志路径

全部在仓库的 `smoke-logs/` 目录（PNG 截图默认不入库，见 `.gitignore`）：

- `results.json` — 逐步结构化结果
- `tree-cold.txt` / `tree-warm.txt` / `tree-after-type.txt` — 观察文本
- `01-before.png` / `02-after.png` — 截图

## 过程中修过的问题

- 事件回调里不要解析无障碍路径，否则应用关闭时会卡到 10s 以上。
- 必须设置 `AT_SPI_BUS_ADDRESS`，否则 libatspi 会在 `XDG_RUNTIME_DIR` 下乱建 socket。
- 紧凑树必须下降 `page tab` 这类包围盒为 -1 的容器，否则看不到文本框。
- at-spi typelib 2.56 里 `Accessible.get_text()` 不再接受起止偏移，要改用 `Atspi.Text.get_text(obj, start, end)`。
- 菜单项随「文档已修改」出现/消失，不应触发整棵树重扫。

## 如何复测

```bash
export DISPLAY=:4    # 按你的实际显示设置
export PATH="$PWD/venv/bin:$PATH"
linux-use smoke
```

## English

Controlled Mousepad smoke test on a Linux cloud desktop (X11 :4). Cold full observe 39.2 ms (90 nodes), cache hit 5.6 ms, incremental observe after typing 1.0 ms (1 node). AT-SPI set_text 3.9 ms. File confirmed marker `LINUX_USE_SMOKE_OK_20261125`. Desktop left tidy. Text logs under `smoke-logs/`; PNG screenshots are generated locally and not stored in the repository.

## 分类坐标图基准（第二次测量）

日期：2026-11-25，约 23:35（Asia/Shanghai）  
目标：点击 Mousepad 菜单栏上的 **File**。对比「每次先强制全量扫树再点」和「用分类坐标图的缓存中心点直接点」。

| 路径 | 意图到点击完成 | 定位（不含 xdotool） | 点击本身 | walked | 来源 |
|--|--|--|--|--|--|
| 无坐标图 + 强制全量扫描 | 163.18 ms | 57.57 ms（其中观察 53.47 ms） | 105.61 ms | 90 | full scan |
| 分类坐标图（第一次） | 108.87 ms | **5.28 ms**（查找 0.01 + 校验 5.24） | 103.58 ms | 0 | `map` |
| 分类坐标图（紧接着再点一次） | 112.06 ms | **7.80 ms**（查找 0.04 + 校验 7.71） | 104.26 ms | 0 | `map` |

- 定位加速：57.57 / 5.28 ≈ **10.9×**
- 含点击的端到端加速：163.18 / 108.87 ≈ **1.5×**（瓶颈是 xdotool 点击，约 104 ms）
- 建表：0.12 ms，8 个可见交互条目（有名称/有效包围盒的菜单和文本框等）
- 校验：只看窗口几何是否变化，不走无障碍树；失败才会增量刷新

命令：

```bash
linux-use map
linux-use act click --name File --role menu
```

## 三种模式对照基准（同一脚本，无 LLM）

日期：2026-11-25 23:51（Asia/Shanghai）。15 次全部成功，每次都把标记写进了临时文件。
命令：`linux-use bench --runs 5`。
三种模式跑**同一段**脚本化任务，不经过 LLM（LLM 时延未计入）。
任务（6 个动作，每个动作后各做一次该模式应有的观察；original 在动作前还会再全量扫一次树）：

1. 初始观察并定位文本区，点击文本区
2. 写入固定标记 `LINUX_USE_BENCH_OK_20261125`
3. 点击 File 菜单
4. Escape 关掉菜单
5. `ctrl+s` 保存到临时文件
6. 核对文件里确有该标记

每轮先打开全新 Mousepad，测完即关闭。三种模式轮转顺序，各跑 5 次，以降低先后偏差。启动窗口的等待不计入任务时延。

模式定义：

- **original**：Windows-Use 风格。每一步动作前、动作后都强制全量扫描 AT-SPI 树，在刚扫出的树上查找元素，不用缓存、不用坐标图。
- **cache**：事件驱动缓存 + 增量刷新，在缓存树上定位（不用分类坐标图）。
- **locmap**：在 cache 之上加分类坐标图，廉价窗口几何校验通过后直接点缓存坐标。

| 模式 | 任务总时延 median (min–max) ms | 观察+定位 median (min–max) ms | 动作 median (min–max) ms | 走过节点 median (min–max) | 相对 original 加速（总时延） | 相对 original 加速（观察+定位） | 成功次数 |
|--|--|--|--|--|--|--|--|
| original | 762.7 (735.8–880.0) | 471.7 (445.5–589.1) | 264.3 (263.0–269.0) | 992 (992–992) | 1.00× | 1.00× | 5/5 |
| cache | 339.6 (337.0–347.4) | 60.4 (55.2–63.1) | 266.6 (262.5–269.5) | 95 (94–95) | 2.25× | 7.81× | 5/5 |
| locmap | 394.9 (389.0–418.7) | 73.1 (72.1–77.5) | 259.4 (257.6–266.8) | 94 (94–95) | 1.93× | 6.45× | 5/5 |


读表时注意：三种模式的**动作时间几乎一样**（median 约 259–267 ms），主要是 3 次 xdotool 点击 + `set_text` + Escape + `ctrl+s`。加速来自观察/定位：original 每步前后都全量扫树（每次走过 992 个节点，观察+定位 median 471.7 ms），cache 用增量缓存（95 个节点，60.4 ms，观察+定位约快 **7.81×**），locmap 在 cache 上再加坐标图和窗口几何校验（94 个节点，73.1 ms，约快 **6.45×**）。

在这个任务里 locmap 的端到端略慢于 cache（394.9 ms vs 339.6 ms），是因为查找本身只要 0.01 ms，但每次点击前的窗口几何校验要约 12 ms；cache 在已有快照上定位只要约 0.02 ms。两端相对 original 仍然分别是 **2.25×** 和 **1.93×**。如果点击目标经常要重新扫树才能找到，坐标图的优势会更大。

English: same scripted Mousepad task, no LLM, 5 alternating runs per mode. original = full AT-SPI rescan before and after every step; cache = event-driven incremental tree; locmap = cache plus classified coordinate map. Setup (launching Mousepad) is excluded from the timed total. Every successful run verified the marker was saved to the temp file.


## 四种模式对照基准（含默认 HYBRID）

日期：2026-11-26 00:00（Asia/Shanghai）。命令：`linux-use bench --runs 5`。  
同一段无 LLM 的 Mousepad 脚本（与上一节三种模式相同的任务），四种模式轮转，各 5 次，共 20 次，**全部成功**（成功率 5/5），每次都把标记 `LINUX_USE_BENCH_OK_20261125` 写进 `/tmp/linux-use-bench-pad.txt`。启动窗口的等待不计入任务时延。测完桌面只留下 xfce4-panel。

新增的 **hybrid** 是产品默认策略（`LINUX_USE_POLICY=hybrid`）：按动作在 AT-SPI 原语、缓存树、分类坐标图之间自动选择，动作后做廉价定向校验；校验失败则增量刷新并重试一次，全量重扫作为最后手段。每一步记录路径。

| 模式 | 任务总时延 median (min–max) ms | 观察+定位 median (min–max) ms | 动作 median (min–max) ms | 走过节点 median (min–max) | 相对 original 加速（总时延） | 相对 original 加速（观察+定位） | 成功率 |
|--|--|--|--|--|--|--|--|
| original | 758.9 (750.7–15277.0) | 473.2 (464.5–14990.8) | 263.5 (261.1–266.0) | 992 (992–992) | 1.00× | 1.00× | 5/5 |
| cache | 337.2 (320.6–360.2) | 62.5 (53.1–64.8) | 261.9 (256.3–279.4) | 94 (94–95) | 2.25× | 7.57× | 5/5 |
| locmap | 390.7 (379.7–401.3) | 73.4 (68.8–78.5) | 259.4 (257.2–263.8) | 94 (94–95) | 1.94× | 6.45× | 5/5 |
| hybrid（默认） | 145.2 (132.9–149.6) | 81.4 (76.1–89.6) | 53.4 (49.5–57.8) | 90 (90–90) | 5.23× | 5.82× | 5/5 |

决策规则（由上一轮三模式数据和本轮路径日志共同确定）：

1. 有可用的无障碍原语时，优先 `do_action` / `set_text` / `grab_focus`，而不是坐标点击。
2. 上一份快照仍可信时（无结构性失效），在缓存树上内存查找（约 0.02 ms），不要做 locmap 的窗口几何校验（约 5–12 ms）。
3. 没有新鲜快照、目标已在坐标图中、窗口几何未变、该区域无相关失效、且控件是静态的（按钮 / 菜单栏 / 标签页 / 工具栏等）时，走 locmap 直接点坐标。
4. 动态内容（文本、列表、网页、滚动区）、滚动/缩放/导航之后、未知目标、或 locmap 校验失败时，走 cache / 增量刷新。下拉 `menu_item` 不算静态（窗口几何不够用来判断菜单是否还开着）。
5. 动作后做廉价定向校验（焦点 / 文本 / 展开状态 / 文件内容）。失败：增量刷新并重试一次；再失败才全量重扫。

本轮 5 次 hybrid 的路径完全一致：

| 步骤 | 路径 | 典型动作时延 | 校验 |
|--|--|--|--|
| 初始观察 | full | — | 约 90 个节点 |
| 点击文本区 | cache → AT-SPI `grab_focus` | 0.5–0.8 ms | showing 约 0.5–2 ms |
| 写入标记 | atspi `set_text` | 2.3–3.5 ms | 文本含标记约 2–6 ms |
| 点击 File 菜单 | cache → AT-SPI `click` | 0.2–0.3 ms | menu-open 约 27–31 ms |
| Escape | shortcut | 16–18 ms | menu-closed 约 1–2 ms |
| ctrl+s | shortcut | 30–37 ms | 最终读文件确认标记 |

hybrid 端到端约快 **5.23×**，主要是因为动作不再走两次约 105 ms 的 xdotool 点击。观察+定位（81 ms）略慢于纯 cache（62 ms），因为多了菜单打开校验。本任务始终有新鲜缓存树，所以没有走到 locmap；这与三模式结论一致：有现成快照时 cache 定位比 locmap 几何校验更便宜。

脚注：original 的第 3 次观察出现一次约 15 s 的 AT-SPI 停顿（总时延 15277 ms，观察 14991 ms）；其余 4 次 original 在 750.7–790.8 ms。表中同时给出 min–max，比较时请用 median。

English: four-mode rerun of the same scripted Mousepad task, no LLM, 5 alternating runs per mode (20/20 OK). hybrid is the new default: AT-SPI primitives first, in-memory cache locate when the last snapshot is trustworthy, locmap only for static chrome when no fresh snapshot is available, cheap post-action verify with one incremental retry. Median total 145.2 ms (5.23× vs original 758.9 ms). One original run hit a 15 s AT-SPI stall (max 15277 ms); compare medians. Every run saved the marker. Desktop left tidy.
