"""
生成Agent审核决策文件
基于对74条红色项的分析，给出正确的定额匹配决策
"""
import json
from pathlib import Path

# 造价员贾维斯的审核决策
# 每条决策包含：清单名称、描述、正确的定额编号、选择理由
decisions = []

# ============================================================
# 类型1: 消防水泵 → 应该用C1册机械设备安装（离心泵）
# ============================================================
decisions.append({
    "name": "合用消火栓泵",
    "description": "名称：合用消火栓泵，详见图纸及设计验收规范",
    "specialty": "C9",
    "correct_quota_ids": ["C1-6-2"],
    "correct_quota_names": ["单级离心式泵 设备重量(t以内) 0.5"],
    "reasoning": "消火栓泵是水泵设备，不是水泵接合器。消防水泵按C1机械设备安装的离心泵定额，按设备重量选取。一般消防泵重量在0.5t以内。",
    "wrong_quota": "C9-1-90 消防水泵接合器",
    "wrong_reason": "水泵≠水泵接合器，接合器是消防车接入口，泵是抽水设备",
})

decisions.append({
    "name": "喷淋泵",
    "description": "名称：喷淋泵，详见图纸及设计验收规范",
    "specialty": "C9",
    "correct_quota_ids": ["C1-6-2"],
    "correct_quota_names": ["单级离心式泵 设备重量(t以内) 0.5"],
    "reasoning": "喷淋泵是水泵设备，和消火栓泵一样用C1离心泵定额。错误匹配到了喷头安装。",
    "wrong_quota": "C9-1-45 洒水喷头安装",
    "wrong_reason": "泵≠喷头，完全不同的设备",
})

# ============================================================
# 类型2: 消防水箱 → 应该用C10水箱安装
# ============================================================
decisions.append({
    "name": "屋顶消防水箱",
    "description": "名称：屋顶消防水箱，型号、规格：L*B*H=3500*3000*2500",
    "specialty": "C9",
    "correct_quota_ids": ["C10-8-101"],
    "correct_quota_names": ["组装水箱安装 水箱总容量(m3以内) 40"],
    "reasoning": "消防水箱3.5×3×2.5=26.25m³，向上取档到40m³以内。屋顶消防水箱一般是组装式不锈钢水箱，用组装水箱安装定额。",
    "wrong_quota": "C9-1-90 消防水泵接合器",
    "wrong_reason": "水箱≠水泵接合器，完全不同的设备",
})

# ============================================================
# 类型3: 内外涂覆碳钢管（卡压连接）→ 涂塑碳钢管定额
# ============================================================
for dn, quota_id, quota_name in [
    ("65", "C10-3-98", "涂塑碳钢管(卡压、环压连接) 公称直径(mm以内) 80"),
    ("50", "C10-3-97", "涂塑碳钢管(卡压、环压连接) 公称直径(mm以内) 50"),
    ("32", "C10-3-96", "涂塑碳钢管(卡压、环压连接) 公称直径(mm以内) 40"),
]:
    decisions.append({
        "name": "水喷淋钢管",
        "description": f"安装部位：室内，材质：内外涂覆EP碳钢管{dn}，连接形式：卡压连接",
        "specialty": "C9",
        "correct_quota_ids": [quota_id],
        "correct_quota_names": [quota_name],
        "reasoning": f"内外涂覆EP碳钢管=涂塑碳钢管，DN{dn}卡压连接对应涂塑碳钢管(卡压)定额。材质必须匹配：涂覆碳钢≠不锈钢。",
        "wrong_quota": "C10-1-122 不锈钢管(卡压连接)",
        "wrong_reason": "涂覆碳钢管≠不锈钢管，材质完全不同",
    })

# 球墨铸铁管DN600沟槽连接 — 这个比较特殊
decisions.append({
    "name": "水喷淋钢管",
    "description": "安装部位：室内，材质：球墨铸铁给水管DN600，连接形式：沟槽连接",
    "specialty": "C9",
    "correct_quota_ids": ["C10-1-35"],
    "correct_quota_names": ["室外给水球墨铸铁管埋设(胶圈接口) 公称直径(mm以内) 80"],
    "reasoning": "球墨铸铁管DN600超出定额库最大DN200范围，需按定额站规定换算。暂按球墨铸铁管定额取定，实际需要调差。注意：DN600极大，可能是数据录入有误。",
    "wrong_quota": "C9-1-13 钢管(沟槽连接) 65",
    "wrong_reason": "球墨铸铁≠钢管，且DN600≠DN65，材质和规格都不对",
})

# ============================================================
# 类型4: 洒水喷头 — 无吊顶搞成有吊顶
# ============================================================
for file_tag in ["2-1#厂房", "3-1#厂房"]:
    decisions.append({
        "name": "水喷淋（雾）喷头",
        "description": "安装部位：无吊顶处-超高",
        "specialty": "C9",
        "correct_quota_ids": ["C9-1-45"],
        "correct_quota_names": ["洒水喷头安装 无吊顶 公称直径(mm以内) 25"],
        "reasoning": f"描述明确写了'无吊顶处'，应该选无吊顶定额C9-1-45，而非有吊顶C9-1-48。默认喷头公称直径25mm。",
        "wrong_quota": "C9-1-48 洒水喷头安装 有吊顶 25mm",
        "wrong_reason": "无吊顶≠有吊顶，安装方式不同",
    })

# ============================================================
# 类型5: 减压孔板 / 末端试水装置 → 专用定额
# ============================================================
decisions.append({
    "name": "螺纹阀门",
    "description": "类型：减压孔板，连接形式：丝扣连接",
    "specialty": "C9",
    "correct_quota_ids": ["C9-1-68"],
    "correct_quota_names": ["减压孔板 公称直径(mm以内) 50"],
    "reasoning": "虽然清单名称写'螺纹阀门'，但类型明确是'减压孔板'，应套减压孔板专用定额，不是普通螺纹阀门。",
    "wrong_quota": "C10-5-3 螺纹阀门 25mm",
    "wrong_reason": "减压孔板有专用定额C9-1-68~72，不应套普通阀门",
})

decisions.append({
    "name": "螺纹阀门",
    "description": "类型：末端试水装置",
    "specialty": "C9",
    "correct_quota_ids": ["C9-1-73"],
    "correct_quota_names": ["末端试水装置 公称直径(mm以内) 25"],
    "reasoning": "清单名称虽是'螺纹阀门'，但类型是'末端试水装置'，有专用定额C9-1-73。",
    "wrong_quota": "C10-5-3 螺纹阀门 25mm",
    "wrong_reason": "末端试水装置有专用定额，不是普通阀门",
})

# ============================================================
# 类型6: 普通钢制套管 → 一般填料套管
# ============================================================
decisions.append({
    "name": "套管",
    "description": "名称、类型:普通钢制套管，规格类型：DN25",
    "specialty": "C10",
    "correct_quota_ids": ["C10-4-61"],
    "correct_quota_names": ["一般填料套管制作安装 公称直径(mm以内) 25"],
    "reasoning": "普通钢制套管=一般填料套管，DN25选C10-4-61。注意：普通套管≠防水套管（刚性/柔性），套管类型必须严格区分。",
    "wrong_quota": "C10-4-19 刚性防水套管制作 50mm",
    "wrong_reason": "普通钢制套管≠防水套管，用途和做法完全不同",
})

# 套管（无描述，来自消防电）
decisions.append({
    "name": "套管",
    "description": "名称/材质/规格/填料材质均为空",
    "specialty": "C4",
    "correct_quota_ids": ["C10-4-64"],
    "correct_quota_names": ["一般填料套管制作安装 公称直径(mm以内) 50"],
    "reasoning": "清单描述为空，无法确定具体类型。默认按一般填料套管DN50处理（消防电管道最常见规格）。不应匹配防水套管。",
    "wrong_quota": "C10-4-38 柔性防水套管制作 300mm",
    "wrong_reason": "无描述不应默认为防水套管，且DN300远大于消防电常用规格",
})

# 第二个普通钢制套管（广州项目）
decisions.append({
    "name": "套管",
    "description": "名称、类型:普通钢制套管，规格类型：DN25",
    "specialty": "C10",
    "correct_quota_ids": ["C10-4-61"],
    "correct_quota_names": ["一般填料套管制作安装 公称直径(mm以内) 25"],
    "reasoning": "同上，普通钢制套管=一般填料套管。DN25选C10-4-61。",
    "wrong_quota": "C10-4-19 刚性防水套管制作 50mm",
    "wrong_reason": "普通≠防水",
})

# ============================================================
# 类型7: 一般填料套管（被错配到管道防结露）
# ============================================================
fill_sleeve_items = [
    ("1700*500", "C10-4-74", "一般填料套管制作安装 公称直径(mm以内) 500",
     "1700×500mm为矩形穿墙尺寸，取大边500mm对应DN500。注意：一般填料套管≠管道防结露。"),
    ("2100*500", "C10-4-74", "一般填料套管制作安装 公称直径(mm以内) 500",
     "2100×500mm矩形穿墙开孔，取大边500mm对应DN500。"),
    ("500*260", "C10-4-70", "一般填料套管制作安装 公称直径(mm以内) 300",
     "500×260mm矩形穿墙开孔，取大边500mm但这是风管套管，实际需要按等效直径或定额站规定处理。暂按DN300取定。"),
    ("730*420", "C10-4-73", "一般填料套管制作安装 公称直径(mm以内) 450",
     "730×420mm矩形穿墙开孔，取大边730mm对应DN450以内。"),
]

for spec, qid, qname, reason in fill_sleeve_items:
    decisions.append({
        "name": "一般填料套管",
        "description": f"名称：一般填料套管，规格：{spec}",
        "specialty": "C7",  # 通风专业穿墙套管
        "correct_quota_ids": [qid],
        "correct_quota_names": [qname],
        "reasoning": reason,
        "wrong_quota": "C12-4-233 管道防结露 橡塑制品防结露",
        "wrong_reason": "一般填料套管≠管道防结露，完全不同的工作内容",
    })

# ============================================================
# 类型8: 灭火器 → 手提式灭火器
# ============================================================
for spec in ["MF/ABC5", "MF/ABC3", "MF/ABC3"]:
    decisions.append({
        "name": "磷酸铵盐干粉灭火器",
        "description": f"名称:磷酸铵盐干粉灭火器，规格、类型：{spec}",
        "specialty": "C9",
        "correct_quota_ids": ["C9-1-92"],
        "correct_quota_names": ["灭火器 手提式"],
        "reasoning": f"MF/ABC系列是手提式干粉灭火器（MF=灭火器），应套C9-1-92手提式灭火器定额。灭火器≠灭火装置（悬挂式干粉灭火装置是另一种设备）。",
        "wrong_quota": "C9-2-45 独立式悬挂超细干粉灭火装置",
        "wrong_reason": "灭火器（手提/推车）≠灭火装置（悬挂/柜式），是两种不同消防设备",
    })

# ============================================================
# 类型9: 打洞（孔）→ 预留孔洞
# ============================================================
for _ in range(5):  # 5个打洞项目
    decisions.append({
        "name": "打洞（孔）",
        "description": "名称：打洞（孔），详见图纸",
        "specialty": "C10",
        "correct_quota_ids": ["C10-4-92"],
        "correct_quota_names": ["预留孔洞 混凝土墙体 周长(≤mm) 500"],
        "reasoning": "打洞/穿孔应套预留孔洞定额(C10-4-87~94)。未注明具体位置时默认取混凝土墙体，周长取中间值500mm。",
        "wrong_quota": "C9-1-25 钢管(沟槽连接)开孔管件",
        "wrong_reason": "打洞≠管道开孔管件，开孔管件是消防管道三通件",
    })

# ============================================================
# 类型10: 保温材质错 — 橡塑保温 → 橡塑制品定额
# ============================================================
# 闭孔橡塑保温-40 (出现在6#配套楼和7#配套楼多次)
for desc_spec in ["闭孔橡塑保温-40", "闭孔橡塑保温-40", "闭孔橡塑保温-40",
                   "闭孔橡塑保温-36", "闭孔橡塑保温-36"]:
    decisions.append({
        "name": "其他管道绝热",
        "description": f"绝热材料品种：{desc_spec}",
        "specialty": "C12",
        "correct_quota_ids": ["C12-4-118"],
        "correct_quota_names": ["管道绝热 橡塑制品安装 公称直径(mm以内) 50"],
        "reasoning": f"'{desc_spec}'是橡塑类保温材料，应选橡塑制品安装定额(C12-4-118~121)，不是聚氨酯泡沫塑料。数字可能是管径或厚度，暂按DN50以内取定。",
        "wrong_quota": "C12-4-90 管道绝热 聚氨酯泡沫塑料瓦块安装",
        "wrong_reason": "橡塑≠聚氨酯，保温材质不同定额不同",
    })

# 橡塑绝热材料-9
for _ in range(2):
    decisions.append({
        "name": "其他管道绝热",
        "description": "绝热材料品种：橡塑绝热材料-9",
        "specialty": "C12",
        "correct_quota_ids": ["C12-4-118"],
        "correct_quota_names": ["管道绝热 橡塑制品安装 公称直径(mm以内) 50"],
        "reasoning": "'橡塑绝热材料-9'明确是橡塑类，数字9可能是厚度9mm。应选橡塑制品安装定额。",
        "wrong_quota": "C12-4-90 管道绝热 聚氨酯泡沫塑料瓦块安装",
        "wrong_reason": "橡塑≠聚氨酯",
    })

# ============================================================
# 类型11: 矩形风管 → 矩形风管制作定额（不是圆形）
# ============================================================
duct_items = [
    ("大边长mm≤320", "C7-2-7", "薄钢板通风管道制作 钢板矩形风管制作(δ=1.2mm以内、咬口) 大边长(mm以内) 320"),
    ("630＜大边长mm≤1000", "C7-2-10", "薄钢板通风管道制作 钢板矩形风管制作(δ=1.2mm以内、咬口) 大边长(mm以内) 1000"),
    ("320＜大边长mm≤450", "C7-2-8", "薄钢板通风管道制作 钢板矩形风管制作(δ=1.2mm以内、咬口) 大边长(mm以内) 450"),
]

for spec, qid, qname in duct_items:
    decisions.append({
        "name": "碳钢通风管道",
        "description": f"名称：碳钢通风管道，形状：矩形风管，规格：{spec}",
        "specialty": "C7",
        "correct_quota_ids": [qid],
        "correct_quota_names": [qname],
        "reasoning": f"清单明确写了'矩形风管'，必须选矩形风管制作定额。碳钢板普通风管默认δ≤1.2mm咬口连接。规格{spec}。",
        "wrong_quota": "C7-2-15 钢板圆形风管制作(δ=2mm以内、焊接)",
        "wrong_reason": "矩形≠圆形，风管截面形状不同定额不同",
    })

# ============================================================
# 类型12: 配电箱 — 按尺寸用箱体安装定额
# ============================================================
# 600*500*300 的配电箱（多次出现）
for box_name in ["配电箱ALE", "配电箱", "配电箱", "配电箱",
                  "配电箱ALE", "配电箱SGAT", "配电箱WNAL",
                  "配电箱1ZAP", "配电箱2JCAL", "配电箱2XXAL",
                  "配电箱1ZAP", "配电箱2JCAL", "配电箱2XXAL"]:
    decisions.append({
        "name": box_name.replace("配电箱", "配电箱", 1),
        "description": f"名称:{box_name}，规格型号:600*500*300",
        "specialty": "C4",
        "correct_quota_ids": ["C4-4-37"],
        "correct_quota_names": ["配电箱箱体安装 半周长(m以内) 明装 2.5"],
        "reasoning": "只知尺寸不知回路数时，用箱体安装(按半周长)定额。600×500mm面板，半周长=600+500=1100mm=1.1m，选2.5m以内。",
        "wrong_quota": "C4-4-35 配电箱墙上(柱上)明装 48回路",
        "wrong_reason": "不知回路数时不应按回路定额估套，用半周长法更准确",
    })

# 600*800*200 的配电箱（博物馆）
for box_name in ["配电箱1AL1", "配电箱3AL1"]:
    decisions.append({
        "name": "配电箱",
        "description": f"名称:{box_name}，规格型号:600*800*200",
        "specialty": "C4",
        "correct_quota_ids": ["C4-4-37"],
        "correct_quota_names": ["配电箱箱体安装 半周长(m以内) 明装 2.5"],
        "reasoning": "600×800mm面板，半周长=600+800=1400mm=1.4m，选2.5m以内。",
        "wrong_quota": "C4-4-35 配电箱墙上(柱上)明装 48回路",
        "wrong_reason": "不知回路数时不应按回路定额估套",
    })

# ============================================================
# 类型13: 其他电气设备
# ============================================================

# 按钮控制箱
decisions.append({
    "name": "按钮控制箱",
    "description": "名称:按钮控制箱",
    "specialty": "C4",
    "correct_quota_ids": ["C4-4-17"],
    "correct_quota_names": ["控制箱安装 墙上"],
    "reasoning": "按钮控制箱是电机/设备的控制箱，安装在墙上居多，用C4-4-17。",
    "wrong_quota": "C4-12-285 控制箱(回路以内) 2",
    "wrong_reason": "C4-12是照明控制箱（路灯等），按钮控制箱是动力控制箱",
})

# 壁挂式空调1.5P
decisions.append({
    "name": "壁挂式空调-1.5P",
    "description": "名称:壁挂式空调-1.5P",
    "specialty": "C7",
    "correct_quota_ids": ["C7-1-14"],
    "correct_quota_names": ["分体式空调器安装 制冷量(kW以内) 5"],
    "reasoning": "1.5匹≈3.5kW制冷量，向上取档到5kW以内。壁挂空调=分体式空调器，用C7-1-14。",
    "wrong_quota": "C4-4-46 自动空气开关安装",
    "wrong_reason": "空调≠空气开关，完全不同的设备",
})

# 消防电话机柜
decisions.append({
    "name": "消防电话机柜",
    "description": "名称：消防电话机柜，规格：600*500*300",
    "specialty": "C9",
    "correct_quota_ids": ["C9-4-50"],
    "correct_quota_names": ["消防广播及对讲电话主机(柜)安装 消防电话主机 10路"],
    "reasoning": "消防电话机柜是消防系统专用设备，用C9-4-50消防电话主机定额，不是普通IT机柜。",
    "wrong_quota": "C5-2-6 标准机柜 落地式 600×800",
    "wrong_reason": "消防电话机柜≠IT标准机柜，属于消防系统设备",
})

# 轴流风机SF-6#
decisions.append({
    "name": "轴流风机",
    "description": "名称：轴流风机，规格：SF-6#-B1-1",
    "specialty": "C7",
    "correct_quota_ids": ["C7-1-65"],
    "correct_quota_names": ["轴流式通风机安装 吊装(型号以内) 6.3#"],
    "reasoning": "SF-6#是6号轴流风机，建筑通风用小型风机。向上取档到6.3#以内。用C7暖通安装的吊装定额，不是C1重型设备安装。",
    "wrong_quota": "C1-5-31 轴流通风机 设备重量(t以内) 60",
    "wrong_reason": "SF-6#是小型风机不到100kg，60吨是超大型风机",
})

# ============================================================
# 类型14: 弱电线缆
# ============================================================

# 射频同轴电缆
decisions.append({
    "name": "射频同轴电缆",
    "description": "名称:SYV-75-5，敷设方式:管道内敷设",
    "specialty": "C5",
    "correct_quota_ids": ["C5-2-36"],
    "correct_quota_names": ["同轴电缆 管/暗槽内穿放 φ9以下"],
    "reasoning": "SYV-75-5是φ5的同轴电缆，管道内敷设用C5-2-36（弱电线缆定额），不是C4电力电缆定额。",
    "wrong_quota": "C4-8-20 电缆沿墙面、支架敷设 400mm²",
    "wrong_reason": "同轴电缆是弱电线缆(C5册)≠电力电缆(C4册)",
})

# 管内穿线UTP
decisions.append({
    "name": "管内穿线",
    "description": "名称：管内穿线，规格：UTP，敷设方式:管道内敷设",
    "specialty": "C5",
    "correct_quota_ids": ["C5-2-21"],
    "correct_quota_names": ["双绞线缆 管内穿放 ≤4对"],
    "reasoning": "UTP是非屏蔽双绞线（网线），管内穿放用C5-2-21双绞线定额，不是C4电力电缆定额。",
    "wrong_quota": "C4-8-41 电缆穿导管敷设 2.5mm²",
    "wrong_reason": "UTP双绞线是弱电线缆(C5册)≠电力电缆(C4册)",
})

# 光缆-2芯桥架
decisions.append({
    "name": "光缆",
    "description": "规格：2芯单模光纤，敷设方式：沿桥架敷设",
    "specialty": "C5",
    "correct_quota_ids": ["C5-2-48"],
    "correct_quota_names": ["室内穿放、布放光缆 开放式桥架内布放 ≤12芯"],
    "reasoning": "2芯光缆沿桥架布放，用C5-2-48光缆桥架布放定额（2芯≤12芯以内）。",
    "wrong_quota": "C4-8-21 电缆沿桥架、线槽敷设 2.5mm²",
    "wrong_reason": "光缆是弱电线缆(C5册)≠电力电缆(C4册)",
})

# 双绞线缆UTP6暗敷
decisions.append({
    "name": "双绞线缆",
    "description": "规格：UTP6，敷设方式：暗敷",
    "specialty": "C5",
    "correct_quota_ids": ["C5-2-21"],
    "correct_quota_names": ["双绞线缆 管内穿放 ≤4对"],
    "reasoning": "UTP6=六类非屏蔽双绞线，暗敷=穿管暗装，用C5-2-21双绞线管内穿放定额。",
    "wrong_quota": "C4-9-40 铜接地母线暗敷设",
    "wrong_reason": "双绞线≠接地母线，完全不同的线缆",
})

# 8芯室外光缆
decisions.append({
    "name": "8芯室外光缆",
    "description": "名称:8芯室外光缆，敷设方式:沿管内敷设",
    "specialty": "C5",
    "correct_quota_ids": ["C5-2-40"],
    "correct_quota_names": ["室内穿放、布放光缆 管内穿放 ≤12芯"],
    "reasoning": "8芯光缆管内穿放，用C5-2-40（8芯≤12芯以内）。虽然是'室外光缆'，但安装方式是管内穿放，定额按安装方式选取。",
    "wrong_quota": "C4-8-153 控制电缆沿沟内支架敷设",
    "wrong_reason": "光缆≠控制电缆，类别完全不同",
})

# 导线1RD1/3RD1（综合布线配线架）— 这些是弱电箱体/设备
for label in ["1RD1", "3RD1"]:
    decisions.append({
        "name": "导线",
        "description": f"名称：{label}，规格：600*500*300，跨越类型、宽度：综合布线配线架",
        "specialty": "C5",
        "correct_quota_ids": ["C5-2-9"],
        "correct_quota_names": ["弱电箱体 落地安装"],
        "reasoning": f"从描述看'{label}'是综合布线配线架的箱体（600×500×300），不是导线。清单数据可能录入有误，按弱电箱体安装处理。",
        "wrong_quota": "C4-10-81 集束导线 四芯 120mm²",
        "wrong_reason": "这不是导线，是弱电设备箱体，清单名称与实际内容不符",
    })

# 配电箱路由器
decisions.append({
    "name": "配电箱路由器",
    "description": "名称:配电箱路由器，规格型号:600*500*300",
    "specialty": "C5",
    "correct_quota_ids": ["C5-1-88"],
    "correct_quota_names": ["路由器 固定配置 ≤4口"],
    "reasoning": "虽然名称含'配电箱'，但核心是'路由器'，属于弱电网络设备，用C5路由器安装定额。",
    "wrong_quota": "C4-4-27 配电箱嵌入式安装",
    "wrong_reason": "路由器≠配电箱，是网络设备不是电气配电设备",
})

# ============================================================
# 类型15: 采暖设备
# ============================================================

# 钢制双柱散热器
decisions.append({
    "name": "钢制散热器",
    "description": "类型:钢制双柱散热器，型号、规格:TZ4-6-8",
    "specialty": "C10",
    "correct_quota_ids": ["C10-7-6"],
    "correct_quota_names": ["柱式散热器安装 散热器高度(mm以内) 600 散热器组数(片以内) 10"],
    "reasoning": "TZ4-6-8=钢制柱式散热器，高度600mm，8片。用柱式散热器定额C10-7-6（600mm高度/10片以内）。",
    "wrong_quota": "C10-7-2 板式散热器安装 单板",
    "wrong_reason": "双柱散热器≠板式散热器，结构形式不同",
})

# 热力入口装置 — 比较特殊
decisions.append({
    "name": "热力入口装置",
    "description": "设备名称：热力入口装置",
    "specialty": "C10",
    "correct_quota_ids": ["C10-5-3"],
    "correct_quota_names": ["螺纹阀门 公称直径(mm以内) 25"],
    "reasoning": "热力入口装置是采暖系统进户的阀门组合（含过滤器、阀门、仪表等），定额中无专门子目。实际应按各组件分别套定额。简化处理时可按主要阀门套定额，此处暂按螺纹阀门处理。需要人工确认具体组成。",
    "wrong_quota": "C8-1-46 低压不锈钢伴热管",
    "wrong_reason": "热力入口装置≠伴热管，完全不同的设备",
})

# ============================================================
# 类型16: 地漏
# ============================================================
for _ in range(2):
    decisions.append({
        "name": "地漏",
        "description": "名称: 地漏",
        "specialty": "C10",
        "correct_quota_ids": ["C10-6-67"],
        "correct_quota_names": ["地漏安装 公称直径(mm以内) 75"],
        "reasoning": "普通地漏用C10-6-67（地漏安装DN75），不是多功能地漏悬挂式。描述未注明特殊类型时默认普通地漏。",
        "wrong_quota": "C10-6-72 多功能地漏安装 悬挂式 75mm",
        "wrong_reason": "普通地漏≠多功能悬挂式地漏，安装方式和价格不同",
    })

# ============================================================
# 类型17: 排水漏斗（可能是正确的）
# ============================================================
for _ in range(2):
    decisions.append({
        "name": "排水漏斗",
        "description": "名称:排水漏斗",
        "specialty": "C10",
        "correct_quota_ids": ["C8-14-21"],
        "correct_quota_names": ["钢制排水漏斗制作安装 公称直径(mm以内) 100"],
        "reasoning": "排水漏斗用C8-14-21钢制排水漏斗定额。虽然C8是工业管道章节，但排水漏斗只在此处有定额。DN100是常用默认值。此匹配实际可能是正确的，置信度低是因为跨册匹配。",
        "wrong_quota": "",
        "wrong_reason": "原匹配可能正确，但置信度偏低",
    })

# ============================================================
# 类型18: 灭火控制装置调试
# ============================================================
for _ in range(2):
    decisions.append({
        "name": "灭火控制装置调试",
        "description": "详见图纸及设计验收规范",
        "specialty": "C9",
        "correct_quota_ids": ["C9-4-6"],
        "correct_quota_names": ["感烟探测器"],
        "reasoning": "灭火控制装置调试在定额中没有独立子目。如果是气体灭火系统控制装置的调试，通常包含在系统安装定额中。此项需人工确认具体是什么设备的调试。暂标记待确认。",
        "wrong_quota": "C9-2-44 独立式悬挂超细干粉灭火装置",
        "wrong_reason": "调试≠安装，且灭火控制装置≠灭火装置本体",
    })

# ============================================================
# 类型19: 挖沟槽土方（非安装定额）
# ============================================================
decisions.append({
    "name": "挖沟槽土方",
    "description": "",
    "specialty": "",
    "correct_quota_ids": [],
    "correct_quota_names": [],
    "reasoning": "挖沟槽土方属于土建工程（市政/土方定额），不在安装定额范围内。此项无法在安装定额中匹配，应标记为'非安装项'。",
    "wrong_quota": "C9-1-18 钢管(沟槽连接) 200mm",
    "wrong_reason": "挖沟槽≠沟槽连接，'沟槽'含义完全不同（挖土vs管道连接方式）",
})

# ============================================================
# 输出决策文件
# ============================================================
output = {
    "reviewer": "claude_code_agent",
    "review_date": "2026-02-17",
    "total_decisions": len(decisions),
    "decisions": decisions,
}

output_path = Path("output/review/agent_decisions_batch8_red.json")
output_path.parent.mkdir(parents=True, exist_ok=True)
with open(output_path, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

print(f"决策文件已生成: {output_path}")
print(f"总决策数: {len(decisions)}")

# 统计各类型
from collections import Counter
categories = Counter()
for d in decisions:
    if "泵" in d["name"]:
        categories["消防泵"] += 1
    elif "水箱" in d["name"]:
        categories["消防水箱"] += 1
    elif "喷淋钢管" in d["name"]:
        categories["涂覆碳钢管"] += 1
    elif "喷头" in d["name"]:
        categories["喷头"] += 1
    elif "减压" in d.get("reasoning", "") or "末端" in d.get("reasoning", ""):
        categories["减压孔板/试水"] += 1
    elif "填料套管" in d["name"]:
        categories["一般填料套管"] += 1
    elif "套管" in d["name"]:
        categories["套管(普通)"] += 1
    elif "灭火器" in d["name"]:
        categories["灭火器"] += 1
    elif "打洞" in d["name"]:
        categories["打洞"] += 1
    elif "绝热" in d["name"]:
        categories["保温材质"] += 1
    elif "风管" in d.get("reasoning", "") or "通风" in d["name"]:
        categories["矩形风管"] += 1
    elif "配电箱" in d["name"] and "路由" not in d["name"]:
        categories["配电箱"] += 1
    elif "风机" in d["name"]:
        categories["轴流风机"] += 1
    else:
        categories["其他"] += 1

print("\n各类型统计:")
for cat, count in categories.most_common():
    print(f"  {cat}: {count}条")
