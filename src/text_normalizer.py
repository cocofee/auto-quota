# -*- coding: utf-8 -*-
"""
经验库匹配专用的文本归一化工具

目标：把"同一个清单项目"的不同写法统一为同一个字符串。
设计得尽量激进 — 宁可偶尔过度归一化（少数不该合并的被合并），
也不要漏掉该合并的（那样经验库就白存了）。

使用场景：
  经验库写入时自动生成 normalized_text 字段，
  匹配时 normalize(输入) == normalize(经验) 即视为命中。

与 text_parser.normalize_bill_text 的区别：
  text_parser 侧重"语义保留"（给搜索用），保留大部分信息。
  本模块侧重"格式统一"（给精确匹配用），激进去除格式差异。
"""

import re

# De→DN 转换表（复用 text_parser.py 的映射关系）
# 塑料管用外径(De)标注，需要转换为公称直径(DN)才能与定额对齐
_DE_TO_DN = {
    20: 15, 25: 20, 32: 25, 40: 32, 50: 40,
    63: 50, 75: 65, 90: 80, 110: 100, 125: 100,
    140: 125, 160: 150, 200: 200, 225: 200,
    250: 250, 315: 300, 355: 350, 400: 400,
}

# 标签式废话关键词（冒号后面的内容无匹配意义）
_LABEL_KEYWORDS = [
    "压力试验", "安装部位", "安装位置", "介质", "施工要求",
    "验收标准", "执行标准", "工作压力", "设计压力", "试验压力",
    "保温要求", "防腐要求", "油漆", "刷油",
]

# 动作词（安装/铺设等，清单和定额对同一物项可能用不同动作词）
_ACTION_WORDS = [
    "安装", "铺设", "敷设", "制作", "施工", "布线", "布设",
    "架设", "配置", "设置",
]

# 合并标签正则（编译一次复用）
_RE_LABELS = re.compile(
    r'(?:' + '|'.join(re.escape(kw) for kw in _LABEL_KEYWORDS) + r')[：:][^\n]*'
)


def normalize_for_match(text: str) -> str:
    """经验库匹配专用的文本归一化

    归一化规则（按顺序执行）：
      1. 去括号及内容（如"(详见图纸)"）
      2. 去行首编号（如"1.名称:"）
      3. 去标签式废话（如"压力试验:xxx"）
      4. 全部转小写
      5. De→DN转换（塑料管外径→公称直径）
      6. 统一DN格式（DN25/Φ25/公称直径25 → dn25）
      7. 统一截面格式（4mm²/4平方 → 4mm2）
      8. 去动作词（安装/铺设/敷设等）
      9. 去所有空格、标点和特殊符号

    返回:
        归一化后的字符串（纯小写、无空格、无标点）
        空输入返回空字符串
    """
    if not text or not text.strip():
        return ""

    s = text

    # 1. 去括号及内容：(详见图纸)、（含安装费）
    s = re.sub(r'[（(][^）)]*[）)]', '', s)

    # 2. 去行首编号：1. / 2、/ 3．
    s = re.sub(r'^\s*\d+[.、．]\s*', '', s, flags=re.MULTILINE)

    # 3. 去标签式废话
    s = _RE_LABELS.sub('', s)

    # 4. 全部转小写
    s = s.lower()

    # 5. De→DN转换：de32 → dn25
    def _de_to_dn(m):
        de_val = int(m.group(1))
        if de_val in _DE_TO_DN:
            return f'dn{_DE_TO_DN[de_val]}'
        # 找最接近的De值
        closest = min(_DE_TO_DN.keys(), key=lambda x: abs(x - de_val))
        return f'dn{_DE_TO_DN[closest]}'

    s = re.sub(r'de\s*(\d+)', _de_to_dn, s)

    # 6. 统一DN格式
    # 公称直径(mm)25 / 公称直径(mm以内)25 → dn25
    s = re.sub(r'公称直径\s*(?:\(mm(?:以内)?\))?\s*(\d+)', r'dn\1', s)
    # DN 25 / DN-25 / dn_25 → dn25
    s = re.sub(r'dn\s*[-_]?\s*(\d+)', r'dn\1', s)
    # Φ25 / φ25 → dn25
    s = re.sub(r'[φΦΦ]\s*(\d+)', r'dn\1', s)

    # 7. 统一截面格式：4mm² / 4mm2 / 4平方 → 4mm2
    s = re.sub(r'(\d+(?:\.\d+)?)\s*(?:mm²|mm2|平方(?:毫米)?)', r'\1mm2', s)

    # 8. 去动作词
    for word in _ACTION_WORDS:
        s = s.replace(word, '')

    # 9. 去所有空格、标点和特殊符号（只保留中文字符、字母、数字）
    s = re.sub(r'[^\u4e00-\u9fffa-z0-9]', '', s)

    return s
