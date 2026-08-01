# nonebot-plugin-ts3-tracker

基于 NoneBot2 的 TeamSpeak 3 插件：在线查询、进退服通知、频道混音录音与实时切片。

通过 TS3 ServerQuery 协议查询在线状态并轮询检测用户变化；可选启用 Rust sidecar 连接语音协议，对指定频道进行 48 kHz 单声道 WAV 录制、切片与过期清理。适配 OneBot V11（NapCat / go-cqhttp 等）。

## 功能

| 模块 | 说明 |
| --- | --- |
| 在线查询 | `/ts`、`/上号` 查看有人频道；`/tsinfo` 查看服务器详情（含空频道与在线时长） |
| 进退服通知 | 轮询检测进服 / 退服，向群聊或私聊推送；支持「仅进服」模式 |
| 群级开关 | `/tsnotify on/off` 持久化控制本群是否接收通知 |
| 群白名单 | 限制群聊命令与群通知的可用范围 |
| 频道录音 | 监控频道达到最低真人数量后自动混音录音；支持测试录音、手动停录 |
| 实时切片 | 从进行中的录音截取最近 N 分钟 WAV；群聊执行后自动发送文件 |
| 文件清理 | 按日期目录自动 / 手动清理过期完整录音与切片 |

## 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | `>=3.10, <4.0` |
| NoneBot2 | `>=2.4.4` |
| 适配器 | [nonebot-adapter-onebot](https://github.com/nonebot/adapter-onebot) V11 |
| 依赖插件 | [nonebot-plugin-localstore](https://github.com/nonebot/plugin-localstore)、[nonebot-plugin-alconna](https://github.com/ArcletProject/nonebot-plugin-alconna) |
| TS3 | 开启 ServerQuery，并配置可查询账号 |
| 频道录音（可选） | Linux 环境 + `ts3-recorder-sidecar` 二进制 + TS voice identity 文件 |

## 安装

```bash
nb plugin install nonebot-plugin-ts3-tracker
```

或：

```bash
pip install nonebot-plugin-ts3-tracker
```

本地开发：

```bash
git clone https://github.com/lizhiqi233-rgb/nonebot-plugin-ts3-tracker.git
cd nonebot-plugin-ts3-tracker
pip install -e .
```

在 NoneBot 项目 `pyproject.toml` 中加载插件：

```toml
[tool.nonebot]
plugins = [
    "nonebot_plugin_ts3_tracker",
    "nonebot_plugin_alconna",
]
```

> `nonebot-plugin-alconna` 用于群聊切片文件的 `UniMessage` + `File` 发送；安装本插件时会作为依赖自动安装。

## 快速开始

复制 `.env.example` 为 `.env`，至少填写 ServerQuery 配置：

```env
TS3_TRACKER__SERVER_HOST=127.0.0.1
TS3_TRACKER__SERVER_PORT=9987
TS3_TRACKER__SERVERQUERY_PORT=10011
TS3_TRACKER__SERVERQUERY_USERNAME=your-serverquery-username
TS3_TRACKER__SERVERQUERY_PASSWORD=your-password
```

重启 NoneBot 后，在群聊或私聊发送 `/ts` 或 `/上号` 即可查询在线状态。

开启进退服通知时，额外设置：

```env
TS3_TRACKER__NOTIFICATION_ENABLED=true
TS3_TRACKER__NOTIFY_TARGET_GROUPS=100000000
```

## 命令

默认需要命令前缀（如 `/`）。设置 `TS3_TRACKER__COMMAND_PREFIX_REQUIRED=false` 后，也支持无前缀纯文本（见下文示例）。

### 查询

| 命令 | 说明 |
| --- | --- |
| `/ts` 或 `/上号` | 查看当前在线频道（仅显示有人的频道） |
| `/tsinfo` | 查看 TS 服务器详细信息（地址、名称、完整频道列表与在线时长） |

### 通知（需 `NOTIFICATION_ENABLED=true`）

| 命令 | 说明 | 范围 |
| --- | --- | --- |
| `/tsnotify on` | 开启本群进退服通知 | 仅群聊 |
| `/tsnotify off` | 关闭本群进退服通知 | 仅群聊 |

### 录音（需 `RECORDING_ENABLED=true`）

| 命令 | 说明 |
| --- | --- |
| `/tsrecord` | 查看录音状态、保留策略与进行中的会话 |
| `/ts 切片 [参数…]` | 从**进行中**的录音截取最近 N 分钟 WAV；群聊中完成后自动发送文件 |
| `/ts 录制 [频道]` | 手动启动**测试录音**（忽略最低人数，轮询不会自动停录） |
| `/ts 停止录制 [频道]` | 停止进行中的录音 |
| `/ts 清理 [录音\|切片]` | 按保留策略立即清理过期文件；省略参数则清理全部 |

`[频道]` 可填频道 ID 或名称；省略时对全部监控 / 进行中的频道生效。

#### 切片参数

默认截取最近 **3** 分钟（由 `RECORDING_SLICE_DEFAULT_MINUTES` 控制），单次请求默认最多 **60** 分钟（由 `RECORDING_SLICE_MAX_MINUTES` 控制）。

| 参数 | 说明 | 示例 |
| --- | --- | --- |
| `-s <分钟>` | 指定截取时长 | `/ts 切片 -s 5` |
| `-m <文件名>` | 自定义保存文件名（不含 `.wav`） | `/ts 切片 -m 测试` |
| `-sm <分钟> <文件名>` | 同时指定时长与文件名 | `/ts 切片 -sm 3 测试` |
| `-c <频道>` | 仅对指定频道切片 | `/ts 切片 -c Lobby` |

参数可组合，例如：`/ts 切片 -sm 3 测试 -c Lobby`。

切片前提：对应频道存在**进行中的录音会话**，且 WAV 中已有音频数据。可用 `/ts 录制` 在空频道启动测试录音进行验证。

切片文件只通过 `UniMessage` 发送一次。发送结果不确定时不会切换其他接口重试，避免大文件重复发送。

### 无前缀模式示例

`TS3_TRACKER__COMMAND_PREFIX_REQUIRED=false` 时可直接发送：

```text
ts
上号
tsinfo
tsnotify on
tsnotify off
tsrecord
ts 切片 -s 3
ts 切片 -m 测试
ts 切片 -c Lobby
ts 切片 -sm 3 测试
ts 录制 Meeting
ts 停止录制
ts 清理 切片
```

## 配置

所有配置项通过环境变量设置，前缀为 `TS3_TRACKER__`。完整示例见仓库根目录 [`.env.example`](.env.example)。

列表类配置（群号、频道等）支持逗号、分号或换行分隔。

### ServerQuery 与通用

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `SERVER_HOST` | `""` | TS3 服务器地址 |
| `SERVER_PORT` | `9987` | TS3 语音端口 |
| `SERVERQUERY_PORT` | `10011` | ServerQuery 端口 |
| `SERVERQUERY_USERNAME` | `""` | ServerQuery 登录账号 |
| `SERVERQUERY_PASSWORD` | `""` | ServerQuery 登录密码 |
| `DEBUG` | `false` | 是否输出调试日志 |
| `COMMAND_PREFIX_REQUIRED` | `true` | 是否必须使用命令前缀 |
| `QUERY_TIMEOUT_SECONDS` | `10` | 单次查询超时（秒） |
| `DATA_DIR` | 空 | 插件数据根目录；留空时使用 localstore 数据目录 |

### 通知

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `NOTIFICATION_ENABLED` | `false` | 是否开启轮询通知 |
| `NOTIFICATION_PUSH_MODE` | `full` | `full`：进退服均通知；`join_only`：仅进服（换频道不产生事件） |
| `NOTIFY_TARGET_GROUPS` | 空 | 默认通知群号 |
| `NOTIFY_TARGET_USERS` | 空 | 默认通知私聊 QQ |
| `GROUP_WHITELIST_ENABLED` | `false` | 是否开启群白名单 |
| `GROUP_WHITELIST_GROUPS` | 空 | 白名单群号 |
| `POLL_INTERVAL_SECONDS` | `5` | 轮询间隔（秒），最小 1 |
| `STARTUP_SILENT` | `true` | 启动时静默建立快照，不立即推送历史变化 |

### 录音

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `RECORDING_ENABLED` | `false` | 是否开启频道录音 |
| `RECORDING_CHANNELS` | 空 | 监控频道（ID 或名称） |
| `RECORDING_IDENTITIES` | 空 | identity 路径 / 文件名 / 内联字符串；留空则加载配置目录 `identities/` 下全部文件 |
| `RECORDING_OUTPUT_DIR` | 空 | 完整录音输出根目录；留空时使用数据目录 `recordings/` |
| `RECORDING_SIDECAR_PATH` | 空 | sidecar 二进制绝对路径；留空则自动探测 |
| `RECORDING_SERVER_PASSWORD` | 空 | TS 服务器密码（传给 sidecar 环境变量，不进进程 argv） |
| `RECORDING_CHANNEL_PASSWORD` | 空 | 默认频道密码（同上） |
| `RECORDING_NICKNAME_PREFIX` | `RecBot` | 录音 bot 昵称前缀（用于识别并排除统计） |
| `RECORDING_MIN_SESSION_SECONDS` | `5` | 低于该秒数的录音会被丢弃 |
| `RECORDING_MIN_HUMAN_COUNT` | `2` | 频道内至少多少**真人**（不含 RecBot）才开始录音 |
| `RECORDING_STOP_GRACE_SECONDS` | `300` | 真人不足阈值后，延迟多少秒再结束录音；期间人数恢复则继续 |
| `RECORDING_SLICE_DEFAULT_MINUTES` | `3` | `/ts 切片` 未指定 `-s` / `-sm` 时的默认分钟数 |
| `RECORDING_SLICE_MAX_MINUTES` | `60` | `/ts 切片` 单次允许请求的最大分钟数 |
| `RECORDING_RETENTION_DAYS` | `7` | 完整录音保留天数；`0` 表示不自动清理 |
| `RECORDING_SLICE_RETENTION_DAYS` | `7` | 切片保留天数；`0` 表示不自动清理 |
| `RECORDING_CLEANUP_TIME` | `04:00` | 每日自动清理的本地时间（`HH:MM`）；启动时也会执行一次 |

## 进退服通知

### 推送模式

`NOTIFICATION_PUSH_MODE` 控制轮询检测到变化时的行为：

- **`full`（默认）**：进服与退服均发送通知
- **`join_only`**：仅发送进服通知；用户以 `unique_id` 为键，换频道不会触发通知

退服通知包含上线时间、下线时间、在线时长与当前在线列表；进服通知为简洁文本。

### 通知目标

通知发送至：

1. `NOTIFY_TARGET_GROUPS` 中配置的群（减去 `/tsnotify off` 关闭的群）
2. `/tsnotify on` 额外加入的群
3. `NOTIFY_TARGET_USERS` 中配置的私聊

开启群白名单后，群通知仅发送给白名单内的群；私聊通知不受影响。

### 群白名单

默认所有群均可使用查询与录音命令，通知仅发往配置的默认目标。

开启白名单后：

```env
TS3_TRACKER__GROUP_WHITELIST_ENABLED=true
TS3_TRACKER__GROUP_WHITELIST_GROUPS=100000000
```

- 仅白名单群可在群聊中使用命令（私聊仍可用）
- 群通知仅发给白名单群
- `/tsnotify on` 加入的群仍受白名单过滤

## 频道录音

录音通过 Rust sidecar（`ts3-recorder-sidecar`）连接 TS 语音协议，在配置的监控频道内混音录制为 **48 kHz、单声道、16-bit PCM WAV**。

### 触发逻辑

1. 轮询检测到监控频道内真人数量 ≥ `RECORDING_MIN_HUMAN_COUNT` 时启动录音
2. 真人数量降至阈值以下时，进入 `RECORDING_STOP_GRACE_SECONDS` 秒宽限期
3. 宽限期内人数恢复则取消停录；超时后才结束会话
4. 监控频道短暂解析失败（如按名配置、瞬时列表不全）时同样走宽限期，不会立刻停录
5. 会话时长低于 `RECORDING_MIN_SESSION_SECONDS` 的 WAV 会被丢弃
6. `/ts 录制` 启动的**测试会话**不受最低人数与自动停录限制，状态标记为 `[测试]`
7. 非测试录音手动停录后，若仍满足最低人数，下一轮轮询可能自动重新开始

每个**同时录制**的频道需要 1 个独立 TS identity；格式需兼容 [tsclientlib](https://github.com/ReSpeak/tsclientlib)。

> 升级插件后请同步更新 sidecar 二进制（重新拉 CI Artifacts 或本地 `cargo build --release`）。新版通过环境变量传递服/频道密码，并以 stdin `STOP` / `SIGTERM` 优雅停录（finalize WAV、正常 disconnect）。旧二进制不认环境变量密码，也无法优雅退出。

### 获取 sidecar 二进制

GitHub Actions 在 push 到 `master` / `main` 时编译 Linux 版本：

1. 打开仓库 [Actions](https://github.com/lizhiqi233-rgb/nonebot-plugin-ts3-tracker/actions)
2. 选择最新的 **Build recorder sidecar** 工作流
3. 在 **Artifacts** 中下载对应架构的产物：
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

未配置 `RECORDING_SIDECAR_PATH` 时，按以下顺序自动探测：

```text
{plugin_dir}/recorder_sidecar/bin/{platform}/ts3-recorder-sidecar
{plugin_dir}/recorder_sidecar/bin/ts3-recorder-sidecar
{plugin_dir}/recorder_sidecar/target/release/ts3-recorder-sidecar
```

`{platform}` 为 `linux-x86_64`、`linux-aarch64` 或 `windows-x86_64`。

### identity 文件

默认放在 NoneBot 配置目录：

```text
{config_dir}/nonebot_plugin_ts3_tracker/identities/rec1.txt
{config_dir}/nonebot_plugin_ts3_tracker/identities/rec2.txt
```

执行 `nb localstore` 可查看实际的 `{data_dir}` 与 `{config_dir}`。

### 文件布局

完整录音与切片按 `日期 / 频道 / 文件` 组织：

```text
{data_dir}/recordings/{YYYY-MM-DD}/{channel_id}_{channel_name}/{HHMMSS}.wav
{data_dir}/recordings/{YYYY-MM-DD}/{channel_id}_{channel_name}/{HHMMSS}.json

{data_dir}/slices/{YYYY-MM-DD}/{channel_id}_{channel_name}/{HHMMSS}_slice_{minutes}m.wav
{data_dir}/slices/{YYYY-MM-DD}/{channel_id}_{channel_name}/{自定义文件名}.wav
```

若设置了 `RECORDING_OUTPUT_DIR`，完整录音根目录可自定义；切片始终写入数据目录下的 `slices/`。

过期清理按目录名 `YYYY-MM-DD` 判定；**进行中的录音**及其目录不会被删除。

### 群聊切片文件发送

在群聊中执行 `/ts 切片` 成功后，插件会自动将 WAV 发送到当前群。发送策略（`file_send.py`）：

1. **Alconna** `UniMessage(File(path=...))`（推荐，兼容 NapCat）
2. OneBot `file` 消息段（本地路径）
3. OneBot `file` 消息段（`base64://`）
4. `upload_group_file` API

NoneBot 与协议端需能访问切片文件的本地路径（同机部署一般无问题）。

### 注意事项

- 录音 bot 麦克风默认静音，仅接收频道语音
- 部署前请确保参与者知晓录音行为
- CI 产物为 Linux 二进制；Windows 需自行编译
- 监控频道尽量配置**频道 ID**，比频道名更稳
- 插件与 sidecar 版本需匹配；更新插件后请一并更新 sidecar

## 输出示例

### `/ts` 或 `/上号`

```text
APEX: TEST
大厅: koishi, Cirno
```

### `/tsinfo`

```text
服务器地址：127.0.0.1:9987
服务器名称：示例 TS3 服务器
服务器频道：
APEX: TEST(42秒)
大厅: koishi(3分12秒), Cirno(1分05秒)
原神
```

### 进服通知

```text
koishi 进入了 TS 服务器
在线列表：koishi
```

### 退服通知（仅 `full` 模式）

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
| 插件数据目录 `slices/` | 命令触发的切片 |
| 插件配置目录 `identities/` | TS voice identity 文件 |

设置 `DATA_DIR` 后，快照、默认录音目录与切片目录均在其下；identity 仍在配置目录。

## 项目结构

```text
nonebot_plugin_ts3_tracker/
├── __init__.py          # 命令注册与插件入口
├── config.py            # 配置模型
├── query.py             # ServerQuery 客户端
├── service.py           # 查询与消息格式化
├── runtime.py           # 轮询、通知、录音生命周期
├── storage.py           # 快照与群通知持久化
├── storage_paths.py     # 数据 / 配置目录解析
├── file_send.py         # 群聊文件发送（Alconna + 回退）
├── channels.py          # 频道匹配与解析
├── parsing.py           # 分隔列表解析
├── models.py            # TS3 数据模型
├── recording/           # 录音管理、切片、过期清理
│   ├── manager.py
│   ├── sidecar.py
│   ├── slice.py
│   ├── retention.py
│   └── ...
└── recorder_sidecar/    # Rust 语音录制 sidecar 源码
```

## 许可证

[MIT](LICENSE)
