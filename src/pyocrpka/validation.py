from pathlib import Path
import re

import pymupdf
from PIL import Image, UnidentifiedImageError

from .models import AppError, CATEGORIES, PAYMENTS, Submission


def safe_component(value: str) -> bool:
    return bool(value and value.strip() == value and len(value) <= 70 and value not in (".", "..")
                and not re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', value) and not value.endswith("."))


def validate_file(path: Path, kind: str) -> None:
    if kind in ("invoice", "image"):
        formats = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg"}
        if kind == "invoice":
            formats[".pdf"] = "pdf"
        actual_kind = formats.get(path.suffix.lower())
        if actual_kind is None:
            raise AppError("发票必须为 PDF、PNG 或 JPG / JPEG" if kind == "invoice" else "图片必须为 PNG 或 JPG / JPEG")
        validate_file(path, actual_kind)
        return
    suffixes = {"pdf": (".pdf",), "png": (".png",), "jpeg": (".jpg", ".jpeg")}
    if not path.is_file():
        raise AppError("文件不存在或已被移动，请重新选择")
    if path.suffix.lower() not in suffixes[kind]:
        raise AppError({"pdf": "发票必须为 PDF", "png": "发票查验必须为 PNG", "jpeg": "照片必须为 JPG / JPEG"}[kind])
    try:
        if kind == "pdf":
            with path.open("rb") as f:
                if not f.read(1024).startswith(b"%PDF-"):
                    raise AppError("文件实际内容不是 PDF")
            with pymupdf.open(path) as doc:
                if doc.needs_pass:
                    raise AppError("PDF 已加密，请先提供可直接读取的发票")
                if not doc.is_pdf or len(doc) == 0:
                    raise AppError("PDF 无有效页面")
                if len(doc) > 20:
                    raise AppError("PDF 超过 20 页，请拆分为单张发票")
                for page in doc:
                    if page.rect.is_empty:
                        raise AppError("PDF 页面无效")
                    page.get_text()
        else:
            with Image.open(path) as im:
                if im.format != {"png": "PNG", "jpeg": "JPEG"}[kind]:
                    raise AppError("文件扩展名与实际图片格式不一致，请重新导出正确格式")
                im.verify()
            with Image.open(path) as im:
                im.load()
    except AppError:
        raise
    except (OSError, ValueError, RuntimeError, UnidentifiedImageError, Image.DecompressionBombError):
        raise AppError("文件损坏或无法读取，请重新选择") from None


def submission_errors(s: Submission, check_files: bool = True) -> dict[str, str]:
    errors = {}
    if not s.work_order.strip():
        errors["work_order"] = "请填写工单号"
    elif not safe_component(s.work_order):
        errors["work_order"] = "工单号不能包含路径或非法字符，且最多 70 字符"
    if not s.name.strip():
        errors["name"] = "请填写姓名"
    elif len(s.name) > 100 or re.search(r"[\x00-\x1f]", s.name):
        errors["name"] = "姓名过长或包含控制字符"
    if s.payment not in PAYMENTS:
        errors["payment"] = "请选择付款方式"
    if s.category not in CATEGORIES:
        errors["category"] = "请选择类别"
    fields = [("invoice_pdf", s.invoice_pdf, "invoice", "请上传发票（PDF / PNG / JPG）"),
              ("verification_png", s.verification_png, "png", "请上传发票查验图片"),
              ("payments", s.payments, "image", "请上传至少一张付款凭证"),
              ("photos", s.photos, "image", "请上传至少一张实物照片")]
    for key, value, kind, missing in fields:
        if not value:
            if key not in ("verification_png", "photos"):
                errors[key] = missing
        elif check_files:
            for path in value if isinstance(value, list) else [value]:
                try:
                    validate_file(path, kind)
                except AppError as e:
                    errors[key] = str(e)
                    break
    return errors
