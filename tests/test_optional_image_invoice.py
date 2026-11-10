from dataclasses import replace

from PIL import Image
import pymupdf
import pytest

from pyocrpka.models import AppError
from pyocrpka.ocr import LocalOCR
from pyocrpka.output import digest, generate
from pyocrpka.recognition import recognize_invoice
from pyocrpka.validation import submission_errors, validate_file


@pytest.mark.parametrize("verification,photos", [(False, False), (True, False), (False, True), (True, True)])
def test_optional_materials_generate(materials, invoice, templates, tmp_path, verification, photos):
    submission = replace(materials, verification_png=materials.verification_png if verification else None,
                         photos=materials.photos if photos else [])
    assert not submission_errors(submission)
    target = generate(submission, invoice, output_dir=tmp_path / "out", template_dir=templates)
    prefix = target.name
    folder = target / f"{prefix}_报销材料"
    assert (folder / f"{prefix}_发票查验.png").exists() == verification
    assert len(list((folder / f"{prefix}_实物").glob("*.jpg"))) == (len(materials.photos) if photos else 0)
    assert len(list(target.rglob("*.xlsx"))) == 2
    assert digest(folder / f"{prefix}_发票.pdf") == digest(materials.invoice_pdf)


@pytest.mark.parametrize("extension,fmt", [("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG")])
def test_image_invoice_packed_as_real_pdf(materials, invoice, templates, tmp_path, monkeypatch, extension, fmt):
    path = tmp_path / f"图片发票.{extension}"
    Image.new("RGB", (90, 50), "white").save(path, fmt)
    original_hash = digest(path)
    monkeypatch.setattr(LocalOCR, "recognize", lambda self, image: [])
    submission = replace(materials, invoice_pdf=path, verification_png=None, photos=[])
    assert not submission_errors(submission)
    target = generate(submission, invoice, output_dir=tmp_path / "out", template_dir=templates)
    output_pdf = target / f"{target.name}_报销材料" / f"{target.name}_发票.pdf"
    validate_file(output_pdf, "pdf")
    with pymupdf.open(output_pdf) as doc:
        assert doc.is_pdf and len(doc) == 1
        images = doc[0].get_images()
        assert len(images) == 1 and images[0][2:4] == (90, 50)
    assert digest(path) == original_hash


@pytest.mark.ocr
@pytest.mark.parametrize("extension,fmt", [("png", "PNG"), ("jpg", "JPEG")])
def test_real_image_invoice_ocr(tmp_path, sample_pdf, extension, fmt):
    path = tmp_path / f"真实样例.{extension}"
    with pymupdf.open(sample_pdf) as source:
        pixmap = source[0].get_pixmap(matrix=pymupdf.Matrix(2, 2))
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        image.save(path, fmt, **({"quality": 98} if fmt == "JPEG" else {}))
    expected = recognize_invoice(sample_pdf)
    result = recognize_invoice(path)
    assert result.source == "本地 OCR"
    for field in ("number", "issued_on", "total", "net_total", "tax_total"):
        assert getattr(result, field) == getattr(expected, field)
    assert result.items[0].quantity == expected.items[0].quantity
    assert result.items[0].gross() == expected.items[0].gross()


def test_invalid_optional_upload_still_rejected(materials, tmp_path):
    fake_png = tmp_path / "伪装.png"
    fake_png.write_bytes(materials.payments[0].read_bytes())
    assert "verification_png" in submission_errors(replace(materials, verification_png=fake_png, photos=[]))
    assert "photos" in submission_errors(replace(materials, verification_png=None, photos=[fake_png]))
    fake_invoice = tmp_path / "发票.jpg"
    fake_invoice.write_bytes(materials.verification_png.read_bytes())
    with pytest.raises(AppError, match="实际图片格式"):
        validate_file(fake_invoice, "invoice")


def test_gui_generation_without_optional_uploads(window, qapp, materials, invoice, templates, tmp_path, monkeypatch):
    from test_gui import fill, wait_job
    import pyocrpka.gui as gui
    monkeypatch.setattr(gui, "generate", lambda s, i, **kw: generate(s, i, output_dir=tmp_path / "out", template_dir=templates, **kw))
    fill(window, materials, invoice)
    window.files["verification_png"].set_paths([])
    window.files["photos"].set_paths([])
    window.confirm.setChecked(True)
    window.start_generation()
    wait_job(window, qapp)
    assert window.output_path and window.output_path.is_dir()
    assert not window.error_labels["verification_png"].text()
    assert not window.error_labels["photos"].text()


def test_gui_accepts_image_invoice(window, materials):
    control = window.files["invoice_pdf"]
    control.set_paths([materials.verification_png])
    assert control.paths == [materials.verification_png]
    assert "PNG" in control.title() and "JPG" in control.title()
