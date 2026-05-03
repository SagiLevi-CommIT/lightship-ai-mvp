"""Pipeline final-frame-first behaviour tests."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lambda-be"))


@pytest.fixture()
def pipeline_mod(monkeypatch):
    """Import src.pipeline without loading the legacy YOLO/depth CVLabeler."""
    monkeypatch.delitem(sys.modules, "src.pipeline", raising=False)
    fake_cv = types.ModuleType("src.cv_labeler")

    class _CVLabeler:
        def __init__(self, *args, **kwargs):  # noqa: D401, ANN002, ANN003
            raise AssertionError("CVLabeler must not be constructed in final-frame-first tests")

    fake_cv.CVLabeler = _CVLabeler
    monkeypatch.setitem(sys.modules, "src.cv_labeler", fake_cv)

    from src import pipeline as pipeline_mod

    yield pipeline_mod
    sys.modules.pop("src.pipeline", None)


def _meta():
    from src.schemas import VideoMetadata

    return VideoMetadata(
        filename="clip.mp4",
        filepath="/tmp/clip.mp4",
        camera="unknown",
        fps=30.0,
        duration_ms=10_000.0,
        total_frames=300,
        width=1280,
        height=720,
    )


class _FakeExtractor:
    def __init__(self):
        self.requested = []

    def extract_frames_with_manifest(self, _video_metadata, snapshots):
        self.requested = list(snapshots)
        frames = {int(s.frame_idx): f"/tmp/frame_{int(s.frame_idx)}.png" for s in snapshots}
        manifest = [
            SimpleNamespace(
                frame_idx=int(s.frame_idx),
                timestamp_ms=float(s.timestamp_ms),
                source="requested",
                status="ok",
                width=1280,
                height=720,
                decoded_idx=int(s.frame_idx),
                error=None,
            )
            for s in snapshots
        ]
        return SimpleNamespace(frames=frames, manifest=manifest)


def _bare_pipeline(pipeline_mod, *, strategy="naive", count=1, native_fps=30.0, mode="count"):
    pipe = pipeline_mod.Pipeline.__new__(pipeline_mod.Pipeline)
    pipe.snapshot_strategy = strategy
    pipe.max_snapshots = count
    pipe.native_fps = native_fps
    pipe.native_sampling_mode = mode
    pipe.snapshot_selector = SimpleNamespace(max_snapshots=count)
    pipe.frame_extractor = _FakeExtractor()
    pipe.last_extraction_manifest = []
    pipe.last_timing_ms = {}
    return pipe


def test_native_count_ignores_native_fps_and_extracts_one_frame(pipeline_mod):
    pipe = _bare_pipeline(pipeline_mod, strategy="naive", count=1, native_fps=30.0, mode="count")

    snapshots, frames = pipe._prepare_v2_final_frames(_meta(), lambda *_args: None)

    assert len(snapshots) == 1
    assert len(frames) == 1
    assert len(pipe.frame_extractor.requested) == 1
    assert snapshots[0].frame_idx == 150


def test_scene_change_selects_before_extraction_and_backfills_without_detection(pipeline_mod):
    from src.schemas import SnapshotInfo

    pipe = _bare_pipeline(pipeline_mod, strategy="scene_change", count=3, native_fps=None)
    pipe.snapshot_selector = SimpleNamespace(
        _select_scene_change=lambda _meta: [
            SnapshotInfo(frame_idx=30, timestamp_ms=1000.0, reason="scene"),
        ],
        max_snapshots=3,
    )

    snapshots, frames = pipe._prepare_v2_final_frames(_meta(), lambda *_args: None)

    assert len(snapshots) == 3
    assert len(frames) == 3
    assert [s.frame_idx for s in pipe.frame_extractor.requested] == [30, 75, 150]


def test_vision_labeler_runs_only_on_final_frames(pipeline_mod, tmp_path, monkeypatch):
    from src.schemas import Center, ObjectLabel, SnapshotInfo

    monkeypatch.setattr(pipeline_mod, "ENABLE_FRAME_PREPROCESSING", False)
    monkeypatch.setattr(pipeline_mod, "OUTPUT_DIR", str(tmp_path / "out"))

    class _Vision:
        detector_backend = "yolo"
        lane_backend = "ufldv2"

        def __init__(self):
            self.calls = []
            self.audit = []

        def detect(self, frame_path, timestamp_ms, video_width, video_height):
            self.calls.append((frame_path, timestamp_ms, video_width, video_height))
            self.audit.append(
                {
                    "primary_backend": "yolo",
                    "primary_elapsed_ms": 7.0,
                    "lane_elapsed_ms": 2.0,
                },
            )
            return [
                ObjectLabel(
                    description="vehicle",
                    start_time_ms=timestamp_ms,
                    distance="moderate",
                    priority="medium",
                    center=Center(x=10, y=10),
                    x_min=0.0,
                    y_min=0.0,
                    x_max=20.0,
                    y_max=20.0,
                    width=20.0,
                    height=20.0,
                ),
            ]

        def build_audit(self):
            return list(self.audit)

    class _Annotator:
        def annotate_frame(self, _frame_path, _objects, output_path, _timestamp_ms):
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text("annotated", encoding="utf-8")

    class _Refiner:
        def refine_frame(self, _frame_path, _annotated_path, objects, _timestamp_ms):
            return objects, pipeline_mod.RefinerStatus.SUCCESS, ""

    class _Hazard:
        def __init__(self):
            self.calls = 0

        def assess_hazards_only(self, frame_objects, frame_images, video_metadata):
            self.calls += 1
            del frame_images, video_metadata
            final = [obj for objects in frame_objects.values() for obj in objects]
            return [], final, {"description": "ok", "traffic": "light", "lighting": "day"}

    pipe = _bare_pipeline(pipeline_mod, strategy="scene_change", count=2, native_fps=None)
    pipe.vision_labeler = _Vision()
    pipe.frame_annotator = _Annotator()
    pipe.frame_refiner = _Refiner()
    pipe.hazard_assessor = _Hazard()
    pipe.last_selected_frames = {}
    pipe.last_annotated_frames = {}
    pipe.last_frame_timestamps = {}
    pipe.last_extraction_manifest = []

    frames = {}
    snapshots = [
        SnapshotInfo(frame_idx=10, timestamp_ms=333.0),
        SnapshotInfo(frame_idx=20, timestamp_ms=666.0),
    ]
    for snap in snapshots:
        frame = tmp_path / f"frame_{snap.frame_idx}.png"
        frame.write_text("frame", encoding="utf-8")
        frames[int(snap.frame_idx)] = str(frame)

    objects, hazards, _metadata = pipe._process_v2_final_frames(
        snapshots,
        frames,
        _meta(),
        progress_cb=lambda *_args: None,
    )

    assert len(pipe.vision_labeler.calls) == 2
    assert [call[1] for call in pipe.vision_labeler.calls] == [333.0, 666.0]
    assert len(objects) == 2
    assert hazards == []
    assert pipe.hazard_assessor.calls == 0
    assert pipe.last_timing_ms["llm_hazard"] < 10.0


def test_process_video_resets_warm_vision_audit(pipeline_mod, tmp_path, monkeypatch):
    from src.schemas import SnapshotInfo

    monkeypatch.setattr(pipeline_mod, "OUTPUT_DIR", str(tmp_path / "out"))

    class _Vision:
        detector_backend = "yolo"
        lane_backend = "ufldv2"

        def __init__(self):
            self.audit = [{"frame_path": "stale_previous_job.png"}]
            self.reset_calls = 0

        def reset_audit(self):
            self.reset_calls += 1
            self.audit.clear()

        def build_audit(self):
            return list(self.audit)

    class _Loader:
        def load_video_metadata(self, video_path):
            meta = _meta()
            return meta.model_copy(update={"filepath": video_path})

    class _Merger:
        output_dir = str(tmp_path)

        def merge_and_save(self, video_metadata, objects, hazard_events, inferred_metadata):
            del objects, hazard_events, inferred_metadata
            output_path = tmp_path / "output.json"
            output_path.write_text(
                (
                    "{"
                    f"\"filename\":\"{video_metadata.filename}\","
                    f"\"fps\":{video_metadata.fps},"
                    "\"camera\":\"unknown\","
                    "\"description\":\"\","
                    f"\"video_duration_ms\":{video_metadata.duration_ms},"
                    "\"objects\":[],"
                    "\"hazard_events\":[]"
                    "}"
                ),
                encoding="utf-8",
            )
            return str(output_path)

        def get_summary_stats(self, _video_output):
            return {}

    pipe = _bare_pipeline(pipeline_mod, strategy="scene_change", count=2, native_fps=None)
    pipe.use_cv_labeler = True
    pipe.vision_labeler = _Vision()
    pipe.video_loader = _Loader()
    pipe.merger = _Merger()
    pipe.cleanup_frames = False

    frame = tmp_path / "frame_1.png"
    frame.write_text("frame", encoding="utf-8")
    snapshots = [SnapshotInfo(frame_idx=1, timestamp_ms=33.0)]
    monkeypatch.setattr(
        pipe,
        "_prepare_v2_final_frames",
        lambda _metadata, _report: (snapshots, {1: str(frame)}),
    )
    monkeypatch.setattr(
        pipe,
        "_process_v2_final_frames",
        lambda _snapshots, _frames, _metadata, progress_cb=None: ([], [], {}),
    )

    output_path = pipe.process_video(str(tmp_path / "clip.mp4"))

    assert pipe.vision_labeler.reset_calls == 1
    assert output_path is not None
    import json

    output_doc = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assert output_doc["vision_audit"]["frames_evaluated"] == 0
    assert output_doc["vision_audit"]["per_frame"] == []
