from dataclasses import replace
from decimal import Decimal as D

import numpy as np
from openpyxl import load_workbook
import pytest

from pyocrpka.output import digest, generate
from pyocrpka.parser import Token, headers_in, parse_invoice, visual_rows
from pyocrpka.paths import PROJECT_ROOT
from pyocrpka.recognition import _recover_item_cells, _restore_specification_spaces, recognize_invoice
from test_parser import dynamic_tokens


@pytest.mark.parametrize("has_ink", [True, False])
def test_missing_quantity_is_read_from_its_own_cell(has_ink):
    tokens = [t for t in dynamic_tokens() if not (t.y0 == 120 and t.x0 == 300)]
    image = np.full((220, 800, 3), 255, dtype=np.uint8)
    if has_ink:
        image[121:128, 301:306] = 0
    headers = next(h for row in visual_rows(tokens) if (h := headers_in(row)))

    class CellOCR:
        calls = 0

        def recognize_region(self, crop):
            self.calls += 1
            return "2"  # 与15/5=3不同，证明没有按金额倒推或使用包装数量。

    engine = CellOCR()
    invoice = parse_invoice(_recover_item_cells(image, tokens, headers, engine))
    assert invoice.items[0].quantity == (D(2) if has_ink else None)
    assert engine.calls == (1 if has_ink else 0)
    assert invoice.items[1].quantity == D(2)


@pytest.mark.parametrize("matches", [True, False])
def test_visible_specification_gap_preserved_only_when_text_matches(matches):
    image = np.full((45, 170, 3), 255, dtype=np.uint8)
    for x in (4, 20, 36, 52, 68, 84, 126, 142):
        image[10:30, x:x + 10] = 0
    token = Token(0, 0, 160, 40, "F4-10M4*10*")

    class SpacedOCR:
        parts = iter(("F4-10M", "4*10*" if matches else "4*12*"))

        def recognize_region(self, crop):
            return next(self.parts)

    restored = _restore_specification_spaces(image, token, SpacedOCR())
    assert restored.text == ("F4-10M 4*10*" if matches else token.text)


def test_decimal_dot_is_not_treated_as_a_word_gap():
    image = np.full((45, 80, 3), 255, dtype=np.uint8)
    image[10:30, 4:18] = 0
    image[28:30, 35:38] = 0
    image[10:30, 55:69] = 0

    class UnexpectedOCR:
        def recognize_region(self, crop):
            raise AssertionError("不能按小数点两侧的空白拆开型号")

    token = Token(0, 0, 75, 40, "2.5")
    assert _restore_specification_spaces(image, token, UnexpectedOCR()) == token


def test_separate_specification_tokens_and_wrapped_continuation():
    headers = [(0, "项目名称"), (100, "规格型号"), (220, "单位"), (300, "数量"),
               (400, "单价"), (500, "金额"), (600, "税率"), (700, "税额")]
    tokens = [Token(x, 100, x + 32, 110, text) for x, text in headers]
    tokens += [Token(0, 120, 40, 130, "*轴承*轴承"),
               Token(100, 120, 140, 130, "F4-10M"), Token(152, 120, 170, 130, "4*10*"),
               Token(220, 120, 230, 130, "件"), Token(300, 120, 310, 130, "4"),
               Token(400, 120, 420, 130, "5"), Token(500, 120, 520, 130, "20"),
               Token(600, 120, 620, 130, "1%"), Token(700, 120, 720, 130, "0.20"),
               Token(100, 145, 130, 155, "4=5只")]
    inv = parse_invoice(tokens)
    assert len(inv.items) == 1
    assert inv.items[0].specification == "F4-10M 4*10*4=5只"
    assert inv.items[0].quantity == D(4)


@pytest.mark.ocr
def test_private_three_item_specification_regression(materials, templates, tmp_path, window):
    photo = PROJECT_ROOT / "other" / "222.jpg"
    if not photo.is_file():
        pytest.skip("本地问题发票图片不随公开仓库提供")
    original_hash = digest(photo)
    inv = recognize_invoice(photo)
    expected_specs = ["15*21*4=2只", "3*6*2.5 10只", "F4-10M 4*10*4=5只"]
    assert [item.specification for item in inv.items] == expected_specs
    assert [item.quantity for item in inv.items] == [D(1), D(2), D(4)]
    assert [item.unit_price for item in inv.items] == [D("4.6435643564"), D("12.4405940594"), D("8.74504950495")]
    assert [item.net_amount for item in inv.items] == [D("4.64"), D("24.88"), D("34.98")]
    assert inv.total == D("65.15") and not inv.warnings()
    window.load_invoice(inv)
    assert [window.table.item(row, 1).text() for row in range(3)] == expected_specs
    assert [window.table.item(row, 3).text() for row in range(3)] == ["1", "2", "4"]
    target = generate(replace(materials, invoice_pdf=photo), inv, output_dir=tmp_path / "out", template_dir=templates)
    first = load_workbook(target / f"{target.name}_表1.xlsx").active
    second = load_workbook(target / f"{target.name}_表2.xlsx").active
    assert D(str(first["C2"].value)) == D("65.15")
    assert [second.cell(row, 6).value for row in (2, 3, 4)] == [1, 2, 4]
    assert [D(str(second.cell(row, 11).value)) for row in (2, 3, 4)] == [D("4.69"), D("25.13"), D("35.33")]
    assert digest(photo) == original_hash
