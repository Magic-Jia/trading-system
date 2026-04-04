# Backtest Data Spec

## Dataset root

Historical datasets live under a single root directory.

- `baseline_account_snapshot.json` — optional fallback account context
- `import_manifest.json` — optional Phase 1 importer-owned root manifest for machine readback
- `<bundle>/metadata.json` — required, contains `timestamp` and `run_id`
- `<bundle>/market_context.json` — required market snapshot
- `<bundle>/derivatives_snapshot.json` — required derivatives snapshot
- `<bundle>/account_snapshot.json` — optional bundle override

## Bundle requirements

Each bundle must be self-describing and deterministic:

- timestamps use ISO-8601 UTC
- bundle files are immutable research inputs
- repeated loads must preserve timestamp ordering
- forward returns / drawdowns belong in `metadata.json`

## Imported dataset assembly contract

这份 spec 约束的是 **最终交给 loader 的 imported dataset root**，因此 assembly 阶段必须保持最小、确定性、可读回：

- dataset root 一级只允许 bundle 目录、可选的 `baseline_account_snapshot.json`，以及可选的 importer-owned `import_manifest.json`
- bundle 内只允许当前 loader contract 需要的 snapshot / metadata 文件
- operator 的 provenance note、handoff note、人工说明、备份目录应保留在 dataset root 外；`import_manifest.json` 是唯一允许留在 root 内的 machine-owned manifest
- 如果目录仍保留 `<exchange>/<market>/<dataset>/<symbol>/<timeframe?>` 结构，它就还是 archive 层，不属于本 spec

这也意味着：当前 repo 的 Phase 1 operator 可以**手工装配 / 人工校对** dataset root，但不能把 future importer / downloader 说成当前仓库已经提供的现成功能

### Phase 1 materialization checklist

在真正把研究输入整理成 dataset root 前，先锁定这五个问题：

- 本次 materialization 对应的是哪个 research window，而不是泛泛地“把 archive 导进来”
- 每个 bundle 的 `timestamp` / `run_id` 准备从哪份已存在的 runtime/archive 记录写入
- `market_context.json`、`derivatives_snapshot.json`、账户快照分别来自哪一份已确认输入
- 账户上下文到底采用 bundle 自带 `account_snapshot.json`，还是 root 级 `baseline_account_snapshot.json`
- provenance / handoff note 准备记录在哪里；答案必须是 dataset root 外部

如果上述任一项仍依赖“等 importer/downloader 以后自动补”，就说明当前 Phase 1 materialization 还没准备好。

### Phase 1 root manifest contract

如果 dataset root 是由 Phase 1 importer materialize 出来的，root 内可以额外存在一个 `import_manifest.json`。

这个文件不是给人手写的 handoff note，而是 importer 自己留下的 machine-readable readback contract。至少应锁定：

- `schema_version`
- `scope`
- `archive_root`
- `dataset_root`
- `snapshot_count`
- `symbols`
- `bundle_dirs`
- `bundle_timestamps`
- `start_timestamp`
- `end_timestamp`
- `source`

如果 `import_manifest.json` 存在，validation 期待它与实际读回结果严格 round-trip：

- `dataset_root` 必须与当前 root 路径完全一致
- `scope` 必须仍在 Phase 1 importer scope 内
- `snapshot_count`、`symbols`、`bundle_dirs`、`bundle_timestamps` 必须与实际加载出的 bundle 集合一致
- `start_timestamp` / `end_timestamp` 必须与实际首尾 bundle 时间戳一致
- `source` 必须与 bundle `metadata.json` / dataset row `meta` 中读回的 source 对象一致
- 若 `source.manifest_paths` 存在，它们必须都位于同一个 `raw-market` 树下，并能反推出同一个 `archive_root`
- `source.manifest_paths` 指向的 raw-market manifest 必须真实存在；“路径看起来像对的”但文件已丢失，不算通过
- 每个被引用的 raw-market manifest 仍必须声明 Phase 1 允许的来源身份：至少保持 `exchange=binance`、`market=futures`；若 manifest 漂成 spot 或其他 market，这个 imported dataset root 就已超出当前 scope

### Imported root identity and source drift contract

只要 `import_manifest.json` 存在，下面这些字段就不再是“方便人看懂的摘要”，而是 imported dataset root 身份的一部分。

更直白地说：**dataset root 现在“能被 loader 读开”，只证明文件形状还勉强像个 dataset；不证明这份 dataset 仍然代表 manifest 当初声明的那一批 bundle、那一组 symbol、那一个 archive/source 身份。**

至少要把这 5 个字段理解成 field-level identity gate：

1. **`snapshot_count`**
   - 保护什么：保护 imported dataset root 里“应有多少个 snapshot / bundle 成员”的基数合同，防止 bundle 被误增、误删、漏拷、混入备份目录后还被当成同一份 dataset
   - 报错意味着什么：manifest 记录的 snapshot 数，与当前按 loader 合同能成立的 bundle 数不一致；常见原因是 root 里多了/少了 bundle，或 manifest 没跟上最近一次 materialization
   - 怎么判断是 manifest 坏了还是 root 现实变了：如果实际 bundle 集合、各 bundle `metadata.json`、外部 handoff note 彼此一致，只是 `import_manifest.json` 数字落后，那是 manifest 旧了；如果 root 里确实多了/少了目录、混进了备份 bundle，或原 bundle 被替换掉，那是 root 现实变了
   - 恢复动作：先按实际一级 bundle 目录 + 必需文件读回真实成员数，再决定是整份重建 manifest，还是移除/补回错误 bundle

2. **`symbols`**
   - 保护什么：保护这份 imported dataset 声称覆盖的是哪一组交易对，而不是只要文件还能读就算同一批研究输入
   - 报错意味着什么：manifest 里的 symbol universe，已经对不上实际 bundle/source 指向的 symbol 集合；常见原因是混入了错误 symbol 的 bundle、archive provenance 漂到另一组 symbols，或 manifest 仍停留在旧研究范围
   - 怎么判断是 manifest 坏了还是 archive/root 现实变了：如果 bundle `metadata.json.source`、被引用的 raw-market manifests、外部 handoff 目标都一致指向同一组 symbols，而只有 manifest 没刷新，那是 manifest 旧了；如果实际 bundle/source 已经指向另一组 symbols，哪怕 loader 还能读，也说明 archive/root 现实已经换了
   - 恢复动作：先以 bundle metadata 与 raw-market manifests 对表实际 symbol 集合；若 bundle/source 才是正确目标，就重建 manifest；若 handoff 目标才正确，就移除错误 symbol 的 bundle 或回到 archive 侧重做 materialization

3. **`archive_root`**
   - 保护什么：保护这份 imported dataset 的 raw-market 来源树身份，确保所有 `source.manifest_paths` 仍然属于同一个 canonical archive root，而不是从多个树、旧路径、临时副本里拼起来
   - 报错意味着什么：manifest 声明的 `archive_root`，已不能稳定解释 `source.manifest_paths` 实际落在哪棵 raw-market 树上；常见原因是 manifest 路径陈旧、source manifest 被搬家，或更糟的是 dataset 已经混用了多个 archive roots
   - 怎么判断是 manifest 坏了还是 archive/root 现实变了：如果 `source.manifest_paths` 仍都存在、仍都能反推出同一个 canonical raw-market root，只是 `archive_root` 文本没更新，那是 manifest 旧了；如果 `source.manifest_paths` 已分叉到多个 roots、引用临时目录、或只能靠人工解释“它们其实差不多”，那是 archive/source 现实变了
   - 恢复动作：先从每个 `source.manifest_paths` 反推真实 raw-market root；若真实来源树唯一且一致，就重建 manifest；若来源树已经分叉，先选定单一 canonical archive root 并重新 materialize，不要手改一个 `archive_root` 字段把问题盖住

4. **`source`**
   - 保护什么：保护 importer trace / raw-market provenance 对象本身，确保 `import_manifest.json.source`、bundle `metadata.json.source`、dataset row `meta.source` 仍在讲同一个来源故事
   - 报错意味着什么：source 对象丢了、被改写了、或不同 bundle/不同层写成了彼此不一致的 provenance 摘要；这说明 dataset 的 machine-readable 来源身份已经断裂
   - 怎么判断是 manifest 坏了还是 archive/root 现实变了：如果各 bundle `metadata.json.source` 与被引用的 raw-market manifests 彼此一致，只有 root manifest 的 `source` 不一致，那是 manifest 旧了；如果 bundle 之间自己的 `source` 就互相打架，或 bundle `source` 与 raw-market manifests / runtime `source_*` 字段对不上，那是 root/source 现实变了
   - 恢复动作：先把实际 bundle/source 与 raw-market manifests、runtime `source_*` 字段重新对表；只有在 machine-readable source 已经重新闭合到同一个 bundle 身份后，才重建 manifest。不要拿 operator note 代替 `source`

5. **`bundle_dirs`**
   - 保护什么：保护 imported dataset root 里“哪几个一级目录属于这次 handoff”的成员身份，避免备份目录、临时目录、重命名目录、旧 bundle 残留被静默算进去
   - 报错意味着什么：manifest 记录的目录成员，与实际 root 下的一级目录集合不一致；常见原因是手工改目录名、残留 `archive/` / `notes/` / `tmp/` / `backup/`，或 materialization 后忘了刷新 manifest
   - 怎么判断是 manifest 坏了还是 root 现实变了：如果实际一级目录集合正是 operator 当前要交付的那一批 bundle，只是 manifest 目录列表没更新，那是 manifest 旧了；如果 root 下出现不该存在的目录、旧 bundle 目录、备份目录，或正确 bundle 被重命名/替换，那是 root 现实变了
   - 恢复动作：先把 root 一级目录清到只剩真实 bundle，再核对每个 bundle 的 `timestamp` / `run_id` / `source`；确认 root 干净后再重建 manifest，而不是反过来改 `bundle_dirs` 让脏目录“看起来合法”

最小总规则：**只要上述 5 个字段中任一项已经不能从实际 bundle/source 读回同一份身份，就算 loader 还能读，合同也已经失效。**

### Imported dataset root drift gates

只要 `import_manifest.json` 存在，下面四类漂移都应视为 **readback fail / repair required**，而不是“manifest 稍微旧一点”：

1. **bundle metadata `schema_version` 漂移**
   - 保护什么：保护每个 bundle `metadata.json` 的字段语义仍属于同一版 importer/runtime metadata contract，避免同一个 dataset root 里混入不同 schema 的 bundle
   - 报错意味着什么：实际 bundle metadata 的 `schema_version` 与 importer / root manifest 预期不一致，或同一 root 内 bundle 彼此混用了不同 schema；这说明 dataset 里的 bundle 已经不再共享同一份 metadata 合同
   - 操作员怎么处理：先回到 source runtime/importer 记录，确认本次 handoff 应该使用哪一版 schema；若是错版本 bundle 混入，就恢复正确 bundle；若是整批数据确实升级到新 schema，就整批重 materialize bundle metadata 与 `import_manifest.json`，不要手改单个 bundle 字段糊过去

2. **manifest `bundle_timestamps` 漂移**
   - 保护什么：保护 root manifest 记录的 bundle 时间戳集合与实际 bundle 集合一致，防止 operator 增删 bundle、重写 metadata、补拷目录后忘了刷新 manifest
   - 报错意味着什么：`import_manifest.json.bundle_timestamps` 与实际从 bundle `metadata.json.timestamp` 读出的有序列表不一致；常见原因是多了旧 bundle、少了新 bundle，或 bundle metadata 已改但 manifest 还停留在旧窗口
   - 操作员怎么处理：先逐个核对实际 bundle 的 `timestamp` / `run_id` 与目标研究窗口；若目录里的 bundle 才是正确 source of truth，就重建 `import_manifest.json`；若 manifest 才是正确目标，就移除或恢复错误 bundle。不要只改 manifest 列表而不核对 bundle 本体

3. **manifest `start_timestamp` 漂移**
   - 保护什么：保护 imported dataset research window 的下边界，确保 operator、importer、backtest 看到的是同一个起点
   - 报错意味着什么：manifest 声明的最早时间戳，不再等于按 loader 合同从实际 bundle 读回后的首个时间戳；常见原因是错误混入更早 bundle、漏掉起始 bundle，或 bundle metadata 起点被改写
   - 操作员怎么处理：先按 `timestamp`、再按 `run_id` 重排实际 bundle，确认真实首个 bundle；若是 root 被污染（例如误放备份 bundle），先清理错误目录；若研究窗口本来就变了，就同步重写 `bundle_timestamps`、`start_timestamp` 与 dataset root 外部 handoff note，然后重新 readback

4. **manifest `end_timestamp` 漂移**
   - 保护什么：保护 imported dataset research window 的上边界，确保交付出去的 dataset root 仍然代表同一个结束点
   - 报错意味着什么：manifest 声明的最后时间戳，不再等于实际 bundle 集合读回后的末尾时间戳；常见原因是增量补数后只换了 bundle 没刷新 manifest，或末尾 bundle 丢失/回退到了旧版本
   - 操作员怎么处理：先确认本次 handoff 预期的终点窗口，再核对最后一个 bundle 的 `timestamp` / `run_id`；若实际 bundle 集合正确，就重建 manifest；若 manifest 目标才正确，就补回缺失 bundle 或回滚错误追加。不要在末尾时间戳漂移时继续把 dataset root 当成已完成 handoff 的研究输入

这四类漂移里，后 3 项都应以 **按 loader 合同从 bundle metadata 读回的实际结果** 为准：先按 `timestamp`、再按 `run_id` 排序，再比较 manifest 的 `bundle_timestamps`、`start_timestamp`、`end_timestamp`。

### Source-manifest and runtime provenance continuity

对 runtime 派生的 imported dataset，Phase 1 现在要求把两条 provenance 链接在同一个 bundle 身份上，而不是分散成几份互不对表的说明：

1. raw-market 身份链：raw-market manifest -> `import_manifest.json.source.manifest_paths` -> bundle `metadata.json.source` -> dataset row `meta.source`
2. runtime 身份链：runtime `latest.json` -> bundle `metadata.json` / config `metadata` / dataset row `meta`

最小连续性要求：

- `import_manifest.json.source` 与每个 bundle `metadata.json.source` 应保持同一个 importer trace 语义，而不是各写各的摘要
- 同一份 bundle `metadata.json` 还应继续保留 `source_bundle`、`source_run_id`、`source_timestamp`、`source_mode`、`source_runtime_env`、`source_finished_at`
- `source.manifest_paths` 解决的是“这份 bundle 用了哪些 raw-market manifests”；`source_bundle` 等字段解决的是“这份 bundle 对应哪次 runtime/research 产物”；两者互补，不互相替代
- 如果只剩 runtime summary 而 bundle metadata 不再带 raw-market `source`，或只剩 `source.manifest_paths` 却对不上 runtime `source_*` 字段，都应视为 provenance continuity 已断

### Phase 1 provenance promotion gate

对 operator 来说，Phase 1 现在还多了一条 promotion gate：**只要 raw-market 身份链或 runtime 身份链有一条断掉，这份 imported dataset 就只能继续留在 readback / repair 状态，不能当成已完成 handoff。**

最小判断规则：

- raw-market manifest 仍在、`source.manifest_paths` 也还能对上，但 bundle `metadata.json` / dataset row `meta` 缺少对应 `source`：说明 imported dataset 丢了 machine-readable raw-market 身份，先补 metadata/importer trace，再谈交付
- runtime `latest.json` 或 config 还能对上 `source_bundle` / `source_run_id` / `source_timestamp`，但 bundle metadata 已经缺这些 `source_*` 字段：说明 runtime 身份链断在 imported dataset 这一层，先修 bundle metadata，再谈交付
- raw-market `source` 与 runtime `source_*` 两条链都还在，但互相指向不同 bundle 身份：说明 provenance join point 已漂移；应直接视为 readback fail，而不是靠 operator note 解释过去
- 只有 human-written note 还能说明“这份数据大概来自哪里”，但 machine contract 已经断掉：说明这份 dataset 目前只剩线索，不算可交付 research input

operator note / handoff note 可以记录 repair 过程，但**不能**替代 `import_manifest.json.source`、bundle `metadata.json.source` 或 bundle `metadata.json` 里的 runtime `source_*` 字段。

## Loader behavior

`trading_system.app.backtest.dataset.load_historical_dataset`:

- sorts bundles by `timestamp`, then `run_id`
- fails loudly if any required snapshot file is missing
- falls back to `baseline_account_snapshot.json` when bundle account data is absent
- treats every first-level directory under dataset root as a bundle candidate
- does not rely on bundle directory names for ordering; directory names are operator-facing labels only

补充几条直接来自当前 loader 实现的 validation 现实：

- `metadata.json` 缺少 `timestamp` 或 `run_id` 时，加载会直接失败，而不是自动补默认值
- `timestamp` 需要能被当前 `datetime.fromisoformat(...replace("Z", "+00:00"))` 解析
- `derivatives_snapshot.json` 既可以是数组，也可以是带 `rows` 数组的对象；其他结构会失败
- `import_manifest.json` 属于 importer readback contract，而不是 loader 排序 contract；loader 不靠它排序，但 importer validation 会校验它
- root 级普通文件目前除 `baseline_account_snapshot.json` 与可选的 `import_manifest.json` 外，不应再混入其他文件，以免 handoff 语义变脏

## Phase 1 boundary

这份 spec 只描述 **imported dataset root**，不描述 raw-market archive 叶子目录。

换句话说：

- `load_historical_dataset` 应读取整理好的 dataset root
- 不应直接读取 `trading_system/data/archive/raw-market/...`
- raw-market archive 仍遵守 Binance-first / futures-first / coverage-driven 的独立 contract

当前仓库现实也要讲清楚：已经落地并可验证的是 dataset loader / backtest CLI；通用 archive importer 还在批准计划内，尚未成为当前 repo 的现成入口。

换句话说，Phase 1 当前可执行的 operator 路径是：

- 先在 raw-market archive 层确认 coverage 与 provenance
- 再按 loader contract 手工整理或人工校对 dataset root
- 最后把 dataset root 交给 `load_historical_dataset`

不要把这段话读成“仓库已经有自动 downloader / importer 会直接产出 dataset root”。

## Operator handoff

operator 在 Phase 1 应按这条链路理解数据流：

1. 先在 raw-market archive 层完成 backfill 或 incremental refresh
2. 再把需要研究的数据整理成当前 loader 认可的 dataset root
3. 最后才交给 `load_historical_dataset` 和 backtest CLI

如果某个目录仍保留 `<exchange>/<market>/<dataset>/<symbol>/<timeframe?>` 结构，它就还是 archive 层，不是本 spec 里的 dataset root。

交接时至少再复核这四件事：

- archive coverage 已经先被证明，而不是边缺数据边假装导入完成
- dataset root 与 `trading_system/data/archive/raw-market/...` 完全分离
- provenance / handoff 说明保留在 dataset root 之外
- 当前表述没有暗示仓库已经存在通用 importer / archive CLI

再补两条当前 loader 视角下的 assembly 复核：

- `metadata.json` 是否真的提供了 `timestamp` 与 `run_id`
- 当 bundle 缺少 `account_snapshot.json` 时，dataset root 是否真的存在 `baseline_account_snapshot.json`

再补两条 root validation 现实：

- dataset root 下若出现 `archive/`、`notes/`、`tmp/`、备份目录等一级目录，loader 会把它们当成 bundle 并在缺文件时报错
- 除 `baseline_account_snapshot.json` 与 importer-owned `import_manifest.json` 外，Phase 1 不应在 dataset root 一级混入 handoff note、checksum 或下载日志，即使这些文件未必会被 loader 直接消费

### Root validation checklist

Phase 1 operator 在交付 dataset root 前，至少逐条复核：

- dataset root 是否与 `trading_system/data/archive/raw-market/...`、runtime bundle 路径、研究输出目录彻底分离
- 一级目录是否全部都是真正 bundle，而不是 archive 镜像目录、`notes/`、`tmp/`、备份目录
- 每个 bundle 是否都具备 `metadata.json`、`market_context.json`、`derivatives_snapshot.json`
- 每个 bundle 的 `metadata.json` 是否都有合法 `timestamp` / `run_id`
- 若 bundle 缺 `account_snapshot.json`，root 级是否真的存在 `baseline_account_snapshot.json`
- 若 root 内存在 `import_manifest.json`，它的 `schema_version` / `scope` / `dataset_root` 是否与当前 materialization 语义一致
- 若 root 内存在 `import_manifest.json`，它的 `snapshot_count`、`symbols`、`bundle_dirs`、`bundle_timestamps`、`start_timestamp`、`end_timestamp` 是否都能从实际加载结果读回
- 若 root 内存在 `import_manifest.json`，它的 `source.manifest_paths` 是否都落在同一个 `raw-market` 树下，并且反推出的 `archive_root` 与 manifest 自身一致
- 若 root 内存在 `import_manifest.json`，它引用的每个 raw-market manifest 是否都还存在，并且 payload 仍明确落在 `binance/futures` Phase 1 scope 内
- 当前文档/交接表述是否仍停留在“手工整理 / 人工校对”，没有冒进宣称已存在自动 importer / downloader

### External readback record

dataset root 通过 root validation，不代表 handoff 已经完整；Phase 1 还应在 dataset root 外保留一份最小 operator 记录。

这点在 `import_manifest.json` 存在时也一样：root manifest 解决的是 machine readback，不替代 operator handoff note。

这份记录至少应写清：

- dataset root 路径，以及它对应的 research window / bundle 集合
- 本次 materialization 对应的 `archive_root`，以及被引用的 raw-market manifest 路径集合（与 `source.manifest_paths` 保持同一语义）
- runtime provenance 依赖的记录位置：至少写清对应的 `latest.json` 与 config metadata 来源；若本次 handoff 还要延续 paper execution continuity，也应一并记下 `runtime_state.json` / ledger 引用
- 用来把 raw-market 与 runtime join 到同一个 bundle 身份上的字段：至少记录 `source_bundle`、`source_run_id`、`source_timestamp`
- 每个 bundle 的 `timestamp` / `run_id` 来源记录
- `market_context.json`、`derivatives_snapshot.json`、账户快照各自来自哪份已确认输入
- 本次账户上下文到底使用 root 级 `baseline_account_snapshot.json`，还是 bundle 级 `account_snapshot.json`
- 最近一次 lightweight readback 的时间、执行人、结果

这份外部记录的作用，是把 dataset root 外围仍然需要 operator 保留的 provenance continuity 固定下来；它可以补足 `import_manifest.json` 不负责保存的人类交接上下文，但**不能**改写 machine-readable `source` / `source_*` 字段。

如果这些信息还只能靠口头补充，或者只能靠“看目录名猜”，就说明当前 Phase 1 materialization 仍不够可交接。

## Related docs

- `trading_system/docs/HISTORICAL_DATA_ARCHITECTURE.md`
- `trading_system/docs/HISTORICAL_DATA_RUNBOOK.md`
- `trading_system/docs/HISTORICAL_DATA_RETENTION.md`
