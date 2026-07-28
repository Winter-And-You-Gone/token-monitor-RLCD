# rlcd-pet-opencode

把 **opencode** 工作状态推送到 [token-monitor-RLCD](..) 摆件动画的 opencode 插件。

opencode 自带 Clawd on Desk 的 `opencode-plugin`(事件发 Clawd 23333),但**没有**
把事件发给 RLCD bridge(7777),导致 Clawd 比 RLCD 多算一个 agent。本插件补上这条
通路:监听同样的 opencode 生命周期事件,POST 到 bridge 的 `/api/pet/state`,
bridge 把 opencode 当成一个新 agent 纳入多会话仲裁--与 Claude Code / Codex /
zcode 并发时会自动切 `headphones-groove` / `working-building` 分档动画。

## 事件映射

| opencode 事件 | 摆件状态 | 对应 hook 事件 |
|---------------|---------|---------------|
| `session.created` | `idle` | SessionStart |
| `session.status` (busy) | `thinking` | UserPromptSubmit |
| `message.part.updated` (tool running) | `working` | PreToolUse |
| `message.part.updated` (tool completed) | `working` | PostToolUse |
| `message.part.updated` (tool error) | `error` | PostToolUseFailure |
| `message.part.updated` (compaction) | `sweeping` | PreCompact |
| `session.compacted` | `sweeping` | PreCompact |
| `session.idle` | `attention` | Stop |
| `session.error` | `error` | StopFailure |
| `session.deleted` / `server.instance.disposed` | `sleeping` | SessionEnd |

> 事件名->状态映射实际由 `bridge/pet_hook.js` 的 `EVENT_TO_STATE` 表决定,
> 本插件只是转发状态。改映射改 `pet_hook.js` 一处即可。

## 启用步骤

opencode 通过 `~/.config/opencode/opencode.json` 的 `plugin` 数组加载插件
(Bun runtime,在 opencode 进程内运行)。把本插件目录加进去:

```jsonc
// ~/.config/opencode/opencode.json
{
  "plugin": [
    "X:/ClawdOnDesk/Clawd on Desk/resources/app.asar.unpacked/hooks/opencode-plugin",
    "X:/ESP32-S3 RLCD/token-monitor-RLCD/rlcd-pet-opencode"
  ]
}
```

两个插件并列共存:Clawd 的发 23333,本插件的发 7777,互不干扰。

改完 **重启 opencode 会话** 才会加载新插件。

## 工作原理

```
opencode 生命周期事件 (Bun runtime, in-process)
  └─ index.mjs translateEvent() 映射为 (state, event)
       └─ postToBridge() fire-and-forget POST /api/pet/state (port 7777)
            └─ bridge 读 bridge/.env 拿 token/URL,带 X-RLCD-Token 认证
            └─ bridge 状态机仲裁 -> 固件长轮询 -> 摆件动画
```

插件读 `bridge/.env` 获取 `RLCD_BRIDGE_URL` / `RLCD_AUTH_TOKEN`(和 `pet_hook.js`
相同的认证方式)。fire-and-forget:bridge 慢或停时不阻塞 opencode。

## 手动验证

不需要重启 opencode,直接模拟事件验证链路:

```bat
:: 1. 起 bridge(在 bridge/ 目录)
uv run python bridge.py

:: 2. 模拟 opencode 工具调用 -> bridge 应显示 agent=opencode, state=working
node bridge/pet_hook.js PreToolUse --agent opencode

:: 3. 查摆件状态
curl http://127.0.0.1:7777/api/pet/state
```

## 卸载

删 `~/.config/opencode/opencode.json` 里 `plugin` 数组的本插件条目,重启 opencode
即可。不影响 bridge、固件、Clawd 或其他 agent。
