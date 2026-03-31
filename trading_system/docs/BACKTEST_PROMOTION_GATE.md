# Trading System Backtest Promotion Gate

这份文档定义一个研究结论什么时候允许进入正式策略主线。

适用范围覆盖整条链路：

1. `regime`
2. `suppression`
3. `candidate`
4. `allocation`
5. `execution`

如果一项改动会改变 live / paper 的候选、仓位、执行或风险暴露，它就必须过这个 gate。

---

## 1. Promotion gate 的核心原则

研究结论不是“看起来合理”就能上线。

任何要进入正式系统的规则、阈值、分配逻辑、成本假设，至少要满足 5 个问题：

1. 这次改动到底在回答什么问题
2. 它相对 baseline 真的更好，还是只是换一种坏法
3. 成本计入后结论还成不成立
4. 样本外是否还能站住
5. 如果上线后失效，怎么观测、怎么回滚

---

## 2. 必须具备的证据包

### 2.1 A/B 或 ablation 证据

必须有明确对照组，至少满足其一：

- baseline vs variant
- current policy vs no policy
- current filter vs filter removed
- current allocator vs baseline allocator

不允许只有单边叙事，没有对照。

### 2.2 样本外验证

至少要有以下之一：

- 固定 in-sample / out-of-sample 切分
- rolling / walk-forward
- 多窗口验证且最终结论明确区分样本内与样本外

如果只在样本内有效，默认不能晋升。

### 2.3 成本后结果

所有 promotion 讨论必须带上统一 friction 口径：

- fee
- slippage
- funding drag（如果适用）

如果一项结论只在“零成本世界”成立，默认视为不成立。

### 2.4 回滚标准

每项 promotion 都必须写清楚：

- 失效条件
- 触发复核的阈值
- 回滚动作

至少要回答：

- 什么指标连续恶化时要停用
- 看多少次 / 多久的 runtime 观测
- 回滚到哪个 baseline 版本

### 2.5 Runtime 可观测性

凡是能进生产的结论，必须能在 runtime 输出中被观测到。

最少要能看到：

- 当前命中的 `regime`
- 当前触发的 `suppression`
- 候选漏斗变化
- allocator / execution 的关键决策理由
- 关联版本号或配置标识

如果上线后连“它有没有真的生效”都看不出来，默认不能上线。

---

## 3. 最低通过门槛

一项研究结论只有在下面 5 条全部满足时，才允许进入候选 promotion：

1. **问题明确**
   - 有清晰 baseline
   - 有清晰 variant
   - 有明确评估窗口

2. **回测结论清楚**
   - 不只是总收益改善
   - 还要解释回撤、换手、成本拖累、错杀率或 funnel 变化

3. **样本外没有明显塌陷**
   - 样本外可以弱一些
   - 但不能方向反转或严重恶化

4. **成本后仍保留边际价值**
   - 不能靠忽略 friction 才成立

5. **有上线后的观测和回滚方案**
   - 有观测字段
   - 有失败阈值
   - 有回滚目标

---

## 4. 明确禁止晋升的情况

出现以下任一情况，默认不能 promote：

- 只在单一窗口有效
- 只在极少数大赢家上有效
- 风险调整后收益没有改善
- 样本外显著变差
- 成本后优势消失
- attribution 说不清到底是哪一层创造价值
- 改动内容无法在 runtime 中被观测

---

## 5. 推荐交付格式

每次准备 promote 一项结论时，研究摘要至少包含：

### 5.1 研究问题

- 本次变更是什么
- baseline 是什么
- variant 是什么
- 期望改善哪项问题

### 5.2 结果摘要

- 总收益
- 最大回撤
- Sharpe / Sortino / Calmar
- turnover
- trade count
- cost drag
- funnel / kill-rate / avoid-loss-rate（适用时）

### 5.3 样本外摘要

- 样本外窗口表现
- 最差窗口表现
- 是否存在明显失稳

### 5.4 上线计划

- runtime 要新增或复用哪些观测字段
- 观察期多长
- 失败后回滚到哪个版本

---

## 6. 简化检查清单

准备把研究结论写回策略主线前，必须逐项打勾：

- [ ] 有 baseline vs variant 对照
- [ ] 有样本外验证
- [ ] 有成本后结果
- [ ] 有 attribution 或 funnel 解释
- [ ] 有 runtime 观测字段
- [ ] 有回滚条件

任何一项没勾，默认继续研究，不进入主线。
