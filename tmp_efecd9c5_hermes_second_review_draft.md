# Hermes Second Review Opinion

## 1. Basic Context
- Task/file/list: 安徽 1#公寓 给排水工程
- Task ID: efecd9c5-2327-43e7-8534-d36339969ecf
- Source list/file: [安徽]1#公寓--给排水工程_wxwork_zip.xlsx
- Environment: auto-quota worktree
- Review time: 2026-04-11T13:26:18

## 2. Review Target
- Review scope: Jarvis 一审后全部黄/红灯与不确定项，共 145 条，重点二审红/黄与明显错配簇。
- Jarvis current summary: 绿灯 36 / 黄灯 34 / 红灯 75；高频缺口集中在套管、刷油/保温、软接头、预留孔洞、抗震支架、泵组设备、阀门/附配件等。

## 3. Hermes grouped decision

### A. agree
- Count: 36
- Indexes: 1,2,4,5,6,7,8,9,11,13,33,42,43,44,45,46,47,48,49,55,60,81,82,86,87,88,89,90,93,102,106,108,123,125,136,137
- Decision: agree
- Suggested action: 保留 Jarvis 当前 top1，不做 override。
- Reason codes:
  - match_family
  - match_param_or_nearest_valid_grade
  - ambiguity_only_not_enough_to_override
  - top1_defensible
- Review note:
  - 这批条目以基础管道、排水管、部分阀门/水表/地漏/支架为主，当前 top1 家族和专业大方向可自洽。
  - 即使存在 manual_review / ambiguity_review 信号，也不足以推翻当前候选。

### B. abstain / keep for human review
- Count: 38
- Indexes: 3,10,12,17,18,19,20,21,22,23,24,25,26,27,28,32,34,37,50,53,59,64,67,70,72,73,74,75,80,83,91,98,107,109,110,116,119,126
- Decision: abstain
- Suggested action: 暂不回流，保留人工复核。
- Reason codes:
  - mixed_signals
  - param_risk
  - synonym_gap
  - evidence_not_strong_enough
- Review note:
  - 这批多为“看起来接近，但证据还不够硬”的项目。
  - 典型情形：DN70 衬塑钢管、减压器/减压阀、部分法兰阀门、设备类条目、刷油/保温类描述等。
  - 当前不宜激进 override，建议人工确认后再正式回流。

### C. current top1 clearly unsafe
- Count: 71
- Indexes: 14,15,16,29,30,31,35,36,38,39,40,41,51,52,54,56,57,58,61,62,63,65,66,68,69,71,76,77,78,79,84,85,92,94,95,96,97,99,100,101,103,104,105,111,112,113,114,115,117,118,120,121,122,124,127,128,129,130,131,132,133,134,135,138,139,140,141,142,143,144,145
- Decision: candidate_pool_insufficient
- Suggested action: 当前 top1 不应直接采用；优先补召回/改搜索，再由人工确认。
- Reason codes:
  - wrong_family
  - wrong_param
  - wrong_category
  - synonym_gap
  - missing_candidate
  - non_quota_item
- Review note:
  - 这批是明显错配簇：套管误落到堵洞/防火套管/漏斗，过滤器或球形止回阀误落到螺纹法兰安装，软接头误召回电气条目，刷油/保温/标识/措施项目本身不适合直接套当前安装定额，抗震支架误召回避雷/线缆条目，厨房卫浴个别器具落错家族。
  - 结论不是“随便换一个候选”，而是“当前候选池质量不足，需补召回或人工定”。

## 4. Key cluster notes
- 套管/套管制作安装：当前主要问题不是单一档位，而是家族混入“堵洞 / 成品防火套管 / 一般钢套管 / 其他近邻项”；需要把“刚性防水套管 / 钢套管 / 套管制作安装”拆开处理。
- 刷油/保温/标识：这类条目被强行落到管道安装或试压，说明 family gate 太松；优先判为 candidate_pool_insufficient 或非本轮直接安装定额。
- 软接头/抗震支架：大量落入电气或避雷条目，属于明显 wrong_family。
- 设备泵组/附配件：生活水箱、主泵/小泵、气压罐、潜污泵等，部分 top1 有一定接近性，但不少仍缺关键参数或对象层级证据。
- 阀门/过滤器/止回阀：要先分清对象类别，再看连接方式与口径；不能用“法兰安装”替代“阀门本体安装”。
- 措施项目：143、144、145 不建议纳入本轮安装定额正式回流。

## 5. Retry search instruction
- Need retry search: yes, but only for C 类为主；B 类按人工优先。
- Retry query directions:
  - 套管：刚性防水套管 / 一般钢套管 / 套管制作安装 / 穿墙套管
  - 阀门：过滤器 / 球形止回阀 / 减压阀 / 法兰阀门 / 螺纹阀门
  - 设备：潜污泵 / 变频加压泵组 / 生活水箱 / 气压罐 / 紫外线消毒器
  - 附配件：地漏 / 洗衣机专用地漏 / 水表 / 压力表 / 不锈钢成品淋浴器
  - 非安装直套项：防结露保温 / 防冻保温 / 刷油 / 标识 / 措施项目

## 6. Human Confirmation Gate
- Human confirm required before flow-back: yes
- Pending confirmation points:
  - 是否接受本次 grouped 二审结论作为正式 review 草稿。
  - 对 B 类 38 条，是否继续做逐条深挖，还是先按 abstain 挂起。
  - 对 C 类 71 条，是否进入“补召回/修规则”模式，再让 Codex 按簇修复。

## 7. Short conclusion
- Hermes 二审结论：本单不宜整体放行。36 条可 agree 保留 Jarvis top1；38 条证据不足，建议 abstain 等人工复核；71 条当前 top1 明显不安全，应判 candidate_pool_insufficient，而不是强行 override。高频系统性问题集中在套管、刷油/保温/标识、软接头、抗震支架、设备泵组与阀门家族混召回，后续若进入修复，应按簇收紧 family gate、补同义词与候选池，而不是逐条打补丁。
