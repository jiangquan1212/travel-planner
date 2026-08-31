# -*- coding: utf-8 -*-
"""PDF 文本提取：使用 pypdf（成熟库，支持中文/压缩流）。"""

from io import BytesIO

try:
    from pypdf import PdfReader
    _HAS_PYPDF = True
except Exception:  # pragma: no cover
    _HAS_PYPDF = False


def extract_pdf_text(buf):
    """提取 PDF 文本；扫描件/加密等无法提取时返回空字符串。"""
    if not _HAS_PYPDF:
        return ""
    try:
        reader = PdfReader(BytesIO(buf))
        parts = []
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            if text.strip():
                parts.append(text.strip())
        return "\n".join(parts)
    except Exception:
        return ""
