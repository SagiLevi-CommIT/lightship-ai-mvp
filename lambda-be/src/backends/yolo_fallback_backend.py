"""Deprecated alias — use :mod:`src.backends.yolo_backend`."""
from src.backends.yolo_backend import YoloBackend as YoloFallbackBackend

__all__ = ["YoloFallbackBackend"]
