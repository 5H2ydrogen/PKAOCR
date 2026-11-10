"""验证、复制、检查后原子发布；从不覆盖已有报销目录。"""
import ctypes
import errno
import hashlib
import os
from pathlib import Path
import shutil
import sys
import tempfile

from .excel import fill_templates, verify_workbooks
from .models import AppError, Invoice, Submission, ValidationError
from .paths import OUTPUT_DIR, TEMPLATE_DIR
from .validation import submission_errors, validate_file


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def publish_exclusive(source: Path, target: Path):
    """macOS 的 RENAME_EXCL 使存在检查到发布之间也不能覆盖同名目录。"""
    if sys.platform != "darwin" and not sys.platform.startswith("linux"):
        raise AppError("当前系统不支持安全发布；本版本优先支持 macOS")
    libc = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        fn = libc.renamex_np
        fn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        result = fn(os.fsencode(source), os.fsencode(target), 4)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    else:
        raise AppError("当前系统不支持安全发布；本版本优先支持 macOS")
    if result != 0:
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise AppError("目标目录已存在，已停止生成，请先检查已有报销材料")
        raise OSError(code, os.strerror(code))


def generate(s: Submission, inv: Invoice, *, output_dir: Path = OUTPUT_DIR,
             template_dir: Path = TEMPLATE_DIR, warnings_confirmed: bool = False) -> Path:
    errors = submission_errors(s)
    errors.update(inv.errors())
    if errors:
        raise ValidationError(errors)
    if inv.warnings() and not warnings_confirmed:
        raise AppError("存在缺失或冲突字段，请检查警告并确认后再生成")
    if inv.source_digest and digest(s.invoice_pdf) != inv.source_digest:
        raise AppError("发票在识别后发生变化，请重新选择并识别")
    # 生成入口同样检查一份 PDF 是否混入不同发票，人工输入不能绕过。
    from .recognition import open_invoice, verify_document_scope
    verify_document_scope(s.invoice_pdf)
    prefix = f"{s.work_order}_{inv.number}"
    if output_dir.is_symlink():
        raise AppError("out 不能是符号链接，请使用项目内的真实文件夹")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise AppError("无法创建 out 目录，请检查写入权限和磁盘空间") from None
    target = output_dir / prefix
    if target.exists() or target.is_symlink():
        raise AppError("目标目录已存在，已停止生成，请先检查已有报销材料")
    try:
        with tempfile.TemporaryDirectory(prefix=".reimbursement-", dir=output_dir) as temporary:
            stage = Path(temporary) / prefix
            materials = stage / f"{prefix}_报销材料"
            materials.mkdir(parents=True)
            copies = [(s.invoice_pdf, materials / f"{prefix}_发票.pdf")]
            if s.verification_png is not None:
                copies.append((s.verification_png, materials / f"{prefix}_发票查验.png"))
            for label, sources in [("实物", s.photos), ("付款凭证", s.payments)]:
                directory = materials / f"{prefix}_{label}"
                directory.mkdir()
                copies.extend((source, directory / f"{prefix}_{label}{i}{source.suffix}") for i, source in enumerate(sources, 1))
            for source, dest in copies:
                before = digest(source)
                if source == s.invoice_pdf and inv.source_digest and before != inv.source_digest:
                    raise AppError("发票在识别后发生变化，请重新选择并识别")
                invoice_image = source == s.invoice_pdf and source.suffix.lower() != ".pdf"
                if invoice_image:
                    # 保持既有输出文件树：将图片真正转为PDF，不伪装扩展名。
                    with open_invoice(source) as document:
                        document.save(dest)
                else:
                    shutil.copyfile(source, dest)
                if (not invoice_image and digest(dest) != before) or digest(source) != before:
                    raise AppError("复制完整性检查失败或原文件被修改，请重新选择材料")
                suffix = dest.suffix.lower()
                validate_file(dest, "pdf" if suffix == ".pdf" else "png" if suffix == ".png" else "jpeg")
            books = fill_templates(template_dir, stage, prefix, s, inv)
            verify_workbooks(books, s, inv)
            if sum(1 for p in stage.rglob("*") if p.is_file()) != 3 + bool(s.verification_png) + len(s.payments) + len(s.photos):
                raise AppError("输出文件不完整，已停止保存")
            publish_exclusive(stage, target)
        return target
    except AppError:
        raise
    except PermissionError:
        raise AppError("没有写入权限，请检查 out 文件夹和模板的访问权限") from None
    except OSError as e:
        if e.errno == errno.ENOSPC:
            raise AppError("磁盘空间不足，本次临时结果已清理") from None
        raise AppError("文件读写失败，请检查材料是否存在、磁盘空间及文件夹权限") from None
    except Exception:
        raise AppError("生成结果检查失败，本次临时结果已清理，请查看本地日志") from None
