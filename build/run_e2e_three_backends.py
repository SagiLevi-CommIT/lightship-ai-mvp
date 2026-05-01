"""Submit three jobs (florence2 / yolo / detectron2) via ALB and print vision_audit.backend."""
from __future__ import annotations

import json
import time
import urllib.request
import ssl

# Use HTTP: ALB TLS cert may not match the ELB hostname; SG allows operator CIDR on 80/443.
ALB = "http://lightship-mvp-alb-140533025.us-east-1.elb.amazonaws.com"
S3_URI = "s3://lightship-mvp-processing-336090301206/input/videos/eb885695-70b0-45d7-b583-2b274a652ea3/samsara_2.mp4"

BASE_CFG = {
    "max_snapshots": 5,
    "snapshot_strategy": "scene_change",
    "native_fps": 2,
    "lane_backend": "opencv",
}


def post_json(path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        ALB + path,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(path: str) -> dict:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(ALB + path, method="GET")
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def wait_done(job_id: str, timeout_s: int = 1200) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        st = get_json(f"/status/{job_id}")
        if st.get("status") in ("COMPLETED", "FAILED"):
            return st
        time.sleep(15)
    raise TimeoutError(job_id)


def main() -> None:
    for backend in ("florence2", "yolo", "detectron2"):
        cfg = {**BASE_CFG, "detector_backend": backend}
        print("Submitting", backend, "...")
        r = post_json(
            "/process-s3-video",
            {"s3_uri": S3_URI, "config": cfg},
        )
        job_id = r["job_id"]
        print("  job_id:", job_id)
        st = wait_done(job_id)
        print("  status:", st.get("status"), st.get("message", "")[:120])
        if st.get("status") != "COMPLETED":
            continue
        out = get_json(f"/download/json/{job_id}")
        audit = out.get("vision_audit") or {}
        print("  vision_audit.backend:", audit.get("backend"))


if __name__ == "__main__":
    main()
