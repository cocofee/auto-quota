# -*- coding: utf-8 -*-
"""
清单扫描器（GUI版）— 拖入文件快速摸底清单数量和专业分布

双击运行或: python tools/bill_scanner_gui.py
"""

import sys
import os
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QTableWidget, QTableWidgetItem,
    QProgressBar, QHeaderView, QGroupBox, QMessageBox, QSplitter,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QDragEnterEvent, QDropEvent, QColor, QBrush

from tools.bill_scanner import (
    collect_excel_files, scan_one_file,
    save_excel_report, MAJOR_CATEGORY, BOOK_NAMES,
)


def _classify_by_sheet(sheet_name: str) -> str:
    """根据sheet名称轻量判断大类，不加载模型"""
    s = sheet_name.lower()
    # 电力相关
    power_kws = ["电力", "输电", "变电", "配网", "输变电", "线路工程",
                 "架空线", "电缆敷设", "杆塔", "高压", "低压"]
    # 石油石化
    petro_kws = ["石油", "石化", "炼油", "催化", "裂化", "检修",
                 "停工检修", "化工", "管廊", "罐区"]
    # 光伏
    solar_kws = ["光伏", "太阳能", "组件", "逆变器", "汇流箱",
                 "箱变", "升压站"]
    # 安装相关
    install_kws = ["安装", "给排水", "给水", "排水", "采暖", "燃气",
                   "电气", "强电", "弱电", "配电", "照明", "动力",
                   "消防", "喷淋", "火灾", "报警",
                   "通风", "空调", "暖通",
                   "智能",
                   "刷油", "防腐", "绝热", "保温",
                   "设备", "管道", "仪表", "通信"]
    # 土建相关
    civil_kws = ["土建", "建筑", "装饰", "装修", "结构", "主体",
                 "基础", "屋面", "门窗", "幕墙", "砌筑",
                 "钢结构", "混凝土", "钢筋", "模板",
                 "楼地面", "天棚", "墙面", "涂料", "油漆"]
    # 市政相关
    muni_kws = ["市政", "道路", "桥梁", "排水管", "路面", "路基",
                "管网", "检查井", "路灯"]
    # 园林相关
    garden_kws = ["园林", "绿化", "苗木", "景观", "种植"]

    for kw in power_kws:
        if kw in s:
            return "电力工程"
    for kw in petro_kws:
        if kw in s:
            return "石油石化"
    for kw in solar_kws:
        if kw in s:
            return "光伏工程"
    for kw in install_kws:
        if kw in s:
            return "安装工程"
    for kw in civil_kws:
        if kw in s:
            return "土建装饰"
    for kw in muni_kws:
        if kw in s:
            return "市政工程"
    for kw in garden_kws:
        if kw in s:
            return "园林绿化"
    return "其他"


class ScanWorker(QThread):
    """后台扫描线程，避免界面卡死"""
    progress = pyqtSignal(int, int, str)       # 当前, 总数, 文件名
    file_done = pyqtSignal(dict)               # 单文件结果
    finished = pyqtSignal(list, dict, float)    # 所有文件结果, 分类结果, 耗时秒
    error = pyqtSignal(str)                    # 错误信息

    def __init__(self, input_path: str):
        super().__init__()
        self.input_path = input_path

    def run(self):
        try:
            t0 = time.time()

            # 收集文件
            self.progress.emit(0, 0, "正在收集文件...")
            excel_files, temp_dirs = collect_excel_files(self.input_path)
            if not excel_files:
                self.error.emit("没有找到Excel文件")
                return

            # 初始化读取器
            from src.bill_reader import BillReader
            reader = BillReader()

            # 逐文件扫描
            file_results = []
            all_items = []
            total = len(excel_files)

            for i, fpath in enumerate(excel_files):
                fname = os.path.basename(fpath)
                self.progress.emit(i + 1, total, fname)
                try:
                    result = scan_one_file(fpath, reader)
                except Exception as e:
                    result = {"file": fname, "path": fpath, "items": [], "error": str(e)[:200]}
                file_results.append(result)
                all_items.extend(result["items"])

            # 去重 + 按sheet名轻量分类
            self.progress.emit(total, total, f"正在去重（{len(all_items)}条）...")
            seen = set()
            unique_count = 0
            by_major = {}  # 大类计数

            for name, desc, sheet, code in all_items:
                key = (name, desc)
                if key not in seen:
                    seen.add(key)
                    unique_count += 1
                    major = _classify_by_sheet(sheet)
                    by_major[major] = by_major.get(major, 0) + 1

            elapsed = time.time() - t0

            classification = {
                "total": len(all_items),
                "unique": unique_count,
                "by_major": by_major,
            }

            # 清理临时目录
            import shutil
            for td in temp_dirs:
                try:
                    shutil.rmtree(td)
                except Exception:
                    pass

            self.finished.emit(file_results, classification, elapsed)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.error.emit(f"扫描出错: {e}")


class DropArea(QLabel):
    """可拖放的区域"""
    path_dropped = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(80)
        self.setText("将文件、文件夹或ZIP拖到这里\n或使用下方按钮选择")
        self.setStyleSheet("""
            QLabel {
                border: 2px dashed #aaa;
                border-radius: 8px;
                background: #f8f8f8;
                color: #666;
                font-size: 14px;
                padding: 15px;
            }
            QLabel:hover {
                border-color: #4472C4;
                background: #f0f4ff;
            }
        """)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet("""
                QLabel {
                    border: 2px dashed #4472C4;
                    border-radius: 8px;
                    background: #e8f0fe;
                    color: #333;
                    font-size: 14px;
                    padding: 15px;
                }
            """)

    def dragLeaveEvent(self, event):
        self.setStyleSheet("""
            QLabel {
                border: 2px dashed #aaa;
                border-radius: 8px;
                background: #f8f8f8;
                color: #666;
                font-size: 14px;
                padding: 15px;
            }
        """)

    def dropEvent(self, event: QDropEvent):
        self.dragLeaveEvent(event)
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            self.setText(path)
            self.path_dropped.emit(path)


# 状态颜色
COLOR_OK = QColor("#2E7D32")       # 深绿
COLOR_EMPTY = QColor("#9E9E9E")    # 灰色
COLOR_ERROR = QColor("#C62828")    # 红色
COLOR_OK_BG = QColor("#E8F5E9")    # 浅绿背景
COLOR_ERR_BG = QColor("#FFEBEE")   # 浅红背景


class BillScannerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("清单扫描器 — 快速摸底")
        self.setMinimumSize(800, 700)
        self.resize(850, 750)

        self.file_results = []
        self.classification = None
        self.input_path = ""
        self.worker = None

        self._build_ui()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setSpacing(8)
        layout.setContentsMargins(12, 12, 12, 12)

        # --- 拖放区域 ---
        self.drop_area = DropArea()
        self.drop_area.path_dropped.connect(self._on_path_selected)
        layout.addWidget(self.drop_area)

        # --- 按钮行 ---
        btn_row = QHBoxLayout()
        self.btn_file = QPushButton("选择文件")
        self.btn_file.clicked.connect(self._pick_file)
        self.btn_folder = QPushButton("选择文件夹")
        self.btn_folder.clicked.connect(self._pick_folder)
        self.btn_scan = QPushButton("开始扫描")
        self.btn_scan.setEnabled(False)
        self.btn_scan.clicked.connect(self._start_scan)
        self.btn_scan.setStyleSheet("""
            QPushButton {
                background: #4472C4; color: white;
                font-size: 14px; font-weight: bold;
                padding: 8px 24px; border-radius: 4px;
            }
            QPushButton:hover { background: #3561b0; }
            QPushButton:disabled { background: #ccc; }
        """)
        btn_row.addWidget(self.btn_file)
        btn_row.addWidget(self.btn_folder)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_scan)
        layout.addLayout(btn_row)

        # --- 进度条 ---
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)

        # --- 汇总信息 ---
        self.summary_group = QGroupBox("扫描结果")
        self.summary_group.setVisible(False)
        summary_layout = QVBoxLayout(self.summary_group)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setFont(QFont("Microsoft YaHei", 11))
        self.lbl_summary.setStyleSheet("padding: 5px;")
        self.lbl_summary.setWordWrap(True)
        summary_layout.addWidget(self.lbl_summary)

        layout.addWidget(self.summary_group)

        # --- 下半部分：大类分布 + 文件明细（左右排列） ---
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左：大类分布表
        self.major_group = QGroupBox("专业分布")
        self.major_group.setVisible(False)
        major_layout = QVBoxLayout(self.major_group)
        self.table_major = QTableWidget()
        self.table_major.setColumnCount(3)
        self.table_major.setHorizontalHeaderLabels(["专业大类", "条数", "占比"])
        self.table_major.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table_major.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_major.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_major.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_major.setAlternatingRowColors(True)
        self.table_major.setMaximumWidth(350)
        major_layout.addWidget(self.table_major)
        splitter.addWidget(self.major_group)

        # 右：文件明细表
        self.table_group = QGroupBox("文件明细")
        self.table_group.setVisible(False)
        table_layout = QVBoxLayout(self.table_group)
        self.table_files = QTableWidget()
        self.table_files.setColumnCount(3)
        self.table_files.setHorizontalHeaderLabels(["文件名", "清单条数", "状态"])
        self.table_files.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table_files.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents)
        self.table_files.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self.table_files.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table_files.setAlternatingRowColors(True)
        table_layout.addWidget(self.table_files)
        splitter.addWidget(self.table_group)

        # 左右比例 3:7
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 7)
        layout.addWidget(splitter, stretch=1)

        # --- 底部按钮行 ---
        bottom_row = QHBoxLayout()
        self.btn_copy = QPushButton("复制结果")
        self.btn_copy.setEnabled(False)
        self.btn_copy.clicked.connect(self._copy_result)
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background: #70AD47; color: white;
                font-size: 13px; font-weight: bold;
                padding: 6px 18px; border-radius: 4px;
            }
            QPushButton:hover { background: #5d9a3a; }
            QPushButton:disabled { background: #ccc; }
        """)
        bottom_row.addStretch()
        bottom_row.addWidget(self.btn_copy)
        layout.addLayout(bottom_row)

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择清单文件", "",
            "Excel/ZIP文件 (*.xlsx *.xls *.zip);;所有文件 (*)")
        if path:
            self._on_path_selected(path)

    def _pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if path:
            self._on_path_selected(path)

    def _on_path_selected(self, path: str):
        self.input_path = path
        self.drop_area.setText(path)
        self.btn_scan.setEnabled(True)

    def _start_scan(self):
        if not self.input_path:
            return

        # 重置界面
        self.btn_scan.setEnabled(False)
        self.btn_file.setEnabled(False)
        self.btn_folder.setEnabled(False)
        self.btn_copy.setEnabled(False)
        self.summary_group.setVisible(False)
        self.major_group.setVisible(False)
        self.table_group.setVisible(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.progress_label.setText("正在扫描...")
        self.file_results = []
        self.classification = None

        # 启动后台线程
        self.worker = ScanWorker(self.input_path)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_progress(self, current: int, total: int, filename: str):
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            self.progress_label.setText(
                f"扫描中: {current}/{total}  {filename}")
        else:
            self.progress_bar.setMaximum(0)  # 不确定进度
            self.progress_label.setText(filename)

    def _on_finished(self, file_results: list, classification: dict, elapsed: float):
        self.file_results = file_results
        self.classification = classification

        # 恢复按钮
        self.btn_scan.setEnabled(True)
        self.btn_file.setEnabled(True)
        self.btn_folder.setEnabled(True)
        self.btn_copy.setEnabled(True)
        self.progress_bar.setVisible(False)

        total = classification['total']
        unique = classification['unique']
        ok_files = sum(1 for r in file_results if r["items"])
        # 区分清单工作簿和其他表
        bill_files = sum(1 for r in file_results if r.get("source") == "standard")
        other_files = sum(1 for r in file_results if r.get("source") == "fallback")
        err_files = sum(1 for r in file_results if r.get("error"))
        empty_files = len(file_results) - ok_files - err_files
        dup_rate = (1 - unique / total) * 100 if total else 0

        # 耗时格式化
        if elapsed < 60:
            time_str = f"{elapsed:.1f}秒"
        else:
            time_str = f"{int(elapsed // 60)}分{int(elapsed % 60)}秒"

        # 汇总文字
        summary_parts = [
            f"扫描完成！  耗时: {time_str}  |  共{len(file_results)}个文件",
            f"清单工作簿: {bill_files}个  |  其他表: {other_files}个",
            f"清单总数: {total}条  |  去重后: {unique}条  |  重复率: {dup_rate:.1f}%",
        ]
        self.lbl_summary.setText("\n".join(summary_parts))
        self.summary_group.setVisible(True)
        self.progress_label.setText(f"扫描完成  耗时{time_str}")

        # --- 填充大类分布表 ---
        by_major = classification.get('by_major', {})
        if by_major:
            sorted_majors = sorted(by_major.items(), key=lambda x: -x[1])
            self.table_major.setRowCount(len(sorted_majors))
            for row, (major, count) in enumerate(sorted_majors):
                pct = count / unique * 100 if unique else 0
                # 专业名
                self.table_major.setItem(row, 0, QTableWidgetItem(major))
                # 条数（右对齐）
                cnt_item = QTableWidgetItem(str(count))
                cnt_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table_major.setItem(row, 1, cnt_item)
                # 占比
                pct_item = QTableWidgetItem(f"{pct:.1f}%")
                pct_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table_major.setItem(row, 2, pct_item)
            self.major_group.setVisible(True)

        # --- 填充文件明细表（全部文件，有效的在前，失败/空的在后） ---
        # 先按有效排前、再按条数降序
        sorted_results = sorted(
            file_results,
            key=lambda r: (-len(r["items"]), 0 if r.get("error") else 1)
        )
        self.table_files.setRowCount(len(sorted_results))
        for row, r in enumerate(sorted_results):
            count = len(r["items"])
            has_error = bool(r.get("error"))

            # 文件名
            name_item = QTableWidgetItem(r["file"])
            self.table_files.setItem(row, 0, name_item)

            # 条数
            cnt_item = QTableWidgetItem(str(count) if count > 0 else "-")
            cnt_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table_files.setItem(row, 1, cnt_item)

            # 状态+颜色（区分清单工作簿和其他表）
            if count > 0:
                src = r.get("source", "")
                if src == "standard":
                    status_item = QTableWidgetItem(f"清单表 ({count}条)")
                else:
                    status_item = QTableWidgetItem(f"其他表 ({count}条)")
                status_item.setForeground(QBrush(COLOR_OK))
            elif has_error:
                err_msg = r["error"][:30] if r.get("error") else "未知错误"
                status_item = QTableWidgetItem(f"失败: {err_msg}")
                status_item.setForeground(QBrush(COLOR_ERROR))
                # 整行浅红背景
                for col in range(3):
                    item = self.table_files.item(row, col)
                    if item:
                        item.setBackground(QBrush(COLOR_ERR_BG))
            else:
                status_item = QTableWidgetItem("无清单")
                status_item.setForeground(QBrush(COLOR_EMPTY))

            self.table_files.setItem(row, 2, status_item)
            # 失败行的状态列也要设背景
            if has_error and count == 0:
                status_item.setBackground(QBrush(COLOR_ERR_BG))

        self.table_group.setVisible(True)

    def _on_error(self, msg: str):
        self.btn_scan.setEnabled(True)
        self.btn_file.setEnabled(True)
        self.btn_folder.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setText(f"错误: {msg}")
        QMessageBox.warning(self, "扫描出错", msg)

    def _copy_result(self):
        """把扫描结果复制到剪贴板（精简版）"""
        if not self.classification:
            return
        c = self.classification
        total = c['total']
        unique = c['unique']

        # 专业分布
        by_major = c.get('by_major', {})
        major_parts = []
        for major, count in sorted(by_major.items(), key=lambda x: -x[1]):
            major_parts.append(f"{major}{count}条")
        major_str = "、".join(major_parts) if major_parts else "未分类"

        bill_files = sum(1 for r in self.file_results if r.get("source") == "standard")
        text = f"清单工作簿{bill_files}个，清单{total}条，去重后{unique}条（{major_str}）"
        QApplication.clipboard().setText(text)
        self.btn_copy.setText("已复制!")
        QTimer.singleShot(2000, lambda: self.btn_copy.setText("复制结果"))


def main():
    # 捕获全局异常，防止闪退
    import traceback

    def exception_hook(exc_type, exc_value, exc_tb):
        msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
        print(f"未捕获异常:\n{msg}")
        QMessageBox.critical(None, "程序错误", f"出现错误:\n{exc_value}\n\n详细信息已打印到终端")

    sys.excepthook = exception_hook

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # 全局字体
    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    window = BillScannerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
