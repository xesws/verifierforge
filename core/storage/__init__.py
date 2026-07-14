"""Storage backends for training runs."""

from .base import Storage
from .local import LocalStorage

__all__ = ["LocalStorage", "Storage"]
