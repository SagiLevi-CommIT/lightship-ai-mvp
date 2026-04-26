"""API client for communicating with Lightship MVP backend server."""
import requests
import json
import os
from typing import Dict, Any, Optional, List
import logging

logger = logging.getLogger(__name__)


class APIClient:
    """Client for Lightship MVP API."""

    def __init__(self, base_url: str = None):
        if base_url is None:
            base_url = os.environ.get("BACKEND_API_URL", "http://localhost:8000")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def check_health(self) -> bool:
        try:
            response = self.session.get(f"{self.base_url}/health", timeout=120)
            return response.status_code == 200
        except Exception as e:
            logger.error("Health check failed: %s", e)
            return False

    def upload_video(self, video_file, config: Optional[Dict[str, Any]] = None) -> Optional[str]:
        try:
            video_file.seek(0)
            try:
                presign_resp = self.session.get(
                    f"{self.base_url}/presign-upload",
                    params={"filename": video_file.name, "content_type": "video/mp4"},
                    timeout=10,
                )
                presign_resp.raise_for_status()
                presign_data = presign_resp.json()
                presign_url = presign_data["presign_url"]
                s3_key = presign_data["s3_key"]
                use_s3_flow = True
            except Exception as e:
                logger.warning("Presign unavailable, falling back: %s", e)
                use_s3_flow = False

            if use_s3_flow:
                video_file.seek(0)
                video_bytes = video_file.read()
                put_headers = presign_data.get("required_headers", {"Content-Type": "video/mp4"})
                put_resp = requests.put(presign_url, data=video_bytes, headers=put_headers, timeout=600)
                if put_resp.status_code not in (200, 204):
                    logger.error("S3 PUT failed: %s %s", put_resp.status_code, put_resp.text[:200])
                    return None
                data: Dict[str, Any] = {"s3_key": s3_key}
                if config:
                    data["config"] = json.dumps(config)
                response = self.session.post(f"{self.base_url}/process-video", data=data, timeout=30)
            else:
                video_file.seek(0)
                files = {"video": (video_file.name, video_file, "video/mp4")}
                data = {}
                if config:
                    data["config"] = json.dumps(config)
                response = self.session.post(f"{self.base_url}/process-video", files=files, data=data, timeout=30)

            if response.status_code == 200:
                return response.json().get("job_id")
            else:
                logger.error("Upload failed: %s - %s", response.status_code, response.text)
                return None
        except Exception as e:
            logger.error("Upload error: %s", e)
            return None

    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.base_url}/status/{job_id}", timeout=10)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            logger.error("Get status error: %s", e)
            return None

    def get_results(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.base_url}/results/{job_id}", timeout=10)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            logger.error("Get results error: %s", e)
            return None

    def get_json_content(self, job_id: str) -> Optional[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.base_url}/download/json/{job_id}", timeout=10)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            logger.error("Get JSON error: %s", e)
            return None

    def get_frames_list(self, job_id: str) -> List[Dict[str, Any]]:
        try:
            response = self.session.get(f"{self.base_url}/frames/{job_id}", timeout=10)
            if response.status_code == 200:
                return response.json().get("frames", [])
            return []
        except Exception as e:
            logger.error("Get frames list error: %s", e)
            return []

    def _fetch_frame(self, url: str) -> Optional[bytes]:
        """Fetch frame image — handles both direct binary and presigned URL JSON responses."""
        try:
            response = self.session.get(url, timeout=15)
            if response.status_code != 200:
                return None
            ct = response.headers.get("content-type", "")
            if "image/" in ct:
                return response.content
            try:
                data = response.json()
                presigned_url = data.get("url")
                if presigned_url:
                    img_resp = requests.get(presigned_url, timeout=30)
                    return img_resp.content if img_resp.status_code == 200 else None
            except Exception:
                return response.content
            return response.content
        except Exception as e:
            logger.error("Fetch frame error: %s", e)
            return None

    def get_frame_image(self, job_id: str, frame_idx: int) -> Optional[bytes]:
        return self._fetch_frame(f"{self.base_url}/download/frame/{job_id}/{frame_idx}")

    def get_annotated_frame_image(self, job_id: str, frame_idx: int) -> Optional[bytes]:
        return self._fetch_frame(f"{self.base_url}/download/annotated-frame/{job_id}/{frame_idx}")

    def list_jobs(self, limit: int = 50) -> list:
        try:
            response = self.session.get(f"{self.base_url}/jobs", params={"limit": limit}, timeout=10)
            return response.json().get("jobs", []) if response.status_code == 200 else []
        except Exception as e:
            logger.error("List jobs error: %s", e)
            return []

    def cleanup(self, job_id: str) -> bool:
        try:
            response = self.session.delete(f"{self.base_url}/cleanup/{job_id}", timeout=10)
            return response.status_code == 200
        except Exception as e:
            logger.error("Cleanup error: %s", e)
            return False
