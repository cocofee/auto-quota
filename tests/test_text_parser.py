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
