"""
山东2025定额交底培训资料导入工具

功能：
1. 读取大TXT文件（约12000行）
2. 按"册"和"章"拆分为独立文本片段
3. 导入到规则知识库（rule_knowledge），附带正确的省份/章节元信息
4. 同时向量化，支持后续语义检索

用法：
    python tools/import_shandong_rules.py "C:\path\to\培训资料.txt"
"""

import re
import sys
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from src.rule_knowledge import RuleKnowledge


def parse_volumes_and_chapters(text: str) -> list[dict]:
    """
    解析培训资料文本，按册+章拆分为结构化片段

    返回: [
        {
            "volume": "第四册 电气设备安装工程",
            "chapter": "概述",
            "content": "正文内容..."
        },
        ...
    ]
    """
    lines = text.split('\n')

    # 跳过目录部分（目录行包含大量·····分隔符）
    # 找到"编制概况"正文开始的位置（目录之后的第一个实质内容）
    content_start = 0
    for i, line in enumerate(lines):
        # 目录结束的标志：行不含·····且不是空行，且前面有过含·····的行
        if '编制概况' in line and '·' not in line and i > 50:
            content_start = i
            break

    if content_start == 0:
        # 备用方案：跳过前150行（通常是目录）
        content_start = 150

    # 册的匹配模式（支持行首和行中间出现）
    volume_pattern = re.compile(r'(第[一二三四五六七八九十]+册\s+\S+工程)')
    # 章的匹配模式（支持行首和行中间出现）
    chapter_pattern = re.compile(r'(第[一二三四五六七八九十\d]+章\s+[^\n]{2,30})')

    segments = []
    current_volume = "编制概况"
    current_chapter = ""
    current_lines = []

    def _save_current():
        """保存当前积累的内容为一个片段"""
        nonlocal current_lines
        if current_lines:
            content = '\n'.join(current_lines).strip()
            if len(content) >= 30:  # 过滤掉太短的片段
                segments.append({
                    "volume": current_volume,
                    "chapter": current_chapter,
                    "content": content
                })
        current_lines = []

    for i in range(content_start, len(lines)):
        line = lines[i].strip()

        # 跳过纯页码行（如 "89\n90"）
        if re.match(r'^\d{1,4}$', line):
            continue

        # 检测册标题（可能在行首或行中间）
        vol_match = volume_pattern.search(line)
        if vol_match:
            # 册标题前面可能有内容，先保存
            before = line[:vol_match.start()].strip()
            if before:
                current_lines.append(before)
            _save_current()
            current_volume = vol_match.group(1).strip()
            current_chapter = "概述"  # 每册开头默认是概述
            # 册标题后面可能有内容
            after = line[vol_match.end():].strip()
            if after:
                current_lines.append(after)
            continue

        # 检测章标题（可能在行首或行中间）
        ch_match = chapter_pattern.search(line)
        if ch_match:
            # 章标题前面可能有内容，先保存到当前章
            before = line[:ch_match.start()].strip()
            if before:
                current_lines.append(before)
            _save_current()
            current_chapter = ch_match.group(1).strip()
            # 章标题后面可能有内容
            after = line[ch_match.end():].strip()
            if after:
                current_lines.append(after)
            continue

        # 普通内容行
        if line:
            current_lines.append(line)

    # 最后一段
    _save_current()

    return segments


def import_to_knowledge(segments: list[dict], province: str = "山东2025"):
    """
    将拆分后的片段导入规则知识库

    参数:
        segments: parse_volumes_and_chapters 的返回值
        province: 省份标识
    """
    rk = RuleKnowledge(province)

    total_added = 0
    total_skipped = 0
    total_segments = 0

    for seg in segments:
        volume = seg["volume"]
        chapter = seg["chapter"]
        content = seg["content"]

        # 组合章节标签：如"第四册 电气设备安装工程 / 第六章 发电机电动机检查接线"
        chapter_label = f"{volume} / {chapter}" if chapter else volume

        # 调用 rule_knowledge 的内部方法进行分段和存储
        # 由于 import_file 只接受文件路径，我们直接操作数据库
        sub_segments = rk._split_text(content, max_len=500)

        import hashlib
        conn = rk._connect()
        try:
            cursor = conn.cursor()
            for sub_seg in sub_segments:
                total_segments += 1
                content_hash = hashlib.md5(
                    f"{province}:安装:{sub_seg}".encode()
                ).hexdigest()

                # 去重检查
                cursor.execute(
                    "SELECT id FROM rules WHERE content_hash = ?",
                    (content_hash,)
                )
                if cursor.fetchone():
                    total_skipped += 1
                    continue

                keywords = rk._extract_keywords(sub_seg)

                cursor.execute("""
                    INSERT INTO rules (province, specialty, chapter, section, content,
                                       content_hash, source_file, keywords)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (province, "安装", chapter_label, "", sub_seg,
                      content_hash, "山东省安装工程消耗量定额交底培训资料.txt",
                      " ".join(keywords)))

                total_added += 1

            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  错误: {e}")
        finally:
            conn.close()

    print(f"\n=== 导入完成 ===")
    print(f"  总片段数: {total_segments}")
    print(f"  新增: {total_added}")
    print(f"  已存在(跳过): {total_skipped}")

    # 更新向量索引
    if total_added > 0:
        print(f"\n正在更新向量索引...")
        rk._update_vector_index()
        print(f"向量索引更新完成")

    return {"total": total_segments, "added": total_added, "skipped": total_skipped}


def main():
    if len(sys.argv) < 2:
        print("用法: python tools/import_shandong_rules.py <培训资料文件路径>")
        print("示例: python tools/import_shandong_rules.py \"C:\\Users\\...\\培训资料.txt\"")
        sys.exit(1)

    file_path = Path(sys.argv[1])
    if not file_path.exists():
        print(f"文件不存在: {file_path}")
        sys.exit(1)

    # 读取文件
    print(f"读取文件: {file_path}")
    try:
        text = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = file_path.read_text(encoding="gbk")

    print(f"文件大小: {len(text)} 字符, {text.count(chr(10))} 行")

    # 第1步：按册+章拆分
    print(f"\n第1步: 按册+章拆分...")
    segments = parse_volumes_and_chapters(text)

    # 统计
    volumes = set(s["volume"] for s in segments)
    print(f"  识别到 {len(volumes)} 个册, 共 {len(segments)} 个章节片段")
    for vol in sorted(volumes):
        vol_segs = [s for s in segments if s["volume"] == vol]
        chapters = set(s["chapter"] for s in vol_segs)
        total_chars = sum(len(s["content"]) for s in vol_segs)
        print(f"    {vol}: {len(chapters)}章, {total_chars}字")

    # 第2步：导入规则知识库
    print(f"\n第2步: 导入规则知识库...")
    stats = import_to_knowledge(segments, province="山东2025")

    return stats


if __name__ == "__main__":
    main()
