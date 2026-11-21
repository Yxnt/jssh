# jssh

在终端里搜索你有权限的 JumpServer 2.4.1 资产，用 fzf 选中后走 KoKo 打开本机 `ssh` 或 `sftp`。

用 Access Key HTTP Signature 调 REST API。不绕过 MFA、授权和审计。不用 JumpServer 3.x 的 `user@root@ip` 写法。

## 安装

```bash
brew install fzf
pipx install .
```

不用 pipx 时跑 `scripts/install.sh`。脚本会装包，并在缺少配置时把 `config.example.yaml` 拷到 `~/.config/jssh/config.yaml`，权限 `600`。

## 配置

编辑 `~/.config/jssh/config.yaml`：

```yaml
jumpserver:
  api_url: "https://jumpserver.example.com"
  ssh_host: "jumpserver.example.com"
  ssh_port: 2222
  ssh_username: "jumpserver_user"
  ssh_identity_file: ~/.ssh/id_ed25519
  org_id: DEFAULT
```

`ssh_identity_file` 为空时走 OpenSSH 默认和 `~/.ssh/config`。

密钥按下面任一方式提供，环境变量优先：

```yaml
auth:
  access_key: "你的AK"
  secret_key: "你的SK"
  totp_secret: "authenticator 的 base32 种子，不是 6 位验证码"
```

文件里写了密钥就必须：

```bash
chmod 600 ~/.config/jssh/config.yaml
```

权限是 644 时 jssh 会拒绝启动。`jssh config` 只显示是否配置了密钥，不打印值。不要把这份 YAML 提交或发给别人。

也可以不写进 YAML：

```bash
export JMS_AK=...
export JMS_SK=...
export JMS_TOTP_SECRET=...
jssh secrets totp-file          # 写到 ~/.config/jssh/totp，权限 0600
jssh secrets keychain-set      # 藏式输入后写入 macOS Keychain
```

新终端窗口不会继承另一个窗口里的 `export`。多窗口自动填 MFA 时，把种子放进配置或 totp 文件。

## 使用

```bash
jssh doctor
jssh
```

无参数时：搜主机名 / IP / 节点（含中文）→ 选系统账号 → 选 SFTP 或 SSH。SSH 或 SFTP 退出后回到资产列表。Esc 返回上一层。

指定主机则跳过选择器：

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

- **Otty**：右侧打开 SFTP 分屏，右上角闪 `sftp pane`。找不到 Otty CLI 或 `JSSH_OTTY=0` 时退回剪贴板。
- **Warp / Terminal / iTerm / kitty / WezTerm**：当前窗口新 tab 跑同一条 `jssh sftp`。失败则复制命令。
- **其他终端**：命令进剪贴板，右上角闪 `sftp copied`。

SSH 会自动填 KoKo 的 `[MFA auth]:`。口令仍需自己输入。选中资产后会把主机名和系统账号发给 KoKo，不再停在网关菜单上手输。

SFTP 用本机交互式 `sftp`，不传 `-b`。`exit` 后还在原来的 shell。

## Otty Shell Integration

Otty 的 Cursor Click-to-Move 需要远程 Bash 的 OSC 133 `A`（prompt 开始）和 `B`（输入区开始）。jssh 走 pexpect 调系统 `ssh`，不会经过 Otty 的 `ssh` wrapper，KoKo 也不会给目标机装这些标记。

在 Otty 里用 jssh SSH（且 MFA 自动填可用）时，资产登录完成后会探测当前 `$PS1`，在原 prompt 前后加上 `A`/`B`，不改颜色、用户名、路径或主题文字。不写 OSC 133 `C`/`D`。

```yaml
terminal:
  otty_shell_integration: auto   # auto：仅 Otty；true / false：强制
  otty_shell_install: false      # true：把同样的块写入远程 ~/.bashrc（先备份）
```

`auto` 看 `TERM_PROGRAM=otty`。`JSSH_OTTY=0` 会关掉 Otty 检测。`JSSH_OTTY_SHELL_INTEGRATION=0` 或 `1` 覆盖配置。

进入远程 Bash 后稍等再敲命令，好让当前会话注入跑完。注入会回显一条命令，随后的 prompt 外观不变。

持久化（需显式打开 `otty_shell_install`）在 `~/.bashrc` 里写入：

```bash
# >>> JSSH OTTY SHELL INTEGRATION >>>
# ...
# <<< JSSH OTTY SHELL INTEGRATION <<<
```

备份文件是 `~/.bashrc.jssh-otty.bak`。卸载：删掉这两行标记之间的块，或从备份恢复。不要默认在生产机上开持久化。

限制：没有 TOTP、关掉 `totp_auto`、或没有 TTY 时走普通 `ssh`，当前会话无法注入。SFTP 不注入。其他终端默认不注入。口令提示出现得很晚时，注入命令可能被当成口令，这时关掉自动注入，登录后再手动 `source` 上面的块。

## 命令

| 命令 | 作用 |
|---|---|
| `jssh` | 交互选择资产，再选 SFTP 或 SSH |
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
| `jssh doctor --probe` | 只读探测 profile 和资产接口 |
| `jssh config` | 打印不含密钥的配置 |
| `jssh secrets totp-file` | 把 TOTP 种子写入 0600 文件 |
| `jssh secrets keychain-set` | 把 AK/SK/TOTP 写入 Keychain |

缓存目录：`~/Library/Caches/jssh/`，默认 TTL 600 秒。过期缓存仍可连，但会提示授权可能已变。缓存不存 AK、SK、口令、MFA。

zsh 补全：把 `scripts/completions/_jssh` 放进 `$fpath` 里的一个目录，再开一个 shell。`jssh ssh <TAB>` 出主机名，`--account <TAB>` 出账号。只读缓存，不调 API，不打印密钥。Otty 默认用 Tab 接受幽灵提示。跑过的 `jssh ssh 主机` 会进 Otty 的本地学习。要把 Tab 还给 zsh，到 Settings → Controls → Autocomplete 改 Accept Suggestion。

## 常见问题

| 现象 | 处理 |
|---|---|
| Config not found | 拷 `config.example.yaml` 到 `~/.config/jssh/config.yaml` |
| 含密钥且 group/world 可读 | `chmod 600 ~/.config/jssh/config.yaml` |
| Access key is missing | 写 `auth.access_key` / `auth.secret_key`，或导出 `JMS_AK` / `JMS_SK` |
| TOTP secret is missing | 写 `auth.totp_secret`，或 `jssh secrets totp-file`，或 `JMS_TOTP_SECRET` |
| 新窗口 MFA 填不上 | 不要只靠另一个窗口的 `export` |
| HTTP 401 / 403 | 核对 AK/SK、时钟、`org_id` |
| HTTP 404 | 接口按 2.4.1。跑 `jssh doctor --probe` |
| 握手停在 host key | 默认已加 `ssh-rsa`。可在 `ssh.options` 覆盖 |
| SFTP 路径不对 | 设 `sftp.path_overrides` |
| 没有系统账号 | 在 JumpServer 确认名称后设 `sftp.account_overrides` |
| fzf missing | `brew install fzf` |
| Otty Ctrl+F 没有分屏 | `jssh doctor` 看 `otty_cli` / `otty_sftp_split`。`JSSH_OTTY=0` 会关掉分屏 |
| Otty 点光标不能跳到输入区 | `jssh doctor` 看 `otty_shell_integration_active`。进入 Bash 后稍等。不要整段替换 `PS1` |

## 测试

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest
```
