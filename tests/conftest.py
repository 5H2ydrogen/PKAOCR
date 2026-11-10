import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from datetime import date
from decimal import Decimal as D
from pathlib import Path
import shutil
import socket
import re

import pymupdf
from PIL import Image
import pytest

from pyocrpka.models import Invoice, Item, Submission
from pyocrpka.paths import TEMPLATE_DIR


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("正常识别和生成流程不允许联网")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


@pytest.fixture
def materials(tmp_path):
    pdf = tmp_path / "原始发票.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((30, 30), "Invoice test fixture")
    doc.save(pdf)
    doc.close()
    png = tmp_path / "查验.png"
    Image.new("RGB", (24, 24), "white").save(png)
    jpgs = []
    for i, color in enumerate(("red", "green", "blue")):
        path = tmp_path / f"照片{i}.jpeg"
        Image.new("RGB", (32, 32), color).save(path, "JPEG")
        jpgs.append(path)
    return Submission("TEST-流水号", "测试成员", "微信", "硬件", pdf, png, jpgs[:2], jpgs[1:])


@pytest.fixture
def invoice():
    return Invoice(number="20268888000012345678", issued_on=date(2026, 5, 12), total=D("26.40"),
                   net_total=D("24"), tax_total=D("2.40"),
                   items=[Item("齿轮", quantity=D("3"), unit="个", unit_price=D("5"), net_amount=D("15"), tax=D("1.50")),
                          Item("电机", quantity=D("2"), unit="只", unit_price=D("4.50"), net_amount=D("9"), tax=D("0.90"))])


@pytest.fixture
def templates(tmp_path):
    dest = tmp_path / "templates"
    dest.mkdir()
    for path in TEMPLATE_DIR.glob("*.xlsx"):
        shutil.copyfile(path, dest / path.name)
    return dest


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    yield app


@pytest.fixture
def window(qapp):
    from pyocrpka.gui import MainWindow
    w = MainWindow()
    yield w
    if w.worker:
        w.worker.wait(30000)
        qapp.processEvents()
    w.close()
    w.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.fixture(scope="session")
def sample_pdf():
    files = list(TEMPLATE_DIR.rglob("*.pdf"))
    if not files:
        pytest.skip("工作区未提供真实 PDF 样例")
    return files[0]


@pytest.fixture(scope="session")
def sample_invoice_number(sample_pdf):
    # 本地私人样例的期望号码直接从原文件读取，不把真实票号发布到源码。
    with pymupdf.open(sample_pdf) as document:
        numbers = [w[4] for page in document for w in page.get_text("words") if re.fullmatch(r"\d{20}", w[4])]
    assert len(set(numbers)) == 1, "本地样例应包含唯一的数电发票号码"
    return numbers[0]
