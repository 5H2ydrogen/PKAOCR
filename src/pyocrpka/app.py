import argparse
import logging
from logging.handlers import RotatingFileHandler
import sys

from .models import AppError
from .paths import PROJECT_ROOT


def setup_logging():
    directory = PROJECT_ROOT / "logs"
    directory.mkdir(exist_ok=True)
    handler = RotatingFileHandler(directory / "app.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger = logging.getLogger("pyocrpka")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def main():
    parser = argparse.ArgumentParser(description="RoboMaster 本地报销工具")
    parser.add_argument("--prepare-models", action="store_true", help="从已安装依赖中准备本地 OCR 模型（不联网）")
    args = parser.parse_args()
    if args.prepare_models:
        from .ocr import prepare_models, LocalOCR
        try:
            paths = prepare_models()
            LocalOCR()._load()
        except AppError as e:
            parser.exit(1, f"模型准备失败：{e}\n")
        print(f"已准备并加载 {len(paths)} 个本地 CPU OCR 模型，可离线使用。")
        return
    from PySide6.QtWidgets import QApplication
    from .gui import MainWindow
    app = QApplication(sys.argv[:1])
    app.setApplicationName("RoboMaster 报销材料整理")
    app.setStyle("Fusion")
    try:
        setup_logging()
    except OSError:
        pass
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
