# nonebot-plugin-ts3-tracker

基于 NoneBot2 的 TeamSpeak 3 在线查询、进退服通知与频道录音插件。

通过 TS3 ServerQuery 协议查询在线状态，轮询检测用户进退服并推送 OneBot V11 消息；可选启用 Rust sidecar 对指定频道进行混音录音、切片与过期清理。

## 功能概览

| 模块 | 能力 |
| --- | --- |
| 在线查询 | 查看当前在线频道与成员；查看服务器详细信息（含空频道与在线时长） |
| 进退服通知 | 轮询检测进服 / 退服，向群聊或私聊推送；支持「仅进服」模式 |
| 群级开关 | `/tsnotify on/off` 持久化控制本群是否接收通知 |
| 群白名单 | 限制群聊命令与群通知的可用范围 |
| 频道录音 | 监控频道达到最低人数后自动录音；支持测试录音、手动停录、实时切片 |
| 文件清理 | 按日期目录自动 / 手动清理过期完整录音与切片 |

## 环境要求

- Python `>=3.10, <4.0`
- NoneBot2 `>=2.4.4`
- [nonebot-adapter-onebot](https://github.com/nonebot/adapter-onebot)（V11）
- [nonebot-plugin-localstore](https://github.com/nonebot/plugin-localstore)
- TS3 服务器需开启 ServerQuery，并配置可查询账号
- 频道录音需 Linux 环境下的 `ts3-recorder-sidecar` 二进制（见下文）

## 安装

```bash
nb plugin install nonebot-plugin-ts3-tracker
```

或：

```bash
pip install nonebot-plugin-ts3-tracker
```

本地开发安装：

```bash
git clone https://github.com/lizhiqi233-rgb/nonebot-plugin-ts3-tracker.git
cd nonebot-plugin-ts3-tracker
pip install -e .
```

## 加载插件

在 NoneBot 项目的 `pyproject.toml` 中：

```toml
[tool.nonebot]
plugins = ["nonebot_plugin_ts3_tracker"]
```

## 命令

默认需要命令前缀（如 `/`）。若设置 `TS3_TRACKER__COMMAND_PREFIX_REQUIRED=false`，则也支持无前缀的纯文本命令。

### 查询

| 命令 | 说明 |
| --- | --- |
| `/ts` 或 `/上号` | 查看当前在线频道（仅显示有人的频道） |
| `/tsinfo` | 查看 TS 服务器详细信息（地址、名称、完整频道列表与在线时长） |

### 通知

| 命令 | 说明 |
| --- | --- |
| `/tsnotify on` | 开启本群进退服通知（仅群聊） |
| `/tsnotify off` | 关闭本群进退服通知（仅群聊） |

需先配置 `TS3_TRACKER__NOTIFICATION_ENABLED=true`。

### 录音（需 `RECORDING_ENABLED=true`）

| 命令 | 说明 |
| --- | --- |
| `/tsrecord` | 查看频道录音状态、保留策略与进行中的会话 |
| `/ts 切片 [分钟数] [频道]` | 从**进行中**的录音截取最近 N 分钟 WAV（默认分钟数见配置） |
| `/ts 录制 [频道]` | 手动启动**测试录音**（忽略最低人数，轮询不会自动停录） |
| `/ts 停止录制 [频道]` | 停止进行中的录音（非测试会话在满足人数时可能再次被自动启动） |
| `/ts 清理 [录音\|切片]` | 按保留策略立即清理过期文件；省略参数则清理全部 |

`[频道]` 可填频道 ID 或频道名称；省略时对全部监控 / 进行中的频道生效。

### 无前缀模式示例

当 `TS3_TRACKER__COMMAND_PREFIX_REQUIRED=false` 时，可直接发送：

```text
ts
上号
tsinfo
tsnotify on
tsnotify off
ts 切片 5 Lobby
ts 录制 Meeting
ts 停止录制
ts 清理 切片
```

## 基础配置

至少需要以下 ServerQuery 相关环境变量：

```env
TS3_TRACKER__SERVER_HOST=127.0.0.1
TS3_TRACKER__SERVER_PORT=9987
TS3_TRACKER__SERVERQUERY_PORT=10011
TS3_TRACKER__SERVERQUERY_USERNAME=your-serverquery-username
TS3_TRACKER__SERVERQUERY_PASSWORD=your-password
```

完整示例（可复制 `.env.example` 后修改）：

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
TS3_TRACKER__NOTIFICATION_PUSH_MODE=full
TS3_TRACKER__NOTIFY_TARGET_GROUPS=100000000
TS3_TRACKER__NOTIFY_TARGET_USERS=

TS3_TRACKER__GROUP_WHITELIST_ENABLED=false
TS3_TRACKER__GROUP_WHITELIST_GROUPS=

TS3_TRACKER__POLL_INTERVAL_SECONDS=5
TS3_TRACKER__STARTUP_SILENT=true
# TS3_TRACKER__DATA_DIR=

TS3_TRACKER__RECORDING_ENABLED=false
TS3_TRACKER__RECORDING_CHANNELS=5,Lobby
# TS3_TRACKER__RECORDING_IDENTITIES=
# TS3_TRACKER__RECORDING_OUTPUT_DIR=
# TS3_TRACKER__RECORDING_SIDECAR_PATH=

TS3_TRACKER__RECORDING_SERVER_PASSWORD=
TS3_TRACKER__RECORDING_CHANNEL_PASSWORD=
TS3_TRACKER__RECORDING_NICKNAME_PREFIX=RecBot
TS3_TRACKER__RECORDING_MIN_SESSION_SECONDS=5
TS3_TRACKER__RECORDING_MIN_HUMAN_COUNT=2
TS3_TRACKER__RECORDING_STOP_GRACE_SECONDS=300
TS3_TRACKER__RECORDING_SLICE_DEFAULT_MINUTES=5
TS3_TRACKER__RECORDING_RETENTION_DAYS=7
TS3_TRACKER__RECORDING_SLICE_RETENTION_DAYS=7
TS3_TRACKER__RECORDING_CLEANUP_INTERVAL_HOURS=24
```

## 配置说明

### ServerQuery 与通用

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `TS3_TRACKER__SERVER_HOST` | `""` | TS3 服务器地址 |
| `TS3_TRACKER__SERVER_PORT` | `9987` | TS3 语音端口 |
| `TS3_TRACKER__SERVERQUERY_PORT` | `10011` | ServerQuery 端口 |
| `TS3_TRACKER__SERVERQUERY_USERNAME` | `""` | ServerQuery 登录账号 |
| `TS3_TRACKER__SERVERQUERY_PASSWORD` | `""` | ServerQuery 登录密码 |
| `TS3_TRACKER__DEBUG` | `false` | 是否输出调试日志 |
| `TS3_TRACKER__COMMAND_PREFIX_REQUIRED` | `true` | 是否必须使用命令前缀 |
| `TS3_TRACKER__QUERY_TIMEOUT_SECONDS` | `10` | 单次查询超时（秒） |
| `TS3_TRACKER__DATA_DIR` | 空 | 插件数据根目录；留空时使用 localstore 数据目录 |

### 通知

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `TS3_TRACKER__NOTIFICATION_ENABLED` | `false` | 是否开启轮询通知 |
| `TS3_TRACKER__NOTIFICATION_PUSH_MODE` | `full` | `full`：进退服均通知；`join_only`：仅进服（换频道不产生事件） |
| `TS3_TRACKER__NOTIFY_TARGET_GROUPS` | 空 | 默认通知群号，逗号 / 分号 / 换行分隔 |
| `TS3_TRACKER__NOTIFY_TARGET_USERS` | 空 | 默认通知私聊 QQ，逗号 / 分号 / 换行分隔 |
| `TS3_TRACKER__GROUP_WHITELIST_ENABLED` | `false` | 是否开启群白名单 |
| `TS3_TRACKER__GROUP_WHITELIST_GROUPS` | 空 | 白名单群号 |
| `TS3_TRACKER__POLL_INTERVAL_SECONDS` | `5` | 轮询间隔（秒），最小 1 |
| `TS3_TRACKER__STARTUP_SILENT` | `true` | 启动时静默建立快照，不立即推送历史变化 |

### 录音

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `TS3_TRACKER__RECORDING_ENABLED` | `false` | 是否开启频道录音 |
| `TS3_TRACKER__RECORDING_CHANNELS` | 空 | 监控频道（ID 或名称，逗号 / 分号 / 换行分隔） |
| `TS3_TRACKER__RECORDING_IDENTITIES` | 空 | identity 路径 / 文件名 / 字符串；留空则加载配置目录 `identities/` 下全部文件 |
| `TS3_TRACKER__RECORDING_OUTPUT_DIR` | 空 | 完整录音输出根目录；留空时使用数据目录 `recordings/` |
| `TS3_TRACKER__RECORDING_SIDECAR_PATH` | 空 | sidecar 二进制绝对路径；留空则自动探测（见下文） |
| `TS3_TRACKER__RECORDING_SERVER_PASSWORD` | 空 | TS 服务器密码 |
| `TS3_TRACKER__RECORDING_CHANNEL_PASSWORD` | 空 | 默认频道密码 |
| `TS3_TRACKER__RECORDING_NICKNAME_PREFIX` | `RecBot` | 录音 bot 昵称前缀（用于识别并排除统计） |
| `TS3_TRACKER__RECORDING_MIN_SESSION_SECONDS` | `5` | 低于该秒数的录音会被丢弃 |
| `TS3_TRACKER__RECORDING_MIN_HUMAN_COUNT` | `2` | 频道内至少多少**真人**（不含 RecBot）才开始录音 |
| `TS3_TRACKER__RECORDING_STOP_GRACE_SECONDS` | `300` | 真人不足阈值后，延迟多少秒再结束录音；期间人数恢复则继续 |
| `TS3_TRACKER__RECORDING_SLICE_DEFAULT_MINUTES` | `5` | `/ts 切片` 未指定分钟数时的默认值 |
| `TS3_TRACKER__RECORDING_RETENTION_DAYS` | `7` | 完整录音保留天数；`0` 表示不自动清理 |
| `TS3_TRACKER__RECORDING_SLICE_RETENTION_DAYS` | `7` | 切片保留天数；`0` 表示不自动清理 |
| `TS3_TRACKER__RECORDING_CLEANUP_INTERVAL_HOURS` | `24` | 定时清理间隔（小时）；启动时也会执行一次 |

## 通知推送模式

`TS3_TRACKER__NOTIFICATION_PUSH_MODE` 控制轮询检测到变化时的推送行为：

- **`full`（默认）**：进服与退服均发送通知
- **`join_only`**：仅发送进服通知；用户换频道不会触发通知（同一用户以 `unique_id` 为键）

退服通知始终包含上线时间、下线时间、在线时长与当前在线列表；进服通知为简洁文本格式。

## 群白名单规则

默认情况下：

- 所有群都可以使用 `/ts`、`/上号`、`/tsinfo` 及录音相关子命令
- 私聊也可以使用查询与录音命令
- 通知只会发送给默认通知群和默认通知私聊

开启白名单后：

```env
TS3_TRACKER__GROUP_WHITELIST_ENABLED=true
TS3_TRACKER__GROUP_WHITELIST_GROUPS=100000000
```

- 只有白名单群可以在群聊中使用命令
- 私聊仍然可用
- 群通知只会发给白名单中的群
- `/tsnotify on` 加入的群仍受白名单过滤

## 群级通知开关

当 `TS3_TRACKER__NOTIFICATION_ENABLED=true` 时：

- `/tsnotify on`：开启本群进退服通知（持久化，重启后仍生效）
- `/tsnotify off`：关闭本群进退服通知

说明：

- `/tsnotify on` 会把当前群加入通知目标
- `/tsnotify off` 会把当前群从通知目标中移除
- 白名单模式下最终仍按白名单过滤

## 频道录音

录音功能通过 Rust sidecar（`ts3-recorder-sidecar`）连接 TS 语音协议，在**配置的监控频道**内满足人数条件时自动混音录制为 48 kHz 单声道 WAV。

### 录音触发逻辑

1. 轮询检测到监控频道内真人数量 ≥ `RECORDING_MIN_HUMAN_COUNT` 时启动录音
2. 真人数量降至阈值以下时，进入 `RECORDING_STOP_GRACE_SECONDS` 秒的宽限期
3. 宽限期内人数恢复则取消停录；超时后才结束会话
4. 会话时长低于 `RECORDING_MIN_SESSION_SECONDS` 的 WAV 会被丢弃
5. `/ts 录制` 启动的**测试会话**不受最低人数与自动停录限制，标记为 `[测试]`

每个**同时录制**的频道需要 1 个独立 TS identity；identity 格式需兼容 [tsclientlib](https://github.com/ReSpeak/tsclientlib)。

### 获取 sidecar 二进制

GitHub Actions 在 push 到 `master` / `main` 时编译 Linux 版本：

1. 打开仓库 [Actions](https://github.com/lizhiqi233-rgb/nonebot-plugin-ts3-tracker/actions)
2. 选择最新的 **Build recorder sidecar** 工作流
3. 在 **Artifacts** 中下载：
   - `ts3-recorder-sidecar-x86_64-unknown-linux-gnu`
   - `ts3-recorder-sidecar-aarch64-unknown-linux-gnu`

安装示例：

```bash
sudo install -m 755 ts3-recorder-sidecar /usr/local/bin/
```

```env
TS3_TRACKER__RECORDING_SIDECAR_PATH=/usr/local/bin/ts3-recorder-sidecar
```

也可本地编译：

```bash
cd nonebot_plugin_ts3_tracker/recorder_sidecar
cargo build --release
```

### sidecar 自动探测路径

未配置 `RECORDING_SIDECAR_PATH` 时，按以下顺序查找：

```text
{plugin_dir}/recorder_sidecar/bin/{platform}/ts3-recorder-sidecar
{plugin_dir}/recorder_sidecar/bin/ts3-recorder-sidecar
{plugin_dir}/recorder_sidecar/target/release/ts3-recorder-sidecar
```

其中 `{platform}` 为 `linux-x86_64`、`linux-aarch64` 或 `windows-x86_64`。

### identity 文件

默认放在 NoneBot 配置目录：

```text
{config_dir}/nonebot_plugin_ts3_tracker/identities/rec1.txt
{config_dir}/nonebot_plugin_ts3_tracker/identities/rec2.txt
```

可用 `nb localstore` 查看实际的 `{data_dir}` 与 `{config_dir}`。

### 文件布局

完整录音与切片均按 `日期 / 频道 / 时间` 组织：

```text
{data_dir}/recordings/{YYYY-MM-DD}/{channel_id}_{channel_name}/{HHMMSS}.wav
{data_dir}/recordings/{YYYY-MM-DD}/{channel_id}_{channel_name}/{HHMMSS}.json

{data_dir}/slices/{YYYY-MM-DD}/{channel_id}_{channel_name}/{HHMMSS}_slice_{minutes}m.wav
{data_dir}/slices/{YYYY-MM-DD}/{channel_id}_{channel_name}/{HHMMSS}_slice_{minutes}m.json
```

若设置了 `TS3_TRACKER__RECORDING_OUTPUT_DIR`，完整录音根目录可自定义；切片仍写入数据目录下的 `slices/`。

过期清理按目录名 `YYYY-MM-DD` 判定；**进行中的录音**及其目录不会被删除。

### 注意事项

- 录音 bot 麦克风默认静音，仅接收频道语音
- 部署前请确保参与者知晓录音行为
- sidecar 官方 CI 产物为 Linux 二进制；Windows 需自行编译

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

进服通知（`full` / `join_only` 均为此格式）：

```text
koishi 进入了 TS 服务器
在线列表：koishi
```

退服通知（仅 `full` 模式）：

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

插件通过 `nonebot-plugin-localstore` 管理路径：

| 位置 | 内容 |
| --- | --- |
| 插件数据目录 | `snapshot.json`（在线快照）、`group_notify.json`（群通知开关） |
| 插件数据目录 `recordings/` | 完整会话录音 |
| 插件数据目录 `slices/` | 手动 / 命令触发的切片 |
| 插件配置目录 `identities/` | TS voice identity 文件 |

若设置了 `TS3_TRACKER__DATA_DIR`，则作为插件数据根目录（快照、默认录音目录、切片目录均在其下）。

## 项目结构

```text
nonebot_plugin_ts3_tracker/
├── __init__.py          # 命令注册与插件入口
├── config.py            # 配置模型
├── query.py             # ServerQuery 客户端
├── service.py           # 查询与消息格式化
├── runtime.py           # 轮询、通知、录音生命周期
├── storage.py           # 快照与群通知持久化
├── recording/           # 录音管理、切片、过期清理
└── recorder_sidecar/    # Rust 语音录制 sidecar 源码
```

## 许可证

[MIT](LICENSE)
