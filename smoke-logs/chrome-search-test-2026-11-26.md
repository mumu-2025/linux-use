# linux-use Chrome 搜索实测（2026-11-26，Asia/Shanghai）

测试时间：2026-11-26 21:13–21:31（Asia/Shanghai，UTC+8）。  
程序：`linux-use 0.1.0`（`/workspace/linux-use`，默认策略 HYBRID）。  
**未使用** `linux-use run`（当前没有 `LINUX_USE_API_KEY` / `OPENAI_API_KEY`）。全部动作走 `inspect` / `act` / Python API（`inspect_desktop`、`act`、`input.type_text`）。  
数字均为实测，没有编造。原始日志与截图在 `/workspace/linux-use/smoke-logs/chrome-search-2026-11-26/`。

## 环境与安装

- 会话：`DISPLAY=:4`（X11），`DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/xdg-runtime-box-4/bus`
- 开始时 **venv 缺失**：`/home/box/.local/bin/linux-use` 指向不存在的 `/workspace/linux-use/venv/bin/linux-use`
- 已重建，未做 git commit：

```bash
cd /workspace/linux-use && python3 -m venv --system-site-packages venv && ./venv/bin/pip install -e .
```

- 重建后 `linux-use --version` 为 `linux-use 0.1.0`，CLI 可运行；但第一次 `linux-use inspect` **失败**：

```
RuntimeError: AT-SPI accessibility bus is not available. Install at-spi2-core and make sure a D-Bus session is running.
```

- 原因：本机当时没有 `at-spi-bus-launcher`，也没有 `Atspi` typelib，`python3-dbus` 也未装；apt 列表为空。已 `apt-get update` 并安装：
  - `at-spi2-core` 2.56.2-1+deb13u2
  - `gir1.2-atspi-2.0` 2.56.2-1+deb13u2
  - `python3-dbus` 1.4.0-1
  - `python3-pyatspi` 2.46.1-1
- 装完后 AT-SPI 总线可启动（`unix:path=/tmp/xdg-runtime-4/at-spi/bus_4`），`inspect` 可以跑通。

## 第一步：inspect 能否看到 Chrome

装好 AT-SPI 后立刻全量 inspect：

| 时刻 | observe_ms | nodes | Chrome 是否在树里 |
|--|--|--|--|
| Chrome 启动前（已有登录用 Chrome） | 1.049（另一次 Python API 为 1.621） | 0 | 否 |
| 用 linux-use 新开带无障碍标志的 Chrome 后约 2 秒 | 87.958 | 159 | 是，`active_app=Google Chrome` |

先前登录检查留下的 Chrome（pid 105724，标题「小红书 - 你的生活兴趣社区 - Google Chrome」）**全程未退出、未关标签**。它启动于 AT-SPI 总线之前，且没有 `--force-renderer-accessibility`，所以不在无障碍树里。

为了测试又不碰到已有标签，用 linux-use `act("app", action="launch")` 另开了一个独立配置目录的窗口：

- 命令含 `--force-renderer-accessibility --user-data-dir=/tmp/linux-use-chrome-a11y-20261126`
- `act_ms=401.05`（其中启动函数本身会固定 sleep 0.4 秒）
- 约 2 秒后树里能看到地址栏：`entry "Address and search bar" text='about:blank'`
- 最大化按钮：HYBRID `path=cache`，经 AT-SPI `click`，wall **0.7 ms**

结论：linux-use **能看到** 带 `--force-renderer-accessibility`、在总线起来之后启动的 Chrome 外壳（标签、地址栏、工具栏按钮）。**不能** 看到此前已打开的那只 Chrome。

## 三次搜索结果表

先说结论：**三次搜索最终都通过 linux-use 提交，并打开了对应的必应结果页**（宿主是 `www2.bing.com`，原因见后）。其中 A、C 能看到相关结果条目；B 的国内版结果页是「没有结果」。

第一次完全走 AT-SPI `set_text` 的尝试 **0/3 失败**（地址栏文字没被改掉，页面停在 about:blank / New Tab）。成功路径是：linux-use 快捷键 `ctrl+l` / `ctrl+a` + 点击地址栏（HYBRID `cache` + AT-SPI click）+ **linux-use 内置 xdotool 输入** 百分号编码后的 ASCII URL。这仍是 linux-use 的输入后端，不是另外手写的 xdotool 脚本。

| 搜索 | 成功与否 | 成功路径总耗时（约） | 关键 observe_ms | 关键 act_ms | path | 说明 |
|--|--|--|--|--|--|--|
| A. 先打开必应再搜 `上海明天天气` | **成功**（国际版可见天气预报类结果；国内版先显示无结果） | 新标签到标题确认约 **3.18 s**；再点「国际版」后约 **15 s** 内看到 3,060 条结果 | 打开后 inspect 135.2；国际版后树 288 节点 | 点地址栏 cache/AT-SPI **0.9 ms**；键入 `https://www2.bing.com` **177.7 ms**（21 字）；键入编码搜索 URL **741.5 ms**（85 字）；点「国际版」cache/AT-SPI **0.54 ms** | 定位 `cache`；输入 `xdotool` | `https://www.bing.com` 在这只测试 Chrome 里被转到 `cn.bing.com` 并显示 Akamai **Access Denied**。改开 `https://www2.bing.com` 后出现必应首页搜索框。国内版结果页标题已是「上海明天天气 - Search」，但正文「There are no results」；点击树里的「国际版」后可见「上海天气预报」「明天天气 - 上海市」等。第一次只用 `set_text` 耗时 **56.927 s** 且失败。 |
| B. `AT-SPI 无障碍树` | **部分成功**：查询已提交、结果页已打开，国内版零结果 | 键入搜索 URL **651.6 ms**（76 字）+ 之后切回该标签确认；确认时一次全量 inspect 很慢 | 确认 inspect **13513.9 ms**（227 节点，异常慢） | 新标签快捷键 **43.6 ms**；点地址栏 **0.6 ms**（`cache`）；键入 URL **651.6 ms**；切标签 `cache`/AT-SPI **1.1 ms** | 定位 `cache`；输入 `xdotool` | 地址栏和搜索框均为 `AT-SPI 无障碍树`，标题「AT-SPI 无障碍树 - Search」。页面正文是 “There are no results for AT-SPI 无障碍树”。未再切国际版。第一次 `set_text` 失败，耗时 **32.961 s**。 |
| C. `哔哩哔哩` | **成功**，可见相关结果 | 键入搜索 URL **613.1 ms**（67 字）；稍后确认 wait **483.7 ms** | 确认 inspect **162.1 ms**（249 节点） | 新标签 **41.7 ms**；点地址栏 **0.7 ms**（`cache`）；键入 URL **613.1 ms** | 定位 `cache`；输入 `xdotool` | 结果页标题「哔哩哔哩 - Search」，搜索框文字「哔哩哔哩」，树里有 “1,650,000 results”，截图可见 bilibili.com、哔哩哔哩下载中心、弹幕视频网等。第一次 `set_text` 失败，耗时 **32.326 s**。 |

截图（成功确认）：

- A 国际版天气结果：`chrome-search-2026-11-26/A-international.png`
- B 国内版零结果：`chrome-search-2026-11-26/B-confirm-results.png`
- C 哔哩哔哩结果：`chrome-search-2026-11-26/C-confirm-results.png`
- 必应首页（www2）曾成功打开：`chrome-search-2026-11-26/A-r2-www2-bing.png`
- www.bing.com / cn.bing.com 被拒：`chrome-search-2026-11-26/A-r2-www-bing.png`

## 总成功率与总耗时

- **搜索动作（打开正确查询的必应结果页）：3/3**
- **页面上能看到与查询相关的结果条目：2/3**（A 国际版、C；B 国内版无结果）
- 第一次纯 `set_text` 尝试：0/3，三段合计 **122.21 s**（大部分耗在 25–30 秒的超时等待上）
- 成功确认脚本自身步骤墙钟合计 **18.71 s**（含 B 那一次 13.5 s 的慢 inspect）；再加上为 A 点「国际版」并取证约 **15 s**
- 整场实测（重建 venv、装 AT-SPI、失败轮次、一次 xclip 卡住后被杀掉、再重试）从 21:13 到 21:31，大约 **18 分钟**。中间有一轮剪贴板回退把 `xclip` 卡成僵尸进程，已杀掉，后来改用百分号编码 URL，不再依赖 xclip

## 和昨晚 Mousepad 基准比（不要夸大）

昨晚（2026-11-26 00:00，Asia/Shanghai）无 LLM 四模式基准是 **Mousepad（GTK 原生编辑器）**，不是浏览器：

- hybrid 中位 **145.2 ms**，original 中位 **758.9 ms**，5/5 成功
- hybrid 能快，主要是因为文本框走 AT-SPI `set_text`（约 2–4 ms）、菜单走 AT-SPI click（约 0.2–0.3 ms），缓存一直可信

这次 Chrome 上的观察：

- **外壳控件**（最大化、地址栏点击、标签点击、「国际版」）HYBRID 经常走 `cache` + AT-SPI `do_action`，单次 wall 约 **0.5–1.1 ms**，这一点和 Mousepad 上「原语很快」是一类现象，但任务不同，不能说「Chrome 也比 original 快 5.23 倍」。
- **地址栏 `set_text` 无效**：hybrid 报 `path=retry-incremental`，`set_text [e23] … text ok=False got='about:blank'`（或空串）。这正是昨晚 Mousepad 上 hybrid 最快的那条路径，在 Chrome omnibox 上走不通。
- 网页内部一开始经常几乎没有可用节点（New Tab 里连 Google 搜索框都不在树上）；结果页加载后会出现「Enter your search here」输入框和 Search Results landmark。树比 Mousepad 稀疏、也更不稳定。
- 一次普通 Chrome inspect 约 **90–170 ms**、160–250 节点；但切到 B 的结果标签时，一次全量 inspect 测到 **13513.9 ms**。昨晚 original 模式也出现过一次约 15 s 的观察停顿。不能据此宣称 hybrid 在 Chrome 上整体更快。
- **因此：不能说明 linux-use 在 Chrome 里已经比原来的操作方式更快。** 昨晚的加速数字只对 Mousepad 那次脚本成立。

## 阻碍与注意点

1. **运行前缺 venv、缺 at-spi2-core / typelib / python3-dbus**。不装的话 inspect 直接报错，树是空的。
2. **AT-SPI 启动之前就打开的应用不会进树**。已登录的那只 Chrome 必须保持不动时，只能另开带 `--force-renderer-accessibility` 的实例。
3. **Chrome 地址栏看得到，但 `set_text` 改不了内容**。hybrid 在校验失败后不会自动退回 xdotool 输入，测试里是显式改走 linux-use 的 `input.type_text`。
4. **页面搜索框经常不在树上**，所以查询是用百分号编码后的 `https://www2.bing.com/search?q=…` 打进地址栏，而不是在必应首页输入框里敲中文。首页其实曾打开过，但当时用于判定的等待条件被其他标签里的 “Access Denied” 节点提前触发，第一次没有在首页框里搜。
5. **`https://www.bing.com` 在测试 Chrome 里遇到 Access Denied**（`http://cn.bing.com/`，Akamai errorsuite）。同机 `curl` 访问 cn.bing.com 却是 HTTP 200 且能拿到必应首页 HTML。`www2.bing.com` 在 Chrome 里可用。这是站点/环境问题，不是 linux-use 定位失败。
6. 曾尝试用 xclip + `ctrl+v` 贴中文，`xclip` 父进程未退出、变成僵尸，脚本卡住约 5 分钟，已终止。之后只用编码 URL，避免了这个问题。
7. 本机当前没有安装 Mousepad，这次没有重跑昨晚那条 GTK 基准。

## 对已有登录窗口的影响

测试结束时窗口仍在：

- pid 105724：`小红书 - 你的生活兴趣社区 - Google Chrome`（原登录会话，未动）
- pid 117780：测试用 Chrome，当前标题 `上海明天天气 - Search - Google Chrome`

没有登出任何网站，也没有关闭原窗口的标签。
