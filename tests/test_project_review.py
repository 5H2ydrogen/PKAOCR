from decimal import Decimal as D

import numpy as np
from openpyxl import load_workbook
import pymupdf
import pytest

from pyocrpka.models import AppError, CATEGORIES, PAYMENTS
from pyocrpka.output import generate, publish_exclusive
from pyocrpka.parser import Token
from pyocrpka.recognition import _recover_item_cells, _restore_specification_spaces, recognize_invoice
from test_parser import dynamic_tokens
from pyocrpka.parser import headers_in, visual_rows


def test_one_cent_conflict_is_reported(invoice):
    invoice.total += D("0.01")
    assert any("价税合计不一致" in warning for warning in invoice.warnings())
    invoice.total -= D("0.01")
    invoice.items[0].net_amount += D("0.01")
    assert any("单价" in warning for warning in invoice.warnings())


def test_output_dropdowns_match_gui_choices(materials, invoice, templates, tmp_path):
    target = generate(materials, invoice, output_dir=tmp_path / "out", template_dir=templates)
    for index, expected in [(1, {"D2": PAYMENTS}), (2, {"C2": CATEGORIES, "M2": PAYMENTS})]:
        workbook = load_workbook(target / f"{target.name}_表{index}.xlsx")
        for cell, choices in expected.items():
            rules = [rule for rule in workbook.active.data_validations.dataValidation if cell in rule.sqref]
            assert len(rules) == 1 and rules[0].formula1 == '"' + ",".join(choices) + '"'
        workbook.close()


def test_ambiguous_multi_page_invoice_is_rejected(tmp_path):
    path = tmp_path / "ambiguous.pdf"
    with pymupdf.open() as doc:
        doc.new_page(); doc.new_page(); doc.save(path)

    class AmbiguousOCR:
        calls = 0

        def recognize(self, image):
            self.calls += 1
            result = [Token(0, 0, 100, 10, "20260000000000000001")]
            if self.calls == 2:
                result.append(Token(0, 30, 100, 40, "20260000000000000002"))
            return result

    with pytest.raises(AppError, match="归属"):
        recognize_invoice(path, AmbiguousOCR())


def test_unsupported_platform_reports_chinese_error(tmp_path, monkeypatch):
    import pyocrpka.output as output
    monkeypatch.setattr(output.sys, "platform", "win32")
    with pytest.raises(AppError, match="当前系统不支持"):
        publish_exclusive(tmp_path / "source", tmp_path / "target")


def test_local_refinement_failure_keeps_available_fields():
    image = np.full((220, 800, 3), 255, dtype=np.uint8)
    tokens = [t for t in dynamic_tokens() if not (t.y0 == 120 and t.x0 == 300)]
    image[121:128, 301:306] = 0
    headers = next(h for row in visual_rows(tokens) if (h := headers_in(row)))

    class FailedRegionOCR:
        def recognize_region(self, crop):
            raise AppError("模拟局部OCR失败")

    restored = _recover_item_cells(image, tokens, headers, FailedRegionOCR())
    assert restored == tokens
    spaced = np.full((45, 170, 3), 255, dtype=np.uint8)
    for x in (4, 20, 36, 52, 68, 84, 126, 142):
        spaced[10:30, x:x + 10] = 0
    token = Token(0, 0, 160, 40, "F4-10M4*10*")
    assert _restore_specification_spaces(spaced, token, FailedRegionOCR()) == token
