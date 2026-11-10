"""真实样例验收，独立系统临时目录中保留结果供复核，不写项目 out。"""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile

from openpyxl import load_workbook
import pymupdf

from pyocrpka.models import Submission
from pyocrpka.output import digest, generate
from pyocrpka.paths import PROJECT_ROOT, TEMPLATE_DIR
from pyocrpka.recognition import recognize_invoice


def main():
    temp = Path(tempfile.mkdtemp(prefix="pyocrpka-acceptance-"))
    pdf = next(TEMPLATE_DIR.rglob("*.pdf"))
    verification = next(TEMPLATE_DIR.rglob("*发票查验.png"))
    photos = sorted(TEMPLATE_DIR.rglob("*实物*.jpg"))
    payments = sorted(TEMPLATE_DIR.rglob("*付款凭证*.jpg"))
    original = {str(p): digest(p) for p in TEMPLATE_DIR.rglob("*") if p.is_file()}
    text = recognize_invoice(pdf)
    scanned_pdf = temp / "扫描版.pdf"
    with pymupdf.open(pdf) as source, pymupdf.open() as scanned:
        pix = source[0].get_pixmap(matrix=pymupdf.Matrix(3, 3))
        page = scanned.new_page(width=source[0].rect.width, height=source[0].rect.height)
        page.insert_image(page.rect, stream=pix.tobytes("png"))
        scanned.save(scanned_pdf)
    ocr = recognize_invoice(scanned_pdf)
    for key in ("number", "issued_on", "total", "net_total", "tax_total"):
        if getattr(text, key) != getattr(ocr, key):
            raise RuntimeError(f"文字层和扫描 OCR 字段不一致：{key}")
    if text.items != ocr.items:
        raise RuntimeError("文字层和扫描 OCR 商品明细不一致")
    submission = Submission("验收样例-001", "验收测试", "微信", "硬件", pdf, verification, payments, photos)
    target = generate(submission, text, output_dir=temp / "out", warnings_confirmed=True)
    books = {}
    for path in sorted(target.glob("*.xlsx")):
        workbook = load_workbook(path)
        books[path.name] = {"headers": [c.value for c in workbook.active[1]],
                            "rows": [[c.value for c in row] for row in workbook.active.iter_rows(min_row=2) if any(c.value is not None for c in row)]}
        workbook.close()
    if {str(p): digest(p) for p in TEMPLATE_DIR.rglob("*") if p.is_file()} != original:
        raise RuntimeError("源材料哈希变化")
    report = {"temporary_directory": str(temp), "generated_directory": str(target),
              "source_unchanged": True, "text_layer": asdict(text), "scanned_ocr": asdict(ocr),
              "files": sorted(str(p.relative_to(target)) for p in target.rglob("*") if p.is_file()), "workbooks": books}
    artifact_dir = PROJECT_ROOT / "artifacts"
    artifact_dir.mkdir(exist_ok=True)
    report_path = artifact_dir / "acceptance.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"真实样例、扫描回退、逐列填写、原文件哈希均通过。\n样例结果：{target}\n报告：{report_path}")


if __name__ == "__main__":
    main()
