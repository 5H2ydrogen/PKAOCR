from copy import deepcopy
import logging
from pathlib import Path
import traceback

from PySide6.QtCore import QEvent, Qt, QThread, Signal, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QFileDialog,
    QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QMainWindow, QPushButton, QRadioButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from .models import AppError, CATEGORIES, Invoice, Item, PAYMENTS, Submission, ValidationError, number, parse_date
from .ocr import LocalOCR
from .output import generate
from .paths import APP_ICON
from .recognition import recognize_invoice
from .validation import submission_errors, validate_file


class Worker(QThread):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action

    def run(self):
        try:
            self.succeeded.emit(self.action())
        except Exception as e:
            # 不记录异常 message、局部变量、发票文字或用户文件路径。
            frames = "; ".join(f"{Path(f.filename).name}:{f.lineno}:{f.name}" for f in traceback.extract_tb(e.__traceback__))
            logging.getLogger("pyocrpka").error("操作失败 type=%s frames=%s", type(e).__name__, frames)
            self.failed.emit(e if isinstance(e, AppError) else AppError("操作失败，请检查文件权限、磁盘空间及本地日志"))


class FileInput(QGroupBox):
    changed = Signal()
    error = Signal(str)

    def __init__(self, title: str, kind: str, multiple: bool = False):
        super().__init__(title)
        self.kind, self.multiple = kind, multiple
        self.paths: list[Path] = []
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setFixedHeight(64)
        self.list.itemDoubleClicked.connect(self.preview)
        if multiple:
            self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
            self.list.model().rowsMoved.connect(self.sync_order)
            self.list.setToolTip("拖动可调整顺序，双击可预览图片")
        layout.addWidget(self.list)
        buttons = QHBoxLayout()
        self.add_button = QPushButton("添加")
        self.add_button.clicked.connect(self.choose)
        self.add_button.setToolTip("添加材料；单文件更换前请先删除")
        buttons.addWidget(self.add_button)
        remove = QPushButton("删除")
        remove.clicked.connect(self.remove)
        buttons.addWidget(remove)
        self.count = QLabel("已选 0 个")
        buttons.addWidget(self.count)
        layout.addLayout(buttons)
        self.error_label = QLabel()
        self.error_label.setObjectName("error")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

    def set_paths(self, paths: list[Path]):
        if not self.multiple and len(paths) > 1:
            raise AppError("此材料只允许选择一个文件")
        for path in paths:
            validate_file(path, self.kind)
        self.paths = list(paths)
        self.refresh()
        self.changed.emit()

    def choose(self):
        if not self.multiple and self.paths:
            return
        filters = {"invoice": "发票 (*.pdf *.png *.jpg *.jpeg)", "image": "图片 (*.png *.jpg *.jpeg)",
                   "pdf": "PDF (*.pdf)", "png": "PNG (*.png)", "jpeg": "JPEG (*.jpg *.jpeg)"}
        if self.multiple:
            names, _ = QFileDialog.getOpenFileNames(self, "选择材料（双击已选图片可预览）", "", filters[self.kind])
        else:
            name, _ = QFileDialog.getOpenFileName(self, "选择材料", "", filters[self.kind])
            names = [name] if name else []
        if not names:
            return
        try:
            paths = self.paths + [Path(n) for n in names] if self.multiple else [Path(n) for n in names]
            self.set_paths(list(dict.fromkeys(paths)))
        except AppError as e:
            self.error_label.setText(str(e))
            self.error.emit(str(e))

    def refresh(self):
        self.list.clear()
        for path in self.paths:
            self.list.addItem(path.name)
            self.list.item(self.list.count() - 1).setToolTip(str(path))
            self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, str(path))
        if self.paths:
            self.list.setCurrentRow(len(self.paths) - 1)
        self.add_button.setEnabled(self.multiple or not self.paths)
        self.count.setText(f"已选 {len(self.paths)} 个")
        self.error_label.setText("")

    def remove(self):
        row = self.list.currentRow()
        if row >= 0:
            self.paths.pop(row)
            self.refresh()
            self.changed.emit()

    def sync_order(self, *_):
        self.paths = [Path(self.list.item(row).data(Qt.ItemDataRole.UserRole)) for row in range(self.list.count())]
        self.changed.emit()

    def preview(self):
        row = self.list.currentRow()
        if row < 0:
            return
        path = self.paths[row]
        try:
            validate_file(path, self.kind)
            if path.suffix.lower() == ".pdf":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
                return
            dialog = QDialog(self)
            dialog.setWindowTitle(path.name)
            layout = QVBoxLayout(dialog)
            label = QLabel()
            label.setPixmap(QPixmap(str(path)).scaled(850, 650, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            layout.addWidget(label)
            dialog.exec()
        except AppError as e:
            self.error.emit(str(e))


ITEM_COLUMNS = [("name", "项目名称"), ("specification", "规格型号"), ("unit", "单位"), ("quantity", "数量"),
                ("unit_price", "单价"), ("net_amount", "金额"), ("tax_rate", "税率/征收率"),
                ("tax", "税额")]

STYLE = """
QMainWindow, QScrollArea, QWidget#canvas { background: #f2f5f9; }
QWidget { color: #233149; font-size: 13px; }
QGroupBox { background: white; border: 1px solid #dce3ed; border-radius: 10px; margin-top: 12px; padding: 14px 10px 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
QLineEdit { background: white; border: 1px solid #cad4e2; border-radius: 6px; padding: 7px; min-height: 18px; }
QListWidget, QTableWidget { background: white; border: 1px solid #e0e6ef; border-radius: 5px; }
QPushButton { background: #e8eef7; border: 0; border-radius: 6px; padding: 8px 12px; }
QPushButton:hover { background: #d4e2f6; }
QPushButton:disabled { background: #edf0f4; color: #93a0b3; }
QPushButton#primary { background: #2563eb; color: white; font-weight: 600; }
QPushButton#primary:disabled { background: #9ab6ec; }
QLabel#error { color: #c92e3d; font-size: 12px; }
QLabel#muted { color: #64748b; }
QLabel#title { font-size: 25px; font-weight: 700; }
QHeaderView::section { background: #eaf0f8; padding: 6px; border: 0; }
QCheckBox, QRadioButton { spacing: 7px; padding: 3px; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PKAOCR")
        self.setWindowIcon(QIcon(str(APP_ICON)))
        self.resize(1280, 940)
        self.setMinimumSize(1000, 720)
        self.setStyleSheet(STYLE)
        self.worker: Worker | None = None
        self.engine = LocalOCR()
        self.invoice = Invoice()
        self.output_path: Path | None = None
        self.validation_started = False
        self.loading = False
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 18, 24, 18)
        heading = QHBoxLayout()
        self.logo = QLabel()
        self._logo_source = QPixmap(str(APP_ICON))
        self.logo.setFixedSize(68, 68)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.update_logo()
        heading.addWidget(self.logo)
        heading_text = QVBoxLayout()
        title = QLabel("PKAOCR")
        title.setObjectName("title")
        heading_text.addWidget(title)
        subtitle = QLabel("材料仅保存在本机")
        subtitle.setObjectName("muted")
        heading_text.addWidget(subtitle)
        heading.addLayout(heading_text, 1)
        layout.addLayout(heading)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.canvas = QWidget()
        self.canvas.setObjectName("canvas")
        columns = QHBoxLayout(self.canvas)
        columns.setContentsMargins(0, 0, 0, 0)
        left, right = QVBoxLayout(), QVBoxLayout()
        columns.addLayout(left, 4)
        columns.addLayout(right, 6)
        scroll.setWidget(self.canvas)
        layout.addWidget(scroll, 1)
        self.error_labels = {}
        base = QGroupBox("01  报销信息 · 全部必填")
        form = QFormLayout(base)
        self.work_order, self.name = QLineEdit(), QLineEdit()
        self.work_order.setPlaceholderText("例如 20260928-013")
        self.name.setPlaceholderText("实际支出人姓名")
        for key, text, widget in [("work_order", "工单号", self.work_order), ("name", "姓名", self.name)]:
            wrap = QVBoxLayout()
            wrap.addWidget(widget)
            wrap.addWidget(self.make_error(key))
            form.addRow(text, wrap)
            widget.textChanged.connect(self.inputs_changed)
        self.payment_group = QButtonGroup(self)
        payment_layout = QHBoxLayout()
        for text in PAYMENTS:
            button = QRadioButton(text)
            self.payment_group.addButton(button)
            payment_layout.addWidget(button)
        self.payment_group.buttonToggled.connect(self.inputs_changed)
        form.addRow("付款方式", payment_layout)
        form.addRow("", self.make_error("payment"))
        self.category_group = QButtonGroup(self)
        category_layout = QGridLayout()
        for i, text in enumerate(CATEGORIES):
            button = QRadioButton(text)
            self.category_group.addButton(button)
            category_layout.addWidget(button, i // 4, i % 4)
        self.category_group.buttonToggled.connect(self.inputs_changed)
        form.addRow("类别", category_layout)
        form.addRow("", self.make_error("category"))
        left.addWidget(base)
        self.files = {}
        for key, text, kind, multi in [("invoice_pdf", "02  发票 · PDF / PNG / JPG", "invoice", False),
                                       ("verification_png", "发票查验 · PNG · 选填", "png", False),
                                       ("payments", "付款凭证 · PNG / JPG / JPEG · 拖动排序", "image", True),
                                       ("photos", "实物 · PNG / JPG / JPEG · 选填 · 拖动排序", "image", True)]:
            control = FileInput(text, kind, multi)
            self.files[key] = control
            self.error_labels[key] = control.error_label
            control.changed.connect(self.inputs_changed)
            control.error.connect(self.set_status_error)
            left.addWidget(control)
        self.files["invoice_pdf"].changed.connect(self.invoice_file_changed)
        left.addStretch()
        editor = QGroupBox("03  发票识别结果 · 所有字段均可修改")
        editor_layout = QVBoxLayout(editor)
        hint = QLabel("空白表示未识别，请对照原票核对。单价、金额按发票原值填写，输出含税总价自动计算。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        editor_layout.addWidget(hint)
        self.source_label = QLabel("等待上传发票，也可人工填写")
        editor_layout.addWidget(self.source_label)
        metadata = QFormLayout()
        self.invoice_fields = {}
        for key, text in [("number", "发票号码 *"), ("issued_on", "开票日期"), ("total", "价税合计（元）"),
                          ("code", "发票代码"), ("check_last6", "校验码后六位"),
                          ("net_total", "未税合计（元）"), ("tax_total", "税额合计（元）")]:
            control = QLineEdit()
            control.textChanged.connect(self.result_changed)
            control.setPlaceholderText("未识别 / 可手动填写")
            self.invoice_fields[key] = control
            metadata.addRow(text, control)
        self.invoice_fields["issued_on"].setPlaceholderText("YYYY-MM-DD；未识别可留空")
        editor_layout.addLayout(metadata)
        self.table = QTableWidget(0, len(ITEM_COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _, label in ITEM_COLUMNS])
        self.table.setMinimumHeight(180)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setColumnWidth(0, 210)
        self.table.setColumnWidth(1, 110)
        for col in range(2, len(ITEM_COLUMNS)):
            self.table.setColumnWidth(col, 100 if col != 5 else 125)
        self.table.itemChanged.connect(self.result_changed)
        editor_layout.addWidget(self.table)
        row_buttons = QHBoxLayout()
        add_row = QPushButton("添加商品行")
        add_row.clicked.connect(lambda: self.add_item())
        delete_row = QPushButton("删除选中行")
        delete_row.clicked.connect(self.delete_item)
        row_buttons.addWidget(add_row)
        row_buttons.addWidget(delete_row)
        row_buttons.addStretch()
        editor_layout.addLayout(row_buttons)
        note = QLabel("此表格可进行修改，请详细确认")
        note.setWordWrap(True)
        note.setObjectName("muted")
        editor_layout.addWidget(note)
        self.result_error = QLabel()
        self.result_error.setObjectName("error")
        self.result_error.setWordWrap(True)
        editor_layout.addWidget(self.result_error)
        self.warnings_label = QLabel()
        self.warnings_label.setWordWrap(True)
        self.warnings_label.setStyleSheet("color: #a15c09;")
        editor_layout.addWidget(self.warnings_label)
        self.calculated_label = QLabel()
        self.calculated_label.setWordWrap(True)
        editor_layout.addWidget(self.calculated_label)
        self.confirm = QCheckBox("确认结果无误")
        self.confirm.setToolTip("修改输入或识别结果后需重新确认")
        editor_layout.addWidget(self.confirm)
        right.addWidget(editor)
        right.addStretch()
        buttons = QHBoxLayout()
        self.ocr_button = QPushButton("开始识别")
        self.ocr_button.clicked.connect(self.start_ocr)
        self.generate_button = QPushButton("生成报销文件")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self.start_generation)
        self.open_button = QPushButton("打开输出文件夹")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(self.open_output)
        for button in (self.ocr_button, self.generate_button, self.open_button):
            buttons.addWidget(button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.status = QLabel("占位符")
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.result_changed()

    def update_logo(self):
        """按物理像素渲染，避免64像素图片在Retina屏幕上被放大。"""
        ratio = self.devicePixelRatioF()
        pixels = max(1, round(64 * ratio))
        pixmap = self._logo_source.scaled(pixels, pixels, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        self.logo.setPixmap(pixmap)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_logo()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange and hasattr(self, "logo"):
            self.update_logo()
        return result

    def make_error(self, key):
        label = QLabel()
        label.setObjectName("error")
        label.setWordWrap(True)
        self.error_labels[key] = label
        return label

    def submission(self) -> Submission:
        one = lambda key: self.files[key].paths[0] if self.files[key].paths else None
        selected = lambda group: group.checkedButton().text() if group.checkedButton() else ""
        return Submission(self.work_order.text().strip(), self.name.text().strip(), selected(self.payment_group),
                          selected(self.category_group), one("invoice_pdf"), one("verification_png"),
                          list(self.files["payments"].paths), list(self.files["photos"].paths))

    def show_errors(self, errors):
        for key, label in self.error_labels.items():
            label.setText(errors.get(key, ""))

    def inputs_changed(self, *_):
        # 构造阶段尚未创建确认框，其后所有更改均撤销旧确认。
        if hasattr(self, "confirm"):
            self.confirm.setChecked(False)
        if self.validation_started:
            self.show_errors(submission_errors(self.submission(), check_files=False))

    def invoice_file_changed(self):
        self.load_invoice(Invoice())
        self.status.setText("发票已添加，请识别或人工填写；旧识别结果已清空" if self.files["invoice_pdf"].paths
                            else "发票已删除，请重新添加；旧识别结果已清空")

    def add_item(self, item: Item | None = None):
        item = item or Item()
        was_loading = self.loading
        self.loading = True
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, (key, _) in enumerate(ITEM_COLUMNS):
            value = getattr(item, key)
            self.table.setItem(row, col, QTableWidgetItem(str(value) if value is not None else ""))
        self.loading = was_loading
        self.result_changed()

    def delete_item(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self.result_changed()

    def read_invoice(self) -> Invoice:
        inv = deepcopy(self.invoice)
        for key, widget in self.invoice_fields.items():
            text = widget.text().strip()
            if key == "issued_on":
                value = parse_date(text)
            elif key in ("total", "net_total", "tax_total"):
                value = number(text, {"total": "价税合计", "net_total": "未税合计", "tax_total": "税额合计"}[key])
            else:
                value = text
            setattr(inv, key, value)
        inv.items = []
        for row in range(self.table.rowCount()):
            values = {}
            for col, (key, label) in enumerate(ITEM_COLUMNS):
                cell = self.table.item(row, col)
                text = cell.text().strip() if cell else ""
                values[key] = number(text, f"第 {row + 1} 行{label}") if key in ("quantity", "unit_price", "net_amount", "tax") else text
            if any(v not in (None, "") for v in values.values()):
                inv.items.append(Item(**values))
        return inv

    def load_invoice(self, inv):
        self.loading = True
        self.invoice = inv
        for key, widget in self.invoice_fields.items():
            value = getattr(inv, key)
            text = value.isoformat() if key == "issued_on" and value else str(value) if value is not None else ""
            widget.setText(text)
        self.table.setRowCount(0)
        for item in inv.items:
            self.add_item(item)
        self.source_label.setText(f"来源：{inv.source} · 商品 {len(inv.items)} 行")
        self.loading = False
        self.result_changed()

    def result_changed(self, *_):
        if self.loading:
            return
        self.confirm.setChecked(False)
        try:
            inv = self.read_invoice()
            self.result_error.setText("\n".join(inv.errors().values()))
            warnings = inv.warnings()
            if len(inv.candidates) > 1:
                warnings.insert(0, "候选发票号码：" + "、".join(inv.candidates) + "。请核对原票后手动填写。")
            self.warnings_label.setText("\n".join(f"• {w}" for w in warnings))
            values = [f"第{i}行：{item.gross():.2f}元" if item.gross() is not None else f"第{i}行：未确定" for i, item in enumerate(inv.items, 1)]
            self.calculated_label.setText("输出含税金额预览　" + "；".join(values) if values else "")
        except AppError as e:
            self.result_error.setText(str(e))
            self.warnings_label.clear()
            self.calculated_label.clear()

    def set_status_error(self, text):
        self.status.setStyleSheet("color: #c92e3d;")
        self.status.setText(text)

    def run_job(self, action, on_success, message):
        if self.worker is not None:
            return
        self.canvas.setEnabled(False)
        for button in (self.ocr_button, self.generate_button, self.open_button):
            button.setEnabled(False)
        self.status.setStyleSheet("")
        self.status.setText(message)
        worker = Worker(action, self)
        self.worker = worker
        worker.succeeded.connect(on_success, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self.job_failed)
        worker.finished.connect(self.job_finished)
        worker.start()

    def job_finished(self):
        self.canvas.setEnabled(True)
        self.ocr_button.setEnabled(True)
        self.generate_button.setEnabled(True)
        self.open_button.setEnabled(self.output_path is not None)
        worker = self.worker
        self.worker = None
        worker.deleteLater()

    def job_failed(self, error):
        if isinstance(error, ValidationError):
            self.show_errors(error.errors)
        self.set_status_error(str(error))

    def start_ocr(self):
        if self.worker:
            return
        paths = self.files["invoice_pdf"].paths
        if not paths:
            self.files["invoice_pdf"].error_label.setText("请上传发票（PDF / PNG / JPG）")
            self.set_status_error("请先上传发票（PDF / PNG / JPG）")
            return
        path = paths[0]
        def completed(inv):
            self.load_invoice(inv)
            self.status.setText("识别完成 · 请逐项核对，可修改任意字段；确认后生成")
        self.run_job(lambda: recognize_invoice(path, self.engine), completed, "正在本地识别发票，请稍候…")

    def start_generation(self):
        if self.worker:
            return
        self.validation_started = True
        submission = self.submission()
        errors = submission_errors(submission, check_files=False)
        self.show_errors(errors)
        try:
            inv = self.read_invoice()
            errors.update(inv.errors())
            self.result_error.setText("\n".join(inv.errors().values()))
        except AppError as e:
            self.result_error.setText(str(e))
            errors["invoice"] = str(e)
        if errors:
            self.set_status_error("请补全红字提示的字段后再生成")
            return
        if not self.confirm.isChecked():
            self.set_status_error("请先对照原票核对结果，并勾选确认")
            return
        def completed(path):
            self.output_path = path
            self.status.setText(f"生成成功 · 文件已完整保存并检查：{path}")
            self.status.setStyleSheet("color: #137c52;")
        self.run_job(lambda: generate(submission, inv, warnings_confirmed=True), completed, "正在检查材料、填写模板并保存，请稍候…")

    def open_output(self):
        if self.output_path and self.output_path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_path)))
        else:
            self.set_status_error("输出目录不存在，请检查是否已被移动")

    def closeEvent(self, event):
        if self.worker is not None:
            self.status.setText("任务正在进行，请等待完成后关闭窗口")
            event.ignore()
        else:
            event.accept()
