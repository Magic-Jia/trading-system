# Trading System v2 / Partial Strategy Stack

面向 Claw 的加密交易工作流骨架。

## 目标

- 读取 Binance 账户（当前默认只读）
- 扫描市场、衍生品状态与当前持仓
- 生成结构化交易候选与组合分配决策
- 对每一笔计划中的开仓 / 平仓给出充分依据
- 记录复盘与系统改进
- 支持模拟执行（paper trading）

## 当前策略结构

当前运行中的主链路已经不是只有单一 trend 脚本，而是一个 **partial v2 strategy stack**：

1. `regime`：市场状态识别，输出 risk multiplier、bucket targets、suppression rules
2. `universe`：拆分 majors / rotation / short universe
3. `trend engine`：majors 为主的顺势延续候选
4. `rotation engine`：强势轮动候选与评分
5. `short engine`：防御型 regime 下的 majors short 候选
6. `validator` + `allocator` + `risk gate`：候选校验、预算分配、组合约束
7. `execution`：paper execution + 幂等防重
8. `lifecycle/reporting`：仓位管理建议、运行摘要、runtime state 输出

当前阶段的明确边界：

- 系统仍然是 **paper-first**，live execution 仍未启用。
- short 候选已经进入 allocator 与 runtime state，但当前执行阶段仍会显式跳过，原因是 `short_execution_not_enabled`。
- `runtime_state.json` 中 `partial_v2_coverage=true` 仍然代表：整体结构已成型，但执行安全与策略成熟度都还没有到“真实资金自动运行”的完成线。

## Short-maturity package status (2026-03-27)

- 当前系统里，“short maturity”已经明确指向：`short engine` 能区分 `BREAKDOWN_SHORT` 与 `FAILED_BOUNCE_SHORT`，会过滤 crowded-short squeeze risk，并把 setup-specific 的止损 / invalidation 语义写进 runtime state 与日报摘要。
- 这次包内已落地的 short 语义包括：`stop_family`、`stop_reference`、`invalidation_source`、`invalidation_reason`，因此 short 不再只是“给一个 generic defensive short 候选”。
- 这次包内**明确不做**：live short execution plumbing、交易所下单链路、额外 short execution-safety 机制；当前 short 仍然只进入分配与报告层，执行层继续用 `short_execution_not_enabled` 显式跳过。
- 最新包验证结果：`PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/codex-uv-cache-short-maturity uv run --with pytest python -m pytest -q -p no:cacheprovider trading_system/tests/test_short_engine.py trading_system/tests/test_stop_policy.py trading_system/tests/test_main_v2_cycle.py trading_system/tests/test_reporting.py` -> `74 passed`。
- 下一包建议优先回到 `Exit system`：先把 time-stop / failure exit / crowding unwind / regime deterioration exit 做厚，再决定 short 是否继续往更完整的独立子系统推进。

## 当前策略缺口

当前系统已经具备 trend / rotation / short / regime / allocator 的骨架，但策略本身仍有明显限制：

- **仍然过于 price-structure-heavy**：大多数入场逻辑仍以 EMA 结构、阶段涨跌幅、pullback / breakout 形态为主。
- **还不够 crypto-native**：衍生品数据目前主要用于 majors 级别的 regime 摘要，尚未成为每个候选的核心过滤器。
- **absolute strength 不够明确**：rotation 已经有 relative strength，但 longs 仍缺少独立的绝对强弱门槛。
- **overheat / crowding 过滤不够**：还没有把 funding、OI、basis、taker imbalance、扩张速度真正做成晚段过热过滤器。
- **止损与退出体系偏单薄**：多个引擎仍主要依赖单一 EMA 锚定止损，exit 也还是通用 lifecycle 阈值，缺少 setup-specific taxonomy，而且当前 entry 明显强于 exit。
- **仓位与分配还不够 edge-aware**：allocator 更像风险预算器，还没有把 setup 质量、crowding、流动性、赔率差异翻译成 aggressiveness。
- **execution friction 还没进入策略层**：fee、spread、slippage、funding drag 还没有被明确写进 candidate 质量与仓位决策。
- **rotation 还缺 turnover / signal stability 控制**：否则容易在噪音 leader 之间高频切换。
- **short 还不成熟**：当前 short engine 偏“防守占位”，还不是一个成熟的 crypto short model。
- **regime crash protection 不够明确**：还没有单独处理 crash / cascade / squeeze 这类真正会改变仓位压缩速度的极端环境。
- **缺少 alpha validation discipline**：还没有明确要求对新增特征做 ablation / attribution，防止规则越来越多但 edge 并未变强。

## 新的策略方向

下一阶段不应继续把重点放在“再多一点价格结构规则”，而应把系统往 **crypto-derivatives-aware strategy** 推进。

最重要的策略升级顺序：

1. **Crypto derivatives + crowding layer**：把 funding / OI / basis / taker flow 从 regime 摘要推进到 candidate 过滤
2. **Absolute strength + overheat filters**：同时要求“真强”与“没热到不值得追”
3. **Regime crash protection**：先把 crash / cascade / squeeze 这类极端环境单独建模
4. **Edge-aware sizing + execution friction + turnover control**：把 setup 质量、流动性、fee/slippage/funding drag、signal stability 写进 aggressiveness
5. **Richer stop taxonomy**：按 breakout / pullback / rotation / short 区分止损模板
6. **Exit system**：加入 partials、trail、time-stop、failure exit、crowding unwind
7. **Short maturity**：让 short 从“防御性占位”变成成熟的可执行子系统
8. **Strategy evaluation / ablation / attribution**：避免规则只增不减，明确每个新增特征是否真的提升 edge

这条策略升级顺序 **要与 execution-safety work 分开看**。
execution-safety 的优先级仍然是 live boundary、hard risk gate、restart-safe state、audit trail；那是“能不能安全运行”的问题。
上面的策略顺序才是“跑什么策略、为什么这样跑”的问题。

## 关键文档

- `docs/superpowers/plans/2026-03-23-trading-system-p0-p1-p2-roadmap.md`：最新双主线 roadmap（execution-safety vs strategy-development）
- `trading_system/docs/STRATEGY_GAPS_AND_UPGRADES.md`：当前策略缺口、为什么仍然太 price-structure-heavy、以及明确的升级顺序
- `trading_system/docs/MVP_ARCHITECTURE.md`：程序骨架与当前模块职责
- `trading_system/docs/PAPER_TRADING_RUNBOOK.md`：paper cycle、ledger、restart-safe replay 的操作说明

## 目录

### 现有脚本层

- `binance_client.py`：Binance 读取客户端（兼容本机变量名）
- `market_scan.py`：市场快扫
- `account_snapshot.py`：账户快照与持仓摘要
- `generate_plan.py`：生成交易计划 JSON
- `journal.py`：交易理由 / 复盘记录
- `data/`：本地状态、快照、计划输出
- `runbook.md`：运行说明

### 模块化程序骨架

- `app/main.py`：一次完整 cycle 的主编排
- `app/market_regime/`：breadth + derivatives 摘要 + regime classifier
- `app/universe/`：majors / rotation / short universe 构建
- `app/signals/`：trend / rotation / short 引擎与评分逻辑
- `app/risk/`：validator、guardrails、position sizing、regime-aware risk
- `app/portfolio/`：allocator、exposure、lifecycle、positions
- `app/execution/`：paper execution、idempotency、management previews
- `app/storage/`：runtime state / journal persistence
- `app/reporting/`：regime、rotation、short、lifecycle 摘要输出

## 环境变量

兼容以下任一组：

- `BINANCE_API_KEY` / `BINANCE_API_SECRET`
- `BINANCE_APIKEY` / `BINANCE_SECRET`

测试网约定：

- 默认优先从 `/home/cn/.local/secrets/binance-testnet.env` 读取测试网凭证。
- 建议在该文件中维护 `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`。
- 若测试网文件不可用，再回退到旧的环境变量来源。

## 当前边界

- 当前版本不自动在真实账户下单。
- 当前版本可用于：读账户、做计划、做模拟执行、记录复盘。
- paper mode 会在 `TRADING_STATE_FILE` 同目录维护 `paper_ledger.jsonl`，用于记录模拟成交并在 runtime state 丢失后恢复已执行 intent。

## 测试与运行

- 单测：`uv run --with pytest python -m pytest trading_system/tests/test_main_v2_cycle.py -v`
- 全量测试：`uv run --with pytest python -m pytest trading_system/tests -v`
- 手动跑一次 paper cycle：
  `TRADING_ACCOUNT_SNAPSHOT_FILE=trading_system/data/account_snapshot.json TRADING_MARKET_CONTEXT_FILE=trading_system/data/market_context.json TRADING_DERIVATIVES_SNAPSHOT_FILE=trading_system/data/derivatives_snapshot.json python -m trading_system.app.main`

运行期预期：

- 标准输出包含 `regime` 与 `portfolio` 两段摘要，其中 `regime.rotation` 会给出 rotation 的紧凑报告。
- 当存在 short 候选时，`regime.short` 会给出 short 的紧凑报告，但当前 short execution 仍默认跳过。
- `portfolio.lifecycle_summary` 会给出 lifecycle 的紧凑视图。
- `portfolio.paper_trading` 会给出当前 paper cycle 的 ledger 路径、ledger event 总数、当次 emitted/replayed 统计与 intent 摘要。
- `trading_system/data/runtime_state.json` 会持续保留最新的 regime / candidates / allocations / paper_trading / lifecycle / rotation / short 摘要。
