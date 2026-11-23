# jssh

在终端里搜索你有权限的 JumpServer 2.4.1 资产，用 fzf 选中后走 KoKo 打开本机 `ssh` 或 `sftp`。

用 Access Key HTTP Signature 调 REST API。不绕过 MFA、授权和审计。不用 JumpServer 3.x 的 `user@root@ip` 写法。

## 安装

需要 Python 3.11 或更高版本。

```bash
brew install fzf
pipx install .
```

不用 pipx 时跑 `scripts/install.sh`。脚本会装包，并在缺少配置时把 `config.example.yaml` 拷到 `~/.config/jssh/config.yaml`，权限 `600`。

## 配置

默认路径是 `~/.config/jssh/config.yaml`。可用 `--config` 或环境变量 `JSSH_CONFIG` 指定别的文件。

编辑该文件：

```yaml
jumpserver:
  api_url: "https://jumpserver.example.com"
  ssh_host: "jumpserver.example.com"
  ssh_port: 2222
  ssh_username: "jumpserver_user"
  ssh_identity_file: ~/.ssh/id_ed25519
  org_id: DEFAULT
```

`ssh_identity_file` 为空时走 OpenSSH 默认和 `~/.ssh/config`。`org_id` 省略或设为 null 时不发送 `X-JMS-ORG`。内网 CA 把路径写在 `tls.ca_bundle`。写入 `ssh.options` 会整表替换默认值（默认已加 `ssh-rsa`），不是追加。

密钥按下面任一方式提供。查找顺序：环境变量，YAML，TOTP 的 `totp` 文件，1Password，macOS Keychain。

```yaml
auth:
  access_key: "你的AK"
  secret_key: "你的SK"
  totp_secret: "authenticator 的 base32 种子，不是 6 位验证码"
```

文件里写了密钥且权限是 644（或其他 group/world 可读）时，jssh 会拒绝启动：

```bash
chmod 600 ~/.config/jssh/config.yaml
```

`jssh config` 只显示是否配置了密钥，不打印值。不要把这份 YAML 提交或发给别人。

也可以不写进 YAML：

```bash
export JMS_AK=...
export JMS_SK=...
export JMS_TOTP_SECRET=...
jssh secrets totp-file          # 写到配置文件同目录的 totp，默认 ~/.config/jssh/totp，权限 0600
```

`jssh secrets keychain-set` 会藏式输入后写入 macOS Keychain，但必须先在配置里设置 `auth.keychain_service`。1Password 把 `op://` 引用写到 `auth.onepassword_ak`、`auth.onepassword_sk`、`auth.onepassword_totp`，并确保本机有 `op` CLI。

新终端窗口不会继承另一个窗口里的 `export`。多窗口自动填 MFA 时，把种子放进配置、totp 文件、Keychain 或 1Password。

## 使用

```bash
jssh doctor
jssh
```

无参数时：搜主机名 / IP / 节点（含中文）→ 选系统账号 → 选 SFTP、SSH，或复制对应命令。SSH 或 SFTP 退出后回到资产列表。账号和操作层按 Esc 返回上一层。资产列表上按 Esc 退出。

指定主机（精确匹配资产 id 或主机名）则跳过选择器：

```bash
jssh ssh sha-jumpserver-10.111.6.11 --account linux-root
jssh sftp sha-jumpserver-10.111.6.11 --account linux-root
jssh sftp --last
```

这类命令在会话结束后直接退出 jssh。

SSH 连上后，在该会话里按 **Ctrl+F**（发给本机 jssh）。

```bash
jssh sftp <主机> --account <账号>
```

不含 TOTP。当前 SSH 不用断。

- **Otty**：默认在右侧打开 SFTP 分屏，右上角闪 `[jssh] sftp pane`。把 `terminal.otty_sftp_layout` 设为 `tab` 则改为新标签页。找不到 Otty CLI、命令失败或 `JSSH_OTTY=0` 时退回剪贴板。
- **Warp / Terminal / iTerm / kitty / WezTerm**：当前窗口新 tab 跑同一条 `jssh sftp`。失败则复制命令。
- **其他终端**：命令进剪贴板，右上角闪 `[jssh] sftp copied`。

SSH 和 SFTP 都会自动填 KoKo 的 `[MFA auth]:`。口令仍需自己输入。选中资产后，SSH 会把主机名和系统账号发给 KoKo，不再停在网关菜单上手输。

SFTP 用本机交互式 `sftp`，不传 `-b`。`exit` 后还在原来的 shell。

## Otty Shell Integration

Otty 的 Cursor Click-to-Move、命令完成标记和远程路径，需要目标机 shell 发出 OSC 133 和 OSC 7。jssh 走 pexpect 调系统 `ssh`，不会经过 Otty 的 `ssh` wrapper，KoKo 也不会给目标机装这些标记。

在 Otty 里用 jssh SSH（且 MFA 自动填可用）时，资产登录完成后会在当前 Bash 或 zsh 会话安装 hook。逻辑对齐 Otty 安装包里的 `ssh-marks.sh`，但不在目标机写临时目录，也不走 Otty 的 ssh bootstrap。

- 每条 prompt 发出 OSC 133 `A`。第一次 prompt 不发 `D`。
- 只在原 `PS1` 末尾追加 `B`（若还没有），不整段替换 `PS1`。zsh 用 `zle-line-init` 发 `B`。
- 命令开始时发 `C`。Bash 4.4 及以上用 `PS0`，更早版本用 `DEBUG` trap，并跳过 hook 自身和补全。zsh 用 `preexec`。
- 命令结束后发 `D;<退出码>`。退出码在 hook 里最先读取。
- 每条 prompt 同时发 OSC 7：`file://<资产主机名><远程当前目录>`。主机名是 JumpServer 资产主机名，不是 KoKo。

```yaml
terminal:
  otty_shell_integration: auto   # auto：仅 Otty；true / false：强制
  otty_shell_install: false      # true：把完整 A/B/C/D 和 OSC 7 块写入远程 ~/.bashrc（先备份）
  otty_sftp_layout: split        # split：右侧分屏（默认）；tab：新标签页
  otty_sftp_direction: right
  otty_sftp_size: 40             # 分屏大小，10-90
  otty_sftp_no_focus: true       # 打开 SFTP 时焦点留在 SSH
```

`auto` 看 `TERM_PROGRAM=otty`。`JSSH_OTTY=0` 会关掉 Otty 检测。`JSSH_OTTY_SHELL_INTEGRATION=0` 或 `1` 覆盖配置。

进入远程 shell 后稍等再敲命令，好让当前会话注入跑完。提前输入会取消本次注入。注入会先关掉远端回显再写入 hook，屏幕上不会打出那一长串命令；prompt 外观不变。

持久化（需显式打开 `otty_shell_install`）在 `~/.bashrc` 里写入：

```bash
# >>> JSSH OTTY SHELL INTEGRATION >>>
# ...
# <<< JSSH OTTY SHELL INTEGRATION <<<
```

备份文件是 `~/.bashrc.jssh-otty.bak`。卸载：删掉这两行标记之间的块，或从备份恢复。不要默认在生产机上开持久化。

在 Otty 里，jssh 还会向本机终端发 OSC 7501。stdout 可能被重定向时写到 `/dev/tty`。

- `jssh sync` 开始时 `state=working`。没有可靠的 0-100 进度时不带 `progress`。成功后 `done`，失败后 `error`。
- 认证失败时 `state=error`，并带说明。
- 开始连一台资产的 SSH 或 SFTP 时，`title` 为资产名的 base64，状态为 `working`。口令提示是 `blocked`（`kind=auth`），进入远程 shell 后变为 `idle`，连接前会读取当前 tab 的 `title` 和 `cwd`（空标题就是 Otty 默认，例如 `~/github/jssh`）。会话结束时 `clear`，按捕获结果恢复：原来没有程序标题就清空标题，并把 OSC 7 工作目录改回本地。tab 上的错误/完成角标目前无法从 CLI 读到，所以不能原样捕获。

Ctrl-F 在 Otty 里默认右侧分屏打开 SFTP，并带 `--size 40`、`--no-focus` 和 `--json`。把 `otty_sftp_layout` 设为 `tab` 则改为 `otty tab new`，并给新标签设 `running` 徽标。jssh 不会监视 SFTP 进程结束，因此不会自动改成 `finished` 或清除徽标。找不到 Otty CLI、命令失败或 `JSSH_OTTY=0` 时，退回剪贴板。

如果已经知道远程当前目录，打开 SFTP 分屏后会用 `otty pane send-text` 向 SFTP 发一次 `cd`。之后远程目录变化时也会再发 `cd`。这需要在 Settings → Advanced 打开 IPC Allow Send Keys。未打开或命令失败时会静默忽略，不影响分屏本身。标签页模式通常拿不到 pane id，这时不会发 `cd`。正在 SFTP 里输入时，自动 `cd` 可能打断当前行。

限制：没有 TOTP、关掉 `totp_auto`、或没有 TTY 时走普通 `ssh`，当前会话无法注入。SFTP 会话不注入这些 hook。其他终端默认不注入。口令提示出现得很晚时，注入命令可能被当成口令，这时关掉自动注入，登录后再手动 `source` 上面的块。本版本不实现 `ottyd`。

## 命令

全局选项：`--version`，`--config <路径>`。

| 命令 | 作用 |
|---|---|
| `jssh` | 交互选择资产，再选 SFTP 或 SSH。`jssh connect` 相同 |
| `jssh sftp [名称] [--account] [--last]` | 打开 SFTP |
| `jssh ssh [名称] [--account] [--last]` | 经 KoKo 打开 SSH |
| `jssh totp` | 打印当前 6 位 TOTP，不打印种子 |
| `jssh sync` | 从 API 刷新有权限的资产 |
| `jssh list` | 打印缓存 |
| `jssh recent` | 打印最近连接 |
| `jssh complete host` | 打印缓存主机名，给 shell 补全用 |
| `jssh complete account [主机]` | 打印缓存系统账号 |
| `jssh cache clear` | 删缓存 |
| `jssh doctor` | 检查配置、fzf、ssh、sftp、Otty、密钥是否存在 |
| `jssh doctor --probe` | 只读探测 profile 和资产接口。`jssh api-probe` 相同 |
| `jssh config` | 打印不含密钥的配置 |
| `jssh secrets totp-file` | 把 TOTP 种子写入配置文件同目录的 `totp`（0600） |
| `jssh secrets keychain-set` | 把 AK/SK/TOTP 写入 Keychain（需先设 `auth.keychain_service`） |

缓存目录：`~/Library/Caches/jssh/`，默认 TTL 600 秒。可用 `JSSH_CACHE_DIR` 或 `search.cache_dir` 覆盖。过期缓存仍可连，但会提示授权可能已变。缓存不存 AK、SK、口令、MFA。

zsh 补全：把 `scripts/completions/_jssh` 放进 `$fpath` 里的一个目录，再开一个 shell。`jssh ssh <TAB>` 出主机名，`--account <TAB>` 出账号。只读缓存，不调 API，不打印密钥。Otty 默认用 Tab 接受幽灵提示。跑过的 `jssh ssh 主机` 会进 Otty 的本地学习。要把 Tab 还给 zsh，到 Settings → Controls → Autocomplete 改 Accept Suggestion。

## 常见问题

| 现象 | 处理 |
|---|---|
| Config not found | 拷 `config.example.yaml` 到 `~/.config/jssh/config.yaml`，或用 `--config` / `JSSH_CONFIG` |
| 含密钥且 group/world 可读 | `chmod 600 ~/.config/jssh/config.yaml` |
| Access key is missing | 写 `auth.access_key` / `auth.secret_key`，或导出 `JMS_AK` / `JMS_SK`，或配置 1Password / Keychain |
| TOTP secret is missing | 写 `auth.totp_secret`，或 `jssh secrets totp-file`，或 `JMS_TOTP_SECRET`，或 1Password / Keychain |
| Set auth.keychain_service | 先在配置里写 `auth.keychain_service`，再跑 `jssh secrets keychain-set` |
| 新窗口 MFA 填不上 | 不要只靠另一个窗口的 `export` |
| HTTP 401 / 403 | 核对 AK/SK、时钟、`org_id` |
| HTTP 404 | 接口按 2.4.1。跑 `jssh doctor --probe` |
| 握手停在 host key | 默认已加 `ssh-rsa`。写入 `ssh.options` 会整表替换默认 |
| SFTP 路径不对 | 设 `sftp.path_overrides` |
| 没有系统账号 | 在 JumpServer 确认名称后设 `sftp.account_overrides` |
| fzf missing | `brew install fzf` |
| Otty Ctrl+F 没有分屏或标签页 | `jssh doctor` 看 `otty_cli` / `otty_sftp_split` / `otty_sftp_layout`。`JSSH_OTTY=0` 会关掉这项能力 |
| Otty 点光标不能跳到输入区 | `jssh doctor` 看 `otty_shell_integration_active`。进入 Bash 或 zsh 后稍等。不要整段替换 `PS1` |
| SFTP 分屏没有跟上远程目录 | 打开 Settings → Advanced → IPC Allow Send Keys。标签页模式不会自动 `cd` |

## 测试

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```

## 给 AI agent

改这个仓库前先读 [`skills/jssh/SKILL.md`](skills/jssh/SKILL.md)。
