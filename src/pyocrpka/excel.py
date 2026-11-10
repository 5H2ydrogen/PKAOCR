"""只负责模板结构检查和数据填写，不解析发票。"""
from copy import copy
from datetime import date
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook

from .models import AppError, CATEGORIES, Invoice, Item, PAYMENTS, Submission

HEADERS = (
    ("工单号", "姓名", "发票金额", "支付方式", "发票号码", "效验码后六位", "发票代码", "开票日期", "备注"),
    ("工单号", "开票日期", "类别", "项目", "支出内容", "数量", "单位", "单价", "税费", "运费", "总价", "支出人", "付款方式", "发票号码", "无票原因", "购买链接", "备注"),
)


def open_template(directory: Path, index: int):
    path = directory / f"工单号_发票号_表{index}.xlsx"
    if not path.is_file():
        raise AppError(f"缺少表{index}模板，请检查 other/工单号_发票号")
    try:
        book = load_workbook(path)
    except Exception:
        raise AppError(f"表{index}模板无法打开") from None
    if book.sheetnames != ["Sheet1"]:
        raise AppError(f"表{index}工作表名称与已验收模板不符，请恢复原模板")
    sheet = book["Sheet1"]
    if tuple(sheet.cell(1, c).value for c in range(1, len(HEADERS[index - 1]) + 1)) != HEADERS[index - 1]:
        raise AppError(f"表{index}模板表头与预期不符，已停止生成")
    if sheet.merged_cells.ranges:
        raise AppError(f"表{index}模板合并区域与已验收版本不符，已停止生成")
    # 除空白样式区域外只允许原示例行；避免误删用户在模板中新增的记录/公式。
    if any(c.value is not None for row in sheet.iter_rows(min_row=3) for c in row):
        raise AppError(f"表{index}模板第3行以后存在额外数据，请提供原始模板")
    return book, sheet


def put(cell, value):
    if isinstance(value, str):
        cell.value = value if value else None
        if value:
            cell.data_type = "s"  # 即使以 = + - @ 开头，用户文本也不能成为公式。
            cell.number_format = "@"
    else:
        cell.value = value
        if isinstance(value, date):
            cell.number_format = "yyyy-mm-dd"
        elif isinstance(value, Decimal) and cell.number_format == "General":
            cell.number_format = "0.00"


def fill_templates(directory: Path, destination: Path, prefix: str, s: Submission, inv: Invoice) -> list[Path]:
    paths = []
    rows = [
        [[s.work_order, s.name, inv.total, s.payment, inv.number, inv.check_last6, inv.code, inv.issued_on, None]],
        [[s.work_order, inv.issued_on, s.category, None, item.name, item.quantity, item.unit,
          item.unit_price, item.tax, None, item.gross(), s.name, s.payment, inv.number, None, None, None]
         for item in inv.items or [Item()]],
    ]
    for index, data in enumerate(rows, 1):
        book, sheet = open_template(directory, index)
        choices = {4: PAYMENTS} if index == 1 else {3: CATEGORIES, 13: PAYMENTS}
        for col, allowed in choices.items():
            for rule in sheet.data_validations.dataValidation:
                if rule.type == "list" and sheet.cell(2, col).coordinate in rule.sqref:
                    rule.formula1 = '"' + ",".join(allowed) + '"'
        styles = [copy(c._style) for c in sheet[2]]
        row_height = sheet.row_dimensions[2].height
        # 替换示例数据，同时清空表头外 J2/R2 的示例说明，保留样式和空白格式区域。
        for cell in sheet[2]:
            cell.value = None
            cell.hyperlink = None
            cell.comment = None
        for row_number, values in enumerate(data, 2):
            for col, style in enumerate(styles, 1):
                sheet.cell(row_number, col)._style = copy(style)
            sheet.row_dimensions[row_number].height = row_height
            for col, value in enumerate(values, 1):
                put(sheet.cell(row_number, col), value)
        path = destination / f"{prefix}_表{index}.xlsx"
        book.save(path)
        book.close()
        paths.append(path)
    return paths


def verify_workbooks(paths: list[Path], s: Submission, inv: Invoice):
    for index, path in enumerate(paths, 1):
        book = load_workbook(path)
        sheet = book["Sheet1"]
        if tuple(sheet.cell(1, c).value for c in range(1, len(HEADERS[index - 1]) + 1)) != HEADERS[index - 1]:
            raise AppError("输出 Excel 表头检查失败")
        if index == 1:
            valid = (sheet["D2"].value == s.payment and sheet["E2"].value == inv.number
                     and sheet["E2"].data_type == "s")
            if inv.total is not None:
                valid = valid and Decimal(str(sheet["C2"].value)) == inv.total
            if not valid:
                raise AppError("表1输出字段校验失败")
        else:
            for row, item in enumerate(inv.items or [Item()], 2):
                gross = item.gross()
                total_valid = sheet.cell(row, 11).value is None if gross is None else Decimal(str(sheet.cell(row, 11).value)) == gross
                if not (sheet.cell(row, 3).value == s.category and sheet.cell(row, 13).value == s.payment
                        and sheet.cell(row, 14).value == inv.number and sheet.cell(row, 11).data_type != "f" and total_valid):
                    raise AppError("表2输出字段校验失败")
        if any("示例，请在填写完成后删除该行" in str(c.value) for row in sheet for c in row):
            raise AppError("输出中仍有示例说明，已停止保存")
        book.close()
