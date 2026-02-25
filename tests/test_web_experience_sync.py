# -*- coding: utf-8 -*-
"""
Web端经验库回流测试

验证 correct_result() 和 confirm_results() 中经验库写入逻辑的正确性。
通过 mock store_one() 验证调用参数，不需要真正的数据库和Web服务器。
"""

import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# 辅助：构造模拟对象（代替真实的ORM模型和请求）
# ============================================================

def _make_match_result(
    bill_name="室内给水管道安装",
    bill_description="材质：镀锌钢管 DN25",
    specialty="C10",
    quotas="DEFAULT",
    corrected_quotas=None,
    review_status="pending",
):
    """构造模拟的 MatchResult 对象"""
    mr = MagicMock()
    mr.bill_name = bill_name
    mr.bill_description = bill_description
    mr.specialty = specialty
    # quotas="DEFAULT" 表示用默认值，quotas=None 表示真正的空
    if quotas == "DEFAULT":
        mr.quotas = [
            {"quota_id": "C10-1-5", "name": "管道安装 镀锌钢管 DN25", "unit": "m"},
        ]
    else:
        mr.quotas = quotas
    mr.corrected_quotas = corrected_quotas
    mr.review_status = review_status
    return mr


def _make_task(province="北京市建设工程施工消耗量标准(2024)"):
    """构造模拟的 Task 对象"""
    task = MagicMock()
    task.province = province
    return task


# ============================================================
# 核心回流逻辑（从 results.py 提取，便于独立测试）
# ============================================================

def _sync_correction_to_experience(match_result, corrected_quotas, review_note, task):
    """纠正数据回流经验库（候选层）

    从 correct_result() 中提取的回流逻辑。
    corrected_quotas: [{"quota_id": ..., "name": ...}, ...]
    """
    try:
        from tools.jarvis_store import store_one
        quota_ids = [q["quota_id"] for q in corrected_quotas]
        quota_names = [q["name"] for q in corrected_quotas]
        if quota_ids:
            store_one(
                name=match_result.bill_name,
                desc=match_result.bill_description or "",
                quota_ids=quota_ids,
                quota_names=quota_names,
                reason=f"Web端纠正: {review_note or ''}",
                specialty=match_result.specialty or "",
                province=task.province,
                confirmed=False,  # 纠正 → 候选层
            )
            return True
    except Exception:
        pass
    return False


def _sync_confirmation_to_experience(results_to_confirm, task):
    """确认数据回流经验库（权威层）

    从 confirm_results() 中提取的回流逻辑。
    results_to_confirm: 要确认的 MatchResult 列表
    """
    confirmed_records = []
    updated = 0
    skipped = 0

    for r in results_to_confirm:
        if r.review_status == "corrected":
            skipped += 1
            continue
        if r.review_status != "confirmed":
            r.review_status = "confirmed"
            updated += 1
            # 优先用纠正后的定额
            quotas_data = r.corrected_quotas or r.quotas
            if quotas_data:
                confirmed_records.append({
                    "name": r.bill_name,
                    "desc": r.bill_description or "",
                    "quota_ids": [q["quota_id"] for q in quotas_data if q.get("quota_id")],
                    "quota_names": [q.get("name", "") for q in quotas_data],
                    "specialty": r.specialty or "",
                })

    # 批量写入经验库
    if confirmed_records:
        try:
            from tools.jarvis_store import store_one
            for rec in confirmed_records:
                if rec["quota_ids"]:
                    store_one(
                        name=rec["name"],
                        desc=rec["desc"],
                        quota_ids=rec["quota_ids"],
                        quota_names=rec["quota_names"],
                        reason="Web端确认",
                        specialty=rec["specialty"],
                        province=task.province,
                        confirmed=True,  # 确认 → 权威层
                    )
        except Exception:
            pass

    return updated, skipped, confirmed_records


# ============================================================
# 测试1：纠正后写入候选层
# ============================================================

class TestCorrectResultExperienceSync:
    """纠正结果 → 经验库候选层"""

    def test_correct_calls_store_one_with_confirmed_false(self):
        """纠正操作应调用 store_one(confirmed=False)"""
        task = _make_task()
        mr = _make_match_result()
        corrected = [{"quota_id": "C10-2-45", "name": "管道安装 PPR管 DN25"}]

        with patch("tools.jarvis_store.store_one") as mock_store:
            _sync_correction_to_experience(mr, corrected, "应该用PPR管定额", task)

            mock_store.assert_called_once()
            kw = mock_store.call_args.kwargs
            assert kw["confirmed"] is False  # 纠正 → 候选层
            assert kw["province"] == "北京市建设工程施工消耗量标准(2024)"
            assert kw["quota_ids"] == ["C10-2-45"]
            assert kw["quota_names"] == ["管道安装 PPR管 DN25"]
            assert "Web端纠正" in kw["reason"]
            assert "应该用PPR管定额" in kw["reason"]

    def test_correct_passes_specialty(self):
        """纠正时正确传递专业册号"""
        task = _make_task()
        mr = _make_match_result(specialty="C4")
        corrected = [{"quota_id": "C4-8-3", "name": "配管安装"}]

        with patch("tools.jarvis_store.store_one") as mock_store:
            _sync_correction_to_experience(mr, corrected, "", task)

            kw = mock_store.call_args.kwargs
            assert kw["specialty"] == "C4"

    def test_correct_empty_quotas_skips_store(self):
        """空定额列表不调用 store_one"""
        task = _make_task()
        mr = _make_match_result()

        with patch("tools.jarvis_store.store_one") as mock_store:
            result = _sync_correction_to_experience(mr, [], "", task)

            mock_store.assert_not_called()
            assert result is False

    def test_correct_store_failure_does_not_raise(self):
        """store_one 异常不影响主流程"""
        task = _make_task()
        mr = _make_match_result()
        corrected = [{"quota_id": "C10-1-1", "name": "测试定额"}]

        with patch("tools.jarvis_store.store_one", side_effect=Exception("数据库锁")):
            # 不应抛异常
            result = _sync_correction_to_experience(mr, corrected, "", task)
            assert result is False

    def test_correct_handles_empty_description(self):
        """清单描述为空时不报错"""
        task = _make_task()
        mr = _make_match_result(bill_description=None)
        corrected = [{"quota_id": "C10-1-1", "name": "测试"}]

        with patch("tools.jarvis_store.store_one") as mock_store:
            _sync_correction_to_experience(mr, corrected, "", task)

            kw = mock_store.call_args.kwargs
            assert kw["desc"] == ""  # None 被转为空字符串


# ============================================================
# 测试2：确认后写入权威层
# ============================================================

class TestConfirmResultsExperienceSync:
    """批量确认 → 经验库权威层"""

    def test_confirm_calls_store_one_with_confirmed_true(self):
        """确认操作应调用 store_one(confirmed=True)"""
        task = _make_task()
        mr = _make_match_result(review_status="pending")

        with patch("tools.jarvis_store.store_one") as mock_store:
            updated, skipped, records = _sync_confirmation_to_experience([mr], task)

            mock_store.assert_called_once()
            kw = mock_store.call_args.kwargs
            assert kw["confirmed"] is True  # 确认 → 权威层
            assert kw["quota_ids"] == ["C10-1-5"]
            assert kw["province"] == "北京市建设工程施工消耗量标准(2024)"
            assert updated == 1
            assert skipped == 0

    def test_confirm_corrected_record_skipped(self):
        """已纠正的记录在批量确认时被跳过（不写经验库也不改状态）"""
        task = _make_task()
        mr = _make_match_result(review_status="corrected")

        with patch("tools.jarvis_store.store_one") as mock_store:
            updated, skipped, records = _sync_confirmation_to_experience([mr], task)

            mock_store.assert_not_called()
            assert updated == 0
            assert skipped == 1
            assert len(records) == 0

    def test_confirm_already_confirmed_skipped(self):
        """已确认的记录不会重复写入"""
        task = _make_task()
        mr = _make_match_result(review_status="confirmed")

        with patch("tools.jarvis_store.store_one") as mock_store:
            updated, skipped, records = _sync_confirmation_to_experience([mr], task)

            mock_store.assert_not_called()
            assert updated == 0  # 已确认，不重复更新

    def test_confirm_uses_corrected_quotas_when_available(self):
        """确认时优先使用 corrected_quotas"""
        task = _make_task()
        mr = _make_match_result(
            review_status="pending",
            corrected_quotas=[
                {"quota_id": "C10-3-10", "name": "纠正后的定额"},
            ],
        )

        with patch("tools.jarvis_store.store_one") as mock_store:
            _sync_confirmation_to_experience([mr], task)

            kw = mock_store.call_args.kwargs
            assert kw["quota_ids"] == ["C10-3-10"]
            assert kw["quota_names"] == ["纠正后的定额"]

    def test_confirm_batch_multiple_records(self):
        """批量确认多条记录"""
        task = _make_task()
        records = [
            _make_match_result(bill_name="给水管安装", review_status="pending"),
            _make_match_result(bill_name="排水管安装", review_status="pending"),
            _make_match_result(bill_name="已纠正的", review_status="corrected"),
        ]

        with patch("tools.jarvis_store.store_one") as mock_store:
            updated, skipped, confirmed = _sync_confirmation_to_experience(records, task)

            # 3条中2条pending被确认，1条corrected被跳过
            assert mock_store.call_count == 2
            assert updated == 2
            assert skipped == 1
            assert len(confirmed) == 2

    def test_confirm_store_failure_does_not_affect_counts(self):
        """经验库写入失败不影响确认计数"""
        task = _make_task()
        mr = _make_match_result(review_status="pending")

        with patch("tools.jarvis_store.store_one", side_effect=Exception("连接超时")):
            updated, skipped, records = _sync_confirmation_to_experience([mr], task)

            # 状态更新成功（在调 store_one 之前已完成）
            assert updated == 1
            assert mr.review_status == "confirmed"

    def test_confirm_empty_quotas_skips_store(self):
        """没有定额数据的记录不触发经验库写入"""
        task = _make_task()
        mr = _make_match_result(review_status="pending", quotas=None, corrected_quotas=None)

        with patch("tools.jarvis_store.store_one") as mock_store:
            updated, skipped, records = _sync_confirmation_to_experience([mr], task)

            # 状态更新了，但不写经验库（没有定额数据）
            assert updated == 1
            mock_store.assert_not_called()
            assert len(records) == 0
