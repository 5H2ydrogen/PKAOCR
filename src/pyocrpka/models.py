"""界面、识别和输出之间传递的数据；财务计算始终使用 Decimal。"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
import re

PAYMENTS = ("微信", "支付宝")
CATEGORIES = ("重装", "三步", "四步", "哨兵", "无人机", "飞镖", "管理", "宣传", "硬件", "新生培训", "场地")
CENT = Decimal("0.01")


class AppError(Exception):
    """可安全展示给用户的中文错误。"""


class ValidationError(AppError):
    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("\n".join(errors.values()))


def number(text: str, label: str = "数值") -> Decimal | None:
    text = text.strip().replace("￥", "").replace("¥", "").replace(",", "")
    if not text:
        return None
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", text):
        raise AppError(f"{label}必须是有效十进制数值")
    try:
        value = Decimal(text)
        if not value.is_finite() or len(value.as_tuple().digits) > 28 or abs(value) > Decimal("1e15"):
            raise InvalidOperation
        return value
    except InvalidOperation:
        raise AppError(f"{label}超出支持范围") from None


def parse_date(text: str) -> date | None:
    if not text.strip():
        return None
    t = re.sub(r"[年/月.]", "-", text.strip()).replace("日", "")
    try:
        parts = t.split("-")
        return date(*(int(v) for v in parts))
    except (ValueError, TypeError):
        raise AppError("开票日期无效，请填写 YYYY-MM-DD") from None


@dataclass
class Item:
    name: str = ""
    specification: str = ""
    quantity: Decimal | None = None
    unit: str = ""
    unit_price: Decimal | None = None
    net_amount: Decimal | None = None
    tax_rate: str = ""
    tax: Decimal | None = None
    gross_amount: Decimal | None = None

    def net(self) -> Decimal | None:
        if self.net_amount is not None:
            return self.net_amount
        if self.quantity is not None and self.unit_price is not None:
            with localcontext() as context:
                context.prec = 64
                return (self.quantity * self.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
        return None

    def gross(self) -> Decimal | None:
        if self.gross_amount is not None:
            return self.gross_amount
        net = self.net()
        with localcontext() as context:
            context.prec = 64
            return (net + self.tax).quantize(CENT, rounding=ROUND_HALF_UP) if net is not None and self.tax is not None else None


@dataclass
class Invoice:
    number: str = ""
    code: str = ""
    check_last6: str = ""
    issued_on: date | None = None
    total: Decimal | None = None
    net_total: Decimal | None = None
    tax_total: Decimal | None = None
    items: list[Item] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    source: str = "人工填写"
    source_digest: str = ""

    def errors(self) -> dict[str, str]:
        errors = {}
        if not re.fullmatch(r"(?:\d{8}|\d{20})", self.number):
            errors["invoice_number"] = "请确认发票号码（普通发票 8 位或数电发票 20 位数字）"
        if self.code and not re.fullmatch(r"\d{10}|\d{12}", self.code):
            errors["invoice_code"] = "发票代码应为 10 或 12 位数字，也可留空"
        if self.check_last6 and not re.fullmatch(r"\d{6}", self.check_last6):
            errors["check"] = "校验码后六位应为 6 位数字，也可留空"
        for label, value in [("价税合计", self.total), ("未税合计", self.net_total), ("税额合计", self.tax_total)]:
            if value is not None and not value.is_finite():
                errors[label] = f"{label}无效"
            elif value is not None and value != value.quantize(CENT):
                errors[label] = f"{label}最多保留两位小数，请核对"
        for i, item in enumerate(self.items, 1):
            for key in ("quantity", "unit_price", "net_amount", "tax", "gross_amount"):
                v = getattr(item, key)
                if v is not None and not v.is_finite():
                    errors[f"item{i}{key}"] = f"第 {i} 行数值无效"
                elif v is not None and key in ("net_amount", "tax", "gross_amount") and v != v.quantize(CENT):
                    errors[f"item{i}{key}"] = f"第 {i} 行金额/税额最多保留两位小数，请核对"
        return errors

    def warnings(self) -> list[str]:
        warnings = list(self.notices)
        for label, value in [("开票日期", self.issued_on), ("价税合计", self.total)]:
            if value is None:
                warnings.append(f"{label}未识别或未填写，输出将留空")
        if not self.items:
            warnings.append("未识别商品明细，表2明细字段将留空")
        if self.net_total is not None and self.tax_total is not None and self.total is not None:
            if self.net_total + self.tax_total != self.total:
                warnings.append("未税合计 + 税额合计 与价税合计不一致")
        for i, item in enumerate(self.items, 1):
            missing = [label for label, value in [("商品名称", item.name or None), ("数量", item.quantity),
                       ("未税单价", item.unit_price), ("未税金额", item.net_amount), ("税额", item.tax)] if value is None]
            if missing:
                warnings.append(f"第 {i} 行缺少{'、'.join(missing)}，请核对")
            if item.gross() is None:
                warnings.append(f"第 {i} 行含税金额无法确定，K列将留空")
            if item.net_amount is not None and item.quantity is not None and item.unit_price is not None:
                with localcontext() as context:
                    context.prec = 64
                    expected_net = (item.quantity * item.unit_price).quantize(CENT, rounding=ROUND_HALF_UP)
                if item.net_amount != expected_net:
                    warnings.append(f"第 {i} 行数量 × 未税单价 与未税金额不一致")
            if item.gross_amount is not None and item.net() is not None and item.tax is not None:
                if item.gross_amount != item.net() + item.tax:
                    warnings.append(f"第 {i} 行含税金额 与未税金额 + 税额不一致")
        for label, values, total in [
            ("商品含税金额", [i.gross() for i in self.items], self.total),
            ("商品未税金额", [i.net() for i in self.items], self.net_total),
            ("商品税额", [i.tax for i in self.items], self.tax_total),
        ]:
            if values and total is not None and all(v is not None for v in values):
                if sum(values, Decimal(0)) != total:
                    warnings.append(f"{label}之和与发票合计不一致")
        return list(dict.fromkeys(warnings))


@dataclass
class Submission:
    work_order: str = ""
    name: str = ""
    payment: str = ""
    category: str = ""
    invoice_pdf: Path | None = None
    verification_png: Path | None = None
    payments: list[Path] = field(default_factory=list)
    photos: list[Path] = field(default_factory=list)
