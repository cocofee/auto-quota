 
import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / 'tools' / 'jarvis_review_task.py'
spec = importlib.util.spec_from_file_location('jarvis_review_task', MODULE_PATH)
review = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = review
spec.loader.exec_module(review)


def _task():
    return {
        'id': 'task-1',
        'original_filename': '[安徽]样本.xlsx',
        'province': '安徽',
        'pricing_name': '安徽安装定额(2018)',
    }


def test_render_task_shows_final_decision_and_learning_route():
    item = {
        'index': 3,
        'bill_name': '单口网络插座',
        'bill_description': '含模块 暗装',
        'bill_unit': '个',
        'confidence': 26,
        'sheet_name': '电气',
        'section': '弱电系统',
        'quotas': [{'quota_id': 'A5-2-61', 'name': '电话插座 单口', 'unit': '个'}],
        'corrected_quotas': [{'quota_id': 'A5-2-88', 'name': '网络插座 单口', 'unit': '个'}],
        'review_status': 'corrected',
        'review_note': '人工终审改判',
        'openclaw_review_status': 'applied',
        'openclaw_reason_codes': ['candidate_pool_better'],
        'human_feedback_payload': {
            'protocol_version': 'lobster_review_feedback.v1',
            'source': 'lobster_audit',
            'adopt_openclaw': False,
            'final_quota': {'quota_id': 'A5-2-88', 'name': '网络插座 单口', 'unit': '个'},
            'manual_reason_codes': ['manual_override', 'wrong_family'],
            'manual_note': '人工确认原候选方向错误，改为网络插座。',
        },
    }
    report, _, _, _ = review.render_task(_task(), {'items': [item]})
    assert '已形成最终裁决 (1 条)' in report
    assert '来源: 人工终审改判' in report
    assert '去向: ExperienceDB/audit_errors/promotion_queue' in report
    assert '定位: sheet=电气, section=弱电系统, index=3' in report


def test_generate_report_separates_confidence_and_review_status():
    pending = {
        'index': 1,
        'bill_name': '五孔插座',
        'bill_description': '暗装',
        'bill_unit': '个',
        'confidence': 58,
        'sheet_name': '电气',
        'section': '强电系统',
        'quotas': [{'quota_id': 'A4-14-401', 'name': '地插安装', 'unit': '个'}],
        'review_status': 'pending',
        'openclaw_review_status': 'pending',
    }
    confirmed_low = {
        'index': 2,
        'bill_name': '单联单控开关',
        'bill_description': '暗装',
        'bill_unit': '个',
        'confidence': 36,
        'sheet_name': '电气',
        'section': '强电系统',
        'quotas': [{'quota_id': 'A4-14-379', 'name': '普通开关安装 单控', 'unit': '个'}],
        'review_status': 'confirmed',
        'review_note': '人工确认 Jarvis 原结果',
        'openclaw_review_status': 'pending',
    }
    report = review.generate_report([(_task(), {'items': [pending, confirmed_low]})])
    assert '置信度分布: 绿灯 0 / 黄灯 0 / 红灯 2' in report
    assert '审核状态: 已确认 1 / 已纠正 0 / 待审核 1' in report
    assert '低置信度但已形成最终裁决' in report

def test_pending_draft_item_marks_missing_fields_and_position():
    draft = {
        'index': 7,
        'bill_name': '六类网线',
        'bill_description': '穿管敷设',
        'bill_unit': 'm',
        'confidence': 52,
        'sheet_name': '电气',
        'section': '弱电系统',
        'quotas': [{'quota_id': 'A5-2-95', 'name': '双绞线缆测试 六类及以上', 'unit': '点'}],
        'review_status': 'pending',
        'openclaw_review_status': 'reviewed',
        'openclaw_suggested_quotas': [{'quota_id': 'A5-2-90', 'name': '六类网线敷设', 'unit': 'm'}],
        'openclaw_review_note': 'OpenClaw 建议改成敷设项，但还没终审。',
        'openclaw_reason_codes': ['candidate_pool_better'],
    }
    report, errors, gaps, _ = review.render_task(_task(), {'items': [draft]})
    assert '| 7 | 六类网线 | A5-2-95 双绞线缆测试 六类及以上' in report
    assert 'draft_only' in report
    assert 'confirmed_final_state' in report
    assert 'sheet=电气, section=弱电系统, index=7' in report
    assert errors['[跨]'] == 1
    assert gaps['六类网线'] == 1
