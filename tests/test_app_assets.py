from PIL import Image
import os
import subprocess
import sys
import pytest

from pyocrpka.paths import APP_ICON


def test_app_icon_has_real_transparency_and_high_resolution():
    with Image.open(APP_ICON) as icon:
        assert icon.format == "PNG" and icon.mode == "RGBA"
        assert min(icon.size) >= 1024
        alpha = icon.getchannel("A")
        assert alpha.getextrema() == (0, 255)
        for corner in ((0, 0), (icon.width - 1, 0), (0, icon.height - 1), (icon.width - 1, icon.height - 1)):
            assert alpha.getpixel(corner) == 0


@pytest.mark.parametrize("ratio", [1.0, 2.0, 3.0])
def test_avatar_uses_native_display_pixel_density(ratio):
    # 使用真实Qt缩放环境，不能只模拟Python方法而让QLabel仍运行在1×屏幕。
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["QT_SCALE_FACTOR"] = str(ratio)
    script = """
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
from pyocrpka.gui import MainWindow
import os
app = QApplication([])
window = MainWindow()
window.show()
app.processEvents()
QApplication.sendEvent(window, QEvent(QEvent.Type.DevicePixelRatioChange))
ratio = float(os.environ['QT_SCALE_FACTOR'])
pixmap = window.logo.pixmap()
assert window.devicePixelRatioF() == ratio
assert not pixmap.isNull() and pixmap.hasAlphaChannel()
assert pixmap.devicePixelRatioF() == ratio
assert max(pixmap.width(), pixmap.height()) == round(64 * ratio)
assert max(pixmap.deviceIndependentSize().width(), pixmap.deviceIndependentSize().height()) == 64
icon = window.windowIcon().pixmap(128, 128).toImage()
assert not icon.isNull() and icon.hasAlphaChannel()
assert icon.pixelColor(0, 0).alpha() == 0
window.close()
"""
    result = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
