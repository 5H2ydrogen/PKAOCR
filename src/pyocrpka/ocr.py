"""显式本地模型路径；没有网络下载或远程识别入口。"""
from importlib.util import find_spec
import json
from pathlib import Path
import shutil
import threading

from .models import AppError
from .parser import Token
from .paths import MODEL_DIR

MODEL_FILES = {"det": "ch_PP-OCRv4_det_infer.onnx", "cls": "ch_ppocr_mobile_v2.0_cls_infer.onnx", "rec": "ch_PP-OCRv4_rec_infer.onnx"}


def prepare_models(directory: Path = MODEL_DIR) -> dict[str, Path]:
    """模型随 rapidocr wheel 安装；显式准备只复制本地文件，不联网。"""
    from .output import digest
    spec = find_spec("rapidocr_onnxruntime")
    if spec is None or not spec.origin:
        raise AppError("未安装 RapidOCR，请先执行 uv sync")
    bundled = Path(spec.origin).parent / "models"
    directory.mkdir(parents=True, exist_ok=True)
    result = {}
    for key, filename in MODEL_FILES.items():
        source, destination = bundled / filename, directory / filename
        if not source.is_file():
            raise AppError("OCR 安装包缺少模型，请重新安装依赖")
        if destination.exists() and digest(source) != digest(destination):
            raise AppError("本地 OCR 模型校验失败，请移走 models 后重新准备")
        if not destination.exists():
            shutil.copyfile(source, destination)
        result[key] = destination
    manifest = {p.name: digest(p) for p in result.values()}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return result


class LocalOCR:
    def __init__(self, directory: Path = MODEL_DIR):
        self.directory = directory
        self._engine = None
        self._lock = threading.Lock()

    def _load(self):
        if self._engine is not None:
            return
        paths = {key: self.directory / filename for key, filename in MODEL_FILES.items()}
        if not all(p.is_file() for p in paths.values()):
            raise AppError("OCR 模型尚未准备，请运行 uv run pyocrpka --prepare-models")
        try:
            from .output import digest
            manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
            if any(manifest.get(p.name) != digest(p) for p in paths.values()):
                raise ValueError("model checksum")
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from rapidocr_onnxruntime import RapidOCR
            self._engine = RapidOCR(**{f"{key}_model_path": str(path) for key, path in paths.items()},
                                    intra_op_num_threads=2, inter_op_num_threads=1,
                                    det_use_cuda=False, cls_use_cuda=False, rec_use_cuda=False)
        except Exception:
            raise AppError("本地 OCR 模型加载失败，请检查 models 或重新准备模型") from None

    def recognize(self, image) -> list[Token]:
        with self._lock:
            self._load()
            try:
                result, _ = self._engine(image)
                return [Token(min(p[0] for p in box), min(p[1] for p in box),
                              max(p[0] for p in box), max(p[1] for p in box), text)
                        for box, text, score in result or [] if score >= .5]
            except Exception:
                raise AppError("本地 OCR 执行失败，请检查发票清晰度或手动填写结果") from None

    def recognize_region(self, image) -> str:
        """对跨列文本的局部裁切重新识别，不通过字符串猜拆数量/单价。"""
        import cv2
        with self._lock:
            self._load()
            image = cv2.copyMakeBorder(image, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=(255, 255, 255))
            result, _ = self._engine(image, use_det=False, use_cls=False)
            return result[0][0] if result and result[0][1] >= .8 else ""
