# J-Batch1 Findings：MergedCell 写入崩溃

## 问题描述

保结构回写模式下，`_write_bill_extra_info()` 直接对 J~O 列写值；当目标格是 `MergedCell`（合并区域中非左上主格）时，openpyxl 禁止写入并抛 `AttributeError: 'MergedCell' object attribute 'value' is read-only`，导致该 sheet 后续全部中断，整份输出失败。

## 复现证据

命令：
```
python tools/jarvis_pipeline.py "data/reference/北京/北京通州数据中心-1#2#精密空调系统.xlsx" --province "北京市建设工程施工消耗量标准(2024)" --quiet
```

结果：退出码 1，堆栈最终落点 `src/output_writer.py:608`：
```
AttributeError: 'MergedCell' object attribute 'value' is read-only
```

## 影响范围

- 任何含合并单元格的原始清单 Excel，在保结构回写时都会崩溃
- 影响 J列（推荐度）、K列（说明）、L/M/N列（备选定额）、O列（主材）的写入
- 同样影响 `_write_alternative_cells()`、`_write_no_match_row()`、`_apply_row_style()` 对合并单元格的操作

## 根因

openpyxl 中 `ws.cell(row, col)` 对合并区域的非主格返回 `MergedCell` 对象，该对象的 `value`、`font`、`fill`、`border`、`alignment` 属性均为只读，赋值会抛异常。
