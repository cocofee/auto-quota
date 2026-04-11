---
title: "线 审核沉淀"
type: "review"
status: "reviewed"
province: "北京市建设工程施工消耗量标准(2024)"
specialty: "C4"
source_refs:
  - "staging:audit_errors:2"
  - "task:0764f6f5-9f66-437f-b0f4-bda4c33f3efc"
  - "result:2bc215c6-0640-4ea8-8cf6-997241685c6c"
source_kind: "staging"
created_at: "2026-04-04"
updated_at: "2026-04-04"
confidence: 90
owner: "41024847"
tags:
  - "audit"
  - "search"
  - "wrong_rank"
  - "high"
related:
  - "rules/rule-0001-线-纠正规则候选.md"
  - "methods/method-0002-线-审核方法候选.md"
  - "cases/case-0003-线-历史案例候选.md"
---

# 线 审核沉淀

## 来源
- 审核记录: `audit_errors:2`
- 任务 ID: `0764f6f5-9f66-437f-b0f4-bda4c33f3efc`
- 结果 ID: `2bc215c6-0640-4ea8-8cf6-997241685c6c`
- 匹配来源: `search`

## 清单信息
- 名称: 线
- 特征: BV-2.5mm2
- 省份: 北京市建设工程施工消耗量标准(2024)
- 专业: C4

## 错配结论
- 错因类型: `wrong_rank`
- 错因等级: `high`
- 原命中定额: `C4-8-31` 电缆沿沟内支架敷设 电缆截面(mm2以内) 2.5
- 修正定额: `C4-11-283` 管内穿铜芯线照明线路 导线截面(mm2以内) 2.5

## 判断依据
明显错配：BV-2.5mm2 是导线/穿线语义，不应落到电缆沿沟内支架敷设。这里补一条可见建议定额，先不做正式 confirm。
明显错配：BV-2.5mm2 是导线/穿线语义，不应落到电缆沿沟内支架敷设。这里补一条可见建议定额，先不做正式 confirm。

## 修正建议
改判为 管内穿铜芯线照明线路 导线截面(mm2以内) 2.5(C4-11-283)

## 根因标签
- search
- ranking

## 可晋升输出
- 可生成规则: 是
- 可生成方法: 是

## 关联页面
- [[rule-0001-线-纠正规则候选]]
- [[method-0002-线-审核方法候选]]
- [[case-0003-线-历史案例候选]]
