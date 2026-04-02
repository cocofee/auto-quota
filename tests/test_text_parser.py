"""
text_parser 参数提取回归测试。

覆盖范围：
1. DN多格式（DN25、De110、Φ150、SC20、PC25、JDG20、直径150）
2. 电缆截面（4×95、BV-2.5、BYJ-4、WDZN-BYJ-4）
3. 回路数（24回路、24路、不匹配路灯）
4. 材质提取（镀锌钢管、PPR、不锈钢）
5. 连接方式（沟槽、热熔、卡压）
"""
import pytest

from src.text_parser import TextParser

# 全局共享的 parser 实例
parser = TextParser()


def test_parse_canonical_distribution_box_with_spd_note_stays_distribution_box():
    result = parser.parse_canonical(
        "落地式配电箱 名称：水泵控制箱；型号规格：箱体外壳采用2mm厚304不锈钢材质，"
        "浪涌保护器应符合当地气象部门要求；安装方式：落地安装。"
    )

    assert result["entity"] == "配电箱"
    assert "配电箱" in result["canonical_name"]
    assert "浪涌保护器" not in result["canonical_name"]
    assert result["family"] == "electrical_box"


def test_parse_canonical_signal_cabinet_is_not_distribution_box():
    result = parser.parse_canonical("安装悬挂式信号机箱")

    assert result["entity"] == "机箱"
    assert result["canonical_name"] == "机箱"
    assert result["family"] == "device_cabinet"
    assert result["system"] == "电气"


class TestExtractDN:
    """DN（公称直径）提取测试"""

    # --- 现有已支持的格式 ---

    def test_dn_standard(self):
        """标准DN格式：DN150"""
        result = parser.parse("给水管道 DN150 镀锌钢管")
        assert result["dn"] == 150

    def test_dn_lowercase(self):
        """小写dn格式：dn100"""
        result = parser.parse("排水管 dn100")
        assert result["dn"] == 100

    def test_dn_with_dash(self):
        """DN带横线：DN-25"""
        result = parser.parse("管道 DN-25 热熔连接")
        assert result["dn"] == 25

    def test_de_format(self):
        """De外径格式：De110 → 直接返回110（塑料管定额按外径分档）"""
        result = parser.parse("PPR管 De110 热熔连接")
        assert result["dn"] == 110  # De110 直接用外径值

    def test_phi_format(self):
        """Φ格式：Φ150"""
        result = parser.parse("钢管 Φ150 焊接")
        assert result["dn"] == 150

    def test_nominal_diameter(self):
        """公称直径格式"""
        result = parser.parse("管道安装 公称直径 100")
        assert result["dn"] == 100

    def test_pipe_diameter(self):
        """管径格式"""
        result = parser.parse("阀门安装 管径50")
        assert result["dn"] == 50

    # --- L2-b 新增：管材代号格式（存为 conduit_dn，不影响参数验证） ---

    def test_sc_pipe(self):
        """SC（焊接钢管）代号：SC20 → conduit_dn=20"""
        result = parser.parse("配管 SC20 明配")
        assert result["conduit_dn"] == 20

    def test_pc_pipe(self):
        """PC（PVC硬塑管）代号：PC25 → conduit_dn=25"""
        result = parser.parse("配管 PC25 暗敷")
        assert result["conduit_dn"] == 25

    def test_jdg_pipe(self):
        """JDG（紧定式薄壁钢管）代号：JDG20 → conduit_dn=20"""
        result = parser.parse("配管 JDG20 明配")
        assert result["conduit_dn"] == 20

    def test_kbg_pipe(self):
        """KBG（扣压式薄壁钢管）代号：KBG25 → conduit_dn=25"""
        result = parser.parse("配管 KBG25 暗敷")
        assert result["conduit_dn"] == 25

    def test_mt_pipe(self):
        """MT（薄壁金属导管）代号：MT16 → conduit_dn=16"""
        result = parser.parse("配管 MT16 明配")
        assert result["conduit_dn"] == 16

    def test_fpc_pipe(self):
        """FPC（可弯曲金属导管）代号：FPC20 → conduit_dn=20"""
        result = parser.parse("配管 FPC20")
        assert result["conduit_dn"] == 20

    def test_rc_pipe(self):
        """RC（水煤气管）代号：RC32 → conduit_dn=32"""
        result = parser.parse("配管 RC32 明配")
        assert result["conduit_dn"] == 32

    def test_pipe_code_with_space(self):
        """管材代号带空格：SC 20"""
        result = parser.parse("配管 SC 20 明配")
        assert result["conduit_dn"] == 20

    def test_pipe_code_not_override_dn(self):
        """标准DN已存在时，管材代号不覆盖"""
        result = parser.parse("管道 DN25 SC20 安装")
        assert result["dn"] == 25
        assert "conduit_dn" not in result

    def test_diameter_keyword(self):
        """'直径'关键词：直径150 → DN150"""
        result = parser.parse("管道 直径150 安装")
        assert result["dn"] == 150


class TestExtractCableSection:
    """电缆截面积提取测试"""

    # --- 现有已支持的格式 ---

    def test_standard_cable(self):
        """标准电缆格式：4×95"""
        result = parser.parse("YJV 4×95 电力电缆")
        assert result["cable_section"] == 95

    def test_multi_section_cable(self):
        """多截面电缆：4×185+1×95 → 取最大185"""
        result = parser.parse("YJV-4×185+1×95 电缆敷设")
        assert result["cable_section"] == 185

    def test_cable_section_keyword(self):
        """截面关键词格式"""
        result = parser.parse("电缆 截面(mm²) 120")
        assert result["cable_section"] == 120

    def test_not_match_watt(self):
        """不误匹配功率：15W 不是截面"""
        result = parser.parse("灯具安装 15W LED")
        assert result.get("cable_section") is None

    # --- L2-b 新增：导线型号格式 ---

    def test_bv_wire(self):
        """BV导线：BV-2.5 → 截面2.5"""
        result = parser.parse("配线 BV-2.5 穿管")
        assert result["cable_section"] == 2.5

    def test_byj_wire(self):
        """BYJ导线：BYJ-4 → 截面4"""
        result = parser.parse("配线 BYJ-4 穿管")
        assert result["cable_section"] == 4

    def test_bvr_wire(self):
        """BVR导线：BVR-6 → 截面6"""
        result = parser.parse("配线 BVR-6")
        assert result["cable_section"] == 6

    def test_rvs_wire(self):
        """RVS导线：RVS-1.5 → 截面1.5"""
        result = parser.parse("消防广播线 RVS-1.5")
        assert result["cable_section"] == 1.5

    def test_wdzn_prefix(self):
        """阻燃前缀：WDZN-BYJ-4 → 截面4"""
        result = parser.parse("配线 WDZN-BYJ-4 穿管")
        assert result["cable_section"] == 4

    def test_nh_prefix(self):
        """耐火前缀：NH-BV-2.5 → 截面2.5"""
        result = parser.parse("配线 NH-BV-2.5 穿管")
        assert result["cable_section"] == 2.5

    def test_wdz_byj(self):
        """低烟无卤前缀：WDZ-BYJ-6 → 截面6"""
        result = parser.parse("配线 WDZ-BYJ-6")
        assert result["cable_section"] == 6

    def test_blv_wire(self):
        """BLV铝导线：BLV-10 → 截面10"""
        result = parser.parse("配线 BLV-10 穿管")
        assert result["cable_section"] == 10


class TestExtractCircuits:
    """回路数提取测试"""

    def test_standard_circuit(self):
        """标准格式：24回路"""
        result = parser.parse("照明配电箱 24回路")
        assert result["circuits"] == 24

    def test_circuit_with_prefix(self):
        """带前缀：回路数:12回路"""
        result = parser.parse("配电箱 回路数:12回路")
        assert result["circuits"] == 12

    # --- L2-b 新增："X路"格式 ---

    def test_lu_format(self):
        """'X路'格式：24路 → 24"""
        result = parser.parse("照明配电箱 24路")
        assert result["circuits"] == 24

    def test_lu_not_match_ludeng(self):
        """不误匹配'路灯'：2路灯 不是2回路"""
        result = parser.parse("路灯安装 2路灯")
        assert result.get("circuits") is None

    def test_lu_not_match_luyou(self):
        """不误匹配'路由'：3路由 不是3回路"""
        result = parser.parse("3路由交换机")
        assert result.get("circuits") is None


class TestExtractMaterial:
    """材质提取测试"""

    def test_galvanized_steel(self):
        """镀锌钢管"""
        result = parser.parse("给水管道 DN25 镀锌钢管")
        assert result["material"] == "镀锌钢管"

    def test_ppr(self):
        """PPR管"""
        result = parser.parse("PPR管 De25 热熔连接")
        assert "PPR" in result.get("material", "")

    def test_stainless_steel(self):
        """不锈钢管"""
        result = parser.parse("不锈钢管 DN50 卡压连接")
        assert result["material"] == "不锈钢管"

    def test_cast_iron(self):
        """铸铁管"""
        result = parser.parse("排水 铸铁管 DN100")
        assert result["material"] == "铸铁管"

    def test_infer_copper_from_bpyjv_model(self):
        """BPYJV等交联电缆型号应补出铜芯"""
        result = parser.parse("阻燃变频电力电缆 型号、规格:ZRC-BPYJV-0.6/1kV,3x240+3x40")
        assert result["material"] == "铜芯"

    def test_infer_aluminum_from_yjlv_model(self):
        """YJLV等电缆型号应补出铝芯"""
        result = parser.parse("电力电缆 型号:YJLV-0.6/1kV-4x120+1x70")
        assert result["material"] == "铝芯"

    def test_infer_mineral_cable_from_btly_model(self):
        """BTLY/BTTZ等矿物绝缘型号应单独区分"""
        result = parser.parse("矿物绝缘电力电缆 型号:BTLY-3x185+2x95")
        assert result["material"] == "矿物绝缘电缆"

    def test_extract_power_cable_type_and_model(self):
        """电力电缆应能提取家族、基础型号和复合敷设方式"""
        result = parser.parse("阻燃变频电力电缆 型号、规格:ZRC-BPYJV-0.6/1kV,3x240+3x40 敷设方式、部位:室内穿管或桥架")
        assert result["cable_type"] == "电力电缆"
        assert result["wire_type"] == "BPYJV"
        assert result["laying_method"] == "桥架/穿管"

    def test_extract_conduit_type_and_layout_from_config_form(self):
        result = parser.parse("配管 材质:JDG 规格:20 配置形式:暗敷")
        assert result["conduit_type"] == "JDG"
        assert result["laying_method"] == "暗配"

    def test_extract_control_cable_head_type_and_model(self):
        """控制电缆头应区分中间头/终端头并保留基础型号"""
        result = parser.parse("控制电缆头制作安装 NH-KVV-3x1.5 中间头")
        assert result["cable_type"] == "控制电缆"
        assert result["cable_head_type"] == "中间头"
        assert result["wire_type"] == "KVV"

    def test_extract_mineral_cable_type_and_model(self):
        """矿物绝缘电缆应保留线缆家族锚点"""
        result = parser.parse("矿物绝缘电力电缆 型号:BTLY-3x185+2x95")
        assert result["cable_type"] == "矿物绝缘电缆"
        assert result["wire_type"] == "BTLY"

    def test_extract_conduit_type_code(self):
        """配管应识别导管类型代号"""
        result = parser.parse("套接紧定式镀锌钢导管(JDG)敷设 JDG20")
        assert result["conduit_type"] == "JDG"
        assert result["conduit_dn"] == 20

    def test_extract_electrical_conduit_noun_does_not_trigger_cable_type(self):
        result = parser.parse("波纹电线管敷设 内径(mm) ≤32")
        assert result["dn"] == 32
        assert "cable_type" not in result

    def test_extract_conduit_type_ignores_pvc_accessory_noise_without_conduit_context(self):
        result = parser.parse_canonical(
            "玻璃隔断 钢化夹胶玻璃 PVC挡水条 包含门及加工、运输、安装、锁具、把手、五金配件等全部施工内容"
        )
        assert result["conduit_type"] == ""

    def test_extract_distribution_box_mount_mode(self):
        """配电箱应区分落地式和悬挂/嵌入式"""
        wall_box = parser.parse("成套配电箱安装 悬挂、嵌入式(半周长) 1.5m")
        floor_box = parser.parse("控制柜 GGD 落地式")
        ambiguous_box = parser.parse("配电箱 1AP1")
        assert wall_box["box_mount_mode"] == "悬挂/嵌入式"
        assert floor_box["box_mount_mode"] == "落地式"
        assert "box_mount_mode" not in ambiguous_box

    def test_extract_outlet_grounding(self):
        grounded = parser.parse("单相二三孔安全型暗装插座")
        plain = parser.parse("单相两孔插座")
        weak_current = parser.parse("信息插座 单口")
        assert grounded["outlet_grounding"] == "带接地"
        assert plain["outlet_grounding"] == "不带接地"
        assert "outlet_grounding" not in weak_current

    def test_extract_outlet_gangs(self):
        combo = parser.parse("插座 名称:单相两孔加三孔插座 安装方式:暗装")
        triple = parser.parse("插座暗装 单相 三联")
        aircon = parser.parse("单相三孔安全型挂机空调暗装插座")
        weak_current = parser.parse("信息插座 单口")
        assert combo["switch_gangs"] == 2
        assert triple["switch_gangs"] == 3
        assert "switch_gangs" not in aircon
        assert "switch_gangs" not in weak_current

    def test_extract_switch_gangs_from_short_switch_control_phrases(self):
        single = parser.parse("单联单控 安装方式:墙面暗装")
        double = parser.parse("双联单控 安装方式:墙面暗装")
        dual = parser.parse("双联双控 安装方式:墙面暗装")

        assert single["switch_gangs"] == 1
        assert double["switch_gangs"] == 2
        assert dual["switch_gangs"] == 2

    def test_extract_bridge_type_and_valve_connection_family(self):
        """桥架细类和阀门连接家族应可结构化提取"""
        bridge = parser.parse("钢制槽式桥架 规格:200x100")
        valve = parser.parse("螺纹法兰阀门 类型:软密封闸阀 规格:DN100")
        assert bridge["bridge_type"] == "槽式"
        assert bridge["bridge_wh_sum"] == 300.0
        assert "cable_section" not in bridge
        assert valve["valve_connection_family"] == "螺纹法兰阀"


class TestExtractConnection:
    """连接方式提取测试"""

    def test_groove(self):
        """沟槽连接"""
        result = parser.parse("给水管道 DN100 沟槽连接")
        assert result["connection"] == "沟槽连接"

    def test_hot_melt(self):
        """热熔连接"""
        result = parser.parse("PPR管 De25 热熔连接")
        assert result["connection"] == "热熔连接"

    def test_clamp(self):
        """卡压连接"""
        result = parser.parse("不锈钢管 DN50 卡压连接")
        assert result["connection"] == "卡压连接"

    def test_thread(self):
        """螺纹连接"""
        result = parser.parse("镀锌钢管 DN25 螺纹连接")
        assert result["connection"] == "螺纹连接"


class TestEdgeCases:
    """边界情况和排除逻辑测试"""

    def test_empty_input(self):
        """空输入返回空字典"""
        result = parser.parse("")
        assert result == {}

    def test_newline_replaced(self):
        """换行符被替换为空格，不截断参数"""
        result = parser.parse("给水管道\nDN150")
        assert result["dn"] == 150

    def test_spec_format_dn(self):
        """规格：65 格式提取DN（需管道上下文）"""
        result = parser.parse("镀锌钢管 沟槽连接 规格：65")
        assert result.get("dn") == 65

    def test_spec_format_without_pipe_context(self):
        """规格：65 无管道上下文时不提取"""
        result = parser.parse("配电箱 规格：65")
        assert result.get("dn") is None

    def test_non_cable_text_should_not_get_cable_cores(self):
        """非电缆文本不应出现默认芯数污染。"""
        result = parser.parse("风机盘管安装 吊顶式")
        assert result.get("cable_cores") is None

    def test_exclude_3d_dimension(self):
        """排除三维尺寸 600x800x300（不是截面）"""
        result = parser.parse("配电箱 600x800x300")
        assert result.get("cable_section") is None

    def test_exclude_spec_dimension(self):
        """排除规格尺寸 规格：800*320（不是截面）"""
        result = parser.parse("风口 规格：800*320")
        assert result.get("cable_section") is None

    def test_de_conversion(self):
        """De63 → 直接返回63（塑料管定额按外径分档，不转换为DN）"""
        result = parser.parse("PPR管 De63 热熔")
        assert result["dn"] == 63

    def test_rvv_wire(self):
        """RVV导线：RVV-1.5 → 截面1.5"""
        result = parser.parse("广播线 RVV-1.5")
        assert result["cable_section"] == 1.5

    def test_zr_prefix_wire(self):
        """ZR-前缀导线：ZR-BV-4 → 截面4（ZR被跳过，BV-4匹配）"""
        result = parser.parse("配线 ZR-BV-4")
        assert result["cable_section"] == 4


class TestGroundBarVsCableConflict:
    """接地扁钢与电缆截面的优先级冲突回归测试（Codex 5.4审核建议）"""

    def test_cable_with_ground_in_work_content(self):
        """电缆清单工作内容含"接地"，不应提取为扁钢"""
        # 真实场景：江西清单"电力电缆 YJV-3×185 ... 接地、测绝缘电阻"
        text = "电力电缆 YJV-3×185+2×95 接地、测绝缘电阻"
        result = parser.parse(text)
        assert result.get("cable_section") == 185.0
        assert "ground_bar_width" not in result

    def test_ground_bar_normal(self):
        """正常接地扁钢应正确提取"""
        result = parser.parse("接地扁钢 40×4")
        assert result.get("ground_bar_width") == 40.0

    def test_mixed_cable_and_ground_bar(self):
        """混合文本：电缆+真实扁钢规格，扁钢应能提取"""
        result = parser.parse("控制电缆敷设，接地扁钢40×4")
        assert result.get("ground_bar_width") == 40.0

    def test_ground_bar_reversed_format(self):
        """扁钢反写格式：4×40也应正确识别"""
        result = parser.parse("接地母线 4×40")
        assert result.get("ground_bar_width") == 40.0

    def test_ground_bar_keyword_after(self):
        """关键词在数字后面：60×6 扁铁"""
        result = parser.parse("60×6 扁铁 安装")
        assert result.get("ground_bar_width") == 60.0


class TestHalfPerimeter:
    """半周长提取回归测试（覆盖30省4种格式）"""

    # --- 广东格式：安装方式(半周长m以内) 数值 ---

    def test_guangdong_suspended(self):
        """广东悬挂式：悬挂式(半周长m以内) 1.0 → 1000mm"""
        result = parser.parse("控制箱安装 悬挂式(半周长m以内) 1.0")
        assert result["half_perimeter"] == 1000.0

    def test_guangdong_embedded(self):
        """广东嵌入式：嵌入式(半周长m以内) 1.5 → 1500mm"""
        result = parser.parse("成套配电箱安装 嵌入式(半周长m以内) 1.5")
        assert result["half_perimeter"] == 1500.0


class TestThirdBatchAnchors:
    def test_extract_third_batch_installation_anchors(self):
        support = parser.parse("管道支架 详见图集03S402-77~79 单件重量5kg")
        sanitary = parser.parse("感应式小便器 壁挂式 埋入式感应开关")
        lamp = parser.parse("LED线形灯 嵌入式安装")

        assert support["support_scope"] == "管道支架"
        assert support["support_action"] == "制作"
        assert sanitary["sanitary_subtype"] == "小便器"
        assert sanitary["sanitary_mount_mode"] == "挂墙式"
        assert sanitary["sanitary_flush_mode"] == "感应"
        assert lamp["lamp_type"] == "灯带"

    def test_guangdong_mm_format(self):
        """广东mm格式：接线箱半周长(mm以内) 700 → 700mm"""
        result = parser.parse("接线箱明装 接线箱半周长(mm以内) 700")
        assert result["half_perimeter"] == 700.0

    # --- 江西格式 ---

    def test_jiangxi_m_suffix(self):
        """江西m后缀：(半周长) 1.5m → 1500mm"""
        result = parser.parse("成套配电箱安装 悬挂、嵌入式(半周长) 1.5m")
        assert result["half_perimeter"] == 1500.0

    def test_jiangxi_mm_le(self):
        """江西mm带≤：半周长(mm) ≤1500 → 1500mm"""
        result = parser.parse("接线箱明装 半周长(mm) ≤1500")
        assert result["half_perimeter"] == 1500.0

    def test_jiangxi_m_le(self):
        """江西m带≤：半周长(m) ≤1.5 → 1500mm"""
        result = parser.parse("半周长(m) ≤1.5")
        assert result["half_perimeter"] == 1500.0

    # --- 浙江格式 ---

    def test_zhejiang_no_parens(self):
        """浙江无括号：悬挂式半周长1.0m → 1000mm"""
        result = parser.parse("成套配电箱安装 悬挂式半周长1.0m")
        assert result["half_perimeter"] == 1000.0

    def test_zhejiang_mm_no_space(self):
        """浙江mm无空格：半周长(mm)≤1500 → 1500mm"""
        result = parser.parse("接线箱明装半周长(mm)≤1500")
        assert result["half_perimeter"] == 1500.0

    # --- 上海格式（之前的bug触发格式） ---

    def test_shanghai_parens_m(self):
        """上海括号m格式：(半周长) 1.5m以内 → 1500mm"""
        result = parser.parse("小型配电箱安装(半周长) 1.5m以内")
        assert result["half_perimeter"] == 1500.0

    def test_shanghai_mm_with_install(self):
        """上海mm带安装方式：明装 半周长500mm以内 → 500mm（之前误返回500000）"""
        result = parser.parse("接线箱安装 明装 半周长500mm以内")
        assert result["half_perimeter"] == 500.0

    def test_shanghai_mm_with_install_dark(self):
        """上海暗装mm格式：暗装 半周长1500mm以内 → 1500mm（之前误返回1500000）"""
        result = parser.parse("接线箱安装 暗装 半周长1500mm以内")
        assert result["half_perimeter"] == 1500.0

    def test_shanghai_le_m(self):
        """上海≤m格式：半周长≤1.5m → 1500mm（之前返回N/A）"""
        result = parser.parse("悬挂嵌入式程序控制箱安装 半周长≤1.5m")
        assert result["half_perimeter"] == 1500.0

    # --- 清单规格计算 ---

    def test_spec_wh(self):
        """从清单规格W*H计算半周长"""
        result = parser.parse("配电箱 规格：420*470*120")
        assert result["half_perimeter"] == 890.0  # 420+470

    # --- 默认值 ---

    def test_default_value(self):
        """无规格的配电箱不再默认套半周长1500mm。"""
        result = parser.parse("照明配电箱安装")
        assert "half_perimeter" not in result


def test_parse_explicit_composite_pipe_material_and_connection():
    text = (
        "复合管 "
        "1.安装部位:室内 "
        "2.介质:给水 "
        "3.材质、规格:钢塑复合压力给水管 1.6MPA DN25 "
        "4.连接形式:电磁感应热熔"
    )
    result = parser.parse(text)

    assert result.get("dn") == 25
    assert result.get("material") == "钢塑复合管"
    assert result.get("connection") == "热熔连接"


def test_parse_pipe_hot_melt_does_not_trigger_sanitary_sensor_mode():
    text = (
        "复合管 "
        "材质、规格:钢塑复合压力给水管 DN25 "
        "连接形式:电磁感应热熔"
    )
    result = parser.parse(text)

    assert result.get("sanitary_flush_mode") is None


def test_parse_extracts_dn_from_jdg_spec_context():
    result = parser.parse("配管 材质：JDG 规格：20 配置形式:暗敷")

    assert result.get("dn") == 20


def test_parse_extracts_cable_section_from_negative_spec_format():
    result = parser.parse("配线 材质:铜芯 规格:-2.5 名称:电气配线 型号:ZB-BYJ 配线形式:管内配线")

    assert result.get("cable_section") == 2.5


def test_parse_extracts_item_length_from_foundation_elevation():
    result = parser.parse("整体塔器安装 5m3不锈钢废水罐 基础标高10m以内 设备重量2t")

    assert result.get("item_length") == 10.0


def test_parse_extracts_item_length_from_inner_perimeter():
    result = parser.parse("暗敷管道补贴人工 区域：卫生间（内周长在 12m 以下）")

    assert result.get("item_length") == 12.0


def test_parse_extracts_large_side_from_direct_threshold_format():
    result = parser.parse("碳钢通风管道 形状：矩形风管 规格：大边长mm≤320")

    assert result.get("large_side") == 320.0


def test_parse_extracts_half_perimeter_from_box_size_label():
    result = parser.parse("配电箱移位 箱体尺寸：450*380*90 包含拆除及恢复")

    assert result.get("half_perimeter") == 830.0


def test_parse_extracts_item_length_from_eave_height_with_floor_count():
    result = parser.parse("垂直运输 建筑物檐口高度、层数:58.5m、16层")

    assert result.get("item_length") == 58.5


def test_parse_extracts_item_length_from_average_well_depth():
    result = parser.parse("混凝土井 φ1800 预制装配式混凝土雨水检查井；平均井深2.898m；")

    assert result.get("item_length") == 2.898


def test_parse_extracts_item_length_from_spray_radius_range():
    result = parser.parse("喷泉设备 喷洒半径:0.15-0.3m")

    assert result.get("item_length") == 0.3


def test_parse_extracts_item_length_from_pole_height_slash_format():
    result = parser.parse("常规照明灯 灯杆材质、高度:杆高10/8m Q235钢")

    assert result.get("item_length") == 10.0


def test_parse_does_not_infer_item_length_from_plain_mm_height_without_cue():
    result = parser.parse("石材栏杆、扶手 栏杆的规格:栓船柱，G603荔枝面φ450*900mm高")

    assert result.get("item_length") is None
