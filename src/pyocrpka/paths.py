from pathlib import Path

# 与启动时的工作目录无关；从 PyCharm 的 src/main.py 也能正常定位。
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = PROJECT_ROOT / "other" / "工单号_发票号"
OUTPUT_DIR = PROJECT_ROOT / "out"
MODEL_DIR = PROJECT_ROOT / "models"
