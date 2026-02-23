# -*- coding: utf-8 -*-
"""
灯具规则回归测试

验证 query_builder._normalize_bill_name() 对各种灯具的转换规则：
- 应急疏散指示灯 → 标志、诱导灯方向（不是荧光灯）
- LED面板灯 → 普通灯具方向
- LED紫外杀菌灯 → 保留"紫外""杀菌"语义（特殊灯具不走通用规则）
- 道路灯杆照明 → 排除规则命中，不改写
"""

import pytest
from src.query_builder import _normalize_bill_name


class TestLampExcludeRule:
    """排除规则：含"灯杆/灯塔/路灯基础"等的不走灯具规则"""

    def test_lamp_pole_excluded(self):
        """灯杆类 → 不走灯具规则，保持原名"""
        result = _normalize_bill_name("道路灯杆")
        # 灯杆不是灯具，不应被改写为"荧光灯具安装"等
        assert "灯具安装" not in result

    def test_lamp_tower_excluded(self):
        """灯塔类 → 不走灯具规则"""
        result = _normalize_bill_name("航标灯塔")
        assert "灯具安装" not in result

    def test_lamp_trough_excluded(self):
        """灯槽类 → 不走灯具规则"""
        result = _normalize_bill_name("灯槽照明")
        assert "荧光灯具安装" not in result


class TestEmergencyLamp:
    """应急疏散指示灯 → 标志/诱导灯方向（不是荧光灯）"""

    def test_evacuation_indicator(self):
        """应急疏散指示灯 → 标志、诱导灯安装"""
        result = _normalize_bill_name("应急疏散指示灯")
        assert "标志" in result or "诱导灯" in result

    def test_exit_indicator(self):
        """出口指示灯 → 标志、诱导灯安装"""
        result = _normalize_bill_name("出口指示灯")
        assert "标志" in result or "诱导灯" in result

    def test_floor_indicator(self):
        """楼层指示灯 → 标志、诱导灯安装"""
        result = _normalize_bill_name("楼层指示灯")
        assert "标志" in result or "诱导灯" in result

    def test_wall_evacuation_indicator(self):
        """壁装疏散指示灯 → 标志、诱导灯 壁式"""
        result = _normalize_bill_name("壁装疏散指示灯")
        assert "壁式" in result or "壁" in result


class TestSpecialLamp:
    """特殊灯具：紫外杀菌灯等 → 保留语义，不走通用灯具规则"""

    def test_uv_germicidal_lamp(self):
        """LED紫外杀菌灯 → 保留"紫外"语义"""
        result = _normalize_bill_name("LED紫外杀菌灯")
        # 特殊灯具排除规则命中，应保留关键语义
        assert "紫外" in result or "杀菌" in result

    def test_surgical_lamp(self):
        """无影灯 → 保留原名（特殊灯具）"""
        result = _normalize_bill_name("无影灯")
        assert "无影" in result


class TestCommonLamp:
    """通用灯具规则"""

    def test_led_panel_lamp(self):
        """LED面板灯 → 普通灯具安装方向"""
        result = _normalize_bill_name("LED面板灯")
        # LED面板灯是通用灯具，应走灯具安装方向
        assert "灯" in result

    def test_fluorescent_lamp(self):
        """荧光灯 → 荧光灯具安装"""
        result = _normalize_bill_name("荧光灯")
        assert "荧光灯具安装" in result

    def test_ceiling_lamp(self):
        """吸顶灯 → 普通灯具安装 吸顶灯"""
        result = _normalize_bill_name("吸顶灯")
        assert "吸顶" in result

    def test_dual_tube_lamp(self):
        """双管灯 → 荧光灯具安装 双管"""
        result = _normalize_bill_name("双管灯")
        assert "荧光灯具安装" in result
        assert "双管" in result

    def test_centralized_power_evacuation(self):
        """集中电源疏散照明灯 → 智能应急灯具"""
        result = _normalize_bill_name("集中电源疏散照明灯")
        assert "智能应急" in result


class TestEmergencyVsIndicator:
    """
    验收场景2：应急疏散指示灯不落入荧光灯

    这是回归测试的核心：确保"应急疏散指示灯"走标志灯路径，
    而不是被"应急"关键词捕获后走荧光灯路径。
    """

    def test_emergency_evacuation_indicator_not_fluorescent(self):
        """应急疏散指示灯 → 不应包含"荧光" """
        result = _normalize_bill_name("应急疏散指示灯")
        assert "荧光" not in result, (
            f"应急疏散指示灯被错误分类为荧光灯: {result}"
        )

    def test_emergency_lighting_is_fluorescent(self):
        """应急照明灯（非指示灯）→ 应该是荧光灯方向"""
        result = _normalize_bill_name("应急照明灯")
        assert "荧光灯具安装" in result

    def test_induction_lamp(self):
        """感应灯 → 普通灯具安装"""
        result = _normalize_bill_name("感应灯")
        assert "普通灯具安装" in result


class TestTubeLampMapping:
    """
    P2回归：直管灯/灯管 应走荧光灯安装定额，不应被映射到 LED灯带

    直管灯/灯管 = 管状灯具（不论LED还是荧光），安装工艺走荧光灯安装定额。
    LED灯带 = 柔性灯带/灯条，安装工艺完全不同。
    线槽灯 = 安装在线槽内的线性灯具，安装工艺接近LED灯带。
    """

    def test_tube_lamp_not_led_strip(self):
        """直管灯 → 荧光灯具安装（不是LED灯带）"""
        result = _normalize_bill_name("直管灯")
        assert "LED灯带" not in result, (
            f"直管灯被错误映射为LED灯带: {result}"
        )
        assert "荧光灯具安装" in result

    def test_lamp_tube_not_led_strip(self):
        """灯管 → 荧光灯具安装（不是LED灯带）"""
        result = _normalize_bill_name("灯管")
        assert "LED灯带" not in result, (
            f"灯管被错误映射为LED灯带: {result}"
        )
        assert "荧光灯具安装" in result

    def test_led_tube_lamp_not_led_strip(self):
        """LED直管灯 → 荧光灯具安装（LED直管灯替换荧光灯管，套荧光灯定额）"""
        result = _normalize_bill_name("LED直管灯")
        assert "LED灯带" not in result
        assert "荧光灯具安装" in result

    def test_trough_lamp_is_led_strip(self):
        """线槽灯 → LED灯带（线槽灯安装工艺接近LED灯带）"""
        result = _normalize_bill_name("线槽灯")
        assert "LED灯带" in result


class TestLongTailLamps:
    """
    P2回归：长尾灯具不被错误归类

    这些灯具没有专门的映射规则，走通用兜底路径。
    关键要求：不被错误映射到其他灯具类别。
    """

    def test_downlight_preserved(self):
        """筒灯 → 保留"筒灯"关键词（不被映射到荧光灯或LED灯带）"""
        result = _normalize_bill_name("筒灯")
        assert "筒灯" in result
        assert "荧光灯具安装" not in result
        assert "LED灯带" not in result

    def test_foot_light_preserved(self):
        """地脚灯 → 保留"地脚灯"关键词"""
        result = _normalize_bill_name("地脚灯")
        assert "地脚灯" in result
        assert "LED灯带" not in result

    def test_courtyard_lamp_preserved(self):
        """庭院灯 → 保留原名（通用兜底）"""
        result = _normalize_bill_name("庭院灯")
        assert "庭院灯" in result
        assert "荧光灯具安装" not in result

    def test_wall_washer_special(self):
        """洗墙灯 → 保留"洗墙"语义（特殊灯具）"""
        result = _normalize_bill_name("洗墙灯")
        assert "洗墙" in result

    def test_track_light_special(self):
        """轨道灯 → 保留"轨道"语义（特殊灯具）"""
        result = _normalize_bill_name("轨道灯")
        assert "轨道" in result

    def test_explosion_proof_lamp(self):
        """防爆灯 → 密闭灯安装 防爆灯"""
        result = _normalize_bill_name("防爆灯")
        assert "防爆" in result
        assert "密闭灯安装" in result

    def test_well_shaft_lamp(self):
        """井道灯 → 密闭灯安装"""
        result = _normalize_bill_name("井道灯")
        assert "密闭灯安装" in result
