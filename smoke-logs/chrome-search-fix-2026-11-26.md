# linux-use Chrome 定位/输入修复与复测（2026-11-26，Asia/Shanghai）

复测时间：2026-11-26 21:36–21:43（Asia/Shanghai，UTC+8）。  
对照报告（改代码之前）：`/workspace/linux-use/smoke-logs/chrome-search-test-2026-11-26.md`  
原始日志与截图：`/workspace/linux-use/smoke-logs/chrome-search-2026-11-26/`（`results-postfix.json`、`A/B/C-postfix-results.png`）。  
数字均为实测，没有编造。未使用 `linux-use run`（无 LLM 密钥）。未推送、未开 PR。

目标：让 linux-use 在这台云电脑上更快、更稳地找到目标并操作，尤其是 Chrome——昨晚 Mousepad 上的加速并没有自动带到浏览器。

## 改了什么

全部改动在 `/workspace/linux-use` 仓库内（版本仍是 0.1.0）。

| 文件 | 行为 |
|--|--|
| `linux_use/web.py`（新） | 识别 Chrome/Firefox 等网页工具包输入框和地址栏；role/name 别名匹配（`entry`↔`text` 等）。 |
| `linux_use/chrome.py`（新） | `launch_accessible_chrome()`：先启动 AT-SPI 总线，再开一只带 `--force-renderer-accessibility`、独立 `--user-data-dir`（默认 `/tmp/linux-use-chrome-a11y`）的 Chrome。不会附着到、也不会重启已经登录的配置。 |
| `linux_use/hybrid.py` | 对网页工具包输入框 **优先走键盘路径**（点击或 `ctrl+l`，再走现有 xdotool type），`path=xdotool-web`，**不再调用 `set_text`**。GTK/Mousepad 仍走 `set_text`（`path=atspi`）。定位时放宽 role 别名，并匹配 name/text/value。 |
| `linux_use/platform/linux_a11y.py` | 默认 inspect **不深入** `document web` 子树（保留文档节点，可见标签/页面标题）。`include_web=True` 时仍有时间上限，并在子节点循环里检查预算，避免一次全量扫描卡十几秒。 |
| `linux_use/tools.py` | `act("type", name=..., role=...)` 可用；`act("app", action="launch-chrome")`；`inspect(include_web=...)`。 |
| `linux_use/api.py`、`__init__.py`、`cli.py`、`config.py` | 导出 `launch_accessible_chrome`、`inspect_desktop(..., include_web=)`；CLI 增加 `--include-web`；环境变量见 README。 |
| `tests/test_hybrid_policy.py` | 新增 Chrome 键盘路径、GTK `set_text` 不被破坏等测试。 |
| `README.md`、`docs/SMOKE.md` | 中英双语说明了新的回退路径、inspect 默认跳过网页子树、以及 `launch-chrome`。 |

相关环境变量：`LINUX_USE_INCLUDE_WEB`、`LINUX_USE_WEB_WALK_BUDGET_MS`（默认 400）、`LINUX_USE_CHROME_USER_DATA_DIR`、`LINUX_USE_CHROME_NO_SANDBOX`（默认 1，普通桌面可设 0）。

未做 git 发布。若要开 PR，等你决定。

## 复测方法

继续使用先前那只独立测试 Chrome（pid 117780，`--force-renderer-accessibility`，配置目录 `/tmp/linux-use-chrome-a11y-20261126`）。**没有**动 pid 105724 的小红书登录窗口。

三次搜索都只通过公开 API：

```python
act("type", name="Address and search bar", role="entry",
    text="https://www2.bing.com/search?q=…",  # 百分号编码
    clear=True, press_enter=True)
inspect_desktop()          # 默认不扫网页子树
```

`www.bing.com` 在这只测试 Chrome 里此前会 Access Denied，因此仍用 `www2.bing.com`。

## 改前 / 改后数字

### 1) 输入路径（地址栏）

| | 改前（2026-11-26 21:18 一轮） | 改后（21:42 复测） |
|--|--|--|
| 路径 | HYBRID `set_text` → `retry-incremental`，文字仍是 `about:blank` | `path=xdotool-web`，`set_text_attempted=False` |
| 三次搜索是否提交成功 | 0/3 | **3/3**（标题分别为「上海明天天气 / AT-SPI 无障碍树 / 哔哩哔哩 - Search」） |
| 单次 `type` 墙钟 | set_text 本身约 33–55 ms，但随后空等 25–30 s；A/B/C 合计 **122.21 s** 且失败 | A **955.2 ms**（85 字 URL）、B **827.6 ms**（76 字）、C **728.0 ms**（67 字） |
| 定位 locate_ms | （失败路径里被淹没） | **0.12–0.21 ms**（缓存树） |
| a11y verify | 失败且不回退到键盘 | 软校验 2–4.5 ms（地址栏 a11y 文本往往对不齐，**不作为失败**） |
| 从新标签到标题确认 | 失败 | A **2.793 s**、B **2.647 s**、C **2.545 s**（含约 1.2 s 最短等待）；三段合计 **7.98 s** |

截图确认：

- A：国际版布局，约 3,060 条，可见「上海天气预报」「明天天气 - 上海市」等（`A-postfix-results.png`）。
- B：查询已提交，国内版正文仍是 “There are no results for AT-SPI 无障碍树”（`B-postfix-results.png`）。这是必应结果，不是定位失败。
- C：约 1,650,000 条，可见 bilibili.com 等（`C-postfix-results.png`）。

**搜索动作 3/3 成功；页面上能看到相关结果条目 2/3**（A、C；B 与改前一样，国内版零结果）。

### 2) inspect / 定位

对象：已打开的必应结果标签「上海明天天气 - Search」（与改前那次 13.5 s 全量扫描同类页面）。

| 操作 | 改前 | 改后（observe_ms / wall_ms） |
|--|--|--|
| 切回结果标签后的默认/全量 inspect | **13513.9 ms**（227 节点，`mode=full`） | 默认 125.9 / 136.6 ms；`force=True` 外壳 114.8 / 124.0 ms（219 节点，`web-subtrees-skipped=2`） |
| 等待结果页时的 inspect | 脚本里常用 `force=True` 全量扫 | `inspect_desktop()` 增量，最后一次 observe **1.6–3.3 ms** |
| `include_web=True` | 改代码前未单独测；13.5 s 就是在走网页子树 | 子循环补上时间上限之后：155.8 / 166.8 ms（288 节点）。说明：刚加上「跳过子树」但还没在子循环里检查预算时，曾测到一次 **14641.0 ms** 且 `truncated=True`；补上循环内检查后再跑两次约为 161–164 ms。以循环修复后的数字为准，并记下那次 14.6 s 的弯路。 |

不能把「外壳 inspect ~120 ms」直接说成比旧桌面 agent 更快：这只说明默认不再把必应结果页整棵网页子树走一遍。

### 3) Mousepad / GTK 有没有回退

安装 Mousepad 后跑了一次 `linux-use smoke`（不是昨晚那组 5 次四模式基准）：

- `type_marker`：**hybrid atspi set_text**，27 字，`text ok=True`，`act=7.5 ms`
- 文件里含标记，冒烟 **全部 OK**（`SMOKE_EXIT=0`）
- 冷观察 141.3 ms（full，280 节点）；热观察 1.2–1.3 ms（incremental）

GTK `set_text` 路径还在。没有重跑昨晚 hybrid 中位 145.2 ms 的五轮对照，所以不把这次冒烟说成「仍然是 145.2 ms」。

单元测试：`tests/test_hybrid_policy.py` 等 22 项通过（含「Chrome 走键盘、Mousepad 仍 set_text」）。

## 仍然存在的缺口

- `www.bing.com` / `cn.bing.com` 在这只测试 Chrome 里仍可能 Access Denied（同机 curl 却是 200）。复测继续用 `www2.bing.com`。
- B 的国内版仍然没有结果条目，需要换国际版或换查询才能看到文档；linux-use 没有替必应「造」出结果。
- 地址栏 `set_text` 本身依然无效，只是不再走它。向页面内部搜索框直接输入中文，这次没有作为主路径复测（主路径是编码后的搜索 URL）。
- 在 AT-SPI 起来之前就打开的登录 Chrome（小红书）**仍然不在树里**。这是预期行为；请用 `launch_accessible_chrome` / `launch-chrome` 另开实例，不要重启已登录的配置。
- `include_web=True` 在极端慢的 AT-SPI 调用上，单次 `get_child` 仍可能堵住预算检查之前的那一下。预算是「尽量」上限，不是硬实时。
- 没有也不应声称：改完之后 linux-use 整体已经比「原来那套桌面操作」更快。昨晚 5.23× 只对 Mousepad 那条脚本成立。这次证明的是：Chrome 地址栏不再空等半分多钟，默认 inspect 不再动辄 13 s。

## 已有登录窗口

复测结束时：

- pid 105724：`小红书 - 你的生活兴趣社区 - Google Chrome`（原登录会话，未退出、未关标签）
- pid 117780：测试用 Chrome，当时标题 `哔哩哔哩 - Search - Google Chrome`

## 建议的发布方式

改动已在本地完成并复测。用户没有要求现在发布。若要推到 GitHub，建议单独开 PR，把本报告和上一份对照报告一并附上，由你决定是否推送。
