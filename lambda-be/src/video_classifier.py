"""Video classifier using Amazon Bedrock (Claude).

Classifies each processed video into one of the 4 training types and infers
road_type, speed, weather, traffic, and collision metadata.
"""
import base64
import json
import logging
import time
from typing import Dict, List, Tuple, Any

import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ReadTimeoutError,
    ConnectionClosedError,
)

from src.schemas import ObjectLabel, HazardEvent
from src.config import (
    AWS_REGION,
    BEDROCK_MODEL_ID,
    TEMPERATURE,
    MAX_TOKENS,
    TOP_P,
    TOP_K,
    VIDEO_CLASS_ENUM,
    ROAD_TYPE_ENUM,
    SPEED_CATEGORIES,
)

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_INITIAL_BACKOFF = 2


class VideoClassifier:
    """Classifies videos and generates scene metadata via Bedrock Claude."""

    def __init__(self):
        config = Config(
            read_timeout=300,
            connect_timeout=10,
            retries={"max_attempts": 0},
        )
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=AWS_REGION,
            config=config,
        )
        logger.info("VideoClassifier initialised (model=%s)", BEDROCK_MODEL_ID)

    def classify(
        self,
        frame_objects: Dict[int, List[ObjectLabel]],
        frame_image_paths: Dict[int, str],
        hazard_events: List[HazardEvent],
        video_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Classify the video and infer scene-level metadata.

        Returns a dict with keys:
            video_class, road_type, speed, traffic, weather, collision,
            description, trial_start_prompt, questions (for Q&A)
        """
        detection_summary = self._build_detection_summary(frame_objects, hazard_events)

        image_b64_list = []
        sorted_indices = sorted(frame_image_paths.keys())
        sample_indices = sorted_indices[:5]
        for idx in sample_indices:
            path = frame_image_paths[idx]
            try:
                with open(path, "rb") as f:
                    image_b64_list.append(base64.b64encode(f.read()).decode("utf-8"))
            except Exception as e:
                logger.warning("Could not read frame %s: %s", path, e)

        prompt = self._build_prompt(detection_summary, video_metadata, len(image_b64_list))
        response_text = self._call_bedrock(image_b64_list, prompt)
        return self._parse_response(response_text)

    def _build_detection_summary(
        self,
        frame_objects: Dict[int, List[ObjectLabel]],
        hazard_events: List[HazardEvent],
    ) -> Dict[str, Any]:
        class_counts: Dict[str, int] = {}
        distance_counts: Dict[str, int] = {}
        total = 0
        for objs in frame_objects.values():
            for obj in objs:
                class_counts[obj.description] = class_counts.get(obj.description, 0) + 1
                distance_counts[obj.distance] = distance_counts.get(obj.distance, 0) + 1
                total += 1

        return {
            "total_objects": total,
            "frames_analysed": len(frame_objects),
            "class_counts": class_counts,
            "distance_counts": distance_counts,
            "hazard_count": len(hazard_events),
            "hazard_types": list({h.hazard_type for h in hazard_events}),
            "hazard_severities": list({h.hazard_severity for h in hazard_events}),
        }

    def _build_prompt(
        self,
        detection_summary: Dict[str, Any],
        video_metadata: Dict[str, Any],
        num_images: int,
    ) -> str:
        return f"""You are classifying a dashcam video for a driver training application.

VIDEO INFO:
- Camera: {video_metadata.get('camera', 'unknown')}
- FPS: {video_metadata.get('fps', 10)}
- Duration: {video_metadata.get('duration_ms', 0):.0f}ms
- Resolution: {video_metadata.get('width', 0)}x{video_metadata.get('height', 0)}

DETECTION SUMMARY:
{json.dumps(detection_summary, indent=2)}

You are provided {num_images} sample frames from this video.

TASKS:
1. Classify the video into EXACTLY ONE of these types:
   - reactivity_braking: Contains a hazard requiring quick driver reaction/braking
   - qa_educational: General driving scenario suitable for educational Q&A
   - hazard_detection: Contains hazards that a driver should notice and monitor
   - job_site_detection: Shows a construction/job site environment

2. Determine the road type: highway, city, town, rural

3. Estimate the road speed limit: <15_mph, 15-25_mph, 25-40_mph, 40-55_mph, 55-70_mph, >70_mph

4. Describe traffic density: low, moderate, high

5. Weather conditions: clear, rain, snow, fog, overcast

6. Collision: "none" or brief description (e.g. "rear-end,car")

7. Write a trial_start_prompt: 1-2 sentence narrative describing what the driver sees at the start of this video, written in second person ("You are driving...")

8. If video_class is qa_educational, generate 3 educational Q&A questions about the driving scenario. Each question has 4 answer options with one correct answer.

9. Write a brief video description (1-2 sentences).

RESPOND WITH VALID JSON ONLY:
{{
  "video_class": "<one of: reactivity_braking, qa_educational, hazard_detection, job_site_detection>",
  "road_type": "<highway|city|town|rural>",
  "speed": "<speed category>",
  "traffic": "<low|moderate|high>",
  "weather": "<clear|rain|snow|fog|overcast>",
  "collision": "<none or description>",
  "description": "<brief video description>",
  "trial_start_prompt": "<narrative text>",
  "questions": [
    {{
      "question": "<text>",
      "options": ["A", "B", "C", "D"],
      "correct_answer": "<A|B|C|D>",
      "explanation": "<why this is correct>"
    }}
  ]
}}

Return ONLY valid JSON, no markdown or explanations."""

    def _call_bedrock(self, images_b64: List[str], prompt: str) -> str:
        content = []
        for img in images_b64:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img},
            })
        content.append({"type": "text", "text": prompt})

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "top_k": TOP_K,
            "messages": [{"role": "user", "content": content}],
        }

        for attempt in range(_MAX_RETRIES):
            try:
                resp = self.client.invoke_model(
                    modelId=BEDROCK_MODEL_ID,
                    body=json.dumps(body),
                )
                resp_body = json.loads(resp["body"].read())
                if "content" in resp_body and resp_body["content"]:
                    return resp_body["content"][0]["text"]
                raise ValueError("Empty Bedrock response")
            except (EndpointConnectionError, ReadTimeoutError, ConnectionClosedError) as e:
                if attempt < _MAX_RETRIES - 1:
                    wait = _INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning("Bedrock transient error (attempt %d): %s", attempt + 1, e)
                    time.sleep(wait)
                else:
                    raise
            except ClientError as e:
                code = e.response.get("Error", {}).get("Code", "")
                if code in ("ThrottlingException", "ServiceUnavailable", "TooManyRequestsException"):
                    if attempt < _MAX_RETRIES - 1:
                        wait = _INITIAL_BACKOFF * (2 ** attempt)
                        logger.warning("Bedrock throttle (attempt %d): %s", attempt + 1, code)
                        time.sleep(wait)
                    else:
                        raise
                else:
                    raise
        raise RuntimeError("Bedrock call exhausted retries")

    def _parse_response(self, text: str) -> Dict[str, Any]:
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1:
                raise ValueError("No JSON found in classifier response")
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            import re
            cleaned = re.sub(r",(\s*[}\]])", r"\1", text)
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            data = json.loads(cleaned[start : end + 1])

        vc = data.get("video_class", "hazard_detection")
        if vc not in VIDEO_CLASS_ENUM:
            vc = "hazard_detection"
        data["video_class"] = vc

        rt = data.get("road_type", "unknown")
        if rt not in ROAD_TYPE_ENUM:
            rt = "unknown"
        data["road_type"] = rt

        if "questions" not in data:
            data["questions"] = []

        return data
