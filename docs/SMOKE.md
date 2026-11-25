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

