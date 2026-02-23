"""
定额词汇反向提取器
功能：
1. 从定额数据库中自动提取材质、连接方式、设备类型等词汇
2. 自动更新jieba专业词典
3. 构建"定额名称词干"索引——每个词干对应一组定额

核心思想：
定额库本身就是最权威的"词典"。
比如定额库中有"钢塑复合管"和"铝塑复合管"两种，
那系统就自动知道这是两种不同的材质，不能混淆。
不需要人工维护词表，从数据里学。
"""

import re
import os
import tempfile
from pathlib import Path
from collections import Counter, defaultdict

from loguru import logger

import config
from db.sqlite import connect as _db_connect


class VocabExtractor:
    """从定额数据库反向提取词汇"""

    def __init__(self, province: str = None):
        self.province = province or config.get_current_province()
        self.db_path = config.get_quota_db_path(self.province)

        # 提取结果
        self.stems = {}            # {词干: [quota_id列表]}  去参数后的定额名称
        self.materials = set()     # 提取到的材质词汇
        self.connections = set()   # 提取到的连接方式词汇
        self.equipment = set()     # 提取到的设备类型词汇
        self.all_terms = set()     # 所有有意义的词汇

    def _connect(self):
        """统一SQLite连接参数"""
        return _db_connect(self.db_path, row_factory=True)

    def extract_all(self):
        """执行完整的反向提取流程"""
        logger.info("开始从定额库反向提取词汇...")

        # 读取所有定额
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT id, quota_id, name, chapter FROM quotas")
            rows = cursor.fetchall()
        finally:
            conn.close()

        logger.info(f"定额总数: {len(rows)} 条")

        # 1. 提取名称词干
        self._extract_stems(rows)

        # 2. 从词干中提取材质词汇
        self._extract_materials()

        # 3. 从词干中提取连接方式
        self._extract_connections()

        # 4. 提取设备类型
        self._extract_equipment()

        # 5. 汇总所有词汇
        self.all_terms = self.materials | self.connections | self.equipment

        # 6. 保存提取结果缓存（供 text_parser 自动加载）
        self._save_vocab_cache()

        logger.info(f"提取完成: 词干{len(self.stems)}个, "
                    f"材质{len(self.materials)}个, "
                    f"连接方式{len(self.connections)}个, "
                    f"设备类型{len(self.equipment)}个")

        return {
            "stems_count": len(self.stems),
            "materials": sorted(self.materials),
            "connections": sorted(self.connections),
            "equipment_count": len(self.equipment),
            "total_terms": len(self.all_terms),
        }

    def _extract_stems(self, rows):
        """
        从定额名称中提取词干（去掉参数值后的名称部分）

        例：
        "室内给水钢塑复合管(螺纹连接) 公称直径(mm以内) 50" → "室内给水钢塑复合管(螺纹连接)"
        "干式变压器安装 容量(kVA以内) 800" → "干式变压器安装"
        "电缆埋地敷设 电缆截面(mm2以内) 185" → "电缆埋地敷设"
        """
        stem_map = defaultdict(list)  # {词干: [quota_id列表]}

        for row in rows:
            name = row["name"]
            quota_id = row["quota_id"]

            # 去掉参数后缀：参数名(单位以内/以下) 数值
            stem = re.sub(r'\s+\S*\([^)]*(?:以内|以下)\)\s*[\d./×*]+.*$', '', name).strip()
            # 去掉末尾的纯数字和DN
            stem = re.sub(r'\s+DN\d+$', '', stem).strip()
            stem = re.sub(r'\s+[\d./×*]+$', '', stem).strip()

            if len(stem) >= 2:
                stem_map[stem].append(quota_id)

        self.stems = dict(stem_map)
        logger.info(f"  提取到 {len(self.stems)} 个名称词干")

    def _extract_materials(self):
        """
        从词干中提取材质词汇

        策略：找出含有管材/材质关键词的词干，提取其中的材质部分
        例："室内给水钢塑复合管(螺纹连接)" → "钢塑复合管"
        """
        # 管材类：包含"管"字的词干
        pipe_patterns = [
            # 从词干中提取"xx管"模式
            r'([\u4e00-\u9fa5A-Za-z]{2,}管)',
        ]

        pipe_materials = set()
        for stem in self.stems:
            for pattern in pipe_patterns:
                matches = re.findall(pattern, stem)
                for m in matches:
                    # 过滤太通用的词
                    if m not in ("管", "的管", "水管", "风管", "气管") and len(m) >= 3:
                        # 去掉前缀位置词（室内/室外/给水/排水等）
                        clean = re.sub(r'^(室内|室外|给水|排水|热水|冷水|消防|采暖|通风)', '', m)
                        if len(clean) >= 3:
                            pipe_materials.add(clean)
                        if len(m) >= 4:
                            pipe_materials.add(m)

        # 电缆材质
        cable_materials = set()
        for stem in self.stems:
            if "电缆" in stem:
                # 提取"xx电缆"
                cable_match = re.findall(r'([\u4e00-\u9fa5]{2,}电缆)', stem)
                for m in cable_match:
                    if len(m) >= 4:
                        cable_materials.add(m)

        self.materials = pipe_materials | cable_materials
        logger.info(f"  提取到 {len(self.materials)} 个材质词汇")

    def _extract_connections(self):
        """
        从词干中提取连接方式

        策略：找括号中的连接方式描述
        例："钢塑复合管(螺纹连接)" → "螺纹连接"
        """
        connections = set()
        for stem in self.stems:
            # 括号中的连接方式
            paren_match = re.findall(r'\(([^)]*连接[^)]*)\)', stem)
            for m in paren_match:
                connections.add(m)

            # 括号中的接口方式
            paren_match = re.findall(r'\(([^)]*接口[^)]*)\)', stem)
            for m in paren_match:
                connections.add(m)

        self.connections = connections
        logger.info(f"  提取到 {len(self.connections)} 个连接方式")

    def _extract_equipment(self):
        """
        提取设备类型（去重后的词干就是设备类型列表）

        策略：章节(sheet名)可以做大类，词干做设备具体名称
        """
        # 对词干进行进一步清洗，提取设备名称核心部分
        equipment = set()
        for stem in self.stems:
            # 去掉连接方式括号部分
            clean = re.sub(r'\([^)]*\)', '', stem).strip()
            # 去掉前缀位置词
            clean = re.sub(r'^(室内|室外|户内|户外)', '', clean).strip()
            if len(clean) >= 3:
                equipment.add(clean)

        self.equipment = equipment

    def update_jieba_dict(self):
        """
        用提取到的词汇更新jieba专业词典

        不会覆盖手动维护的词条，只添加新词
        """
        dict_path = config.ENGINEERING_DICT_PATH
        dict_path.parent.mkdir(parents=True, exist_ok=True)

        # 读取现有词典
        existing_words = set()
        if dict_path.exists():
            with open(dict_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    word = line.split()[0]
                    if word:
                        existing_words.add(word)

        # 添加新词
        new_words = []
        for term in sorted(self.all_terms):
            if term not in existing_words and len(term) >= 2:
                new_words.append(term)

        if new_words:
            with open(dict_path, "a", encoding="utf-8") as f:
                f.write(f"\n# === 以下词汇从定额库自动提取 ===\n")
                for word in new_words:
                    f.write(f"{word} 5 n\n")

            logger.info(f"jieba词典已更新: 新增 {len(new_words)} 个词条")
        else:
            logger.info("jieba词典无需更新，所有词汇已存在")

        return new_words

    def get_stem_index(self) -> dict:
        """
        返回词干索引，供搜索时使用

        返回:
            {词干: [quota_id列表]}
            例：{"干式变压器安装": ["C4-1-9", "C4-1-10", ...]}
        """
        return self.stems

    def _save_vocab_cache(self):
        """
        保存提取的材质和连接方式到缓存文件
        text_parser 会自动读取这个文件来增强材质/连接方式识别
        """
        cache_path = Path(__file__).parent.parent / "data" / "dict" / "extracted_vocab.txt"
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                prefix=f"{cache_path.stem}_tmp_",
                dir=str(cache_path.parent),
                encoding="utf-8",
                delete=False,
            ) as f:
                tmp_path = f.name
                f.write("[materials]\n")
                for m in sorted(self.materials):
                    f.write(f"{m}\n")
                f.write("\n[connections]\n")
                for c in sorted(self.connections):
                    f.write(f"{c}\n")
            os.replace(tmp_path, cache_path)
        finally:
            if tmp_path and Path(tmp_path).exists():
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

        logger.info(f"提取词汇缓存已保存: {cache_path}")

    def find_matching_stems(self, query: str) -> list[str]:
        """
        从查询文本中找到匹配的定额词干

        参数:
            query: 清单描述文本

        返回:
            匹配到的词干列表（按长度降序，长词优先）
        """
        matched = []
        for stem in self.stems:
            # 检查词干（或其核心部分）是否在查询文本中
            # 去掉括号部分的词干核心
            core = re.sub(r'\([^)]*\)', '', stem).strip()
            if len(core) >= 3 and core in query:
                matched.append(stem)

        # 按词干长度降序排列（长词更精确）
        matched.sort(key=len, reverse=True)
        return matched


# ================================================================
# 命令行入口：执行反向提取
# ================================================================

if __name__ == "__main__":
    extractor = VocabExtractor()
    stats = extractor.extract_all()

    # 打印提取结果
    logger.info(f"\n=== 材质词汇 ({len(stats['materials'])}个) ===")
    for m in stats["materials"][:30]:
        # 找到使用这个材质的定额词干示例
        examples = [s for s in extractor.stems if m in s][:2]
        logger.info(f"  {m:20s} 例: {', '.join(examples[:2])}")

    logger.info(f"\n=== 连接方式 ({len(stats['connections'])}个) ===")
    for c in stats["connections"]:
        logger.info(f"  {c}")

    # 更新jieba词典
    new_words = extractor.update_jieba_dict()
    if new_words:
        logger.info(f"\n新增词条示例: {new_words[:20]}")
