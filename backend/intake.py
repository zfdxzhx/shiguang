"""PDF 接入：校验、SHA256、页数统计、首页渲染与私有存储。

第 2 步：只做上传与预览，不调用任何 AI。
原文件只保存在本机私有目录 runtime/private/（已在 .gitignore 中排除）。
所有对外返回的元信息都不包含本机绝对路径。
"""

from __future__ import annotations

import hashlib
import time
import uuid
from pathlib import Path

import pymupdf  # PyMuPDF 1.28+：推荐 import pymupdf（fitz 是旧别名）

# 私有存储根目录：<项目>/runtime/private
PRIVATE_DIR = Path(__file__).resolve().parent.parent / "runtime" / "private"

# 预览图最大边长（像素），避免大幅面图纸渲染过大
MAX_PREVIEW_SIDE = 1600
# 上传大小上限（字节）
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class IntakeError(Exception):
    """上传/校验失败，携带面向用户的中文提示与 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def _safe_filename(filename: str) -> str:
    """只保留文件名本身，剥掉任何路径分隔符，杜绝把本机路径带进元信息。"""
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not name or name in {".", ".."}:
        raise IntakeError("文件名无效，请重新选择 PDF 文件。")
    return name


def _looks_like_pdf(head: bytes) -> bool:
    return head[:5] == b"%PDF-"


def _fit_matrix(page: pymupdf.Page) -> pymupdf.Matrix:
    """按页面尺寸计算缩放矩阵，使预览图边长不超过 MAX_PREVIEW_SIDE。"""
    zoom = min(MAX_PREVIEW_SIDE / page.rect.width, MAX_PREVIEW_SIDE / page.rect.height, 2.0)
    zoom = max(zoom, 0.1)
    return pymupdf.Matrix(zoom, zoom)


def _cleanup_best_effort(path: Path) -> None:
    """尽力删除私有文件；Windows 上句柄释放有延迟，重试几次后放弃。"""
    for _ in range(3):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.1)


def ingest_pdf(raw: bytes, original_filename: str) -> dict:
    """校验并保存一个上传的 PDF，返回页面可展示的元信息（不含本机路径）。"""
    filename = _safe_filename(original_filename)

    if not raw:
        raise IntakeError("文件内容为空，请重新选择 PDF 文件。")
    if not _looks_like_pdf(raw[:16]):
        raise IntakeError("这不是一个 PDF 文件（文件头不是 %PDF），请选择正确的图纸 PDF。")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise IntakeError("文件过大（超过 100MB），请压缩后再上传。")

    # 保存原文件到私有目录
    document_id = uuid.uuid4().hex[:12]
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)
    original_path = PRIVATE_DIR / f"{document_id}_original.pdf"
    original_path.write_bytes(raw)

    sha256 = hashlib.sha256(raw).hexdigest()
    size = len(raw)

    doc = None
    ok = False
    preview_path = PRIVATE_DIR / f"{document_id}_page_1.png"
    try:
        try:
            doc = pymupdf.open(original_path)
        except Exception:
            raise IntakeError("文件无法解析，请确认是有效且未损坏的 PDF。")

        page_count = doc.page_count
        if page_count < 1:
            raise IntakeError("PDF 没有可显示的页面，无法预览。")

        # 渲染第一页为 PNG 预览
        page = doc.load_page(0)
        pix = page.get_pixmap(matrix=_fit_matrix(page), alpha=False)
        pix.save(preview_path)
        ok = True
    except IntakeError:
        raise
    except Exception:
        raise IntakeError("预览生成失败，请确认 PDF 未损坏。")
    finally:
        # 先释放文档句柄（Windows 会锁文件），再尽力清理失败产生的残留文件
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass
        if not ok:
            _cleanup_best_effort(original_path)
            _cleanup_best_effort(preview_path)

    return {
        "document_id": document_id,
        "filename": filename,
        "page_count": page_count,
        "size": size,
        "sha256": sha256,
    }


def preview_path_for(document_id: str) -> Path:
    """返回指定文档第一页预览图的私有路径；不存在时抛错。"""
    path = PRIVATE_DIR / f"{document_id}_page_1.png"
    if not path.is_file():
        raise IntakeError("预览不存在或已失效，请重新上传。", status=404)
    return path


def render_all_pages(document_id: str, max_edge: int = 1600) -> list[bytes]:
    """在审核时把该图纸所有页渲染为 PNG（只保存在内存，不落盘、不外传其他方）。"""
    original_path = PRIVATE_DIR / f"{document_id}_original.pdf"
    if not original_path.is_file():
        raise IntakeError("文档不存在或已失效，请重新上传。", status=404)

    doc = pymupdf.open(original_path)
    try:
        images: list[bytes] = []
        for page in doc:
            zoom = min(max_edge / page.rect.width, max_edge / page.rect.height, 2.0)
            zoom = max(zoom, 0.1)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            images.append(pix.tobytes("png"))
        return images
    finally:
        doc.close()
