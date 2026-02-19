"""
清理 extracted_vocab.txt 中 [materials] 部分的错误词条

这些词条不是真正的"材质"，而是包含了设备名称、安装方式、位置等信息。
例如"薄钢板通风管"中"薄钢板"才是材质，"通风管"是设备名，整条应移除。

过滤规则：
1. 包含"风管"（设备名称，不是材质）
2. 包含"配管"（安装方式，不是材质）
3. 包含"管道安装"/"管制作"/"管安装"（安装动作，不是材质）
4. 包含"敷设"（安装方式，不是材质）
5. 包含"布放"（安装方式，不是材质）
6. 以"式"开头（安装类型，不是材质，如"式薄壁钢管"）
7. 以"建筑物内"开头（位置描述，不是材质）
8. 包含"插头"或"连接器"（连接件，不是材质）
"""

from pathlib import Path
import os
import tempfile


def clean_vocab():
    """清理 extracted_vocab.txt 中的错误材质词条"""
    vocab_path = Path(__file__).parent.parent / "data" / "dict" / "extracted_vocab.txt"

    if not vocab_path.exists():
        print(f"错误：找不到文件 {vocab_path}")
        return

    # 读取原文件
    with open(vocab_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # 定义过滤规则
    # 包含这些词的条目要移除（设备名/安装方式，不是材质）
    bad_contains = ("风管", "配管", "管道安装", "管制作", "管安装", "敷设", "布放", "插头", "连接器")
    # 以这些词开头的条目要移除
    bad_startswith = ("式", "建筑物内")

    # 逐行处理
    output_lines = []
    section = None  # 当前所在的section
    removed_count = 0  # 统计移除了多少条

    for line in lines:
        stripped = line.strip()

        # 检测section标记
        if stripped == "[materials]":
            section = "mat"
            output_lines.append(line)
            continue
        elif stripped == "[connections]":
            section = "conn"
            output_lines.append(line)
            continue
        elif stripped.startswith("["):
            section = None
            output_lines.append(line)
            continue

        # 只过滤 [materials] 部分的词条
        if section == "mat" and stripped:
            # 检查是否匹配过滤规则
            should_remove = False

            # 规则：包含指定关键词
            for bad_word in bad_contains:
                if bad_word in stripped:
                    should_remove = True
                    break

            # 规则：以指定词开头
            if not should_remove:
                for prefix in bad_startswith:
                    if stripped.startswith(prefix):
                        should_remove = True
                        break

            if should_remove:
                removed_count += 1
                print(f"  移除: {stripped}")
                continue  # 跳过这一行，不写入输出

        # 保留该行
        output_lines.append(line)

    # 写回文件
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            prefix=f"{vocab_path.stem}_tmp_",
            dir=str(vocab_path.parent),
            encoding="utf-8",
            delete=False,
        ) as f:
            tmp_path = f.name
            f.writelines(output_lines)
        os.replace(tmp_path, vocab_path)
    finally:
        if tmp_path and Path(tmp_path).exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    print(f"\n清理完成！共移除 {removed_count} 个错误词条。")
    print(f"文件已更新: {vocab_path}")


if __name__ == "__main__":
    clean_vocab()
