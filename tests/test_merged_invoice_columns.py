from dataclasses import replace
from decimal import Decimal as D

from openpyxl import load_workbook
import pytest

from pyocrpka.output import digest, generate
from pyocrpka.parser import Token, parse_invoice
from pyocrpka.paths import PROJECT_ROOT
from pyocrpka.recognition import recognize_invoice
from test_parser import dynamic_tokens


def merged_tokens(quantity, price, separator=" ", shift=0):
    tokens = [t for t in dynamic_tokens(shift)
              if not (t.y0 == 120 + shift and t.x0 in (300 + shift, 400 + shift))]
    tokens.append(Token(300 + shift, 120 + shift, 416 + shift, 129 + shift,
                        f"{quantity}{separator}{price}"))
    return tokens


@pytest.mark.parametrize("quantity,price,separator,shift", [
    ("100", "0.1069306930693", " ", 0),
    ("250", "0.032", "　", 40),
    ("12", "2.50", "", 80),
])
def test_merged_numeric_columns_are_recognized_separately(materials, quantity, price, separator, shift):
    class MergedOCR:
        def __init__(self):
            self.crops = []
            self.values = iter((quantity, price))

        def recognize(self, image):
            return merged_tokens(quantity, price, separator, shift)

        def recognize_region(self, image):
            assert image.size > 0
            self.crops.append(image.shape)
            return next(self.values)

    engine = MergedOCR()
    inv = recognize_invoice(materials.invoice_pdf, engine)
    assert len(engine.crops) == 2
    assert inv.items[0].quantity == D(quantity)
    assert inv.items[0].unit_price == D(price)
    assert inv.items[0].net_amount == D("15")
    assert inv.items[1].quantity == D(2)
    assert inv.items[1].unit_price == D("4.50")


def test_unsplit_numeric_values_are_not_concatenated():
    inv = parse_invoice(merged_tokens("100", "0.1069306930693"))
    assert inv.items[0].quantity is None
    assert inv.items[0].unit_price is None
    assert inv.items[0].net_amount == D("15")
    assert any("数值未能解析" in notice for notice in inv.notices)


def test_spacing_around_decimal_point_remains_readable():
    values = {"5.00": "5. 00", "15.00": "¥ 15 . 00", "1.50": "1. 50"}
    tokens = [replace(token, text=values.get(token.text, token.text)) for token in dynamic_tokens()]
    inv = parse_invoice(tokens)
    assert inv.items[0].quantity == D(3)
    assert inv.items[0].unit_price == D("5.00")
    assert inv.items[0].net_amount == D("15.00")
    assert inv.items[0].gross() == D("16.50")
    assert not inv.warnings()


@pytest.mark.ocr
def test_private_two_item_photo_regression(materials, templates, tmp_path):
    photo = PROJECT_ROOT / "other" / "111.jpg"
    if not photo.is_file():
        pytest.skip("本地问题发票图片不随公开仓库提供")
    original_hash = digest(photo)
    inv = recognize_invoice(photo)
    assert len(inv.items) == 2
    assert inv.items[0].quantity == D(100)
    assert inv.items[0].unit_price == D("0.1069306930693")
    assert inv.items[0].net_amount == D("10.69")
    assert inv.items[0].tax == D("0.11")
    assert inv.items[1].quantity == D(2)
    assert inv.items[1].unit_price == D("17.7029702970297")
    assert inv.items[1].net_amount == D("35.41")
    assert inv.total == D("46.56")
    assert not inv.warnings()
    target = generate(replace(materials, invoice_pdf=photo), inv,
                      output_dir=tmp_path / "out", template_dir=templates)
    first = load_workbook(target / f"{target.name}_表1.xlsx").active
    second = load_workbook(target / f"{target.name}_表2.xlsx").active
    assert D(str(first["C2"].value)) == D("46.56")
    assert second["F2"].value == 100
    assert D(str(second["H2"].value)) == D("0.1069306930693")
    assert D(str(second["K2"].value)) == D("10.80")
    assert D(str(second["K3"].value)) == D("35.76")
    assert digest(photo) == original_hash
