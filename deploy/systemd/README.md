# systemd --user 定时任务（留档）

本目录是**运行中 systemd `--user` 单元的快照留档**，实际生效副本在
`~/.config/systemd/user/`。放这里是为了纳入版本管理、方便 review 与重装。

## 为什么用 systemd timer 而非 crontab

2026-07-06 排查发现:宿主机会 `systemd-suspend`(睡眠),固定时间点的 crontab 任务
若计划时间落在睡眠窗口就被**静默跳过、不补跑**。故把这几个日/周任务迁到 systemd timer,
全部设 `Persistent=true`——机器从睡眠/关机恢复后会**自动补跑错过的一次**。
详见 `docs/`（及项目记忆 cron-suspend-miss-fix-2026-07-06）。

## 四个单元

| 单元 | 频率 | 作用 |
|------|------|------|
| `polymarket-cleanup` | 每日 08:10 | `cleanup_stale_data.py` 清理过期数据 |
| `polymarket-data-quality` | 每日 08:30 | `data_quality_check.py` 只读数据质检 |
| `polymarket-pzero-weekly` | 每周一 09:30 | `weekly_pzero.sh` P0-B OOS 复验(推进 cutoff) |
| `polymarket-slippage-probe` | 每 4 小时 | `h6_slippage_probe.py` H6 宇宙滑点测量 |

## ⚠️ 路径是本机硬编码

`.service` 里的 `WorkingDirectory`/`ExecStart`/日志路径都写死了
`/home/xmren/.openclaw/workspace/polymarket-project` 与 `/usr/bin/python3`。
换机器/换用户需相应改路径。

## 安装 / 更新

```bash
cp deploy/systemd/*.service deploy/systemd/*.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now \
  polymarket-cleanup.timer polymarket-data-quality.timer \
  polymarket-pzero-weekly.timer polymarket-slippage-probe.timer
```

## 查看状态

```bash
systemctl --user list-timers 'polymarket-*' --all
journalctl --user -u polymarket-slippage-probe.service -n 50
```

注:需 `loginctl enable-linger $USER` 才能在未登录时运行(本机已开)。
迁移前的旧 crontab 条目已删除,避免重复运行。
