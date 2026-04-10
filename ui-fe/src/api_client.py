"""API client for communicating with Lightship MVP backend server."""
import requests
import json
import os
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


class APIClient:
    """Client for Lightship MVP API."""

    def __init__(self, base_url: str = None):
        """Initialize API client.

        Args:
            base_url: Base URL of the API server. Falls back to BACKEND_API_URL env var or localhost.
        """
        if base_url is None:
            base_url = os.environ.get("BACKEND_API_URL", "http://localhost:8000")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def check_health(self) -> bool:
        """Check if API server is healthy.

        Returns:
            True if server is reachable and healthy
        """
        try:
            # Lambda cold start can take 60-90s (model loading), use generous timeout
            response = self.session.get(f"{self.base_url}/health", timeout=120)
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    def upload_video(
        self,
        video_file,
        config: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """Upload video for processing.

        Args:
            video_file: File-like object or UploadedFile
            config: Processing configuration

        Returns:
            job_id if successful, None otherwise
        """
        try:
            # Reset file pointer
            video_file.seek(0)

            files = {
                'video': (video_file.name, video_file, 'video/mp4')
            }

            data = {}
            if config:
                data['config'] = json.dumps(config)

            response = self.session.post(
                f"{self.base_url}/process-video",
                files=files,
                data=data,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                return result.get('job_id')
            else:
                logger.error(f"Upload failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            logger.error(f"Upload error: {e}")
            return None

    def get_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get processing status for a job.

        Args:
            job_id: Job identifier

        Returns:
            Status dictionary or None if failed
        """
        try:
            response = self.session.get(
                f"{self.base_url}/status/{job_id}",
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Get status failed: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Get status error: {e}")
            return None

    def get_results(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get processing results for a completed job.

        Args:
            job_id: Job identifier

        Returns:
            Results dictionary or None if failed
        """
        try:
            response = self.session.get(
                f"{self.base_url}/results/{job_id}",
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Get results failed: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Get results error: {e}")
            return None

    def get_json_content(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get output JSON content.

        Args:
            job_id: Job identifier

        Returns:
            JSON data or None if failed
        """
        try:
            response = self.session.get(
                f"{self.base_url}/download/json/{job_id}",
                timeout=10
            )

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Get JSON failed: {response.status_code}")
                return None

        except Exception as e:
            logger.error(f"Get JSON error: {e}")
            return None

    def download_frame(self, job_id: str, frame_idx: int, save_path: str) -> bool:
        """Download frame image.

        Args:
            job_id: Job identifier
            frame_idx: Frame index
            save_path: Path to save image

        Returns:
            True if successful
        """
        try:
            response = self.session.get(
                f"{self.base_url}/download/frame/{job_id}/{frame_idx}",
                timeout=10
            )

            if response.status_code == 200:
                with open(save_path, 'wb') as f:
                    f.write(response.content)
                return True
            else:
                logger.error(f"Download frame failed: {response.status_code}")
                return False

        except Exception as e:
            logger.error(f"Download frame error: {e}")
            return False

    def list_jobs(self, limit: int = 50) -> list:
        """List recent jobs from the backend.

        Args:
            limit: Maximum number of jobs to return

        Returns:
            List of job dicts, newest first. Empty list on failure.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/jobs",
                params={"limit": limit},
                timeout=10
            )
            if response.status_code == 200:
                return response.json().get("jobs", [])
            else:
                logger.error(f"List jobs failed: {response.status_code}")
                return []
        except Exception as e:
            logger.error(f"List jobs error: {e}")
            return []

    def cleanup(self, job_id: str) -> bool:
        """Cleanup temporary files for a job.

        Args:
            job_id: Job identifier

        Returns:
            True if successful
        """
        try:
            response = self.session.delete(
                f"{self.base_url}/cleanup/{job_id}",
                timeout=10
            )

            return response.status_code == 200

        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            return False

