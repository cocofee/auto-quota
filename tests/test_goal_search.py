import json
import sqlite3

import config
from src.goal_search.national_index import extract_signal, infer_family
from src.goal_search.searcher import GoalSearcher, _quota_book, clear_goal_search_cache


def _make_quota_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        create table quotas (
            id integer primary key,
            quota_id text,
            name text,
            unit text,
            chapter text,
            specialty text,
            book text,
            circuits integer,
            cable_section real,
            search_text text
        )
        """
    )
    conn.executemany(
        """
        insert into quotas(
            quota_id, name, unit, chapter, specialty, book, circuits, cable_section, search_text
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("L-1", "配电箱墙上明装 规格(回路以内) 8", "台", "", "C4", "C4", 8, None, "配电箱墙上明装 规格(回路以内) 8"),
            ("L-4", "配电箱墙上明装 规格(回路以内) 4", "台", "", "C4", "C4", 4, None, "配电箱墙上明装 规格(回路以内) 4"),
            ("L-2", "法兰阀门安装 公称直径 DN100", "个", "", "C10", "C10", None, None, "法兰阀门 DN100"),
            ("L-3", "空气过滤器 接口直径 DN100", "个", "", "C9", "C9", None, None, "空气过滤器 DN100"),
            ("B-150", "钢制槽式桥架(宽+高)(mm以下) 150", "m", "", "C4", "C4", None, None, "钢制槽式桥架(宽+高)(mm以下) 150"),
            ("B-400", "钢制槽式桥架(宽+高)(mm以下) 400", "m", "", "C4", "C4", None, None, "钢制槽式桥架(宽+高)(mm以下) 400"),
            ("B-T", "钢制梯式桥架(宽+高)(mm以下) 200", "m", "", "C4", "C4", None, None, "钢制梯式桥架(宽+高)(mm以下) 200"),
            ("S-M", "普通插座安装 单相 明插座电流(A) ≤15", "个", "", "C4", "C4", None, None, "普通插座安装 单相 明插座电流(A) ≤15"),
            ("S-D", "普通插座安装 单相 暗插座电流(A) ≤15", "个", "", "C4", "C4", None, None, "普通插座安装 单相 暗插座电流(A) ≤15"),
            ("S-5D", "5孔单相暗插座15A安装", "个", "", "C4", "C4", None, None, "5孔单相暗插座15A安装"),
            ("E-C", "控制箱(回路以内) 4", "台", "", "C4", "C4", 4, None, "控制箱(回路以内) 4"),
            ("E-SPD", "模块式电涌保护器安装 总配电箱电涌保护器", "台", "", "C4", "C4", None, None, "模块式电涌保护器安装 总配电箱电涌保护器"),
            ("E-SET", "成套配电箱安装 落地式", "台", "", "C4", "C4", None, None, "成套配电箱安装 落地式"),
            ("SL-P", "塑料套管制作安装 公称直径(mm以内) 80", "个", "", "C10", "C10", None, None, "塑料套管制作安装 公称直径(mm以内) 80"),
            ("SL-R", "刚性防水套管制作 公称直径(mm以内) 80", "个", "", "C10", "C10", None, None, "刚性防水套管制作 公称直径(mm以内) 80"),
            ("SL-H", "人防段防护密闭穿墙水管套管预埋 公称直径(mm以内) 80", "个", "", "G7", "G7", None, None, "人防段防护密闭穿墙水管套管预埋 公称直径(mm以内) 80"),
            ("D-P", "装配式镀锌薄钢板矩形风管安装 长边长(mm) ≤320", "m2", "", "C7", "C7", None, None, "装配式镀锌薄钢板矩形风管安装 长边长(mm) ≤320"),
            ("D-F", "镀锌薄钢板矩形风管(δ=1.2mm以内咬口)制作安装 长边长(mm) ≤320", "m2", "", "C7", "C7", None, None, "镀锌薄钢板矩形风管(δ=1.2mm以内咬口)制作安装 长边长(mm) ≤320"),
            ("D-SS", "不锈钢板圆形风管(电焊)制作安装 直径×壁厚(mm) ≤400×2", "m2", "", "C7", "C7", None, None, "不锈钢板圆形风管(电焊)制作安装 直径×壁厚(mm) ≤400×2"),
            ("D-GI", "镀锌薄钢板圆形风管(δ=1.2mm以内咬口)制作安装 直径(mm) ≤320", "m2", "", "C7", "C7", None, None, "镀锌薄钢板圆形风管(δ=1.2mm以内咬口)制作安装 直径(mm) ≤320"),
            ("D-COMP", "玻纤复合型圆形风管(粘接)直径(mm)≤630", "m2", "", "C7", "C7", None, None, "玻纤复合型圆形风管(粘接)直径(mm)≤630"),
            ("AO-900", "风口安装 百叶风口 周长(mm以内) 900", "个", "", "C7", "C7", None, None, "风口安装 百叶风口 周长(mm以内) 900"),
            ("AO-1280", "风口安装 百叶风口 周长(mm以内) 1280", "个", "", "C7", "C7", None, None, "风口安装 百叶风口 周长(mm以内) 1280"),
            ("INS-F", "风管纤维类制品(板)安装 其他纤维类制品(板)", "m3", "", "C12", "C12", None, None, "风管纤维类制品(板)安装 其他纤维类制品(板)"),
            ("INS-P", "风管其他绝热材料安装 聚氨酯泡沫板", "m3", "", "C12", "C12", None, None, "风管其他绝热材料安装 聚氨酯泡沫板"),
            ("P-32", "给排水管道 室内镀锌钢管(螺纹连接) 公称直径(mm以内) 32", "m", "", "C10", "C10", None, None, "给排水管道 室内镀锌钢管(螺纹连接) 公称直径(mm以内) 32"),
            ("P-UPVC", "给排水管道 室内塑料排水管(粘接) 公称外径(mm以内) 110", "m", "", "C10", "C10", None, None, "给排水管道 室内塑料排水管(粘接) 公称外径(mm以内) 110"),
            ("P-UPVC-OUT", "给排水管道 室外塑料排水管(粘接) 公称外径(mm以内) 110", "m", "", "C10", "C10", None, None, "给排水管道 室外塑料排水管(粘接) 公称外径(mm以内) 110"),
            ("P-PROTECT", "塑料管道保护管制作安装 外径(mm以内) 110", "m", "", "C10", "C10", None, None, "塑料管道保护管制作安装 外径(mm以内) 110"),
            ("P-HEAT", "采暖管道 室内塑料管(热熔连接) 公称外径(mm以内) 110", "m", "", "C10", "C10", None, None, "采暖管道 室内塑料管(热熔连接) 公称外径(mm以内) 110"),
            ("P-STEEL", "室内钢管(沟槽连接) 公称直径(mm以内) 100", "m", "", "C10", "C10", None, None, "室内钢管(沟槽连接) 公称直径(mm以内) 100"),
            ("P-PLASTIC", "室内塑料排水管(卡箍连接) 公称直径(mm以内) 100", "m", "", "C10", "C10", None, None, "室内塑料排水管(卡箍连接) 公称直径(mm以内) 100"),
            ("P-CS", "低中压碳钢管 电弧焊 公称直径100mm以内", "m", "", "C8", "C8", None, None, "低中压碳钢管 电弧焊 公称直径100mm以内"),
            ("P-BEND", "低中压碳钢、合金钢管机械煨弯 公称直径100mm以内", "个", "", "C8", "C8", None, None, "低中压碳钢、合金钢管机械煨弯 公称直径100mm以内"),
            ("P-BRANCH", "分歧管安装 分歧管分歧前端铜管外径(mm) ≤19.1", "个", "", "C10", "C10", None, None, "分歧管安装 分歧管分歧前端铜管外径(mm) ≤19.1"),
            ("W-BYJ", "管内穿照明线路 铜芯导线 截面(mm2以内) 2.5", "m", "", "C4", "C4", None, 2.5, "管内穿照明线路 铜芯导线 截面(mm2以内) 2.5"),
            ("W-SOFT", "多芯软导线敷设 二芯 截面(mm2以内) 2.5", "m", "", "C4", "C4", None, 2.5, "多芯软导线敷设 二芯 截面(mm2以内) 2.5"),
            ("W-TWIST", "双绞线缆测试", "m", "", "C4", "C4", None, None, "双绞线缆测试"),
            ("LG-C", "吸顶灯具安装 灯罩周长(mm) ≤800", "套", "", "C2", "2", None, None, "吸顶灯具安装 灯罩周长(mm) ≤800"),
            ("LG-F", "荧光灯具安装 吸顶式 单管", "套", "", "C2", "2", None, None, "荧光灯具安装 吸顶式 单管"),
            ("LG-B", "荧光艺术装饰灯具安装 天棚荧光灯带", "套", "", "C2", "2", None, None, "荧光艺术装饰灯具安装 天棚荧光灯带"),
            ("LG-N", "霓虹灯安装 灯管 直径(mm) ≤10", "套", "", "C2", "2", None, None, "霓虹灯安装 灯管 直径(mm) ≤10"),
            ("CB-AL1", "铝芯电力电缆敷设35mm2(单芯)", "m", "", "C4", "C4", None, 35, "铝芯电力电缆敷设35mm2(单芯)"),
            ("CB-AL4", "铝芯电力电缆敷设35mm2(四芯)", "m", "", "C4", "C4", None, 35, "铝芯电力电缆敷设35mm2(四芯)"),
            ("CB-CU1", "铜芯电力电缆敷设35mm2(单芯)", "m", "", "C4", "C4", None, 35, "铜芯电力电缆敷设35mm2(单芯)"),
            ("CB-CU4", "铜芯电力电缆敷设35mm2(四芯)", "m", "", "C4", "C4", None, 35, "铜芯电力电缆敷设35mm2(四芯)"),
            ("CB-CU5", "铜芯电力电缆敷设35mm2(五芯)", "m", "", "C4", "C4", None, 35, "铜芯电力电缆敷设35mm2(五芯)"),
            ("CB-C4", "电缆沿桥架、线槽敷设 电缆截面(mm2以内) 6", "m", "", "C4", "C4", None, 6, "电缆沿桥架、线槽敷设 电缆截面(mm2以内) 6"),
            ("CB-G5", "电力电缆沿夹层敷设 电缆截面(mm2以下) 6", "m", "", "G5", "G5", None, 6, "电力电缆沿夹层敷设 电缆截面(mm2以下) 6"),
            ("CH-1-6", "户内干包式非铠装电力电缆终端头制作安装 1kV以下(mm2以内) 1×6", "个", "", "C4", "C4", None, None, "户内干包式非铠装电力电缆终端头制作安装 1kV以下(mm2以内) 1×6"),
            ("CH-5-16", "户内干包式非铠装电力电缆终端头制作安装 1kV以下(mm2以内) 5×16", "个", "", "C4", "C4", None, None, "户内干包式非铠装电力电缆终端头制作安装 1kV以下(mm2以内) 5×16"),
            ("CH-MID", "电力电缆中间头制作安装 1kV以下室内干包式铜芯电力电缆 电缆截面(mm2) ≤16", "个", "", "C4", "C4", None, None, "电力电缆中间头制作安装 1kV以下室内干包式铜芯电力电缆 电缆截面(mm2) ≤16"),
            ("SUP-C4", "设备基础型钢 (槽钢)", "kg", "", "C4", "C4", None, None, "设备基础型钢 (槽钢)"),
            ("SUP-C10", "制作 一般管架", "kg", "", "C10", "C10", None, None, "制作 一般管架"),
            ("SUP-TPL", "现浇混凝土模板及支架 基础梁 复合模板", "m2", "", "A", "A", None, None, "现浇混凝土模板及支架 基础梁 复合模板"),
            ("LP-TRACK", "滑轨式射灯安装", "套", "", "C4", "C4", None, None, "滑轨式射灯安装"),
            ("LP-SIGN", "嵌入式标志、诱导装饰灯具安装", "套", "", "C4", "C4", None, None, "嵌入式标志、诱导装饰灯具安装"),
            ("LP-CEIL", "吸顶式点光源艺术装饰灯具安装", "套", "", "C4", "C4", None, None, "吸顶式点光源艺术装饰灯具安装"),
            ("LP-EMB", "嵌入式点光源艺术装饰灯具安装φ150", "套", "", "C4", "C4", None, None, "嵌入式点光源艺术装饰灯具安装φ150"),
            ("LP-ROD", "吊杆式荧光灯安装 无吊顶处 单管", "套", "", "C4", "C4", None, None, "吊杆式荧光灯安装 无吊顶处 单管"),
            ("LP-CHAIN", "普通灯具安装 吊链灯", "套", "", "C4", "C4", None, None, "普通灯具安装 吊链灯"),
            ("AO-SQ", "风口安装 方形散流器 周长(mm以内) 1280", "个", "", "C7", "C7", None, None, "风口安装 方形散流器 周长(mm以内) 1280"),
            ("AO-FRP", "玻璃钢风口安装 周长(mm以内) 1280", "个", "", "C7", "C7", None, None, "玻璃钢风口安装 周长(mm以内) 1280"),
            ("AO-WIN", "钢百叶窗安装 周长(mm以内) 1280", "个", "", "C7", "C7", None, None, "钢百叶窗安装 周长(mm以内) 1280"),
            ("D-FLEX-I", "柔性接口及伸缩节制作安装 帆布 有法兰", "m2", "", "C7", "C7", None, None, "柔性接口及伸缩节制作安装 帆布 有法兰"),
            ("D-FLEX-D", "柔性软风管安装 直径(mm以内) 320", "m2", "", "C7", "C7", None, None, "柔性软风管安装 直径(mm以内) 320"),
            ("D-INS", "聚乙烯高发泡(PEF)保温板安装 通风管道 厚度(mm以内/层) 20", "m3", "", "C12", "C12", None, None, "聚乙烯高发泡(PEF)保温板安装 通风管道 厚度(mm以内/层) 20"),
            ("D-CS", "碳钢矩形风管制作安装 长边长(mm) ≤320", "m2", "", "C7", "C7", None, None, "碳钢矩形风管制作安装 长边长(mm) ≤320"),
            ("F-8900", "轴流式通风机安装 风量(m3/h) 8900以下", "台", "", "C7", "C7", None, None, "轴流式通风机安装 风量(m3/h) 8900以下"),
            ("F-25000", "轴流式通风机安装 风量(m3/h) 25000以下", "台", "", "C7", "C7", None, None, "轴流式通风机安装 风量(m3/h) 25000以下"),
            ("F-63000", "轴流式通风机安装 风量(m3/h) 63000以下", "台", "", "C7", "C7", None, None, "轴流式通风机安装 风量(m3/h) 63000以下"),
            ("F-EXHAUST", "风扇安装 天花式排气扇", "台", "", "C4", "C4", None, None, "风扇安装 天花式排气扇"),
            ("F-LAMP", "LED方型扣板式天花灯安装 半周长(mm) ≤1000", "套", "", "C4", "C4", None, None, "LED方型扣板式天花灯安装 半周长(mm) ≤1000"),
            ("U-W", "壁挂式小便器安装 感应开关 埋入式", "组", "", "C2", "2", None, None, "壁挂式小便器安装 感应开关 埋入式"),
            ("U-F", "落地式小便器安装 埋入式感应开关", "组", "", "C2", "2", None, None, "落地式小便器安装 埋入式感应开关"),
            ("FD-50", "地漏安装 公称直径(mm以内) 50", "个", "", "C10", "C10", None, None, "地漏安装 公称直径(mm以内) 50"),
            ("FD-80", "地漏安装 公称直径(mm以内) 80", "个", "", "C10", "C10", None, None, "地漏安装 公称直径(mm以内) 80"),
            ("FD-100", "地漏安装 公称直径(mm以内) 100", "个", "", "C10", "C10", None, None, "地漏安装 公称直径(mm以内) 100"),
            ("FD-DRAIN", "排水栓安装 公称直径(mm以内) 100", "组", "", "C10", "C10", None, None, "排水栓安装 公称直径(mm以内) 100"),
            ("SN-IND", "喷射除锈 气柜 喷石英砂 水槽壁板", "m2", "", "C12", "C12", None, None, "喷射除锈 气柜 喷石英砂 水槽壁板"),
            ("SN-WALL", "挂墙式洗脸盆 冷热水", "组", "", "C10", "C10", None, None, "挂墙式洗脸盆 冷热水"),
            ("SN-BASIN", "立柱式洗脸盆 冷热水", "组", "", "C10", "C10", None, None, "立柱式洗脸盆 冷热水"),
        ],
    )
    conn.commit()
    conn.close()


def _make_experience_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        create table experiences (
            id integer primary key,
            bill_text text not null,
            bill_name text,
            quota_ids text not null,
            source text,
            confidence integer,
            province text,
            project_name text,
            layer text,
            disputed integer
        )
        """
    )
    conn.execute(
        """
        insert into experiences(
            bill_text, bill_name, quota_ids, source, confidence, province, project_name, layer, disputed
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "Y型过滤器 规格：DN100 介质：水 连接形式：法兰",
            "Y型过滤器",
            json.dumps(["L-2"], ensure_ascii=False),
            "test",
            95,
            "Local Test",
            "leak-source",
            "authority",
            0,
        ),
    )
    conn.commit()
    conn.close()


def test_goal_search_utf8_family_rules():
    assert infer_family("配电箱 1AP1") == "electrical_box"
    assert infer_family("Y型过滤器 规格：DN100 介质：水 连接形式：法兰") == "valve"
    assert infer_family("支吊架、基础型钢") == "support"
    assert infer_family("一般管架") == "support"
    assert infer_family("电缆桥架支撑架制作") == "support"
    assert infer_family("刚性防水套管 DN100") == "sleeve"
    assert infer_family("座便器") == "sanitary"
    assert infer_family("电缆头 1kV 4x35") == "cable_head"
    assert infer_family("管内穿铜芯线照明线路") == "wire"
    assert infer_family("管内穿照明线路铜芯2.5mm2") == "wire"
    assert infer_family("600mm*600mm平板灯") == "lamp"
    assert infer_family("五孔插座") == "socket"
    assert infer_family("二、三极单相组合插座") == "socket"
    assert infer_family("双联单控开关") == "switch"
    assert infer_family("单联单控") == "switch"
    assert infer_family("感应式水龙头") == "sanitary"
    assert infer_family("拖把池") == "sanitary"
    assert infer_family("蹲式大便器安装 感应开关 埋入式") == "sanitary"
    assert infer_family("落地式小便器安装 埋入式感应开关") == "sanitary"
    assert infer_family("MEE-70℃电动防火阀") == "duct"
    assert infer_family("单层百叶风口") == "duct"
    assert infer_family("复合型风管") == "duct"
    assert infer_family("轴流通风机") == "fan"
    assert infer_family("天花板管道式换气扇") == "fan"
    assert infer_family("超薄暗装天花风管式空调KN22") == "fan"
    assert infer_family("管道风机DB-250") == "fan"
    assert extract_signal("电力电缆 YJV-4x35").cable_section == 35
    assert extract_signal("电力电缆头 5×10mm2").cable_cores == 5
    assert extract_signal("电力电缆 3X25+2X16").cable_cores == 5
    assert extract_signal("电力电缆 2*35+1*16").cable_cores == 3
    assert extract_signal("电缆终端头 5×16 电缆终端头 5×16").cable_cores == 5
    assert extract_signal("配线 WDZDN-BYJ2.5").cable_section == 2.5
    assert extract_signal("JDG 20 暗敷").install_method == "暗配"
    assert extract_signal("焊接钢管 材质、规格:SC32").dn == 32
    assert extract_signal("成品不锈钢地漏DN110").dn == 110
    assert extract_signal("地漏 DN100以内").dn == 100
    assert extract_signal("塑料管 De50mm").dn == 50
    assert extract_signal("焊接钢管 SC32暗配").dn == 32
    assert infer_family("焊接钢管 材质、规格:SC32") == "pipe"
    assert infer_family("无缝钢管") == "pipe"
    assert infer_family("分歧器 规格型号:φ19.05/φ9.52") == "pipe"
    assert infer_family("分歧管安装 分歧管分歧前端铜管外径(mm) ≤19.1") == "pipe"
    assert extract_signal("C10 给排水支架").concrete_grade is None
    assert _quota_book("G4-6-70") == "G4"
    assert _quota_book("2-4-12-8") == "2"


def test_goal_search_can_disable_answer_priors_and_keep_local_results(tmp_path, monkeypatch):
    quota_db = tmp_path / "quota.db"
    _make_quota_db(quota_db)
    common_dir = tmp_path / "common"
    _make_experience_db(common_dir / "experience.db")
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "get_quota_db_path", lambda province=None: quota_db)
    monkeypatch.setattr(config, "resolve_province", lambda province: province)
    clear_goal_search_cache()

    searcher = GoalSearcher("Local Test")
    prior_query = {
        "bill_name": "Y型过滤器",
        "bill_text": "Y型过滤器 规格：DN100 介质：水 连接形式：法兰",
        "unit": "个",
    }
    hits_default = searcher.search(prior_query, top_k=3)
    assert all("exact_prior" not in " ".join(hit.reasons) for hit in hits_default)

    hits_with_prior = searcher.search(
        {
            **prior_query,
            "goal_allow_answer_priors": True,
        },
        top_k=3,
    )
    assert any("exact_prior" in " ".join(hit.reasons) for hit in hits_with_prior)

    hits_without_prior = searcher.search(
        {
            "bill_name": "Y型过滤器",
            "bill_text": "Y型过滤器 规格：DN100 介质：水 连接形式：法兰",
            "unit": "个",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )
    assert all("exact_prior" not in " ".join(hit.reasons) for hit in hits_without_prior)
    assert {hit.quota_id for hit in hits_without_prior} <= set(searcher.index.by_quota_id)


def test_goal_search_uses_structured_tier_columns(tmp_path, monkeypatch):
    quota_db = tmp_path / "quota.db"
    _make_quota_db(quota_db)
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "get_quota_db_path", lambda province=None: quota_db)
    monkeypatch.setattr(config, "resolve_province", lambda province: province)
    clear_goal_search_cache()

    searcher = GoalSearcher("Local Test")
    hits = searcher.search(
        {
            "bill_name": "配电箱1-AL",
            "bill_text": "配电箱1-AL 规格:7回路 安装方式:明装",
            "unit": "台",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )
    assert [hit.quota_id for hit in hits[:2]] == ["L-1", "L-4"]


def test_goal_search_ranks_size_tier_and_install_subtype(tmp_path, monkeypatch):
    quota_db = tmp_path / "quota.db"
    _make_quota_db(quota_db)
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "get_quota_db_path", lambda province=None: quota_db)
    monkeypatch.setattr(config, "resolve_province", lambda province: province)
    clear_goal_search_cache()

    searcher = GoalSearcher("Local Test")
    bridge_hits = searcher.search(
        {
            "bill_name": "桥架",
            "bill_text": "桥架 名称:普通强电桥架 规格:100*100",
            "unit": "m",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )
    assert bridge_hits[0].quota_id == "B-400"

    socket_hits = searcher.search(
        {
            "bill_name": "五孔插座",
            "bill_text": "五孔插座 安装方式:墙面暗装",
            "unit": "个",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )
    assert socket_hits[0].quota_id == "S-D"


def test_goal_search_ranks_common_same_family_defaults(tmp_path, monkeypatch):
    quota_db = tmp_path / "quota.db"
    _make_quota_db(quota_db)
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "get_quota_db_path", lambda province=None: quota_db)
    monkeypatch.setattr(config, "resolve_province", lambda province: province)
    clear_goal_search_cache()

    searcher = GoalSearcher("Local Test")
    assert searcher.search(
        {
            "bill_name": "控制箱AC",
            "bill_text": "控制箱AC 型号:设备自带控制箱 安装方式:底边距地1.2m明装 仅考虑安装费",
            "unit": "台",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "L-4"
    assert searcher.search(
        {
            "bill_name": "套管",
            "bill_text": "套管 名称:入户套管 材质:钢管 规格:DN80",
            "unit": "个",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "SL-R"
    assert searcher.search(
        {
            "bill_name": "碳钢通风管道",
            "bill_text": "碳钢通风管道 材质:薄钢板 形状:矩形风管 规格:大边长mm≤320 板材厚度:0.5",
            "unit": "m2",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "D-F"
    assert searcher.search(
        {
            "bill_name": "长条LED灯 管",
            "bill_text": "长条LED灯 管",
            "unit": "套",
            "specialty": "C2",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "LG-C"
    assert searcher.search(
        {
            "bill_name": "600mm*600mm平板灯",
            "bill_text": "600mm*600mm平板灯 名称:平板灯 规格:600*600mm",
            "unit": "套",
            "specialty": "C2",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "LG-C"
    assert searcher.search(
        {
            "bill_name": "感应式小便斗",
            "bill_text": "感应式小便斗 组装形式:立式 附件名称:感应器",
            "unit": "组",
            "specialty": "C2",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "U-F"


def test_goal_search_ranks_book_and_material_conflicts(tmp_path, monkeypatch):
    quota_db = tmp_path / "quota.db"
    _make_quota_db(quota_db)
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "get_quota_db_path", lambda province=None: quota_db)
    monkeypatch.setattr(config, "resolve_province", lambda province: province)
    clear_goal_search_cache()

    searcher = GoalSearcher("Local Test")
    assert searcher.search(
        {
            "bill_name": "配电箱 1AP1",
            "bill_text": "配电箱 1AP1",
            "unit": "台",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "E-SET"
    assert searcher.search(
        {
            "bill_name": "插座",
            "bill_text": "插座",
            "unit": "个",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "S-5D"
    assert searcher.search(
        {
            "bill_name": "不锈钢板通风管道",
            "bill_text": "不锈钢板通风管道 形状:圆形风管 规格:直径≤320 板材厚度:1.2",
            "unit": "m2",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "D-SS"
    assert searcher.search(
        {
            "bill_name": "单层百叶风口",
            "bill_text": "单层百叶风口 规格:250*250",
            "unit": "个",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "AO-1280"
    assert searcher.search(
        {
            "bill_name": "通风管道绝热",
            "bill_text": "通风管道绝热 绝热材料品种:隔热棉-50",
            "unit": "m3",
            "specialty": "C12",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "INS-F"
    assert searcher.search(
        {
            "bill_name": "焊接钢管",
            "bill_text": "焊接钢管 材质、规格:SC32 连接形式:螺纹连接",
            "unit": "m",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=2,
    )[0].quota_id == "P-32"


def test_goal_search_ranks_pipe_duct_wire_and_sanitary_buckets(tmp_path, monkeypatch):
    quota_db = tmp_path / "quota.db"
    _make_quota_db(quota_db)
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "get_quota_db_path", lambda province=None: quota_db)
    monkeypatch.setattr(config, "resolve_province", lambda province: province)
    clear_goal_search_cache()

    searcher = GoalSearcher("Local Test")
    assert searcher.search(
        {
            "bill_name": "塑料管",
            "bill_text": "塑料管 材质、规格:UPVC排水DN100 连接形式:胶粘连接",
            "unit": "m",
            "specialty": "C10",
            "target_primary_params": {"dn": 100},
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "P-UPVC"
    assert searcher.search(
        {
            "bill_name": "镀锌钢管",
            "bill_text": "镀锌钢管 规格:DN100 连接形式:沟槽连接",
            "unit": "m",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "P-STEEL"
    assert searcher.search(
        {
            "bill_name": "无缝钢管",
            "bill_text": "无缝钢管 规格:DN100",
            "unit": "m",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "P-STEEL"
    assert searcher.search(
        {
            "bill_name": "无缝钢管 -超高",
            "bill_text": "无缝钢管 -超高 规格:DN100 连接形式:焊接 其他:包含管件供应安装，综合单价中含穿非混凝土构件的套管制作及安装",
            "unit": "m",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "P-STEEL"
    assert searcher.search(
        {
            "bill_name": "低压碳钢管",
            "bill_text": "低压碳钢管",
            "unit": "m",
            "specialty": "C8",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "P-CS"
    assert searcher.search(
        {
            "bill_name": "分歧器",
            "bill_text": "分歧器 规格型号:φ19.05/φ9.52 甲供材:分歧器",
            "unit": "个",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "P-BRANCH"
    assert searcher.search(
        {
            "bill_name": "配线",
            "bill_text": "配线 BYJ2.5mm2 管内穿线",
            "unit": "m",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "W-BYJ"
    assert searcher.search(
        {
            "bill_name": "单层防雨百叶",
            "bill_text": "单层防雨百叶 400*200",
            "unit": "个",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "AO-1280"
    assert searcher.search(
        {
            "bill_name": "柔性软风管",
            "bill_text": "柔性软风管 材质:帆布 规格:大边长mm≤320",
            "unit": "m2",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "D-FLEX-I"
    assert searcher.search(
        {
            "bill_name": "碳钢通风管道",
            "bill_text": "碳钢通风管道 形状:矩形风管 规格:大边长mm≤320",
            "unit": "m2",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "D-CS"
    assert searcher.search(
        {
            "bill_name": "碳钢通风管道",
            "bill_text": "碳钢通风管道 形状:圆形 规格:直径≤320",
            "unit": "m2",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "D-GI"
    assert searcher.search(
        {
            "bill_name": "复合型风管",
            "bill_text": "复合型风管 规格:直径≤630",
            "unit": "m2",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "D-COMP"
    assert searcher.search(
        {
            "bill_name": "复合型风管",
            "bill_text": "复合型风管 名称:硅酸钙复合板防火包裹成品风管 规格:直径≤630 其它:不含吊托支架制安",
            "unit": "m2",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "D-COMP"
    assert searcher.search(
        {
            "bill_name": "轴流通风机",
            "bill_text": "轴流通风机 风量:38200",
            "unit": "台",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "F-63000"
    pipe_false_trigger_hits = searcher.search(
        {
            "bill_name": "管道风机DB-250",
            "bill_text": "管道风机DB-250",
            "unit": "台",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )
    assert searcher.index.by_quota_id[pipe_false_trigger_hits[0].quota_id].signal.family == "fan"
    assert all("domain:default indoor plumbing pipe" not in " ".join(hit.reasons) for hit in pipe_false_trigger_hits)
    duct_false_trigger_hits = searcher.search(
        {
            "bill_name": "超薄暗装天花风管式空调KN22",
            "bill_text": "超薄暗装天花风管式空调KN22",
            "unit": "台",
            "specialty": "C7",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )
    assert searcher.index.by_quota_id[duct_false_trigger_hits[0].quota_id].signal.family == "fan"
    assert all("domain:duct fabrication book" not in " ".join(hit.reasons) for hit in duct_false_trigger_hits)
    assert searcher.search(
        {
            "bill_name": "天花板管道式换气扇",
            "bill_text": "天花板管道式换气扇 风量:300 服务区域:卫生间排风",
            "unit": "台",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "F-EXHAUST"
    assert searcher.search(
        {
            "bill_name": "不锈钢地漏",
            "bill_text": "不锈钢地漏 规格:DN110",
            "unit": "个",
            "specialty": "C10",
            "target_primary_params": {"dn": 110},
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "FD-100"
    assert searcher.search(
        {
            "bill_name": "单孔水槽",
            "bill_text": "单孔水槽 插材质:不锈钢 组装形式:成品安装",
            "unit": "组",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "SN-BASIN"
    assert searcher.search(
        {
            "bill_name": "感应式小便斗",
            "bill_text": "感应式小便斗 附件名称:感应器",
            "unit": "组",
            "specialty": "C2",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "U-F"


def test_goal_search_ranks_priority_cable_support_and_lamp_buckets(tmp_path, monkeypatch):
    quota_db = tmp_path / "quota.db"
    _make_quota_db(quota_db)
    monkeypatch.setattr(config, "DB_DIR", tmp_path)
    monkeypatch.setattr(config, "get_quota_db_path", lambda province=None: quota_db)
    monkeypatch.setattr(config, "resolve_province", lambda province: province)
    clear_goal_search_cache()

    searcher = GoalSearcher("Local Test")
    assert searcher.search(
        {
            "bill_name": "电力电缆",
            "bill_text": "电力电缆 型号:WDZB-YJY-0.6/1KV-2*35mm2+1*16mm2 材质:铜芯",
            "unit": "m",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "CB-CU4"
    assert searcher.search(
        {
            "bill_name": "电力电缆",
            "bill_text": "电力电缆 型号:WDZBN-YJY23-0.6/1kV 3X25+2X16",
            "unit": "m",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "CB-CU5"
    assert searcher.search(
        {
            "bill_name": "电力电缆",
            "bill_text": "电力电缆 型号:NH-YJV-4*4 材质:铜芯 敷设方式:沿桥架或穿管敷设",
            "unit": "m",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "CB-C4"
    assert searcher.search(
        {
            "bill_name": "电力电缆头",
            "bill_text": "电力电缆头",
            "unit": "个",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "CH-5-16"
    assert searcher.search(
        {
            "bill_name": "支/吊架、基础型钢",
            "bill_text": "支/吊架、基础型钢 系统:给排水",
            "unit": "kg",
            "specialty": "C10",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "SUP-C10"
    assert searcher.search(
        {
            "bill_name": "装饰灯",
            "bill_text": "装饰灯 名称:led天花射灯 规格:轨道4.6m",
            "unit": "套",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "LP-TRACK"
    assert searcher.search(
        {
            "bill_name": "普通灯具",
            "bill_text": "普通灯具 名称:壁装LED 规格:1x18W 安装形式:管吊",
            "unit": "套",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "LP-ROD"
    assert searcher.search(
        {
            "bill_name": "普通灯具",
            "bill_text": "普通灯具 嵌入式筒灯CEA1601H 15W LED",
            "unit": "套",
            "specialty": "C4",
            "goal_no_answer_priors": True,
        },
        top_k=3,
    )[0].quota_id == "LP-CEIL"
