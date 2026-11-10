from dataclasses import replace

from PIL import Image
from PySide6.QtCore import QModelIndex
from PySide6.QtWidgets import QFileDialog, QPushButton
import pytest

from pyocrpka.models import AppError
from pyocrpka.output import digest, generate
from pyocrpka.validation import submission_errors, validate_file


def test_all_file_controls_only_have_add_delete(window):
    for control in window.files.values():
        assert [button.text() for button in control.findChildren(QPushButton)] == ["添加", "删除"]


def test_single_add_requires_delete_before_another(window, materials, monkeypatch):
    control = window.files["invoice_pdf"]
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(materials.invoice_pdf), ""))
    control.add_button.click()
    assert control.paths == [materials.invoice_pdf] and not control.add_button.isEnabled()
    control.findChildren(QPushButton)[1].click()
    assert control.paths == [] and control.add_button.isEnabled()
    control.add_button.click()
    assert control.paths == [materials.invoice_pdf]


def test_multiple_add_appends_and_drag_order_matches_paths(window, materials, tmp_path, monkeypatch):
    one = tmp_path / "one" / "图片.png"
    two = tmp_path / "two" / "图片.png"
    one.parent.mkdir(); two.parent.mkdir()
    Image.new("RGB", (20, 20), "red").save(one)
    Image.new("RGB", (20, 20), "blue").save(two)
    answers = iter(([str(one)], [str(two), str(one)]))
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *args: (next(answers), ""))
    control = window.files["payments"]
    control.add_button.click(); control.add_button.click()
    assert control.paths == [one, two] and control.add_button.isEnabled()
    window.confirm.setChecked(True)
    assert control.list.model().moveRow(QModelIndex(), 1, QModelIndex(), 0)
    assert control.paths == [two, one] and not window.confirm.isChecked()
    control.list.setCurrentRow(0)
    control.findChildren(QPushButton)[1].click()
    assert control.paths == [one]


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "P"])
def test_png_attachment_keeps_original_bytes(materials, invoice, templates, tmp_path, mode):
    png = tmp_path / "透明凭证.png"
    image = Image.new("RGBA", (40, 24), (0, 0, 0, 0))
    image.paste((255, 0, 0, 255), (0, 0, 15, 24))
    image.convert(mode).save(png)
    before = digest(png)
    submission = replace(materials, payments=[png, materials.payments[0]], photos=[png])
    assert not submission_errors(submission)
    target = generate(submission, invoice, output_dir=tmp_path / "out", template_dir=templates)
    prefix = target.name
    for label in ("付款凭证", "实物"):
        dest = target / f"{prefix}_报销材料" / f"{prefix}_{label}" / f"{prefix}_{label}1.png"
        validate_file(dest, "png")
        assert digest(dest) == before
        with Image.open(dest) as result:
            assert result.format == "PNG" and result.size == (40, 24)
            if mode == "RGBA":
                assert result.getpixel((30, 12))[3] == 0
    copied_jpg = target / f"{prefix}_报销材料" / f"{prefix}_付款凭证" / f"{prefix}_付款凭证2{materials.payments[0].suffix}"
    assert digest(copied_jpg) == digest(materials.payments[0])
    assert digest(png) == before


@pytest.mark.parametrize("extension,format", [("png", "PNG"), ("jpg", "JPEG"), ("jpeg", "JPEG"),
                                              ("PNG", "PNG"), ("JpG", "JPEG"), ("JPEG", "JPEG")])
def test_attachment_format_and_extension_are_preserved(materials, invoice, templates, tmp_path, extension, format):
    source = tmp_path / f"上传材料.{extension}"
    Image.new("RGB", (16, 20), "blue").save(source, format)
    before = digest(source)
    submission = replace(materials, payments=[source], photos=[source])
    target = generate(submission, invoice, output_dir=tmp_path / "out", template_dir=templates)
    for label in ("实物", "付款凭证"):
        folder = target / f"{target.name}_报销材料" / f"{target.name}_{label}"
        dest = folder / f"{target.name}_{label}1.{extension}"
        assert list(folder.iterdir()) == [dest]
        assert digest(dest) == before and digest(source) == before
        validate_file(dest, "image")


def test_attachment_rejects_disguised_image_and_pdf(materials, tmp_path):
    wrong = tmp_path / "wrong.jpg"
    wrong.write_bytes(materials.verification_png.read_bytes())
    with pytest.raises(AppError, match="实际图片格式"):
        validate_file(wrong, "image")
    with pytest.raises(AppError):
        validate_file(materials.invoice_pdf, "image")


def test_gui_generates_png_attachments(window, qapp, materials, invoice, templates, tmp_path, monkeypatch):
    from test_gui import fill, wait_job
    import pyocrpka.gui as gui
    monkeypatch.setattr(gui, "generate", lambda s, i, **kw: generate(s, i, output_dir=tmp_path / "out", template_dir=templates, **kw))
    fill(window, materials, invoice)
    window.files["payments"].set_paths([materials.verification_png])
    window.files["photos"].set_paths([materials.verification_png])
    window.confirm.setChecked(True)
    window.start_generation()
    wait_job(window, qapp)
    assert window.output_path and window.output_path.is_dir()


def test_invalid_amount_clears_old_total_preview(window, invoice):
    window.load_invoice(invoice)
    assert window.calculated_label.text()
    window.table.item(0, 4).setText("非法单价")
    assert window.result_error.text() and not window.calculated_label.text()
