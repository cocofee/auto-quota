"""
结构化文本解析器
从定额名称和清单描述中提取结构化参数：
- 管径(DN)、截面(mm²)、电流(A)、重量(t/kg)
- 材质、连接方式、设备类型等文字参数
- 数值统一格式化（DN150→150, 4×185→185等）
"""

import re
from typing import Optional


class TextParser:
    """从工程文本中提取结构化参数"""

    def parse(self, text: str) -> dict:
        """
        解析文本，提取所有可识别的参数

        参数:
            text: 定额名称或清单描述文字

        返回:
            字典，包含提取到的各项参数
        """
        if not text:
            return {}

        result = {}

        # 提取管径（DN）
        dn = self._extract_dn(text)
        if dn is not None:
            result["dn"] = dn

        # 提取电缆截面（mm²）
        section = self._extract_cable_section(text)
        if section is not None:
            result["cable_section"] = section

        # 提取容量（kVA）
        kva = self._extract_kva(text)
        if kva is not None:
            result["kva"] = kva

        # 提取电压等级（kV）
        kv = self._extract_kv(text)
        if kv is not None:
            result["kv"] = kv

        # 提取电流（A）
        ampere = self._extract_ampere(text)
        if ampere is not None:
            result["ampere"] = ampere

        # 提取重量（t或kg）
        weight = self._extract_weight(text)
        if weight is not None:
            result["weight_t"] = weight  # 统一转为吨

        # 提取材质
        material = self._extract_material(text)
        if material:
            result["material"] = material

        # 提取连接方式
        connection = self._extract_connection(text)
        if connection:
            result["connection"] = connection

        return result

    def _extract_dn(self, text: str) -> Optional[int]:
        """
        提取管径DN值，统一返回整数（毫米）

        支持格式: DN150, DN-150, dn150, 公称直径150, 公称直径(mm以内) 150
        """
        patterns = [
            r'[Dd][Nn]\s*[-_]?\s*(\d+)',                         # DN150, DN-150
            r'公称直径\s*(?:\(mm(?:以内)?\))?\s*(\d+)',            # 公称直径150, 公称直径(mm以内) 150
            r'管径\s*(\d+)',                                       # 管径150
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1))
        return None

    def _extract_cable_section(self, text: str) -> Optional[float]:
        """
        提取电缆截面积（mm²），返回主线芯截面

        支持格式:
        - YJV-4*185+1*95 → 185
        - 3×70 → 70
        - 截面(mm²以内) 185 → 185
        - 4X16 → 16
        """
        # 格式: 数字*截面 或 数字×截面（取最大的截面值作为主截面）
        pattern = r'(\d+)\s*[*×xX]\s*(\d+(?:\.\d+)?)'
        matches = re.findall(pattern, text)
        if matches:
            # 取所有截面值中最大的作为主截面
            sections = [float(m[1]) for m in matches]
            return max(sections)

        # 格式: 截面(mm²以内) 数值
        match = re.search(r'截面\s*(?:\(mm[²2]?(?:以内)?\))?\s*(\d+(?:\.\d+)?)', text)
        if match:
            return float(match.group(1))

        return None

    def _extract_kva(self, text: str) -> Optional[float]:
        """
        提取变压器容量（kVA）

        支持格式: 800kva, 800kV·A, 容量(kV·A) 800
        """
        patterns = [
            r'(\d+(?:\.\d+)?)\s*[kK][vV][·.]?[aA]',              # 800kva, 800kV·A
            r'容量\s*(?:\([kK][vV][·.]?[aA](?:以内)?\))?\s*(\d+(?:\.\d+)?)',  # 容量(kV·A) 800
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
        return None

    def _extract_kv(self, text: str) -> Optional[float]:
        """
        提取电压等级（kV）

        支持格式: 10kV, 10KV, 0.6/1kV, 8.5/15kv
        """
        # 格式: 数字/数字kV（取后面的值作为电压等级）
        match = re.search(r'(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*[kK][vV]', text)
        if match:
            return float(match.group(2))

        # 格式: 数字kV
        match = re.search(r'(\d+(?:\.\d+)?)\s*[kK][vV](?![·.aA])', text)
        if match:
            return float(match.group(1))

        return None

    def _extract_ampere(self, text: str) -> Optional[float]:
        """
        提取电流值（A）

        支持格式: 100A, 电流(A以内) 100
        """
        patterns = [
            r'(\d+(?:\.\d+)?)\s*[aA](?![a-zA-Z])',               # 100A
            r'电流\s*(?:\([aA](?:以内)?\))?\s*(\d+(?:\.\d+)?)',    # 电流(A以内) 100
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))
        return None

    def _extract_weight(self, text: str) -> Optional[float]:
        """
        提取重量，统一转为吨(t)

        支持格式: 30t, 30吨, 设备重量(t以内) 30, 500kg
        """
        # 吨
        patterns_t = [
            r'(\d+(?:\.\d+)?)\s*[tT](?![a-zA-Z])',               # 30t
            r'(\d+(?:\.\d+)?)\s*吨',                               # 30吨
            r'(?:重量|质量)\s*(?:\([tT](?:以内)?\))?\s*(\d+(?:\.\d+)?)',  # 重量(t以内) 30
        ]
        for pattern in patterns_t:
            match = re.search(pattern, text)
            if match:
                return float(match.group(1))

        # 千克 → 转为吨
        match = re.search(r'(\d+(?:\.\d+)?)\s*[kK][gG]', text)
        if match:
            return float(match.group(1)) / 1000

        return None

    def _extract_material(self, text: str) -> Optional[str]:
        """提取材质"""
        materials = [
            "镀锌钢管", "焊接钢管", "无缝钢管", "不锈钢管",
            "铜管", "铝管", "PPR管", "PE管", "PVC管", "UPVC管",
            "铸铁管", "球墨铸铁管", "钢制", "铸铁",
            "铜芯", "铝芯", "铜导线", "铝导线",
            "高压铝芯电缆", "铝芯电缆", "铜芯电缆",
        ]
        for mat in materials:
            if mat in text:
                return mat
        return None

    def _extract_connection(self, text: str) -> Optional[str]:
        """提取连接方式"""
        connections = [
            "沟槽连接", "螺纹连接", "焊接连接", "法兰连接",
            "热熔连接", "卡压连接", "承插连接", "粘接",
            "卡箍连接",
        ]
        for conn in connections:
            if conn in text:
                return conn
        return None

    def build_search_text(self, name: str, description: str = "") -> str:
        """
        构建用于搜索的文本
        合并项目名称和特征描述，去除无用信息

        参数:
            name: 项目名称
            description: 项目特征描述

        返回:
            清洗后的搜索文本
        """
        # 合并名称和描述
        text = f"{name} {description}".strip()

        # 去除序号前缀（如 "1.", "2." 等）
        text = re.sub(r'^\d+\.\s*', '', text)

        # 去除"详见图纸及设计验收规范"等通用废话
        noise_patterns = [
            r'详见图纸.*?规范',
            r'其他[：:]\s*详见.*',
            r'详见设计图纸',
        ]
        for pattern in noise_patterns:
            text = re.sub(pattern, '', text)

        # 去除多余空白
        text = re.sub(r'\s+', ' ', text).strip()

        return text

    def params_match(self, bill_params: dict, quota_params: dict) -> tuple[bool, float]:
        """
        检查清单参数和定额参数是否匹配

        参数:
            bill_params: 清单提取的参数
            quota_params: 定额提取的参数

        返回:
            (是否匹配, 匹配分数0-1)
            匹配分数: 1.0=完全匹配, 0.5=部分匹配, 0.0=不匹配
        """
        if not bill_params or not quota_params:
            return True, 0.5  # 没有参数可比较，算部分匹配

        score = 0.0
        total_checks = 0

        # 检查DN（管径）
        if "dn" in bill_params and "dn" in quota_params:
            total_checks += 1
            if bill_params["dn"] == quota_params["dn"]:
                score += 1.0
            else:
                # DN不匹配是硬伤，直接标记不匹配
                return False, 0.0

        # 检查电缆截面
        if "cable_section" in bill_params and "cable_section" in quota_params:
            total_checks += 1
            bill_sec = bill_params["cable_section"]
            quota_sec = quota_params["cable_section"]
            if bill_sec == quota_sec:
                score += 1.0
            elif bill_sec <= quota_sec:
                # 清单截面小于定额档位，可能是向上取档，扣一点分
                score += 0.5
            else:
                return False, 0.0

        # 检查容量（kVA）
        if "kva" in bill_params and "kva" in quota_params:
            total_checks += 1
            if bill_params["kva"] == quota_params["kva"]:
                score += 1.0
            elif bill_params["kva"] <= quota_params["kva"]:
                score += 0.5
            else:
                return False, 0.0

        # 检查材质（软匹配，不匹配只扣分不判死）
        if "material" in bill_params and "material" in quota_params:
            total_checks += 1
            if bill_params["material"] == quota_params["material"]:
                score += 1.0
            else:
                score += 0.3

        # 检查连接方式（软匹配）
        if "connection" in bill_params and "connection" in quota_params:
            total_checks += 1
            if bill_params["connection"] == quota_params["connection"]:
                score += 1.0
            else:
                score += 0.3

        if total_checks == 0:
            return True, 0.5

        final_score = score / total_checks
        is_match = final_score >= 0.5

        return is_match, final_score


# 模块级单例，方便直接导入使用
parser = TextParser()
