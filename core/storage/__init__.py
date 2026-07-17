"""Storage backends for training runs."""

from .base import Storage
from .local import LocalStorage
from .s3 import S3Storage

__all__ = ["LocalStorage", "S3Storage", "Storage"]
