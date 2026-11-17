# 深度登记 印刷拼版用纸损耗与订单成本分摊

规模：中型。核心规则：按版面旋转比较可排数量，固定开机损耗与印量损耗分开计，再对订单做最大余数分摊。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 工单采信 | 查看并复算工单采信 | `/api/m1_ingest` | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py` |
| M2 纸张换算 | 查看并复算纸张换算 | `/api/m2_mapping` | `app/modules/m2_mapping/rules.py` | `tests/test_mapping.py` |
| M3 拼版排算 | 查看并复算拼版排算 | `/api/m3_versions` | `app/modules/m3_versions/rules.py` | `tests/test_versions.py` |
| M4 损耗认定 | 查看并复算损耗认定 | `/api/m4_segments` | `app/modules/m4_segments/rules.py` | `tests/test_segments.py` |
| M5 余料结转 | 查看并复算余料结转 | `/api/m5_calculate` | `app/modules/m5_calculate/rules.py` | `tests/test_calculate.py` |
| M6 成本对账 | 查看并复算成本对账 | `/api/m6_reconcile` | `app/modules/m6_reconcile/compare.py` | `tests/test_reconcile.py` |
| M7 批次归档 | 查看并复算批次归档 | `/api/m7_archive` | `app/modules/m7_archive/export_out.py` | `tests/test_archive.py` |
| M8 换纸裁决 | 查看并复算换纸裁决与封存 | `/api/m8_substitution` | `app/modules/m8_substitution/rules.py` | `tests/test_substitution.py`、`tests/test_substitution_api.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | GB18030中文 | 固定UTF-8失败 | 按编码链回退 | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_legacy_encoding` |
| TRAP-02 | 表头BOM | 首列键失配 | 去除BOM | `app/modules/m1_ingest/decode.py` | `tests/test_ingest.py::test_bom` |
| TRAP-03 | 引号内换行 | 分行误计 | csv逻辑记录 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_preserves_multiline_note` |
| TRAP-05 | 列数错位 | 静默补列 | WIDTH隔离 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_width_isolation` |
| TRAP-06 | 全角千分位 | float失败 | NFKC与Decimal | `app/modules/m2_mapping/coerce.py` | `tests/test_mapping.py::test_thousands` |
| TRAP-07 | 空值哨兵 | 空值当零 | MISSING隔离 | `app/modules/m2_mapping/coerce.py` | `tests/test_mapping.py::test_missing_numeric` |
| TRAP-08 | 前导零ID | 编码失真 | 字符串保形 | `app/modules/m2_mapping/rules.py` | `tests/test_mapping.py::test_identifier_zeroes` |
| TRAP-10 | 浮点误差 | 累计失准 | Decimal求和 | `app/modules/m2_mapping/coerce.py` | `tests/test_mapping.py::test_decimal_precision` |
| TRAP-14 | 同版冲突 | 最后覆盖 | 整组隔离 | `app/modules/m2_mapping/rules.py` | `tests/test_mapping.py::test_real_conflict_quarantine` |
| TRAP-18 | 三种空态 | 除零或伪成功 | 三个错误码 | `app/modules/m1_ingest/reader.py` | `tests/test_ingest.py::test_empty_three_states` |
| TRAP-26 | 旧版本复核 | 覆盖新结论 | 事务条件更新 | `app/store.py` | `tests/test_archive.py::test_stale_review` |
| TRAP-15 | cm与mm | 尺寸相差10倍 | 尺寸统一mm | `app/modules/m3_versions/rules.py` | `tests/test_versions.py::test_real_cm_conversion` |
| TRAP-24 | 分币尾差 | 合计不相等 | 按纸批最大余额 | `app/modules/m5_calculate/rules.py` | `tests/test_calculate.py::test_largest_remainder` |
| TRAP-30 | 未选纸批被预占 | 候选即扣库存 | 仅被选做法在projection扣减 | `app/modules/m8_substitution/reconcile.py` | `tests/test_substitution.py::test_unselected_lots_are_not_preempted` |
| TRAP-31 | 刚好够产两做法混淆 | 只给一种排数 | full/split双口径并标注exact_fit | `app/modules/m8_substitution/rules.py` | `tests/test_substitution.py::test_exact_fit_flagged_and_two_policies_differ` |
| TRAP-32 | 封存失败覆盖旧版 | 先写后验 | 对账通过才条件写入，失败保留旧版 | `app/store.py` | `tests/test_substitution.py::test_seal_idempotent_and_keeps_old_on_failure` |
| TRAP-33 | 重跑重复计纸计成本 | 每次封存新增 | 决策指纹INSERT OR IGNORE幂等 | `app/store.py` | `tests/test_substitution_api.py::test_seal_flow_via_api` |

## 对账规则

输入行数=采纳+隔离+空行+重复表头+完全重复；业务总数与计算证据独立求和。隔离行保留来源行、ID、原因码与原值。

## 已知不覆盖

不做多租户权限和外部服务联调。行业规则以项目数据字典为准。文件上限2MB，逐行解析后在上限内完成分组计算。
