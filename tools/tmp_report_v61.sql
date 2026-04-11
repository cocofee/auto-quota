\pset pager off
\pset format unaligned
\pset fieldsep ''
\o C:/Users/Administrator/Documents/trae_projects/auto-quota/reports/lobster_audit/637f43d6-f313-40a7-9199-b9e649454bef_审核报告_v6.1.md
WITH stats AS (
  SELECT count(*) total,
         sum(case when confidence>=90 then 1 else 0 end) green,
         sum(case when confidence>=70 and confidence<90 then 1 else 0 end) yellow,
         sum(case when confidence<70 then 1 else 0 end) red,
         sum(case when review_status='confirmed' then 1 else 0 end) confirmed,
         sum(case when review_status='corrected' then 1 else 0 end) corrected,
         sum(case when review_status not in ('confirmed','corrected') then 1 else 0 end) pending,
         sum(case when openclaw_review_status='reviewed' then 1 else 0 end) openclaw_reviewed,
         sum(case when openclaw_review_status='applied' then 1 else 0 end) openclaw_applied
  FROM match_results where task_id='637f43d6-f313-40a7-9199-b9e649454bef'
)
SELECT '# Jarvis 自动审核汇总报告（' || to_char(now(),'YYYY-MM-DD HH24:MI') || E'）\n\n'
 || '## 全局汇总' || E'\n'
 || '- 任务数: 1' || E'\n'
 || '- 总条数: ' || total || E'\n'
 || '- 置信度分布: 绿灯 ' || green || ' / 黄灯 ' || yellow || ' / 红灯 ' || red || E'\n'
 || '- 审核状态: 已确认 ' || confirmed || ' / 已纠正 ' || corrected || ' / 待审核 ' || pending || E'\n'
 || '- OpenClaw: 草稿 ' || openclaw_reviewed || ' / 已应用 ' || openclaw_applied || E'\n\n'
 || 'JARVIS审核报告 v6.1' || E'\n'
 || '文件: [安徽]59m2户型电气工程_wx_zip.xlsx' || E'\n'
 || '任务ID: 637f43d6-f313-40a7-9199-b9e649454bef' || E'\n'
 || '省份: 安徽省安装工程计价定额(2018)' || E'\n'
 || '定额: 安徽省安装工程计价定额(2018)' || E'\n'
 || '处理时间: ' || to_char(now(),'YYYY-MM-DD HH24:MI') || E'\n'
 || '--------------------------------------------------' || E'\n\n'
 || '📊 统计总览' || E'\n'
 || '总条数: ' || total || E'\n'
 || '置信度分布: 绿灯(>=90%) ' || green || ' 条 | 黄灯(70-89%) ' || yellow || ' 条 | 红灯(<70%) ' || red || E' 条\n'
 || '审核状态: 已确认 ' || confirmed || ' 条 | 已纠正 ' || corrected || ' 条 | 待审核 ' || pending || E' 条\n'
 || 'OpenClaw: 已出草稿 ' || openclaw_reviewed || ' 条 | 已正式应用 ' || openclaw_applied || E' 条\n\n'
 || '--------------------------------------------------' || E'\n\n'
FROM stats;

SELECT E'✅ 已形成最终裁决 (' || count(*) || E' 条)\n'
FROM match_results where task_id='637f43d6-f313-40a7-9199-b9e649454bef' and review_status in ('confirmed','corrected');

SELECT string_agg(
  (row_number() over(order by index)) || '. ' || bill_name || ' | 最终: ' || coalesce(corrected_quotas->0->>''quota_id'', quotas->0->>''quota_id'', '-') || ' ' || coalesce(corrected_quotas->0->>''name'', quotas->0->>''name'', '-')
  || ' | 当前: ' || coalesce(quotas->0->>''quota_id'', '-') || ' ' || coalesce(quotas->0->>''name'', '-')
  || ' | 来源: ' || case when review_status='corrected' then '人工终审改判' when review_status='confirmed' then '人工确认 Jarvis 原结果' else '待处理' end
  || ' | 去向: ExperienceDB'
  || ' | 定位: ' || coalesce('sheet=' || nullif(sheet_name,''), 'sheet=-') || ', ' || coalesce('section=' || nullif(section,''), 'section=-') || ', index=' || index,
  E'\n'
) FROM match_results where task_id='637f43d6-f313-40a7-9199-b9e649454bef' and review_status in ('confirmed','corrected');

SELECT E'\n⏳ 待人工复核 (' || count(*) || E' 条)\n| # | 清单 | 当前候选 | 当前状态 | 分类 | 可吸收 | 学习去向 | 缺失字段 | 定位 |\n|---|------|----------|----------|------|--------|----------|----------|------|'
FROM match_results where task_id='637f43d6-f313-40a7-9199-b9e649454bef' and review_status not in ('confirmed','corrected');

SELECT string_agg(
 '| ' || index || ' | ' || left(bill_name,20) || ' | ' || left(coalesce(quotas->0->>''quota_id'',''-'') || ' ' || coalesce(quotas->0->>''name'',''-''),24) || ' | ' ||
 case when openclaw_review_status='reviewed' then 'draft_only' else 'pending' end || ' | ' ||
 case
   when is_measure_item or bill_name like '%脚手架%' then '[非]'
   when bill_name like '%网络%' or bill_name like '%网线%' or bill_name like '%投影仪%' or bill_name like '%冰箱%' or bill_name like '%洗衣机%' or bill_name like '%烟机%' or bill_name like '%微波炉%' or bill_name like '%电水壶%' then '[跨]'
   when bill_name like '%插座%' or bill_name like '%开关盒%' then '[档]'
   else '[词]'
 end || ' | ' ||
 case when openclaw_review_status='reviewed' then 'partial' else 'not_absorbable' end || ' | ' ||
 case when openclaw_review_status='reviewed' then 'manual_only' else 'manual_only' end || ' | ' ||
 case when openclaw_review_status='reviewed' then 'confirmed_final_state' else 'final_quota,reason_codes,manual_note_or_review_note,confirmed_final_state' end || ' | ' ||
 coalesce('sheet=' || nullif(sheet_name,''), 'sheet=-') || ', ' || coalesce('section=' || nullif(section,''), 'section=-') || ', index=' || index || ' |',
 E'\n' order by index)
FROM match_results where task_id='637f43d6-f313-40a7-9199-b9e649454bef' and review_status not in ('confirmed','corrected');

\o
