from __future__ import annotations

from typing import Any
import re


ENTITY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("电缆头", ("电缆终端头", "电缆中间头", "终端头", "中间头", "电缆头")),
    ("地漏", ("地漏", "洗衣机地漏", "侧排地漏")),
    ("雨水斗", ("雨水斗", "雨水口")),
    ("倒流防止器", ("倒流防止器",)),
    ("水表", ("水表",)),
    ("过滤器", ("过滤器", "Y型过滤器", "Y形过滤器", "除污器")),
    ("软接头", ("软接头", "橡胶软接头", "可曲挠橡胶接头", "金属波纹管", "波纹软接头")),
    ("减压器", ("减压器", "减压孔板")),
    ("水龙头", ("水龙头", "龙头")),
    ("坐便器", ("坐便器", "座便器", "马桶")),
    ("蹲便器", ("蹲便器", "蹲式大便器")),
    ("大便器", ("大便器",)),
    ("洗脸盆", ("洗脸盆", "洗面盆")),
    ("洗涤盆", ("洗涤盆", "水槽", "单孔水槽")),
    ("小便器", ("小便器",)),
    ("淋浴器", ("淋浴器", "淋浴喷头")),
    ("水泵", ("水泵", "潜水泵", "离心泵")),
    ("支吊架", ("支吊架", "支架", "吊架")),
    ("套管", ("防水套管", "刚性防水套管", "柔性防水套管", "套管")),
    ("末端试水装置", ("末端试水装置",)),
    ("信号阀", ("信号阀",)),
    ("闸阀", ("闸阀",)),
    ("止回阀", ("止回阀",)),
    ("蝶阀", ("蝶阀",)),
    ("压力开关", ("压力开关",)),
    ("报警按钮", ("手动报警按钮", "报警按钮", "消火栓按钮")),
    ("消防模块", ("输入模块", "输出模块", "模块")),
    ("报警器", ("声光报警器", "报警器")),
    ("消防广播", ("消防广播", "广播扬声器", "扬声器", "紧急呼叫扬声器")),
    ("消防电话插孔", ("消防电话插孔", "电话插孔", "消防电话")),
    ("金属软管", ("金属软管", "软管敷设")),
    ("接线盒", ("接线盒", "接线箱", "分线盒")),
    ("开关", ("照明开关", "开关")),
    ("插座", ("插座",)),
    ("吸顶灯", ("吸顶灯",)),
    ("筒灯", ("筒灯",)),
    ("应急灯", ("应急灯",)),
    ("灯具", ("灯具",)),
    ("母线", ("母线", "封闭母线", "插接母线")),
    ("桥架附件", ("桥架弯头", "桥架三通", "桥架四通", "桥架附件")),
    ("桥架", ("桥架", "线槽", "电缆桥架")),
    ("电缆", ("电缆", "电线", "导线", "配线", "电力电缆", "控制电缆", "光缆")),
    ("配管", ("配管", "电气配管", "钢管敷设", "JDG", "KBG", "SC", "PC", "PVC管", "线管", "电线管", "钢导管")),
    ("喷头", ("喷头", "喷淋头")),
    ("报警阀组", ("报警阀组", "报警阀")),
    ("水流指示器", ("水流指示器",)),
    ("探测器", ("探测器", "感烟探测器", "感温探测器")),
    ("风机盘管", ("风机盘管",)),
    ("风阀", ("风阀", "调节阀", "防火阀", "排烟阀")),
    ("风口", ("风口", "散流器", "百叶风口")),
    ("卫生间通风器", ("卫生间通风器", "吊顶式通风器", "天花板管道式换气扇", "管道式换气扇")),
    ("排气扇", ("排气扇", "换气扇", "风扇")),
    ("暖风机", ("暖风机",)),
    ("风机", ("风机", "离心风机", "轴流风机", "排风机", "送风机")),
    ("风管", ("风管",)),
    ("阀门", ("阀门", "截止阀", "电磁阀")),
    ("管道", ("管道", "钢管", "塑料管", "复合管", "喷淋管", "给水管", "排水管")),
    ("配电箱", ("配电箱", "配电柜", "控制箱", "控制柜")),
    ("开关插座", ("开关", "插座", "按钮")),
    ("网络设备", ("交换机", "配线架", "集线器", "路由器", "网络设备")),
    ("机箱", ("信号机箱", "控制机箱", "设备控制机箱", "设备机箱", "抱杆机箱", "机箱（柜）", "机箱(柜)", "标准墙装机箱", "壁挂机箱", "落地机箱", "机箱", "机柜")),
]


SYSTEM_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("消防", (
        "消防", "喷淋", "消火栓", "火灾报警", "灭火", "声光报警", "消防广播",
        "末端试水装置", "信号阀", "压力开关", "输入模块", "输出模块", "声光报警器", "报警阀组",
        "水流指示器", "探测器", "喷头",
    )),
    ("给排水", (
        "给水", "排水", "污水", "雨水", "中水", "地漏", "雨水斗", "水龙头", "水表", "倒流防止器",
        "过滤器", "除污器", "软接头", "橡胶接头", "减压器",
    )),
    ("电气", (
        "电气", "桥架", "电缆", "配线", "配管", "照明", "动力", "交换机", "配线架",
        "配电箱", "配电柜", "控制箱", "控制柜", "接线箱", "开关", "插座",
        "金属软管", "JDG", "KBG", "母线", "灯具", "接线盒", "分线盒",
    )),
    ("通风空调", (
        "通风", "空调", "风管", "风阀", "风口", "散流器", "风机盘管", "风机",
        "暖风机", "排气扇", "换气扇", "卫生间通风器",
    )),
]


SPECIALTY_TO_SYSTEM = {
    "C4": "电气",
    "C5": "电气",
    "C7": "通风空调",
    "C9": "消防",
    "C10": "给排水",
    "C11": "电气",
}

ENTITY_TO_SYSTEM = {
    "电缆头": "电气",
    "地漏": "给排水",
    "雨水斗": "给排水",
    "倒流防止器": "给排水",
    "水表": "给排水",
    "过滤器": "给排水",
    "软接头": "给排水",
    "减压器": "给排水",
    "水龙头": "给排水",
    "坐便器": "给排水",
    "蹲便器": "给排水",
    "大便器": "给排水",
    "洗脸盆": "给排水",
    "洗涤盆": "给排水",
    "小便器": "给排水",
    "淋浴器": "给排水",
    "水泵": "给排水",
    "支吊架": "给排水",
    "套管": "给排水",
    "末端试水装置": "消防",
    "信号阀": "消防",
    "闸阀": "给排水",
    "止回阀": "给排水",
    "蝶阀": "给排水",
    "压力开关": "消防",
    "报警按钮": "消防",
    "消防模块": "消防",
    "报警器": "消防",
    "消防广播": "消防",
    "消防电话插孔": "消防",
    "金属软管": "电气",
    "接线盒": "电气",
    "开关": "电气",
    "插座": "电气",
    "吸顶灯": "电气",
    "筒灯": "电气",
    "应急灯": "电气",
    "灯具": "电气",
    "母线": "电气",
    "桥架附件": "电气",
    "桥架": "电气",
    "电缆": "电气",
    "配管": "电气",
    "配电箱": "电气",
    "浪涌保护器": "电气",
    "开关插座": "电气",
    "网络设备": "电气",
    "喷头": "消防",
    "报警阀组": "消防",
    "水流指示器": "消防",
    "探测器": "消防",
    "风机盘管": "通风空调",
    "风阀": "通风空调",
    "风口": "通风空调",
    "卫生间通风器": "通风空调",
    "排气扇": "通风空调",
    "暖风机": "通风空调",
    "风机": "通风空调",
    "风管": "通风空调",
    "消火栓": "消防",
    "机箱": "电气",
}


MATERIAL_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("镀锌钢板", ("白铁", "白铁皮", "镀锌钢板")),
    ("镀锌钢管", ("镀锌管", "镀锌钢管", "白铁管")),
    ("喷淋钢管", ("喷淋管", "喷淋钢管")),
    ("焊接钢管", ("焊接钢管",)),
    ("无缝钢管", ("无缝钢管",)),
    ("不锈钢管", ("不锈钢管", "薄壁不锈钢管")),
    ("铸铁管", ("铸铁管", "柔性铸铁管", "球墨铸铁管", "柔性铸铁", "球墨铸铁")),
    ("塑料管", ("塑料管", "PVC管", "UPVC管", "PE管", "HDPE管")),
    ("PPR管", ("PPR", "PP-R", "PPR管", "PPR冷水管", "PPR热水管")),
    ("复合管", ("复合管", "钢塑复合管", "铝塑复合管", "衬塑钢管")),
    ("JDG管", ("JDG", "JDG管")),
    ("KBG管", ("KBG", "KBG管")),
    ("SC钢管", ("SC", "SC管")),
    ("铜管", ("铜管",)),
    ("金属软管", ("金属软管",)),
    ("铜芯", ("铜芯", "铜导线", "铜芯电缆")),
    ("铝芯", ("铝芯", "铝导线", "铝芯电缆")),
]


CONNECTION_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("沟槽连接", ("沟槽连接", "卡箍连接", "卡箍")),
    ("螺纹连接", ("螺纹连接", "丝扣连接", "丝扣", "螺纹")),
    ("法兰连接", ("法兰连接", "法兰")),
    ("焊接连接", ("焊接连接", "焊接", "对焊连接", "电弧焊")),
    ("热熔连接", ("热熔连接", "双热熔", "热熔")),
    ("卡压连接", ("卡压连接", "卡压", "环压连接", "环压")),
    ("承插连接", ("承插连接", "承插")),
    ("粘接", ("粘接", "承插粘接")),
]


INSTALL_METHOD_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("明敷", ("明敷", "明设")),
    ("暗敷", ("暗敷", "暗设")),
    ("明装", ("明装",)),
    ("暗装", ("暗装",)),
    ("落地", ("落地", "地装")),
    ("挂壁", ("挂壁", "壁挂", "挂墙", "壁式", "壁装", "墙上式")),
    ("嵌入", ("嵌入", "嵌墙", "嵌装")),
    ("吊装", ("吊装", "吊式", "吊挂", "吊顶式", "天花式", "天棚式")),
    ("吸顶", ("吸顶式", "吸顶安装")),
    ("悬挂", ("悬挂式", "悬挂安装")),
]


TRAIT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("带自救卷盘", ("带自救卷盘", "含自救卷盘", "自救卷盘")),
    ("单栓", ("单栓",)),
    ("双栓", ("双栓",)),
    ("刚性", ("刚性",)),
    ("柔性", ("柔性",)),
    ("感烟", ("感烟",)),
    ("感温", ("感温",)),
    ("单控", ("单控",)),
    ("双控", ("双控",)),
    ("单相", ("单相",)),
    ("三相", ("三相",)),
    ("三孔", ("三孔",)),
    ("五孔", ("五孔",)),
    ("直立型", ("直立型",)),
    ("下垂型", ("下垂型", "下喷型")),
    ("边墙型", ("边墙型", "侧墙型")),
    ("湿式", ("湿式",)),
    ("干式", ("干式",)),
    ("预作用", ("预作用",)),
    ("雨淋", ("雨淋",)),
    ("吸顶灯", ("吸顶灯",)),
    ("筒灯", ("筒灯",)),
    ("应急灯", ("应急灯",)),
    ("吸顶式", ("吸顶式",)),
    ("壁挂式", ("壁挂式", "挂墙式", "壁装", "墙上式")),
    ("落地式", ("落地式",)),
    ("悬挂式", ("悬挂式",)),
    ("嵌入式", ("嵌入式", "嵌顶式")),
    ("连体水箱", ("连体水箱",)),
    ("隐蔽水箱", ("隐蔽水箱", "隐藏水箱")),
    ("高水箱", ("高水箱",)),
    ("低水箱", ("低水箱",)),
    ("感应开关", ("感应开关", "感应式", "感应")),
    ("脚踏开关", ("脚踏开关", "脚踏式")),
    ("自闭阀", ("自闭阀",)),
    ("带接地", ("带接地", "带保护极", "保护极", "接地极", "二三极", "二、三极", "二三孔", "二、三孔", "三孔", "五孔", "四孔")),
    ("不带接地", ("两孔", "二孔", "二极")),
    ("冷水", ("冷水",)),
    ("冷热水", ("冷热水",)),
    ("单嘴", ("单嘴",)),
    ("双嘴", ("双嘴",)),
    ("离心式", ("离心式",)),
    ("轴流式", ("轴流式",)),
    ("手动", ("手动",)),
    ("电动调节", ("电动调节", "电动")),
    ("托盘式", ("托盘式",)),
    ("槽式", ("槽式",)),
    ("梯式", ("梯式",)),
    ("线槽", ("线槽", "金属线槽")),
    ("一般管架", ("一般管架",)),
    ("支撑架", ("支撑架", "桥架支撑架")),
    ("防雨百叶", ("防雨百叶",)),
    ("格栅风口", ("格栅风口", "格栅")),
    ("钢百叶窗", ("钢百叶窗",)),
    ("板式排烟口", ("板式排烟口", "排烟口")),
    ("带调节阀", ("带调节阀",)),
    ("卫生间通风器", ("卫生间通风器", "吊顶式通风器", "天花板管道式换气扇")),
    ("排气扇", ("排气扇", "换气扇")),
    ("线形灯", ("线形灯", "线型灯")),
    ("灯带", ("灯带", "荧光灯带")),
    ("管内穿线", ("管内穿线", "管内穿", "穿线")),
    ("桥架内", ("桥架内", "沿桥架", "桥架敷设")),
    ("吊顶内", ("吊顶内",)),
    ("角钢", ("角钢",)),
    ("槽钢", ("槽钢", "C型槽钢", "C槽钢")),
    ("圆钢", ("圆钢",)),
    ("扁钢", ("扁钢",)),
    ("手工除锈", ("手工除锈",)),
    ("机械除锈", ("机械除锈", "动力工具除锈")),
    ("喷砂除锈", ("喷砂除锈",)),
    ("防锈漆", ("防锈漆", "红丹防锈漆")),
    ("调和漆", ("调和漆", "调合漆")),
    ("银粉漆", ("银粉漆",)),
    ("防爆", ("防爆",)),
    ("防水", ("防水",)),
    ("防尘", ("防尘",)),
]


CANONICAL_NAME_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"白铁(?:皮)?风管"), "镀锌钢板风管"),
    (re.compile(r"白铁(?:管)?"), "镀锌钢管"),
    (re.compile(r"喷淋管"), "喷淋钢管"),
    (re.compile(r"镀锌管"), "镀锌钢管"),
    (re.compile(r"Y[型形]过滤器|除污器"), "过滤器"),
    (re.compile(r"可曲挠橡胶接头|橡胶软接头|金属波纹管"), "软接头"),
    (re.compile(r"洗涤盆|水槽"), "洗涤盆"),
    (re.compile(r"卫生间通风器|吊顶式通风器|天花板管道式换气扇"), "卫生间通风器"),
    (re.compile(r"暖风机"), "暖风机"),
    (re.compile(r"金属软管"), "金属软管"),
]


def normalize_text(text: str) -> str:
    text = text or ""
    text = text.replace("\n", " ").replace("\r", " ")
    return re.sub(r"\s+", " ", text).strip()


_SURGE_PROTECTOR_ENTITY_PATTERN = re.compile(
    r"(?:^|[\s(（\[【])(?:信号|电源|计算机|网络|用户分)?(?:电涌保护器|浪涌保护器|防雷器|避雷器|SPD)(?:安装|调试)?(?:$|[\s)）\]】,，;；])",
    re.IGNORECASE,
)
_SURGE_PROTECTOR_BLOCK_WORDS = (
    "避雷网",
    "避雷针",
    "避雷引下线",
    "避雷带",
    "接闪",
    "避雷装置",
)


def _looks_like_surge_protector_subject(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _SURGE_PROTECTOR_BLOCK_WORDS):
        return False
    if any(keyword in text for keyword in ("电涌保护器", "浪涌保护器", "信号避雷器", "电源避雷器", "防雷器")):
        return True
    if "SPD" in text.upper():
        return True
    if "避雷器" in text:
        return True
    return bool(_SURGE_PROTECTOR_ENTITY_PATTERN.search(text))


def _pick_by_rules(text: str, rules: list[tuple[str, tuple[str, ...]]]) -> str:
    if not text:
        return ""
    for canonical, aliases in rules:
        if any(alias and alias in text for alias in aliases):
            return canonical
    return ""


def _normalize_by_rules(value: str, text: str,
                        rules: list[tuple[str, tuple[str, ...]]]) -> str:
    value = (value or "").strip()
    text = text or ""
    picked = _pick_by_rules(value, rules)
    if picked:
        return picked
    picked = _pick_by_rules(text, rules)
    if picked:
        return picked
    return value


def detect_entity(text: str) -> str:
    text = text or ""
    if any(
        keyword in text
        for keyword in (
            "波纹电线管", "镀锌电线管", "金属电线管", "PVC电线管",
            "电线管", "钢导管", "紧定式钢导管", "扣压式钢导管",
        )
    ):
        return "配管"
    # 给排水清单经常写成"PVC-U塑料管/PP-R压力管/静音管/防水套管"，
    # 如果 bill 侧实体为空，后续 family/entity gate 无法生效。
    if any(keyword in text for keyword in ("防水套管", "刚性防水套管", "柔性防水套管", "穿墙管", "套管")):
        return "套管"
    if any(
        keyword in text
        for keyword in (
            "给水管", "排水管", "雨水管", "冷凝水管", "污、废水管", "污废水管",
            "PVC-U塑料管", "PPR管", "PP-R", "塑料管", "复合静音管", "静音管",
            "内螺旋管", "压力管",
        )
    ):
        return "管道"
    if ("消火栓" in text or "消防栓" in text) and not any(
        keyword in text for keyword in ("钢管", "管道", "立管", "支管")
    ):
        return "消火栓"
    primary_text = re.split(
        r"(?:名称|型号(?:、规格)?|规格(?:、型号)?|材质(?:、规格)?|敷设方式(?:、部位)?|安装部位|配置形式|部位|电压等级|类别)\s*[:：]",
        text,
        maxsplit=1,
    )[0].strip()
    if _looks_like_surge_protector_subject(primary_text):
        return "浪涌保护器"
    picked = _pick_by_rules(primary_text, ENTITY_RULES)
    if picked:
        return picked
    return _pick_by_rules(text, ENTITY_RULES)


def detect_system(text: str, specialty: str = "",
                  context_prior: dict[str, Any] | None = None,
                  entity: str = "") -> str:
    context_prior = dict(context_prior or {})
    picked = _pick_by_rules(text, SYSTEM_RULES)
    if picked:
        return picked

    system_hint = str(context_prior.get("system_hint") or "").strip()
    picked = _pick_by_rules(system_hint, SYSTEM_RULES)
    if picked:
        return picked

    context_hints = context_prior.get("context_hints") or []
    for hint in context_hints:
        picked = _pick_by_rules(str(hint or ""), SYSTEM_RULES)
        if picked:
            return picked

    specialty = str(context_prior.get("specialty") or specialty or "").strip()
    picked = SPECIALTY_TO_SYSTEM.get(specialty, "")
    if picked:
        return picked
    return ENTITY_TO_SYSTEM.get(entity or "", "")


def normalize_material(material: str, text: str = "") -> str:
    return _normalize_by_rules(material, text, MATERIAL_RULES)


def normalize_connection(connection: str, text: str = "") -> str:
    return _normalize_by_rules(connection, text, CONNECTION_RULES)


def normalize_install_method(install_method: str, text: str = "") -> str:
    return _normalize_by_rules(install_method, text, INSTALL_METHOD_RULES)


def resolve_canonical_name(text: str, entity: str = "", material: str = "") -> str:
    text = text or ""
    for pattern, canonical in CANONICAL_NAME_RULES:
        if pattern.search(text):
            return canonical
    if material and entity:
        if entity in material:
            return material
        return f"{material}{entity}"
    return entity or material or ""


def detect_family(text: str,
                  *,
                  entity: str = "",
                  system: str = "",
                  material: str = "",
                  install_method: str = "",
                  traits: list[str] | None = None,
                  context_prior: dict[str, Any] | None = None) -> str:
    text = text or ""
    system = str(system or "").strip()
    entity = str(entity or "").strip()
    material = str(material or "").strip()
    install_method = str(install_method or "").strip()
    context_prior = dict(context_prior or {})
    trait_set = {
        str(value).strip()
        for value in (traits or [])
        if str(value).strip()
    }
    prior_family = str(context_prior.get("prior_family") or "").strip()
    context_hints = " ".join(str(value or "") for value in (context_prior.get("context_hints") or []))
    combined_text = " ".join(
        part for part in (text, prior_family, context_hints, material, install_method)
        if part
    )

    if entity in {"桥架", "桥架附件"}:
        if any(word in combined_text for word in ("支撑架", "支架", "支吊架")):
            return "bridge_support"
        return "bridge_raceway"

    if entity == "支吊架":
        if (
            any(word in combined_text for word in ("桥架", "电缆桥架", "母线槽", "抗震支吊架"))
            or "支撑架" in trait_set
            or system == "电气"
        ):
            return "bridge_support"
        if (
            any(word in combined_text for word in ("管道", "管架", "给水", "排水", "喷淋", "消火栓", "采暖"))
            or "一般管架" in trait_set
            or system in {"给排水", "消防", "通风空调"}
        ):
            return "pipe_support"
        return "pipe_support"

    if entity in {"阀门", "闸阀", "止回阀", "蝶阀"}:
        return "valve_body"

    if entity in {"过滤器", "水表", "倒流防止器", "软接头", "减压器"}:
        return "valve_accessory"

    if entity == "管道":
        return "pipe_run"

    if entity == "套管":
        return "pipe_sleeve"

    if entity == "机箱":
        return "device_cabinet"

    if entity == "风口":
        return "air_terminal"

    if entity == "风阀":
        return "air_valve"

    if entity in {"风机", "排气扇", "卫生间通风器", "暖风机", "风机盘管"}:
        return "air_device"

    if entity in {"坐便器", "蹲便器", "小便器", "洗脸盆", "洗涤盆", "淋浴器"}:
        return "sanitary_fixture"

    if entity == "水龙头":
        return "sanitary_accessory"

    if entity == "配电箱":
        return "electrical_box"

    if entity == "浪涌保护器":
        return "protection_device"

    if entity in {"配管", "金属软管", "接线盒"}:
        return "conduit_raceway"

    if entity == "电缆头":
        return "cable_head_accessory"

    if entity == "电缆":
        return "cable_family"

    return ""


def build_numeric_params(params: dict[str, Any] | None) -> dict[str, Any]:
    params = params or {}
    keys = (
        "dn", "cable_section", "cable_cores", "kva", "kw", "kv", "ampere",
        "circuits", "port_count", "weight_t", "perimeter", "half_perimeter",
        "large_side", "ground_bar_width", "elevator_stops", "elevator_speed",
        "bridge_wh_sum",
        "switch_gangs",
    )
    return {key: params[key] for key in keys if params.get(key) is not None}


def build_specs(params: dict[str, Any] | None) -> dict[str, Any]:
    params = params or {}
    keys = (
        "cable_bundle", "shape", "elevator_type", "cable_type",
        "cable_head_type", "conduit_type", "wire_type",
        "box_mount_mode", "bridge_type", "valve_connection_family",
        "conduit_dn", "install_method", "laying_method", "voltage_level",
        "valve_type", "support_material", "support_scope", "support_action",
        "surface_process", "sanitary_subtype", "sanitary_mount_mode",
        "sanitary_flush_mode", "sanitary_water_mode", "sanitary_nozzle_mode",
        "sanitary_tank_mode", "lamp_type", "outlet_grounding",
    )
    return {key: params[key] for key in keys if params.get(key) not in (None, "", [])}


def collect_traits(params: dict[str, Any] | None,
                   context_prior: dict[str, Any] | None = None,
                   raw_text: str = "") -> list[str]:
    params = params or {}
    context_prior = dict(context_prior or {})
    traits: list[str] = []
    for key in (
        "shape",
        "elevator_type",
        "cable_type",
        "cable_head_type",
        "conduit_type",
        "wire_type",
        "box_mount_mode",
        "bridge_type",
        "valve_connection_family",
        "voltage_level",
        "laying_method",
        "valve_type",
        "support_material",
        "support_scope",
        "support_action",
        "surface_process",
        "sanitary_subtype",
        "sanitary_mount_mode",
        "sanitary_flush_mode",
        "sanitary_water_mode",
        "sanitary_nozzle_mode",
        "sanitary_tank_mode",
        "lamp_type",
        "outlet_grounding",
    ):
        value = params.get(key)
        if value:
            traits.append(str(value))
    cable_type = context_prior.get("cable_type")
    if cable_type:
        traits.append(str(cable_type))
    for canonical, aliases in TRAIT_RULES:
        if any(alias and alias in raw_text for alias in aliases):
            traits.append(canonical)
    return list(dict.fromkeys(traits))
