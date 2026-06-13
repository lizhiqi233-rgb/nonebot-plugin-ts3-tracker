# nonebot-plugin-ts3-tracker

基于 NoneBot2 的 TeamSpeak 3 在线查询与进退服通知插件。

插件支持：

- 查询当前在线频道与在线成员
- 查询 TS3 服务器详细信息
- 轮询检测用户进服、退服
- 发送群聊 / 私聊主动通知
- 群级通知开关持久化
- 群白名单限制命令使用范围
- 指定频道有人时自动录音（Rust sidecar）

## 安装

```bash
nb plugin install nonebot-plugin-ts3-tracker
```

或：

```bash
pip install nonebot-plugin-ts3-tracker
```

## 加载插件

`pyproject.toml`：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_ts3_tracker"]
```

## 命令

默认情况下需要带命令前缀：

- `/ts` 或 `/上号`：查看当前在线频道
- `/tsinfo`：查看 TS 服务器详细信息
- `/tsnotify on`：开启本群进退服通知
- `/tsnotify off`：关闭本群进退服通知
- `/tsrecord`：查看频道录音状态

如果配置 `TS3_TRACKER__COMMAND_PREFIX_REQUIRED=false`，则也支持直接发送：

- `ts`
- `上号`
- `tsinfo`
- `tsnotify on`
- `tsnotify off`

命令行为说明：

- `/ts` 和 `/上号` 只显示有人的频道，不显示空频道
- `/tsinfo` 显示服务器地址、端口、名称，以及完整频道列表
- `/tsnotify on/off` 仅可在群聊中使用

## 基础配置

至少需要配置以下环境变量：

```env
TS3_TRACKER__SERVER_HOST=127.0.0.1
TS3_TRACKER__SERVER_PORT=9987
TS3_TRACKER__SERVERQUERY_PORT=10011
TS3_TRACKER__SERVERQUERY_USERNAME=your-serverquery-username
TS3_TRACKER__SERVERQUERY_PASSWORD=your-password
```

完整示例：

```env
HOST=127.0.0.1
PORT=8080

TS3_TRACKER__SERVER_HOST=127.0.0.1
TS3_TRACKER__SERVER_PORT=9987
TS3_TRACKER__SERVERQUERY_PORT=10011
TS3_TRACKER__SERVERQUERY_USERNAME=your-serverquery-username
TS3_TRACKER__SERVERQUERY_PASSWORD=your-password

TS3_TRACKER__DEBUG=false
TS3_TRACKER__COMMAND_PREFIX_REQUIRED=true
TS3_TRACKER__QUERY_TIMEOUT_SECONDS=10

TS3_TRACKER__NOTIFICATION_ENABLED=true
TS3_TRACKER__NOTIFY_TARGET_GROUPS=100000000
TS3_TRACKER__NOTIFY_TARGET_USERS=

TS3_TRACKER__GROUP_WHITELIST_ENABLED=false
TS3_TRACKER__GROUP_WHITELIST_GROUPS=

TS3_TRACKER__POLL_INTERVAL_SECONDS=5
TS3_TRACKER__STARTUP_SILENT=true
TS3_TRACKER__DATA_DIR=data/ts3_tracker
```

## 配置说明

| 配置项 | 说明 |
| --- | --- |
| `TS3_TRACKER__SERVER_HOST` | TS3 服务器地址 |
| `TS3_TRACKER__SERVER_PORT` | TS3 语音端口，默认 `9987` |
| `TS3_TRACKER__SERVERQUERY_PORT` | TS3 ServerQuery 端口，默认 `10011` |
| `TS3_TRACKER__SERVERQUERY_USERNAME` | ServerQuery 登录账号 |
| `TS3_TRACKER__SERVERQUERY_PASSWORD` | ServerQuery 登录密码 |
| `TS3_TRACKER__DEBUG` | 是否输出调试日志 |
| `TS3_TRACKER__COMMAND_PREFIX_REQUIRED` | 是否必须使用命令前缀，默认 `true` |
| `TS3_TRACKER__QUERY_TIMEOUT_SECONDS` | 单次查询超时时间，单位秒 |
| `TS3_TRACKER__NOTIFICATION_ENABLED` | 是否开启轮询通知功能 |
| `TS3_TRACKER__NOTIFY_TARGET_GROUPS` | 默认通知群号，支持逗号、分号、换行分隔 |
| `TS3_TRACKER__NOTIFY_TARGET_USERS` | 默认通知私聊 QQ，支持逗号、分号、换行分隔 |
| `TS3_TRACKER__GROUP_WHITELIST_ENABLED` | 是否开启群白名单模式 |
| `TS3_TRACKER__GROUP_WHITELIST_GROUPS` | 允许使用群命令、允许接收群通知的白名单群号 |
| `TS3_TRACKER__POLL_INTERVAL_SECONDS` | 轮询间隔，单位秒 |
| `TS3_TRACKER__STARTUP_SILENT` | 启动时是否静默建立快照，不立即推送历史变化 |
| `TS3_TRACKER__DATA_DIR` | 自定义数据目录，不填写时使用 `nonebot-plugin-localstore` |
| `TS3_TRACKER__RECORDING_ENABLED` | 是否开启指定频道录音 |
| `TS3_TRACKER__RECORDING_CHANNELS` | 监控频道列表（频道 ID 或名称，逗号/换行分隔） |
| `TS3_TRACKER__RECORDING_IDENTITIES` | 录音 bot identity 文件路径或字符串，多个用逗号/换行分隔 |
| `TS3_TRACKER__RECORDING_OUTPUT_DIR` | 录音输出目录，默认插件目录下 `recordings/` |
| `TS3_TRACKER__RECORDING_SIDECAR_PATH` | sidecar 二进制绝对路径，留空则自动探测 |
| `TS3_TRACKER__RECORDING_SERVER_PASSWORD` | TS 服务器密码（如有） |
| `TS3_TRACKER__RECORDING_CHANNEL_PASSWORD` | 默认频道密码（如有） |
| `TS3_TRACKER__RECORDING_NICKNAME_PREFIX` | 录音 bot 昵称前缀，默认 `RecBot` |
| `TS3_TRACKER__RECORDING_MIN_SESSION_SECONDS` | 低于该秒数的录音会被丢弃，默认 `5` |

## 频道录音

录音功能通过 Rust sidecar（`ts3-recorder-sidecar`）连接 TS 语音协议，在**配置的频道内出现真人用户**时自动混音录制为 WAV。

### 获取 sidecar 二进制

GitHub Actions 会在每次 push 后编译 Linux 版本：

1. 打开仓库 [Actions](https://github.com/lizhiqi233-rgb/nonebot-plugin-ts3-tracker/actions)
2. 选择最新的 **Build recorder sidecar** 工作流
3. 在 **Artifacts** 中下载对应架构：
   - `ts3-recorder-sidecar-aarch64-unknown-linux-gnu`：ARM64（树莓派 64 位、主流 ARM 服务器）
   - `ts3-recorder-sidecar-x86_64-unknown-linux-gnu`：x86_64

解压后安装并配置路径：

```bash
sudo install -m 755 ts3-recorder-sidecar /usr/local/bin/
```

```env
TS3_TRACKER__RECORDING_SIDECAR_PATH=/usr/local/bin/ts3-recorder-sidecar
```

### 录音配置示例

```env
TS3_TRACKER__RECORDING_ENABLED=true
TS3_TRACKER__RECORDING_CHANNELS=5,Lobby,Meeting
TS3_TRACKER__RECORDING_IDENTITIES=/opt/ts3/identity1.txt,/opt/ts3/identity2.txt
TS3_TRACKER__RECORDING_OUTPUT_DIR=/var/lib/ts3-recordings
TS3_TRACKER__RECORDING_NICKNAME_PREFIX=RecBot
```

### 文件布局

```text
{RECORDING_OUTPUT_DIR}/{channel_id}_{channel_name}/2026-06-13_143052.wav
{RECORDING_OUTPUT_DIR}/{channel_id}_{channel_name}/2026-06-13_143052.json
```

### 注意事项

- 每个**同时录制**的频道需要 1 个独立 TS identity
- identity 格式需兼容 [tsclientlib](https://github.com/ReSpeak/tsclientlib)
- 录音 bot 麦克风默认静音，仅接收频道语音
- 部署前请确保参与者知晓录音行为

## 群白名单规则

默认情况下：

- 所有群都可以使用 `/ts`、`/上号`、`/tsinfo`
- 私聊也可以使用查询命令
- 通知只会发送给默认通知群和默认通知私聊

如果开启白名单：

```env
TS3_TRACKER__GROUP_WHITELIST_ENABLED=true
TS3_TRACKER__GROUP_WHITELIST_GROUPS=100000000
```

则行为变为：

- 只有白名单群可以在群聊中使用查询命令
- 私聊查询仍然可用
- 群通知只会发给白名单中的群
- 即使使用 `/tsnotify on`，也仍然受白名单限制

## 群级通知开关

当 `TS3_TRACKER__NOTIFICATION_ENABLED=true` 时，可以在群里动态控制本群是否接收通知：

- `/tsnotify on`：开启本群进退服通知
- `/tsnotify off`：关闭本群进退服通知

说明：

- 该状态会持久化保存，重启后仍然生效
- `/tsnotify on` 可以把当前群加入通知目标
- `/tsnotify off` 可以把当前群从通知目标中关闭
- 如果开启了白名单模式，最终仍然会按白名单过滤

## 查询示例

`/ts` 或 `/上号`：

```text
APEX: TEST
大厅: koishi, Cirno
```

`/tsinfo`：

```text
服务器地址：127.0.0.1:9987
服务器端口：9987
服务器名称：示例 TS3 服务器
服务器频道：
APEX: TEST(42秒)
大厅: koishi(3分12秒), Cirno(1分05秒)
原神
```

## 通知示例

进服通知：

```text
🔔用户 koishi 已进入服务器
🧾 昵称：koishi
🟢 上线时间：2026-03-25 23:45:36
📣 koishi 进入了 TS 服务器
👥 当前在线人数：1
📜 在线列表：koishi
```

退服通知：

```text
📤 用户下线通知
🧾 昵称：koishi
🟢 上线时间：2026-03-25 23:45:36
🔴 下线时间：2026-03-25 23:58:10
⏱️ 在线时长：12分34秒
👥 当前在线人数：0
📜 在线列表：暂无在线用户
```

## 数据文件

插件会保存以下运行时数据：

- `snapshot.json`：在线用户快照
- `group_notify.json`：群级通知开关状态

如果没有设置 `TS3_TRACKER__DATA_DIR`，则默认使用 `nonebot-plugin-localstore` 的插件数据目录。
