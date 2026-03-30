# Batch Runtime Runbook

## 目的

- 给 `paper` 模式提供一个最小可用的 `systemd` 定时跑批模板。
- 只负责“按固定频率执行一轮 `python -m trading_system.run_cycle --mode paper`”。
- 不改动业务逻辑，不额外引入常驻 daemon。

## 适用范围与边界

- 本模板默认使用 **system service**：`sudo systemctl ...`，不使用 `systemctl --user`。
- 当前模板只调度 strategy cycle；当 paper bucket 缺少 `account_snapshot.json`、`market_context.json`、`derivatives_snapshot.json` 时，`run_cycle --mode paper` 会先在 bucket 内自动生成最小可用输入，再进入主循环。
- 若自动快照生成链路遇到真实网络/API 阻塞，service 会 fail-fast，并在同目录 `error.json` / `latest.json` 写出清楚错误，而不是静默回退到旧的根目录快照路径。
- `paper_ledger.jsonl` 会跟随 `runtime_state.json` 写到同级目录，因此 runtime bucket 必须放在可持久化目录下。

## 模板文件

- `deploy/systemd/trading-system-paper.service`
- `deploy/systemd/trading-system-paper.timer`

临时替代脚本：

- `deploy/cron/trading-system-paper-cron.sh`
- `deploy/cron/install-trading-system-paper-crontab.sh`

默认设计：

- `service` 使用 `Type=oneshot`，每次只跑一轮。
- `timer` 用 `OnCalendar=*:0/15`，即每 15 分钟触发一次。
- `Persistent=true` 打开后，主机在离线期间错过的定时会在 timer 恢复后补跑一次。

## 临时 cron 替代（过渡方案）

- 正式部署仍以 `systemd service + timer` 为准；`deploy/cron/` 里的脚本只是给尚未切过去的机器临时顶住。
- `trading-system-paper-cron.sh` 默认固定 `TRADING_EXECUTION_MODE=paper`、`TRADING_RUNTIME_ENV=paper`，因此默认 runtime bucket 也是 `.../data/runtime/paper/paper/`。
- 如果没有显式覆盖，wrapper 会把 `TRADING_ACCOUNT_SNAPSHOT_FILE`、`TRADING_MARKET_CONTEXT_FILE`、`TRADING_DERIVATIVES_SNAPSHOT_FILE` 都指向同一个 paper bucket，不会回退到旧的根目录 `data/*.json` 快照路径。
- `install-trading-system-paper-crontab.sh` 会把一段受管 block 写进当前用户 crontab，默认表达式是 `*/15 * * * *`，可用 `TRADING_PAPER_CRON_EXPR` 覆盖。
- 同一个安装脚本可重复执行；它会先移除旧的 `# >>> trading-system-paper cron >>>` block，再写入最新配置。

## 目录与路径约定

模板里预设的部署路径是：

- 仓库根目录：`/opt/trading-system`
- paper runtime bucket：`/opt/trading-system/trading_system/data/runtime/paper/paper/`
- 可选 env 文件：`/etc/default/trading-system-paper`

若你的实际部署目录不同，请同步修改 `trading-system-paper.service` 里的：

- `User`
- `Group`
- `WorkingDirectory`
- `ExecStart`
- `EnvironmentFile`

## paper bucket 约定

默认 paper 入口会读取：

- `/opt/trading-system/trading_system/data/runtime/paper/paper/account_snapshot.json`
- `/opt/trading-system/trading_system/data/runtime/paper/paper/market_context.json`
- `/opt/trading-system/trading_system/data/runtime/paper/paper/derivatives_snapshot.json`

并在同目录写出：

- `/opt/trading-system/trading_system/data/runtime/paper/paper/runtime_state.json`
- `/opt/trading-system/trading_system/data/runtime/paper/paper/paper_ledger.jsonl`
- `/opt/trading-system/trading_system/data/runtime/paper/paper/latest.json`
- `/opt/trading-system/trading_system/data/runtime/paper/paper/error.json`

## 可选 env 文件示例

如果需要额外风险参数、切换隔离环境或覆盖默认值，可在 `/etc/default/trading-system-paper` 中提供：

```bash
TRADING_RUNTIME_ENV=paper
TRADING_MAX_OPEN_POSITIONS=8
TRADING_MAX_TOTAL_RISK_PCT=0.03
```

建议：

- 除非你明确要切到别的 runtime env，否则可以不写任何路径变量；默认入口已经固定到 paper bucket。
- 若要切到别的隔离环境，可把 `TRADING_RUNTIME_ENV` 改成例如 `testnet`，对应 bucket 就会变成 `/opt/trading-system/trading_system/data/runtime/paper/testnet/`。
- runtime bucket 本身要放在持久化目录中，这样 `runtime_state.json` 与 `paper_ledger.jsonl` 都能跨重启保留。
- 若需要额外风险参数（如 `TRADING_MAX_OPEN_POSITIONS`），也统一放进这个 env 文件。

## 安装步骤

1. 安装 unit：

   ```bash
   sudo install -D -m 0644 deploy/systemd/trading-system-paper.service /etc/systemd/system/trading-system-paper.service
   sudo install -D -m 0644 deploy/systemd/trading-system-paper.timer /etc/systemd/system/trading-system-paper.timer
   ```

2. 如有需要，写 `/etc/default/trading-system-paper`；默认情况下不用再手工预铺三份输入快照，首轮 `run_cycle --mode paper` 会在当前 bucket 内自动补齐缺失文件。

3. 重新加载并启用 timer：

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now trading-system-paper.timer
   ```

4. 立即手动跑一轮做首检：

   ```bash
   sudo systemctl start trading-system-paper.service
   ```

## 临时 cron 安装步骤

1. 确认 paper runtime bucket 目录可写；首次运行时，脚本会把缺失的三份输入快照直接生成到 `/opt/trading-system/trading_system/data/runtime/paper/paper/`（或你实际 `TRADING_BASE_DIR` 对应的同名目录）。

2. 直接安装当前用户 crontab：

   ```bash
   bash deploy/cron/install-trading-system-paper-crontab.sh
   ```

3. 如需改频率，可在安装前覆盖 cron 表达式：

   ```bash
   TRADING_PAPER_CRON_EXPR="*/5 * * * *" bash deploy/cron/install-trading-system-paper-crontab.sh
   ```

4. 安装后可用下面命令确认 block 已写入：

   ```bash
   crontab -l
   ```

## 日常操作

- 看 timer 状态：

  ```bash
  sudo systemctl status trading-system-paper.timer
  sudo systemctl list-timers trading-system-paper.timer
  ```

- 看单轮 service 状态：

  ```bash
  sudo systemctl status trading-system-paper.service
  ```

- 追日志：

  ```bash
  sudo journalctl -u trading-system-paper.service -n 100
  sudo journalctl -u trading-system-paper.service -f
  ```

- 停止定时：

  ```bash
  sudo systemctl disable --now trading-system-paper.timer
  ```

## 首次上线检查项

1. `sudo systemctl start trading-system-paper.service` 成功退出。
2. `journalctl` 中能看到本轮 `regime` / `portfolio` 摘要，而不是 import 或 env 错误。
3. `/opt/trading-system/trading_system/data/runtime/paper/paper/runtime_state.json` 已更新。
4. 同目录已出现或持续维护 `paper_ledger.jsonl`。
5. `portfolio.paper_trading.mode` 为 `paper`。
6. timer 已显示下一次触发时间。

## 常见问题

### service 能跑，但没有新数据

优先检查：

- 上游快照文件是否在 timer 触发前更新；
- 上游是不是把文件写到了 `/opt/trading-system/trading_system/data/runtime/paper/paper/`；
- 本轮是否只是“重复消费旧快照”。

### runtime state 有了，但 ledger 不在预期目录

`paper_ledger.jsonl` 不单独配置路径，它跟随当前 runtime bucket 里的 `runtime_state.json` 同级生成。先检查 bucket 是否跑到了预期 env。

### timer 已启用，但错过停机期间的计划执行

确认：

- `trading-system-paper.timer` 已启用 `Persistent=true`；
- timer 是在系统恢复后重新被激活的；
- `OnCalendar` 没有被本地改成别的表达式。
