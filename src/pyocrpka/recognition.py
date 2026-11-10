"""PDF 文本优先、逐页 OCR 回退。此模块不接触任何 GUI 控件。"""
from pathlib import Path
from contextlib import contextmanager

import pymupdf

from .models import AppError, Invoice
from .ocr import LocalOCR
from .parser import Token, extract_candidates, parse_invoice, visual_rows, headers_in
from .validation import validate_file


@contextmanager
def open_invoice(path: Path):
    """图片发票仅在内存中包装成单页 PDF，复用原有识别流程。"""
    with pymupdf.open(path) as source:
        if source.is_pdf:
            yield source
        else:
            with pymupdf.open("pdf", source.convert_to_pdf()) as document:
                yield document


def page_tokens(page) -> list[Token]:
    return [Token(*word[:5]) for word in page.get_text("words") if "\ufffd" not in word[4]]


def scan_tokens(page, engine: LocalOCR) -> list[Token]:
    import numpy as np
    # 最长边限制在约3500像素，避免异常大页面消耗无限内存。
    scale = min(3, 3500 / max(page.rect.width, page.rect.height))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
    pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    image = pixels[:, :, ::-1].copy()
    tokens = engine.recognize(image)
    # 扫描件常把紧挨的“数量 + 高精度单价”检测为一个框；依据动态表头裁切重识别。
    if not hasattr(engine, "recognize_region"):
        return tokens
    headers = {}
    for row in visual_rows(tokens):
        found = headers_in(row)
        if "quantity" in found and "unit_price" in found:
            headers = found
            break
    if not headers:
        return tokens
    quantity, price = headers["quantity"], headers["unit_price"]
    boundary = quantity.x1 + (quantity.y1 - quantity.y0) * .15
    refined = []
    for token in tokens:
        if (token.y0 >= quantity.y1 - 2 and token.x0 < boundary < token.x1
                and token.x1 > price.x0 and all(c in "0123456789.+-" for c in token.text)):
            for x0, x1 in [(token.x0, boundary), (boundary, token.x1)]:
                crop = image[max(0, int(token.y0) - 1):int(token.y1) + 2, max(0, int(x0)):int(x1) + 1]
                value = engine.recognize_region(crop)
                if value:
                    refined.append(Token(x0, token.y0, x1, token.y1, value))
        else:
            refined.append(token)
    return refined


def _sufficient(inv: Invoice) -> bool:
    return bool(inv.number and inv.issued_on and inv.total is not None and inv.items
                and all(i.name and i.quantity is not None and i.unit_price is not None and i.net_amount is not None and i.tax is not None for i in inv.items))


def recognize_invoice(path: Path, engine: LocalOCR | None = None, *, force_ocr: bool = False) -> Invoice:
    validate_file(path, "invoice")
    from .output import digest
    original_digest = digest(path)
    engine = engine or LocalOCR()
    results = []
    page_numbers = []
    try:
        with open_invoice(path) as document:
            for page in document:
                tokens = page_tokens(page)
                result = parse_invoice(tokens)
                if sum(t.text.count("发票号码") for t in tokens) > 1 and len(result.candidates) > 1:
                    raise AppError("一页中包含多张发票，请先拆分")
                result.source = "PDF 文字层" if path.suffix.lower() == ".pdf" else "本地 OCR"
                scanned_tokens = []
                if force_ocr or not _sufficient(result):
                    try:
                        scanned_tokens = scan_tokens(page, engine)
                        scanned = parse_invoice(scanned_tokens)
                        scanned.source = "本地 OCR"
                        if force_ocr:
                            result = scanned
                        else:
                            for field in ("number", "code", "check_last6", "issued_on", "total", "net_total", "tax_total"):
                                original, fallback = getattr(result, field), getattr(scanned, field)
                                if original in (None, ""):
                                    setattr(result, field, fallback)
                                elif fallback not in (None, "") and original != fallback:
                                    result.notices.append(f"文字层与 OCR 的 {field} 结果不同，请手动核对")
                            if not result.items:
                                result.items = scanned.items
                            elif len(result.items) == len(scanned.items):
                                for original_item, scanned_item in zip(result.items, scanned.items):
                                    if original_item.name != scanned_item.name:
                                        continue
                                    for key in ("quantity", "unit_price", "net_amount", "tax", "unit", "specification", "tax_rate"):
                                        if getattr(original_item, key) in (None, ""):
                                            setattr(original_item, key, getattr(scanned_item, key))
                            result.candidates = list(dict.fromkeys(result.candidates + scanned.candidates))
                            if len(result.candidates) > 1:
                                result.number = ""
                                result.notices.append("存在多个候选发票号码，请手动确认")
                            result.notices.extend(scanned.notices)
                            result.source = "PDF 文字层 + 本地 OCR"
                            if path.suffix.lower() != ".pdf":
                                result.source = "本地 OCR"
                    except AppError as e:
                        result.notices.append(str(e))
                if sum(t.text.count("发票号码") for t in scanned_tokens) > 1 and len(extract_candidates(scanned_tokens)) > 1:
                    raise AppError("一页中包含多张发票，请先拆分")
                page_numbers.append(set(result.candidates))
                results.append(result)
    except AppError:
        raise
    except Exception:
        raise AppError("发票读取或渲染失败，请检查文件是否损坏") from None
    unique = set().union(*(n for n in page_numbers if len(n) == 1))
    if len(unique) > 1:
        raise AppError("PDF 中检测到多张不同发票，请拆分后分别生成")
    if len(results) > 1 and any(not n for n in page_numbers):
        raise AppError("多页 PDF 中有页面无法确认发票归属，请拆分或补充清晰文件")
    first = results[0]
    for other in results[1:]:
        first.items.extend(other.items)
        first.notices.extend(other.notices)
        if first.total is None:
            first.total = other.total
        elif other.total is not None and other.total != first.total:
            first.notices.append("多页发票的价税合计不同，请核对")
    if digest(path) != original_digest:
        raise AppError("发票在识别过程中发生变化，请重新选择并识别")
    first.source_digest = original_digest
    return first


def verify_document_scope(path: Path):
    with open_invoice(path) as doc:
        if len(doc) == 1:
            # 同页多个发票标题/号码布局，不允许把拼接PDF当单张发票。
            tokens = page_tokens(doc[0])
            if len(tokens) < 3:
                try:
                    tokens = scan_tokens(doc[0], LocalOCR())
                except AppError:
                    pass  # 单页OCR故障时仍允许人工录入；识别界面明确显示缺失警告。
            labels = sum(t.text.count("发票号码") for t in tokens)
            if labels > 1 and len(extract_candidates(tokens)) > 1:
                raise AppError("一页中包含多张发票，请先拆分")
            return
    recognize_invoice(path)  # 多页必须逐页验证归属，扫描续页使用本地OCR。
