---
title: "配管（SC20） 审核沉淀"
type: "review"
status: "reviewed"
province: "北京市建设工程施工消耗量标准(2024)"
specialty: "C4"
source_refs:
  - "staging:audit_errors:5"
  - "task:0764f6f5-9f66-437f-b0f4-bda4c33f3efc"
  - "result:3f9760da-6894-4634-a74b-805a8d024a6a"
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
  - "rules/rule-0010-配管（SC20）-纠正规则候选.md"
  - "methods/method-0011-配管（SC20）-审核方法候选.md"
  - "cases/case-0012-配管（SC20）-历史案例候选.md"
---

# 配管（SC20） 审核沉淀

## 来源
- 审核记录: `audit_errors:5`
- 任务 ID: `0764f6f5-9f66-437f-b0f4-bda4c33f3efc`
- 结果 ID: `3f9760da-6894-4634-a74b-805a8d024a6a`
- 匹配来源: `search`

## 清单信息
- 名称: 配管（SC20）
- 特征: 配管SC20，暗敷,从配电箱至灯位
- 省份: 北京市建设工程施工消耗量标准(2024)
- 专业: C4

## 错配结论
- 错因类型: `wrong_rank`
- 错因等级: `high`
- 原命中定额: `C4-4-37` 配电箱箱体安装 配电箱半周长(m以内) 明装 2.5
- 修正定额: `C4-11-35` 焊接钢管砖、混凝土结构暗配 公称直径(mm以内) 20

## 判断依据
人工二次确认: 正式确认测试：接受 OpenClaw 对 SC20 配管建议定额。
正式确认测试：接受 OpenClaw 对 SC20 配管建议定额。
人工二次确认: 正式确认测试：接受 OpenClaw 对 SC20 配管建议定额。

## 修正建议
改判为 焊接钢管砖、混凝土结构暗配 公称直径(mm以内) 20(C4-11-35)

## 根因标签
- search
- ranking

## 可晋升输出
- 可生成规则: 是
- 可生成方法: 是

## 关联页面
- [[rule-0010-配管（SC20）-纠正规则候选]]
- [[method-0011-配管（SC20）-审核方法候选]]
- [[case-0012-配管（SC20）-历史案例候选]]
