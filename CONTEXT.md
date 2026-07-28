# RLCD Token Monitor

桌面摆件，在反射式 LCD 屏上实时展示 AI agent 的 token 用量，辅以天气、室内温湿度和动画宠物 Clawd。

## Language

**用量 (Usage)**：
一次桥接刷新周期内上报的全部 Agent 消耗与余额聚合数据。
_Avoid_：消费、配额

**Agent**：
产生 token 消耗的 AI 服务账号，每个 Agent 有独立的用量维度。当前包含 Claude、DeepSeek、Codex。
_Avoid_：Provider、模型

**Bucket (时窗桶)**：
某一 Agent 在特定时间窗口内的 token 消耗量与费用。包含 `tokens_used` 和 `cost_usd`。

**今日 (Today)**：
从当天零点到当前时刻的累计消耗。
_Avoid_：当天

**本月 (Month)**：
从当月首日零点到当前时刻的累计消耗。
_Avoid_：月度

**合计 (Lifetime)**：
从该 Agent 有记录以来的全部累计消耗。
_Avoid_：总计、总量

**余额 (Balance)**：
DeepSeek 账户的剩余可用余额（¥）。
_Avoid_：配额、剩余

**桥接 (Bridge)**：
运行在宿主机上的 Python 守护进程，从 ccusage / DeepSeek API / 天气源聚合数据，通过 `:7777` HTTP 提供给固件。
_Avoid_：后端、Server

**固件 (Firmware)**：
ESP32-S3 上的 ESP-IDF 固件，驱动 RLCD 屏、LVGL UI、传感器，每 60s 轮询桥接获取用量数据。

**ccusage**：
Node.js CLI 工具，解析 `~/.claude/projects/**/*.jsonl` 会话日志，输出 Claude 和 DeepSeek 模型的 token 统计。

**降级 (Degradation)**：
当桥接不可达或某数据源失败时，对应屏面区域显示 `--`。

## Relationships

- 一次桥接刷新产出一个 **用量**，包含多个 **Agent** 的数据
- 一个 **Agent**（Claude / Codex）包含 "今日"、"本月"、"合计" 三个 **Bucket**
- 一个 **Agent**（DeepSeek）包含 **余额** 和 "今日" token 数
- **固件** 每 60s 向 **桥接** 请求一次全量 **用量**
- **桥接** 依赖 **ccusage**（本地 CLI）和 DeepSeek API（远程）获取原始数据

## Example dialogue

> **Dev**："Claude 用量和 Codex 用量在屏上是分开的轮播页还是合并的？"
> **Domain expert**："分开的三个轮播页——Claude、DeepSeek、Codex 各占半屏，每 5 秒自动翻页。"

> **Dev**："如果桥接的 ccusage 调用超时了，固件上 Claude 的今日用量显示什么？"
> **Domain expert**："`--`。保持降级语义，不显示过期数据。"

## Flagged ambiguities

- "weekly" 曾是 Claude Agent 的第四个 Bucket，已被移除——屏上只渲染今日/本月/合计三维。
- "active_block"（5 小时计费窗口）和 "projection"（投影预测）曾是 schema 字段，从未在屏上渲染，已移除。
- "限额 (limits)"——5h/7d 利用率条——编译开关 `SHOW_CLAUDE_LIMITS=0`，已从领域模型中移除。