# efecd9c5 Systemic Repair Plan

> For Codex/Hermes: treat this as a cluster-level repair plan, not a per-item patch list.

Goal:
Reduce obvious wrong-family / wrong-category / missing-candidate failures on task efecd9c5-2327-43e7-8534-d36339969ecf, then rerun the same bill and verify red/yellow items drop for the targeted clusters.

Architecture:
This is mainly a retrieval + family-gate + query-building + final-validation repair. Do not hardcode single quota ids for single bill rows. Fix the whole class: candidate generation, family boundary checks, synonym expansion, and review reason reporting.

Primary evidence:
- Task summary: output/tasks/efecd9c5-2327-43e7-8534-d36339969ecf/审核报告_v6.1.md
- Hermes grouped review draft: tmp_efecd9c5_hermes_second_review_draft.md
- Task review summary: output/tasks/efecd9c5-2327-43e7-8534-d36339969ecf/openclaw_review_summary.json

Current grouped conclusion:
- agree: 36
- abstain: 38
- candidate_pool_insufficient: 71

Do first:
1. Build regression fixtures from this bill for the representative rows below.
2. Add failing tests before code changes.
3. Fix one cluster at a time.
4. After each cluster, rerun targeted tests.
5. After all clusters, rerun the same efecd9c5 bill and compare cluster outcomes.

Likely code areas:
- web/backend/app/services/openclaw_review_service.py
- web/backend/app/api/openclaw.py
- web/backend/app/services/match_service.py
- matching / query / validator pipeline files under repo main codepath
- tests/test_openclaw_review_policy_regressions.py
- tests/test_final_validator.py
- tests/test_query_builder_stage3_recall_cleanup.py
- tests/test_match_pipeline_installation_common_picks.py
- tests/test_match_pipeline_installation_family_picks.py
- tests/test_match_pipeline_plumbing_accessory_pick.py
- tests/test_review_correctors_strategy.py

---

## Cluster 1: 套管 / 套管制作安装 family split

Problem:
套管类被错误召回到 堵洞 / 成品防火套管 / 钢制排水漏斗 / 一般钢套管 的混合池里，family 边界太松。

Representative rows:
- 12, 13, 14, 15, 16
- 54, 55, 56, 57
- 86, 92, 93, 94
- 116, 117, 118, 119, 120

Observed wrong matches:
- 套管 -> 堵洞
- 套管 -> 成品防火套管
- 套管制作安装 -> 钢制排水漏斗制作与安装

Root-cause hypothesis:
- Query builder overweights shared words like “制作安装 / 公称直径”.
- Retrieval lacks explicit sleeve subtype intent extraction.
- Final validator flags mismatch but candidate pool still lacks same-family items.

Required repairs:
1. Add sleeve subtype extraction:
   - 刚性防水套管
   - 一般钢套管
   - 防火套管
   - 穿墙/穿板套管
   - 套管制作安装
2. Add hard family gate so “堵洞 / 漏斗” cannot outrank sleeve family when bill name/desc clearly says 套管.
3. Expand retrieval/query synonyms around 套管制作安装 and 刚性防水套管.
4. Improve review reason codes so these cases report wrong_family / missing_candidate instead of generic ambiguity.

Acceptance:
- Representative rows no longer top1 到 堵洞/漏斗。
- If exact sleeve candidate still absent, output must cleanly become candidate_pool_insufficient, not fake-nearest wrong family.

---

## Cluster 2: 刷油 / 保温 / 标识 non-installation gate

Problem:
刷油、保温、标识类被强行落到 管道安装 / 试压 / 铜管 等安装项，说明 installation family gate 太松。

Representative rows:
- 34, 35, 37, 38, 39, 40
- 50, 51, 52, 53
- 83, 91, 98, 99
- 114, 121, 126

Observed wrong matches:
- 防冻保温 -> 管道水压试验 / 铜管安装
- 防结露保温 -> 无稳定候选或错家族
- 标识刷调和漆 -> 管道安装
- 金属结构刷油 -> 无候选或安装近邻

Root-cause hypothesis:
- Parser extracts pipe/material tokens but misses work-type intent: 保温 / 刷油 / 标识.
- Query route keeps these in installation retrieval lane.
- Validator catches late but no earlier gate blocks the wrong family.

Required repairs:
1. Add work-type intent tags: insulation / painting / marking / anti-corrosion.
2. Add route gate: these intents should not default into plain pipe-installation family retrieval.
3. Add explicit fallback policy: when installation book lacks valid same-family candidates, emit candidate_pool_insufficient instead of unsafe top1.
4. Improve reason reporting to surface non_installation_worktype or wrong_family clearly.

Acceptance:
- These rows no longer top1 to pipe installation/test items.
- When unsupported in current quota scope, result should be explicit candidate_pool_insufficient / non_quota_item style outcome.

---

## Cluster 3: 软接头 / 抗震支架 wrong-family suppression

Problem:
软接头和抗震支架大量误召回到电气、避雷、线缆条目，属于非常明显的 wrong_family。

Representative rows:
- 76, 77, 78, 112, 113
- 137, 138, 139, 140, 141, 142

Observed wrong matches:
- 软接头安装 -> 滑触线拉紧装置 / 可挠金属套管敷设
- 水管两管侧纵向/侧向支吊架 -> 避雷引下线 / 塑料护套线 / 铜母线 / 钢管铺设

Root-cause hypothesis:
- Shared tokens like 支架/吊架/软/管 cause lexical collision.
- Retrieval lacks plumbing support-family prior.
- No strong specialty/family suppression before ranking.

Required repairs:
1. Add plumbing accessory/support family prior for 给排水专业.
2. Add specialty-aware negative boosts against electrical lightning/cable families for these intents.
3. Add support/brace/accessory query templates specifically for 给排水支吊架、抗震支架、软接头.
4. Add final validator escalation from ambiguity to wrong_family for cross-discipline obvious collisions.

Acceptance:
- Representative rows must not top1 to electrical / lightning items.
- If exact support family still missing, return candidate_pool_insufficient with clear wrong_family evidence.

---

## Cluster 4: 阀门 / 过滤器 / 止回阀 object-type separation

Problem:
过滤器、球形止回阀、减压阀等对象被混到“螺纹法兰安装”或“减压器组成安装”，对象类别没分清就开始按连接方式找。

Representative rows:
- 18, 19
- 28, 29, 30, 31, 32
- 108, 109, 110, 111

Observed wrong matches:
- 过滤器 -> 螺纹法兰安装
- 球形止回阀 -> 螺纹法兰安装
- 减压阀/减压器 -> 减压器组成安装（对象边界不清）

Root-cause hypothesis:
- Pipeline overweights connection form and diameter before object type.
- Query builder does not preserve valve subtype strongly enough.
- Candidate pool may include installation action items but miss object-specific entries.

Required repairs:
1. Extract object subtype first: 过滤器 / 球形止回阀 / 减压阀 / 法兰阀门 / 螺纹阀门 / 水表.
2. Rank object-type match above connection-form match.
3. Add mismatch penalty when bill object is 阀/过滤器/止回阀 but candidate is only 法兰安装 action without matching object.
4. Improve final validation labels for category_mismatch vs connection_mismatch.

Acceptance:
- Rows 29/30/31/110/111 no longer land on generic 螺纹法兰安装 as safe top1.
- If no exact object candidate exists, route to candidate_pool_insufficient, not fake object substitution.

---

## Cluster 5: 设备泵组 / 水箱 / 气压罐 grouped-equipment parsing

Problem:
泵组设备类有的接近，有的仍缺对象层级证据；主泵/小泵/生活水箱/气压罐等需要更强设备对象解析，不能只靠“设备质量/口径”近似。

Representative rows:
- 62, 64, 65, 66, 67, 68, 69
- 102, 103
- 63, 70, 75, 79

Observed risks:
- 主泵/小泵 -> 变频给水设备，可能部分可接受但对象粒度不稳定
- 生活水箱 -> 矩形钢板水箱制作，需确认“制作 vs 成品/安装”
- 潜污泵 -> 民用潜水排污泵安装，可能方向对但仍需对象和参数核实

Root-cause hypothesis:
- Equipment parser underextracts “组内部件 vs 整体设备”.
- Ranking does not clearly separate 成套设备 / 单泵 / 附属罐体.
- Some cases are true gray-zone; need better explicit evidence, not blind override.

Required repairs:
1. Add grouped-equipment parsing: 整体泵组 / 主泵 / 小泵 / 气压罐 / 水箱 / 消毒器.
2. Preserve object granularity in query builder and ranker features.
3. Add validator checks for 制作 vs 安装, 成套 vs 单体, 设备 vs 附件.
4. Keep conservative policy: if evidence is still mixed, candidate_pool_insufficient is acceptable.

Acceptance:
- Representative rows should either become clearly defensible same-object candidates or explicit insufficient-candidate outcomes.
- Avoid false certainty on equipment subcomponents.

---

## Cluster 6: 卫浴/附配件 synonym and family cleanup

Problem:
部分卫浴器具/附配件近似对了但对象还会漂移，另一些明显是 synonym gap。

Representative rows:
- 95, 96, 101, 124
- 127, 128, 129, 130, 132, 133, 134, 135

Observed risks:
- 不锈钢成品淋浴器 -> 饮水器安装
- 抽油烟机 -> 暖风机安装
- 附配件 -> 穿墙管/地面扫除口/止水环近邻

Root-cause hypothesis:
- Household fixture synonyms incomplete.
- Retrieval overmatches generic appliance or plumbing-adjacent terms.
- No strong object-family whitelist for kitchen/bath fixtures.

Required repairs:
1. Expand synonym dictionary for 卫浴器具/厨房灶具/附配件.
2. Add appliance-family separation so 抽油烟机 / 淋浴器 / 洗涤盆 / 水龙头 do not drift to unrelated appliance/plumbing items.
3. Add fixture-family validator with stronger wrong_family escalation.

Acceptance:
- Representative rows stop drifting to unrelated appliance families.
- Same-family fixture candidates should dominate when available.

---

## Cluster 7: OpenClaw review payload quality

Problem:
二审需要更少“泛化 manual_review / ambiguity_review”，更多结构化可行动 reason codes，方便后续自动汇总和人工确认。

Files:
- web/backend/app/services/openclaw_review_service.py
- web/backend/app/api/openclaw.py
- tests/test_openclaw_review_policy_regressions.py
- tests/test_web_openclaw.py

Required repairs:
1. Ensure review context exposes enough signals for family/category/object mismatch, not only confidence and compact candidate pool.
2. Normalize second-review outputs around these codes where applicable:
   - wrong_family
   - wrong_category
   - wrong_param
   - synonym_gap
   - missing_candidate
   - non_quota_item
3. Keep human-confirm gate intact; do not auto-promote uncertain corrections.

Acceptance:
- Draft review payload is easier to batch by cause cluster.
- Human reviewer can distinguish “wrong but fixable by retrieval” from “gray but defensible”.

---

## Recommended execution order

1. Cluster 1 套管
2. Cluster 3 软接头/抗震支架
3. Cluster 4 阀门/过滤器/止回阀
4. Cluster 2 刷油/保温/标识
5. Cluster 5 设备泵组
6. Cluster 6 卫浴/附配件
7. Cluster 7 OpenClaw reason/reporting cleanup

Why this order:
- 1/3/4 are highest-confidence wrong-family errors and should yield the fastest red-light reduction.
- 2 prevents unsafe installation fallback on non-installation worktypes.
- 5/6 are more gray and should be fixed after family boundaries are tighter.
- 7 makes the next review cycle more usable.

---

## Validation plan

Targeted tests:
- Add/extend regressions for each representative row cluster.
- Prefer fixture-style tests over one-off quota-id assertions when exact candidate availability may evolve.
- Assert at minimum:
  - top1 family is valid, or
  - result is explicit candidate_pool_insufficient / wrong_family path
  - not unrelated-family unsafe top1

Workflow validation:
1. Rerun the same bill efecd9c5.
2. Compare against current baseline:
   - agree 36 / abstain 38 / candidate_pool_insufficient 71
3. Success criteria:
   - obvious wrong-family top1s in target clusters materially decrease
   - abstain does not increase just by relabeling
   - candidate_pool_insufficient may rise temporarily if it replaces fake-safe wrong top1s
   - final outcome is more truthful and easier for second review to act on

Non-goals:
- Do not optimize for lower red count by forcing weak green/yellow matches.
- Do not hardcode single quota ids only for this bill.
- Do not loosen retrieval globally without family protection.
