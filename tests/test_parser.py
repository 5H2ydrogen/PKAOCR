from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pymupdf
import pytest

from pyocrpka.models import AppError, Invoice, Item
from pyocrpka.ocr import LocalOCR
from pyocrpka.parser import Token, parse_invoice
from pyocrpka.recognition import recognize_invoice


def row(y, values):
    return [Token(x, y, x + len(text) * 4, y + 9, text) for x, text in values]


def dynamic_tokens(shift=0):
    tokens = row(10, [(0, "发票号码：12345678"), (220, "发票代码：012345678901")])
    tokens += row(25, [(0, "开票日期：2026年04月13日"), (220, "校验码：12345678901234567890")])
    tokens += row(100, [(0, "项目名称"), (100, "规格型号"), (200, "单位"), (300, "数量"), (400, "单价"), (500, "金额"), (600, "税率"), (700, "税额")])
    tokens += row(120, [(0, "*金属*齿轮"), (100, "M3"), (200, "个"), (300, "3"), (400, "5.00"), (500, "15.00"), (600, "10%"), (700, "1.50")])
    tokens += row(140, [(0, "*机电*电机"), (200, "只"), (300, "2"), (400, "4.50"), (500, "9.00"), (600, "10%"), (700, "0.90")])
    tokens += row(170, [(0, "合计"), (500, "¥24.00"), (700, "¥2.40")])
    tokens += row(190, [(0, "价税合计（大写）"), (500, "（小写）¥26.40")])
    return [Token(t.x0 + shift, t.y0 + shift, t.x1 + shift, t.y1 + shift, t.text) for t in tokens]


@pytest.mark.parametrize("shift", [0, 45, 300])
def test_dynamic_multi_item_layout(shift):
    inv = parse_invoice(dynamic_tokens(shift))
    assert inv.number == "12345678"
    assert inv.code == "012345678901"
    assert inv.check_last6 == "567890"
    assert inv.issued_on == date(2026, 4, 13)
    assert inv.total == D("26.40")
    assert len(inv.items) == 2
    assert inv.items[0].specification == "M3"
    assert [i.gross() for i in inv.items] == [D("16.50"), D("9.90")]
    assert not inv.warnings()


def test_real_text_layer(sample_pdf, sample_invoice_number):
    class NoOCR:
        def recognize(self, _):
            raise AssertionError("完整文字层无需 OCR")
    inv = recognize_invoice(sample_pdf, NoOCR())
    # 样例验收值仅存在测试中，生产代码没有发票常量。
    assert inv.source == "PDF 文字层"
    assert inv.number == sample_invoice_number
    assert inv.issued_on == date(2026, 3, 8)
    assert inv.total == D("15.80")
    assert inv.net_total == D("15.64") and inv.tax_total == D("0.16")
    assert inv.items[0].quantity == D(1) and inv.items[0].unit == "条"
    assert inv.items[0].gross() == D("15.80")
    assert not inv.warnings()


def test_scanned_fallback_contract(materials):
    class FakeOCR:
        called = False
        def recognize(self, image):
            self.called = True
            assert image.ndim == 3
            return dynamic_tokens()
    engine = FakeOCR()
    inv = recognize_invoice(materials.invoice_pdf, engine)
    assert engine.called and inv.total == D("26.40") and len(inv.items) == 2


@pytest.mark.ocr
def test_real_scanned_fallback(tmp_path, sample_pdf, sample_invoice_number):
    scanned = tmp_path / "扫描发票.pdf"
    with pymupdf.open(sample_pdf) as original, pymupdf.open() as doc:
        pix = original[0].get_pixmap(matrix=pymupdf.Matrix(3, 3))
        page = doc.new_page(width=original[0].rect.width, height=original[0].rect.height)
        page.insert_image(page.rect, stream=pix.tobytes("png"))
        doc.save(scanned)
    inv = recognize_invoice(scanned)
    assert "本地 OCR" in inv.source
    assert inv.number == sample_invoice_number
    assert inv.total == D("15.80") and inv.issued_on == date(2026, 3, 8)
    assert inv.items[0].quantity == D(1)
    assert inv.items[0].net_amount == D("15.64") and inv.items[0].tax == D("0.16")


def test_partial_failure(materials):
    class BrokenOCR:
        def recognize(self, _):
            raise AppError("模型加载失败")
    inv = recognize_invoice(materials.invoice_pdf, BrokenOCR())
    assert not inv.number and inv.total is None and not inv.items
    assert inv.errors() and any("模型" in w for w in inv.warnings())


def test_candidates_and_bad_fields():
    inv = parse_invoice(row(0, [(0, "发票号码:12345678 发票号码:87654321")]))
    assert inv.number == "" and inv.candidates == ["12345678", "87654321"]
    assert inv.errors()
    inv.number = inv.candidates[1]
    assert not inv.errors()
    bad = parse_invoice(row(0, [(0, "开票日期:2026年02月30日")]))
    assert bad.issued_on is None and bad.notices


def test_amount_conflicts(invoice):
    invoice.total = D("100")
    assert any("不一致" in w for w in invoice.warnings())
    invoice.items[0].quantity = D(2)
    assert any("数量" in w for w in invoice.warnings())


def test_unknowns_not_invented():
    inv = parse_invoice([])
    assert inv.total is None and inv.issued_on is None and inv.items == []
    assert Item(quantity=D(3), unit_price=D(5)).gross() is None
    assert Item(quantity=D(3), unit_price=D(5), tax=D(0)).gross() == D(15)


def test_multiple_invoices_rejected(tmp_path):
    path = tmp_path / "two.pdf"
    with pymupdf.open() as doc:
        for value in ["12345678", "87654321"]:
            page = doc.new_page()
            page.insert_text((30, 40), value)
        doc.save(path)
    class TwoOCR:
        count = 0
        def recognize(self, _):
            self.count += 1
            return row(0, [(0, f"发票号码:{'12345678' if self.count == 1 else '87654321'}")])
    with pytest.raises(AppError, match="多张"):
        recognize_invoice(path, TwoOCR())


def test_same_invoice_continuation(tmp_path):
    path = tmp_path / "continued.pdf"
    with pymupdf.open() as doc:
        doc.new_page();doc.new_page();doc.save(path)
    class SameOCR:
        def recognize(self, _):
            return dynamic_tokens()
    inv = recognize_invoice(path, SameOCR())
    assert len(inv.items) == 4
    assert any("不一致" in w for w in inv.warnings())


def test_missing_model(tmp_path):
    with pytest.raises(AppError, match="尚未准备"):
        LocalOCR(tmp_path)._load()


def test_single_page_two_invoices_rejected(materials):
    class TwoOnPage:
        def recognize(self, _):
            return row(10, [(0, "发票号码:12345678")]) + row(200, [(0, "发票号码:87654321")])
    with pytest.raises(AppError, match="多张"):
        recognize_invoice(materials.invoice_pdf, TwoOnPage(), force_ocr=True)


def test_amount_precision(invoice):
    invoice.total = D("1.234")
    assert "价税合计" in invoice.errors()
