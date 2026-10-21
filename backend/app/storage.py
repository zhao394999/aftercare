import hashlib
import subprocess
import sys
import logging
from threading import BoundedSemaphore
from pathlib import Path

import boto3
from botocore.config import Config

from .config import settings
from .security import require
from .models import uid

PDF_PARSERS = BoundedSemaphore(1)


def validate_file(content: bytes, filename: str):
    require(
        0 < len(content) <= 20 * 1024 * 1024, "file_size", "文件大小须在20MB以内", 422
    )
    suffix = Path(filename).suffix.lower()
    text = ""
    if suffix == ".pdf":
        require(content.startswith(b"%PDF-"), "file_type", "PDF文件签名不匹配", 422)
        require(
            PDF_PARSERS.acquire(blocking=False),
            "pdf_busy",
            "PDF解析繁忙，请稍后重试",
            503,
        )
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("pdf_extract.py"))],
                input=content,
                capture_output=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            require(False, "pdf_limit", "PDF解析超过时限，请改用文本摘要", 422)
        finally:
            PDF_PARSERS.release()
        require(
            result.returncode == 0 and len(result.stdout) <= 120000,
            "pdf_limit",
            "PDF无效或超过解析资源限制，请使用50页以内未加密PDF或文本摘要",
            422,
        )
        text = result.stdout.decode("utf-8")
        mime = "application/pdf"
    elif suffix == ".png":
        require(
            content.startswith(b"\x89PNG\r\n\x1a\n"),
            "file_type",
            "PNG文件签名不匹配",
            422,
        )
        mime = "image/png"
    elif suffix in {".jpg", ".jpeg"}:
        require(
            content.startswith(b"\xff\xd8\xff"), "file_type", "JPEG文件签名不匹配", 422
        )
        mime = "image/jpeg"
    elif suffix in {".txt", ".md"}:
        try:
            text = content.decode("utf-8")[:30000]
        except UnicodeDecodeError:
            require(False, "invalid_text", "文本文件需要使用UTF-8编码", 422)
        require("\x00" not in text, "invalid_text", "文本文件包含无效内容", 422)
        mime = "text/plain"
    else:
        require(False, "file_type", "支持PDF、PNG、JPEG、TXT与Markdown", 422)
    return mime, text, hashlib.sha256(content).hexdigest()


def store_file(tenant_id: str, content: bytes, mime: str) -> str:
    cfg = settings()
    key = f"{tenant_id}/{uid()}"
    if cfg.storage_backend == "s3":
        try:
            s3_client().put_object(
                Bucket=cfg.s3_bucket, Key=key, Body=content, ContentType=mime
            )
        except Exception:
            discard_file(key)
            raise
    else:
        target = cfg.storage_dir / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return key


def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings().s3_endpoint or None,
        config=Config(connect_timeout=5, read_timeout=5, retries={"max_attempts": 1},
                      s3={"addressing_style": settings().s3_addressing_style} if settings().s3_endpoint else {}),
    )


def discard_file(key):
    cfg = settings()
    try:
        if cfg.storage_backend == "s3":
            s3_client().delete_object(Bucket=cfg.s3_bucket, Key=key)
        else:
            (cfg.storage_dir / key).unlink(missing_ok=True)
    except Exception:
        logging.getLogger("aftercare").error("file_cleanup_failed")


def file_location(key: str, filename: str):
    cfg = settings()
    if cfg.storage_backend == "s3":
        return s3_client().get_object(Bucket=cfg.s3_bucket, Key=key)["Body"]
    target = (cfg.storage_dir / key).resolve()
    require(
        target.is_relative_to(cfg.storage_dir.resolve()) and target.is_file(),
        "file_missing",
        "文件不可用，请联系管理员",
        404,
    )
    return target
