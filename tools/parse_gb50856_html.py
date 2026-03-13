"""
从造价HOME的HTML页面解析GB/T 50856-2024全部清单项目特征数据。

输入：output/temp/zaojiahome_anzhuang.html（造价HOME在线版HTML）
输出：data/bill_features_2024.json（结构化的项目特征数据库）

数据结构：
{
  "version": "GB/T 50856-2024",
  "items": [
    {
      "code": "030101001",        # 9位项目编码
      "name": "机床",              # 项目名称
      "features": ["名称", "型号", "规格", ...],  # 项目特征列表
      "unit": "台",               # 计量单位
      "calc_rule": "按设计图示数量计算",  # 工程量计算规则
      "work_content": ["本体安装", "地脚螺栓孔灌浆", ...],  # 工作内容列表
      "section": "A.1",           # 所属节号
      "section_name": "切削设备安装",  # 所属节名称
      "appendix": "A",            # 所属附录
      "appendix_name": "机械设备安装工程",  # 附录名称
      "table_code": "030101"      # 表格编码（6位）
    },
    ...
  ]
}
"""

import re
import json
import sys
from pathlib import Path
from html.parser import HTMLParser


class TableExtractor(HTMLParser):
    """从HTML中提取所有<table>的内容，保留行列结构和rowspan信息。"""

    def __init__(self):
        super().__init__()
        self.tables = []          # 所有表格
        self.current_table = None  # 当前表格的行列数据
        self.current_row = None    # 当前行
        self.current_cell = None   # 当前单元格文本
        self.current_rowspan = 1   # 当前单元格的rowspan
        self.in_table = False
        self.in_td = False
        self.in_h2 = False
        self.in_h3 = False
        self.in_b = False

        # 上下文追踪（用于关联附录/节信息）
        self.context_sections = []  # [(h2_text, h3_text, b_text), ...]
        self.current_h2 = ""
        self.current_h3 = ""
        self.current_b = ""
        self._text_buf = ""

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if tag == "table":
            self.in_table = True
            self.current_table = {
                "rows": [],
                "h2": self.current_h2,
                "h3": self.current_h3,
                "b": self.current_b,
            }
        elif tag == "tr" and self.in_table:
            self.current_row = []
        elif tag == "td" and self.in_table:
            self.in_td = True
            self.current_cell = ""
            self.current_rowspan = int(attrs_dict.get("rowspan", 1))
        elif tag == "br" and self.in_td:
            # <br> 在单元格内作为换行分隔符
            self.current_cell += "\n"
        elif tag == "h2":
            self.in_h2 = True
            self._text_buf = ""
        elif tag == "h3":
            self.in_h3 = True
            self._text_buf = ""
        elif tag == "b" and not self.in_table:
            self.in_b = True
            self._text_buf = ""

    def handle_endtag(self, tag):
        if tag == "table" and self.in_table:
            self.in_table = False
            if self.current_table and self.current_table["rows"]:
                self.tables.append(self.current_table)
            self.current_table = None
        elif tag == "tr" and self.in_table and self.current_row is not None:
            self.current_table["rows"].append(self.current_row)
            self.current_row = None
        elif tag == "td" and self.in_td:
            self.in_td = False
            self.current_row.append({
                "text": self.current_cell.strip(),
                "rowspan": self.current_rowspan,
            })
        elif tag == "h2" and self.in_h2:
            self.in_h2 = False
            self.current_h2 = self._text_buf.strip()
        elif tag == "h3" and self.in_h3:
            self.in_h3 = False
            self.current_h3 = self._text_buf.strip()
        elif tag == "b" and self.in_b:
            self.in_b = False
            self.current_b = self._text_buf.strip()

    def handle_data(self, data):
        if self.in_td:
            self.current_cell += data
        elif self.in_h2 or self.in_h3 or self.in_b:
            self._text_buf += data


def _parse_list_text(text: str) -> list[str]:
    """把 '1.名称\n2.型号\n3.规格' 这样的文本解析为列表。"""
    if not text:
        return []
    items = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去掉开头的序号（如 "1." "2." "1、" 等）
        cleaned = re.sub(r"^\d+[\.\、\．]\s*", "", line).strip()
        if cleaned:
            items.append(cleaned)
    # 如果没有序号格式，整体作为一项
    if not items and text.strip():
        items = [text.strip()]
    return items


def _extract_table_code(b_text: str) -> str:
    """从表格标题中提取6位编码，如 '表 A.1.1切削设备安装(编码：030101)' → '030101'。"""
    m = re.search(r"编码[：:]\s*(\d{6})", b_text)
    return m.group(1) if m else ""


def _extract_appendix_info(h2_text: str) -> tuple[str, str]:
    """从h2文本中提取附录字母和名称。
    '附录 A机械设备安装工程' → ('A', '机械设备安装工程')
    """
    m = re.search(r"附录\s*([A-Z])\s*(.*)", h2_text)
    if m:
        return m.group(1), m.group(2).strip()
    return "", h2_text


def _extract_section_info(h3_text: str) -> tuple[str, str]:
    """从h3文本中提取节号和名称。
    'A.1 切削设备安装' → ('A.1', '切削设备安装')
    """
    m = re.match(r"([A-Z]\.\d+)\s*(.*)", h3_text)
    if m:
        return m.group(1), m.group(2).strip()
    return "", h3_text


def resolve_rowspan(rows):
    """处理rowspan：把跨行的单元格值向下填充，使每行都有完整的6列数据。

    HTML表格中，如果某个td有rowspan=3，那么下面2行在该列位置不会有td。
    需要把跨行单元格的值填充到后续行中。
    """
    if not rows:
        return []

    # 先构建一个二维网格，处理rowspan
    max_cols = max(len(r) for r in rows)
    grid = []
    # pending[col] = (text, remaining_rows)
    pending = {}

    for row_idx, row in enumerate(rows):
        grid_row = []
        col_idx = 0
        cell_idx = 0

        while col_idx < max_cols:
            # 检查是否有pending的rowspan填充
            if col_idx in pending and pending[col_idx][1] > 0:
                grid_row.append(pending[col_idx][0])
                pending[col_idx] = (pending[col_idx][0], pending[col_idx][1] - 1)
                if pending[col_idx][1] == 0:
                    del pending[col_idx]
                col_idx += 1
            elif cell_idx < len(row):
                cell = row[cell_idx]
                grid_row.append(cell["text"])
                if cell["rowspan"] > 1:
                    pending[col_idx] = (cell["text"], cell["rowspan"] - 1)
                cell_idx += 1
                col_idx += 1
            else:
                grid_row.append("")
                col_idx += 1

        grid.append(grid_row)

    return grid


def parse_html(html_path: str) -> list[dict]:
    """解析HTML文件，提取所有清单项目特征数据。"""

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # 解析HTML提取表格
    parser = TableExtractor()
    parser.feed(html)

    print(f"共找到 {len(parser.tables)} 个表格")

    items = []
    skipped_tables = 0

    for table in parser.tables:
        rows = table["rows"]
        h2 = table["h2"]
        h3 = table["h3"]
        b_text = table["b"]

        # 跳过非数据表格（如目录表等）
        if not rows or len(rows) < 2:
            skipped_tables += 1
            continue

        # 检查表头行
        header = rows[0]
        header_texts = [c["text"] for c in header]
        if "项目编码" not in header_texts:
            skipped_tables += 1
            continue

        # 提取上下文信息
        appendix_letter, appendix_name = _extract_appendix_info(h2)
        section_code, section_name = _extract_section_info(h3)
        table_code = _extract_table_code(b_text)

        # 判断表格类型：标准6列 vs 措施项目4列
        num_header_cols = len(header_texts)
        is_measure_table = (num_header_cols == 4 and "项目特征" not in header_texts)

        # 处理rowspan，得到完整的网格
        data_rows = rows[1:]  # 跳过表头
        grid = resolve_rowspan(data_rows)

        for row in grid:
            if len(row) < 3:
                continue

            code = row[0].strip()
            # 验证编码格式（9位数字，03开头）
            if not re.match(r"^03\d{7}$", code):
                continue

            name = row[1].strip()

            # 措施项目表（4列：编码/名称/单位/工作内容，无项目特征和计算规则）
            if is_measure_table:
                features_text = ""
                unit = row[2].strip() if len(row) > 2 else ""
                calc_rule = ""
                work_content_text = row[3].strip() if len(row) > 3 else ""
            elif len(row) >= 6:
                # 智能列对齐：有些行缺少"项目特征"列（HTML中根本没有这个td），
                # 导致后面的列（单位/计算规则/工作内容）左移一位。
                # 检测方法：如果row[2]看起来像计量单位而不是项目特征，说明特征列缺失。
                known_units = {"台", "m", "m²", "个", "组", "套", "座", "条", "根",
                              "只", "副", "块", "把", "支", "对", "付", "樘", "处",
                              "项", "面", "系统", "回路", "kW", "kg", "t", "km",
                              "10m", "100m", "m³", "元"}
                col2 = row[2].strip()
                # 如果第3列（本该是项目特征）看起来像计量单位，说明特征列被跳过
                if col2 in known_units:
                    features_text = ""
                    unit = col2
                    calc_rule = row[3].strip()
                    work_content_text = row[4].strip()
                else:
                    features_text = col2
                    unit = row[3].strip()
                    calc_rule = row[4].strip()
                    work_content_text = row[5].strip()
            elif len(row) == 5:
                # 只有5列：code, name, unit, calc_rule, work（缺项目特征）
                features_text = ""
                unit = row[2].strip()
                calc_rule = row[3].strip()
                work_content_text = row[4].strip()
            else:
                continue

            item = {
                "code": code,
                "name": name,
                "features": _parse_list_text(features_text),
                "unit": unit,
                "calc_rule": calc_rule,
                "work_content": _parse_list_text(work_content_text),
                "section": section_code,
                "section_name": section_name,
                "appendix": appendix_letter,
                "appendix_name": appendix_name,
                "table_code": table_code,
            }
            items.append(item)

    print(f"跳过非数据表格: {skipped_tables}")
    return items


def main():
    # 输入输出路径
    project_root = Path(__file__).resolve().parent.parent
    html_path = project_root / "output" / "temp" / "zaojiahome_anzhuang.html"
    output_path = project_root / "data" / "bill_features_2024.json"

    if not html_path.exists():
        print(f"错误: HTML文件不存在: {html_path}")
        print("请先下载造价HOME的安装清单页面")
        sys.exit(1)

    print(f"解析: {html_path}")
    items = parse_html(str(html_path))

    # 后处理：清理单位字段中的换行和序号
    for item in items:
        unit = item["unit"]
        # 去掉序号格式（如 "1.台\n2.组" → "台/组"）
        if "\n" in unit:
            parts = []
            for part in unit.split("\n"):
                part = re.sub(r"^\d+[\.\、\．]\s*", "", part.strip())
                if part:
                    parts.append(part)
            item["unit"] = "/".join(parts)
        # 去掉多余空白
        item["unit"] = re.sub(r"\s+", "", item["unit"])

    # 构建输出数据
    result = {
        "version": "GB/T 50856-2024",
        "source": "造价HOME (zaojiahome.com)",
        "description": "通用安装工程工程量计算标准 - 项目特征数据库",
        "total_items": len(items),
        "items": items,
    }

    # 统计各附录的项目数
    appendix_stats = {}
    for item in items:
        key = f"附录{item['appendix']} {item['appendix_name']}"
        appendix_stats[key] = appendix_stats.get(key, 0) + 1

    print(f"\n=== 解析结果 ===")
    print(f"总清单项目数: {len(items)}")
    print(f"\n各附录分布:")
    for k, v in sorted(appendix_stats.items()):
        print(f"  {k}: {v}条")

    # 保存
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n已保存到: {output_path}")
    print(f"文件大小: {output_path.stat().st_size / 1024:.1f} KB")

    # 输出几个示例
    print(f"\n=== 示例数据 ===")
    for item in items[:3]:
        print(f"\n{item['code']} {item['name']}")
        print(f"  项目特征: {item['features']}")
        print(f"  单位: {item['unit']}")
        print(f"  计算规则: {item['calc_rule']}")
        print(f"  工作内容: {item['work_content']}")

    # 找几个高频附录的示例
    print(f"\n=== 高频附录示例 ===")
    for target in ["D.12", "J.1", "K.1", "G.2"]:
        for item in items:
            if item["section"] == target:
                print(f"\n{item['code']} {item['name']} ({item['section']} {item['section_name']})")
                print(f"  项目特征: {item['features']}")
                print(f"  工作内容: {item['work_content']}")
                break


if __name__ == "__main__":
    main()
