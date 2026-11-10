from copy import copy
from dataclasses import replace
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path
import shutil

from openpyxl import load_workbook
from PIL import Image
import pytest

from pyocrpka.models import AppError, Invoice, Item, ValidationError
from pyocrpka.output import digest, generate, publish_exclusive


def make(materials, invoice, tmp_path, templates, **kwargs):
    return generate(materials, invoice, output_dir=tmp_path / "out", template_dir=templates, **kwargs)


@pytest.mark.parametrize("payment", ["微信", "支付宝"])
def test_every_excel_field(materials, invoice, tmp_path, templates, payment):
    materials.payment = payment
    originals = {p: digest(p) for p in templates.glob("*.xlsx")}
    target = make(materials, invoice, tmp_path, templates)
    prefix = target.name
    w1 = load_workbook(target / f"{prefix}_表1.xlsx")
    w2 = load_workbook(target / f"{prefix}_表2.xlsx")
    a, b = w1.active, w2.active
    assert [a.cell(2, i).value for i in range(1, 10)] == [materials.work_order, materials.name, 26.4, payment, invoice.number, None, None, datetime(2026, 5, 12), None]
    assert a["E2"].data_type == "s" and a["C2"].data_type == "n" and a["H2"].is_date
    assert a["J2"].value is None and b["R2"].value is None
    for row, item in enumerate(invoice.items, 2):
        expected = [materials.work_order, datetime(2026, 5, 12), "硬件", None, item.name,
                    float(item.quantity), item.unit, float(item.unit_price), float(item.tax), None,
                    float(item.gross()), materials.name, payment, invoice.number, None, None, None]
        assert [b.cell(row, col).value for col in range(1, 18)] == expected
        assert b.cell(row, 14).data_type == "s"
        assert b.cell(row, 11).data_type == "n"
    assert b["K2"].value == 16.5 and b["K3"].value == 9.9
    assert b["K2"].value != float(invoice.total)
    for index, out in [(1, a), (2, b)]:
        source = load_workbook(templates / f"工单号_发票号_表{index}.xlsx").active
        assert out.title == source.title
        assert [c.value for c in out[1]] == [c.value for c in source[1]]
        assert [c._style for c in out[1]] == [c._style for c in source[1]]
        assert {k: (v.width, v.hidden, v.min, v.max, tuple(v._style or [0] * 9)) for k, v in out.column_dimensions.items()} == {k: (v.width, v.hidden, v.min, v.max, tuple(v._style or [0] * 9)) for k, v in source.column_dimensions.items()}
        for col in range(1, source.max_column + 1):
            assert copy(out.cell(2, col).font) == copy(source.cell(2, col).font)
            assert copy(out.cell(2, col).fill) == copy(source.cell(2, col).fill)
            assert copy(out.cell(2, col).border) == copy(source.cell(2, col).border)
            assert copy(out.cell(2, col).alignment) == copy(source.cell(2, col).alignment)
        for row in out:
            for cell in row:
                assert cell.data_type != "f"
                assert "示例，请" not in str(cell.value)
                assert "淘宝" not in str(cell.value)
    assert {p: digest(p) for p in originals} == originals


@pytest.mark.parametrize("count", [1, 2, 3])
def test_exact_tree_and_copy_order(materials, invoice, tmp_path, templates, count):
    sources = [materials.payments[0], *materials.photos]
    materials.photos = sources[:count]
    materials.payments = list(reversed(sources[:count]))
    before = {p: digest(p) for p in [materials.invoice_pdf, materials.verification_png, *sources]}
    target = make(materials, invoice, tmp_path, templates)
    p = target.name
    expected = {f"{p}_表1.xlsx", f"{p}_表2.xlsx", f"{p}_报销材料/{p}_发票.pdf", f"{p}_报销材料/{p}_发票查验.png"}
    for kind, files in [("实物", materials.photos), ("付款凭证", materials.payments)]:
        for i, source in enumerate(files, 1):
            relative = f"{p}_报销材料/{p}_{kind}/{p}_{kind}{i}.jpg"
            expected.add(relative)
            assert digest(target / relative) == digest(source)
            with Image.open(target / relative) as image:
                assert image.format == "JPEG"
    assert {str(f.relative_to(target)) for f in target.rglob("*") if f.is_file()} == expected
    assert digest(target / f"{p}_报销材料/{p}_发票.pdf") == before[materials.invoice_pdf]
    assert {f: digest(f) for f in before} == before
    assert not list((tmp_path / "out").glob(".reimbursement-*"))


def test_conflict_no_overwrite(materials, invoice, tmp_path, templates):
    target = make(materials, invoice, tmp_path, templates)
    snapshots = {p: digest(p) for p in target.rglob("*") if p.is_file()}
    with pytest.raises(AppError, match="已存在"):
        make(materials, invoice, tmp_path, templates)
    assert {p: digest(p) for p in snapshots} == snapshots


def test_atomic_publish_no_empty_directory_overwrite(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir(); target.mkdir()
    with pytest.raises(AppError, match="已存在"):
        publish_exclusive(source, target)
    assert source.is_dir() and target.is_dir()


@pytest.mark.parametrize("failure", ["copy", "save", "template", "disk"])
def test_failure_cleans_staging(materials, invoice, tmp_path, templates, monkeypatch, failure):
    if failure == "copy":
        def fail(*args):
            raise OSError("copy failed")
        monkeypatch.setattr(shutil, "copyfile", fail)
    elif failure == "save":
        import openpyxl.workbook.workbook
        def fail(*args):
            raise PermissionError("save denied")
        monkeypatch.setattr(openpyxl.workbook.workbook.Workbook, "save", fail)
    elif failure == "disk":
        import errno
        def fail(*args):
            raise OSError(errno.ENOSPC, "full")
        monkeypatch.setattr(shutil, "copyfile", fail)
    else:
        next(templates.glob("*表1.xlsx")).unlink()
    with pytest.raises(AppError):
        make(materials, invoice, tmp_path, templates)
    assert not list((tmp_path / "out").iterdir())


def test_template_conflict(materials, invoice, tmp_path, templates):
    path = templates / "工单号_发票号_表2.xlsx"
    book = load_workbook(path);book.active["C1"] = "错误表头";book.save(path)
    with pytest.raises(AppError, match="表头"):
        make(materials, invoice, tmp_path, templates)
    assert not list((tmp_path / "out").iterdir())


def test_unknown_fields_and_no_shipping_zero(materials, invoice, tmp_path, templates):
    invoice.total = None;invoice.issued_on = None
    invoice.items = [Item(name="未税信息不完整", quantity=D(3), unit_price=D(5))]
    target = make(materials, invoice, tmp_path, templates, warnings_confirmed=True)
    p = target.name
    a = load_workbook(target / f"{p}_表1.xlsx").active
    b = load_workbook(target / f"{p}_表2.xlsx").active
    assert a["C2"].value is None and a["H2"].value is None
    assert all(b[c].value is None for c in ("I2", "J2", "K2", "D2", "O2", "P2", "Q2"))


def test_confirm_required_for_conflict(materials, invoice, tmp_path, templates):
    invoice.total = D(100)
    with pytest.raises(AppError, match="确认"):
        make(materials, invoice, tmp_path, templates)
    target = make(materials, invoice, tmp_path, templates, warnings_confirmed=True)
    sheet = load_workbook(target / f"{target.name}_表1.xlsx").active
    assert sheet["C2"].value == 100


def test_manual_correction_and_formula_injection(materials, invoice, tmp_path, templates):
    materials.name = "=HYPERLINK(\"https://invalid.example\")"
    materials.work_order = "=1+2"
    invoice.number = "00000001"
    invoice.total = D("11")
    invoice.net_total = D("10");invoice.tax_total = D(1)
    invoice.items = [Item(name="=2+3", quantity=D(2), unit_price=D(5), tax=D(1))]
    target = make(materials, invoice, tmp_path, templates, warnings_confirmed=True)
    a = load_workbook(target / f"{target.name}_表1.xlsx").active
    b = load_workbook(target / f"{target.name}_表2.xlsx").active
    assert a["A2"].data_type == "s" and a["B2"].data_type == "s"
    assert a["E2"].value == "00000001" and b["E2"].data_type == "s"
    assert b["K2"].value == 11


def test_invalid_invoice_and_symlink(materials, invoice, tmp_path, templates):
    invoice.number = "../unsafe"
    with pytest.raises(ValidationError):
        make(materials, invoice, tmp_path, templates)
    invoice.number = "12345678"
    (tmp_path / "out").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(AppError, match="符号链接"):
        make(materials, invoice, tmp_path, templates)


def test_gross_priority():
    assert Item(quantity=D(4), unit_price=D(5), tax=D(2)).gross() == D(22)
    assert Item(quantity=D(4), unit_price=D(5), net_amount=D(19), tax=D(2)).gross() == D(21)
    assert Item(net_amount=D(19), tax=D(2), gross_amount=D(25)).gross() == D(25)


def test_pdf_changed_after_recognition(materials, invoice, tmp_path, templates):
    invoice.source_digest = digest(materials.invoice_pdf)
    with materials.invoice_pdf.open("ab") as stream:
        stream.write(b"\n% changed after recognition\n")
    with pytest.raises(AppError, match="识别后发生变化"):
        make(materials, invoice, tmp_path, templates)
    assert not (tmp_path / "out").exists()


def test_file_changes_to_wrong_type_during_copy(materials, invoice, tmp_path, templates, monkeypatch):
    original_copy = shutil.copyfile
    def corrupt_copy(source, dest):
        original_copy(source, dest)
        if dest.suffix == ".jpg":
            dest.write_bytes(materials.verification_png.read_bytes())
    monkeypatch.setattr(shutil, "copyfile", corrupt_copy)
    with pytest.raises(AppError):
        make(materials, invoice, tmp_path, templates)
    assert not list((tmp_path / "out").iterdir())
