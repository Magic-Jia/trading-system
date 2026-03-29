# Batch Runtime Runbook

## 目的

- 给 `paper` 模式提供一个最小可用的 `systemd` 定时跑批模板。
- 只负责“按固定频率执行一轮 `python -m trading_system.app.main`”。
- 不改动业务逻辑，不额外引入常驻 daemon。

## 适用范围与边界

- 本模板默认使用 **system service**：`sudo systemctl ...`，不使用 `systemctl --user`。
- 当前模板只调度 strategy cycle；`account_snapshot.json`、`market_context.json`、`derivatives_snapshot.json` 需要在定时触发前已经准备好。
- 若上游快照生成链路尚未接入，timer 仍会按时执行，但只能消费当时文件里已有的数据。
- `paper_ledger.jsonl` 会跟随 `TRADING_STATE_FILE` 写到同级目录，因此 state 路径必须放在可持久化目录下。

## 模板文件

- `deploy/systemd/trading-system-paper.service`
- `deploy/systemd/trading-system-paper.timer`

默认设计：

- `service` 使用 `Type=oneshot`，每次只跑一轮。
- `timer` 用 `OnCalendar=*:0/15`，即每 15 分钟触发一次。
- `Persistent=true` 打开后，主机在离线期间错过的定时会在 timer 恢复后补跑一次。

## 目录与路径约定

模板里预设的部署路径是：

- 仓库根目录：`/opt/trading-system`
- env 文件：`/etc/default/trading-system-paper`

若你的实际部署目录不同，请同步修改 `trading-system-paper.service` 里的：

- `User`
- `Group`
- `WorkingDirectory`
- `ExecStart`
- `EnvironmentFile`

## 最小 env 文件示例

在 `/etc/default/trading-system-paper` 中至少提供：

```bash
TRADING_EXECUTION_MODE=paper
TRADING_ACCOUNT_SNAPSHOT_FILE=/opt/trading-system/trading_system/data/account_snapshot.json
TRADING_MARKET_CONTEXT_FILE=/opt/trading-system/trading_system/data/market_context.json
TRADING_DERIVATIVES_SNAPSHOT_FILE=/opt/trading-system/trading_system/data/derivatives_snapshot.json
TRADING_STATE_FILE=/opt/trading-system/trading_system/data/runtime_state.json
```

建议：

- 所有路径都写成绝对路径，避免 `systemd` 环境和交互式 shell 行为不一致。
- `TRADING_STATE_FILE` 放在持久化目录中，这样 `runtime_state.json` 与 `paper_ledger.jsonl` 都能跨重启保留。
- 若需要额外风险参数（如 `TRADING_MAX_OPEN_POSITIONS`），也统一放进这个 env 文件。

## 安装步骤

1. 安装 unit：

   ```bash
   sudo install -D -m 0644 deploy/systemd/trading-system-paper.service /etc/systemd/system/trading-system-paper.service
   sudo install -D -m 0644 deploy/systemd/trading-system-paper.timer /etc/systemd/system/trading-system-paper.timer
   ```

2. 写好 `/etc/default/trading-system-paper`。

3. 重新加载并启用 timer：

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now trading-system-paper.timer
   ```

4. 立即手动跑一轮做首检：

   ```bash
   sudo systemctl start trading-system-paper.service
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
3. `TRADING_STATE_FILE` 指向的 `runtime_state.json` 已更新。
4. `TRADING_STATE_FILE` 同级目录已出现或持续维护 `paper_ledger.jsonl`。
5. `portfolio.paper_trading.mode` 为 `paper`。
6. timer 已显示下一次触发时间。

## 常见问题

### service 能跑，但没有新数据

优先检查：

- 上游快照文件是否在 timer 触发前更新；
- env 文件里的三个输入路径是否写对；
- 本轮是否只是“重复消费旧快照”。

### runtime state 有了，但 ledger 不在预期目录

`paper_ledger.jsonl` 不单独配置路径，它跟随 `TRADING_STATE_FILE` 同级目录生成。先检查 `TRADING_STATE_FILE`。

### timer 已启用，但错过停机期间的计划执行

确认：

- `trading-system-paper.timer` 已启用 `Persistent=true`；
- timer 是在系统恢复后重新被激活的；
- `OnCalendar` 没有被本地改成别的表达式。
