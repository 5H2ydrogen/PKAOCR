"""原生桌面启动与截图验收；只读取参考文件，不生成报销记录。"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pyocrpka.gui import MainWindow
from pyocrpka.paths import PROJECT_ROOT, TEMPLATE_DIR
from pyocrpka.recognition import recognize_invoice

app = QApplication([])
app.setStyle("Fusion")
window = MainWindow()
window.work_order.setText("验收样例-001")
window.name.setText("验收测试")
window.payment_group.buttons()[0].click()
window.category_group.buttons()[8].click()
for key, paths in [
    ("invoice_pdf", list(TEMPLATE_DIR.rglob("*.pdf"))),
    ("verification_png", list(TEMPLATE_DIR.rglob("*发票查验.png"))),
    ("payments", sorted(TEMPLATE_DIR.rglob("*付款凭证*.jpg"))),
    ("photos", sorted(TEMPLATE_DIR.rglob("*实物*.jpg"))),
]:
    window.files[key].set_paths(paths)
window.load_invoice(recognize_invoice(window.files["invoice_pdf"].paths[0]))
window.show()


def check():
    app.processEvents()
    artifact_dir = PROJECT_ROOT / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    if not window.grab().save(str(artifact_dir / "界面验收.png")):
        raise RuntimeError("截图失败")
    print(f"GUI 平台：{app.platformName()}；窗口可见：{window.isVisible()}；截图完成", flush=True)
    window.close()
    app.quit()


QTimer.singleShot(2000, check)
app.exec()
