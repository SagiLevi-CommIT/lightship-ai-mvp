"""Vision labeler backends package.

Exposes interchangeable backends:

  - florence2   : zero-shot / open-vocabulary (Florence-2)
  - yolo        : YOLO11n COCO (``yolo_backend``)
  - detectron2  : Mask R-CNN R50-FPN COCO (``detectron2_real_backend``)
  - ufldv2      : lane-line detection (default lane backend in worker)
"""
