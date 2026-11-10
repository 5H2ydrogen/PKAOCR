from dataclasses import replace
from pathlib import Path

from PIL import Image
import pymupdf
import pytest

from pyocrpka.models import AppError, Submission, number, parse_date
from pyocrpka.validation import submission_errors, validate_file


def test_all_empty():
    assert set(submission_errors(Submission())) == {"work_order", "name", "payment", "category", "invoice_pdf", "payments"}


@pytest.mark.parametrize("field", ["work_order", "name", "payment", "category", "invoice_pdf", "verification_png", "payments", "photos"])
def test_each_missing(materials, field):
    empty = [] if field in ("payments", "photos") else None if field in ("invoice_pdf", "verification_png") else ""
    assert set(submission_errors(replace(materials, **{field: empty}))) == (set() if field in ("verification_png", "photos") else {field})


def test_multiple_missing(materials):
    assert set(submission_errors(replace(materials, payment="", category="", name=""))) == {"payment", "category", "name"}


@pytest.mark.parametrize("value", ["../foo", "a/b", "a\\b", "..", ".", "a:b", "a*", "a?", "a\x00", "a|b", "a<", "a>", 'a"', "a.", "x" * 71])
def test_path_rejected(materials, value):
    assert "work_order" in submission_errors(replace(materials, work_order=value))


@pytest.mark.parametrize("field,value", [("payment", "微信支付"), ("payment", "现金"), ("category", "英雄")])
def test_invalid_choices(materials, field, value):
    assert field in submission_errors(replace(materials, **{field: value}))


def test_actual_content_and_corruption(tmp_path, materials):
    fake = tmp_path / "伪装.png"
    fake.write_bytes(materials.photos[0].read_bytes())
    with pytest.raises(AppError, match="实际图片格式"):
        validate_file(fake, "png")
    fake_jpg = tmp_path / "伪装.jpg"
    fake_jpg.write_bytes(materials.verification_png.read_bytes())
    with pytest.raises(AppError):
        validate_file(fake_jpg, "jpeg")
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.7 broken")
    with pytest.raises(AppError):
        validate_file(broken, "pdf")
    fake.write_bytes(b"broken")
    with pytest.raises(AppError):
        validate_file(fake, "png")
    with pytest.raises(AppError):
        validate_file(materials.photos[0], "png")


def test_encrypted_pdf(tmp_path):
    path = tmp_path / "secret.pdf"
    doc = pymupdf.open(); doc.new_page()
    doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="password", owner_pw="owner")
    doc.close()
    with pytest.raises(AppError, match="加密"):
        validate_file(path, "pdf")


def test_deleted_file(materials):
    materials.payments[0].unlink()
    assert "photos" not in submission_errors(materials)  # photos列表未引用已删的付款图片
    assert "payments" in submission_errors(materials)


@pytest.mark.parametrize("text", ["NaN", "Infinity", "1e3", "abc", "1.2.3"])
def test_invalid_decimal(text):
    with pytest.raises(AppError):
        number(text)


@pytest.mark.parametrize("text", ["2026-02-30", "2026-13-01", "not a date"])
def test_invalid_date(text):
    with pytest.raises(AppError):
        parse_date(text)
