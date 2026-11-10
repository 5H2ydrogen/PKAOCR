from pathlib import Path
import time

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
import pytest

from pyocrpka.models import CATEGORIES, PAYMENTS, Item


def wait_job(window, qapp, timeout=15):
    deadline = time.monotonic() + timeout
    while window.worker is not None and time.monotonic() < deadline:
        qapp.processEvents()
        QTest.qWait(10)
    assert window.worker is None


def fill(window, materials, invoice):
    window.work_order.setText(materials.work_order)
    window.name.setText(materials.name)
    window.payment_group.buttons()[0].click()
    window.category_group.buttons()[8].click()
    for key in ("invoice_pdf", "verification_png", "payments", "photos"):
        value = getattr(materials, key)
        window.files[key].set_paths(value if isinstance(value, list) else [value])
    window.load_invoice(invoice)


def test_empty_and_live_error_clear(window):
    assert window.submission().payment == "" and window.submission().category == ""
    window.start_generation()
    assert all(label.text() for key, label in window.error_labels.items() if key not in ("verification_png", "photos"))
    assert not window.error_labels["verification_png"].text() and not window.error_labels["photos"].text()
    assert window.worker is None
    window.name.setText("张同学")
    assert window.error_labels["name"].text() == ""
    assert window.error_labels["work_order"].text()
    window.payment_group.buttons()[0].click()
    assert not window.error_labels["payment"].text()
    window.category_group.buttons()[0].click()
    assert not window.error_labels["category"].text()


def test_exclusive_choices_and_latest(window):
    assert [b.text() for b in window.payment_group.buttons()] == list(PAYMENTS)
    assert [b.text() for b in window.category_group.buttons()] == list(CATEGORIES)
    for group, key in [(window.payment_group, "payment"), (window.category_group, "category")]:
        for button in group.buttons():
            button.click()
            assert sum(b.isChecked() for b in group.buttons()) == 1
            assert getattr(window.submission(), key) == button.text()


def test_order_remove_and_replace(window, materials):
    control = window.files["photos"]
    control.set_paths(materials.photos)
    first, second = control.paths
    control.list.setCurrentRow(1);control.move(-1)
    assert control.paths == [second, first]
    control.list.setCurrentRow(0);control.remove()
    assert control.paths == [first] and "1" in control.count.text()
    control.set_paths([second])
    assert control.paths == [second]


def test_manual_edit_dates_and_confirmation(window, materials, invoice):
    fill(window, materials, invoice)
    window.confirm.setChecked(True)
    window.invoice_fields["total"].setText("123.45")
    assert not window.confirm.isChecked()
    assert str(window.read_invoice().total) == "123.45"
    assert "不一致" in window.warnings_label.text()
    window.invoice_fields["issued_on"].setText("2026-02-30")
    window.confirm.setChecked(True)
    window.start_generation()
    assert window.worker is None and "日期" in window.result_error.text()


def test_invoice_change_clears_old_result(window, materials, invoice):
    fill(window, materials, invoice)
    window.confirm.setChecked(True)
    window.files["invoice_pdf"].set_paths([materials.invoice_pdf])
    assert not window.read_invoice().number and not window.read_invoice().items
    assert not window.confirm.isChecked()


def test_ocr_async_success(window, qapp, sample_pdf, sample_invoice_number):
    window.files["invoice_pdf"].set_paths([sample_pdf])
    window.start_ocr()
    assert not window.canvas.isEnabled()
    wait_job(window, qapp)
    assert window.read_invoice().number == sample_invoice_number
    assert window.canvas.isEnabled() and "识别完成" in window.status.text()


def test_generate_background_responsive_and_close_guard(window, qapp, materials, invoice, templates, tmp_path, monkeypatch):
    from pyocrpka.output import generate
    import pyocrpka.gui as gui
    def local_generate(s, inv, **kwargs):
        # 用事件同步验证执行期间事件循环可继续处理，不依赖耗时阈值。
        import threading
        assert released.wait(5)
        return generate(s, inv, output_dir=tmp_path / "out", template_dir=templates, **kwargs)
    import threading
    released = threading.Event()
    monkeypatch.setattr(gui, "generate", local_generate)
    fill(window, materials, invoice)
    window.start_generation()
    assert window.worker is None and "勾选确认" in window.status.text()
    window.confirm.setChecked(True)
    window.show()
    window.start_generation()
    assert window.worker is not None and not window.generate_button.isEnabled()
    assert not window.close()
    QTimer.singleShot(10, released.set)
    wait_job(window, qapp)
    assert window.output_path.is_dir()
    assert "生成成功" in window.status.text()
    assert window.open_button.isEnabled()


def test_generate_failure_reenables(window, qapp, materials, invoice, monkeypatch):
    import pyocrpka.gui as gui
    from pyocrpka.models import AppError
    def fail(*args, **kwargs):
        raise AppError("模拟磁盘错误")
    monkeypatch.setattr(gui, "generate", fail)
    fill(window, materials, invoice)
    window.confirm.setChecked(True)
    window.start_generation()
    wait_job(window, qapp)
    assert "模拟磁盘错误" in window.status.text()
    assert window.generate_button.isEnabled() and window.output_path is None


def test_latest_manual_edits_reach_excel(window, qapp, materials, invoice, templates, tmp_path, monkeypatch):
    from openpyxl import load_workbook
    import pyocrpka.gui as gui
    from pyocrpka.output import generate
    monkeypatch.setattr(gui, "generate", lambda s, i, **kw: generate(s, i, output_dir=tmp_path / "out", template_dir=templates, **kw))
    fill(window, materials, invoice)
    window.payment_group.buttons()[1].click()
    window.category_group.buttons()[10].click()
    window.invoice_fields["number"].setEditText("00001111")
    window.invoice_fields["total"].setText("31.90")
    window.invoice_fields["net_total"].setText("29.00")
    window.invoice_fields["tax_total"].setText("2.90")
    window.table.item(0, 2).setText("4")
    window.table.item(0, 5).setText("20.00")
    window.table.item(0, 7).setText("2.00")
    window.confirm.setChecked(True)
    window.start_generation()
    wait_job(window, qapp)
    target = window.output_path
    assert target and target.name.endswith("_00001111")
    a = load_workbook(target / f"{target.name}_表1.xlsx").active
    b = load_workbook(target / f"{target.name}_表2.xlsx").active
    assert a["C2"].value == 31.9 and a["D2"].value == "支付宝"
    assert b["C2"].value == "场地" and b["M2"].value == "支付宝"
    assert b["K2"].value == 22 and b["K3"].value == 9.9


def test_deleted_upload_shows_error_near_field(window, qapp, materials, invoice, templates, tmp_path, monkeypatch):
    import pyocrpka.gui as gui
    from pyocrpka.output import generate
    monkeypatch.setattr(gui, "generate", lambda s, i, **kw: generate(s, i, output_dir=tmp_path / "out", template_dir=templates, **kw))
    fill(window, materials, invoice)
    materials.verification_png.unlink()
    window.confirm.setChecked(True)
    window.start_generation()
    wait_job(window, qapp)
    assert "不存在" in window.error_labels["verification_png"].text()
    assert window.output_path is None
