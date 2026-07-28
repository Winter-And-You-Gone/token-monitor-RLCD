# rlcd-pet-zcode

把 **zcode** 工作状态推送到 [token-monitor-RLCD](..) 摆件动画的 zcode 插件。

本插件复用项目的 `bridge/pet_hook.js`(单一真相源),不重复实现状态映射逻辑。
zcode 触发的 hook 事件经 `run-hook.cmd` 转发到 `pet_hook.js --agent zcode`,
后者 POST 到 bridge 的 `/api/pet/state`,bridge 把 zcode 当成一个新 agent 纳入
多会话仲裁——与 Claude Code / Codex 并发时会自动切 `headphones-groove` /
`working-building` 分档动画(见 `bridge/bridge.py` 的 working tier 注释)。

## 事件映射

| zcode hook 事件 | 摆件状态 | 说明 |
|-----------------|---------|------|
| `SessionStart` | `idle` | 会话开始,摆件待命 |
| `UserPromptSubmit` | `thinking` | 用户提交 prompt,摆件思考 |
| `PreToolUse` | `working` | 工具调用前,摆件工作 |
| `PostToolUse` | `working` | 工具调用后,保持工作 |
| `PostToolUseFailure` | `error` | 工具调用失败,摆件报错 |
| `Stop` | `attention` | 回合结束,摆件提示 |
| `PermissionRequest` | `notification` | 权限请求,摆件通知 |

> 事件名→状态映射实际由 `bridge/pet_hook.js` 的 `EVENT_TO_STATE` 表决定,
> 本插件只是转发事件名。改映射改 `pet_hook.js` 一处即可。

## 启用步骤

zcode 通过 `~/.zcode/cli/config.json` 的 `plugins.dirs` 发现本地插件
(不走 settings.json hooks,也不需要 marketplace 注册)。把本插件目录加进去:

```jsonc
// ~/.zcode/cli/config.json
{
  "mcp": { /* ... */ },
  "plugins": {
    "dirs": [
      "X:\\ESP32-S3 RLCD\\token-monitor-RLCD\\rlcd-pet-zcode"
    ],
    "enabledPlugins": {
      "superpowers@zcode-plugins-official": true,
      "rlcd-pet-zcode@inline": true
    }
  }
}
```

要点:
- `dirs` 的值是**插件根目录**(含 `.zcode-plugin/plugin.json` 的目录),不是它的父目录。
- `enabledPlugins` 的 key 必须是 `<name>@inline`——`@inline` 后缀来自 `plugins.dirs`
  来源的 marketplace 标记(zcode 写死常量 `Sxt="inline"`)。
- 改完 **重启 zcode 会话** 才会加载新配置。

## 工作原理

```
zcode 生命周期事件
  └─ hooks/hooks.json 匹配事件,调 run-hook.cmd <EventName>
       └─ run-hook.cmd 用 %CLAUDE_PLUGIN_ROOT% 反推项目根,调:
            node bridge/pet_hook.js <EventName> --agent zcode
            └─ pet_hook.js 读 bridge/.env 拿 token/URL,POST /api/pet/state
                 └─ bridge 状态机仲裁 → 固件长轮询 → 摆件动画
```

`CLAUDE_PLUGIN_ROOT` 由 zcode 注入(见 `zcode.cjs` 的 `Vkt` 环境注入函数),
指向本插件根目录。`run-hook.cmd` 用 `..` 反推到项目根找 `bridge/pet_hook.js`
(本插件目录就在项目根下一层)。

## 手动验证

不需要重启 zcode,直接模拟 hook 调用验证链路:

```bat
:: 1. 起 bridge(在 bridge/ 目录)
uv run python bridge.py

:: 2. 模拟 zcode 注入 PLUGIN_ROOT,直接调 run-hook.cmd
set CLAUDE_PLUGIN_ROOT=X:\ESP32-S3 RLCD\token-monitor-RLCD\rlcd-pet-zcode
"X:\ESP32-S3 RLCD\token-monitor-RLCD\rlcd-pet-zcode\hooks\run-hook.cmd" PreToolUse

:: 3. 查摆件状态,应看到 agent=zcode, state=working
curl http://127.0.0.1:7777/api/pet/state
```

## 卸载

删 `~/.zcode/cli/config.json` 里 `plugins.dirs` 的本插件条目和
`enabledPlugins` 的 `rlcd-pet-zcode@inline` 条目,重启 zcode 即可。
不影响 bridge、固件或其他 agent。
