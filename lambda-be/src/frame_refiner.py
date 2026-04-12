"""Per-frame LLM refinement for object validation and correction.

This module provides per-frame refinement using LLM visual analysis to:
- Validate CV detections (mark false positives)
- Refine distance labels
- Refine priority levels
- Add location descriptions
"""
import base64
import json
import logging
from enum import Enum
from typing import List, Tuple, Dict
import boto3
from botocore.exceptions import ClientError
from botocore.config import Config

from src.schemas import ObjectLabel
from src.config import (
    AWS_REGION,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    BEDROCK_MODEL_ID,
    TEMPERATURE,
    MAX_TOKENS,
    TOP_P,
    TOP_K,
    FRAME_REFINER_MAX_RETRIES,
    DISTANCE_ENUM,
    THREAT_LEVEL_ENUM
)

logger = logging.getLogger(__name__)


class RefinerStatus(Enum):
    """Status codes for frame refinement results."""
    SUCCESS = "success"
    NEEDS_RETRY = "needs_retry"
    FAILED = "failed"


class FrameRefiner:
    """Per-frame LLM refinement for object validation and correction."""

    def __init__(self):
        """Initialize FrameRefiner with Bedrock client."""
        # Configure boto3 with timeout settings
        config = Config(
            read_timeout=300,  # 5 minutes for single frame (shorter than window calls)
            connect_timeout=10,
            retries={'max_attempts': 0}  # We handle retries manually
        )

        self.bedrock_client = boto3.client(
            service_name='bedrock-runtime',
            region_name=AWS_REGION,
            config=config
        )
        logger.info(f"FrameRefiner initialized with model: {BEDROCK_MODEL_ID}")

    def refine_frame(
        self,
        frame_path: str,
        annotated_frame_path: str,
        objects: List[ObjectLabel],
        timestamp_ms: float
    ) -> Tuple[List[ObjectLabel], RefinerStatus, str]:
        """Refine objects for a single frame using LLM visual analysis.

        Args:
            frame_path: Path to original frame image
            annotated_frame_path: Path to frame with CV bboxes drawn
            objects: List of CV-detected objects
            timestamp_ms: Timestamp of frame in milliseconds

        Returns:
            Tuple of (refined_objects, status, reason):
            - refined_objects: List of validated/refined ObjectLabel instances
            - status: SUCCESS | NEEDS_RETRY | FAILED
            - reason: Explanation if not success (empty string if success)
        """
        logger.info(f"Refining frame at {timestamp_ms:.2f}ms with {len(objects)} objects")

        try:
            # Read and encode both images
            with open(frame_path, 'rb') as f:
                original_base64 = base64.b64encode(f.read()).decode('utf-8')

            with open(annotated_frame_path, 'rb') as f:
                annotated_base64 = base64.b64encode(f.read()).decode('utf-8')

            # Build prompt
            prompt = self._build_refiner_prompt(objects, timestamp_ms)

            # Call LLM
            response = self._call_bedrock([original_base64, annotated_base64], prompt)

            # Parse and apply refinements
            refined_objects, status, reason = self._parse_refiner_response(response, objects)

            logger.info(f"Frame refinement status: {status.value}, {len(refined_objects)} objects kept")
            return refined_objects, status, reason

        except Exception as e:
            logger.error(f"Error refining frame: {e}", exc_info=True)
            return objects, RefinerStatus.FAILED, str(e)

    def _build_refiner_prompt(self, objects: List[ObjectLabel], timestamp_ms: float) -> str:
        """Build LLM prompt for per-frame refinement.

        Args:
            objects: List of CV-detected objects
            timestamp_ms: Frame timestamp

        Returns:
            Prompt string
        """
        # Format objects for prompt
        objects_text = []
        for idx, obj in enumerate(objects):
            obj_str = (
                f"  Object {idx}: {obj.description} - "
                f"CV distance: {obj.distance}, "
                f"CV priority: {obj.priority}, "
                f"bbox: ({obj.x_min:.0f}, {obj.y_min:.0f}) to ({obj.x_max:.0f}, {obj.y_max:.0f})"
            )
            if obj.center:
                obj_str += f", center: ({obj.center.x}, {obj.center.y})"
            objects_text.append(obj_str)

        objects_formatted = "\n".join(objects_text)

        # Build distance enum string
        distance_options = ", ".join(DISTANCE_ENUM)

        # Build priority enum string
        priority_options = ", ".join(THREAT_LEVEL_ENUM)

        prompt = f"""You are analyzing a single dashcam frame for autonomous vehicle safety. You will receive TWO images:
1. The original frame
2. The same frame with CV-detected objects annotated (bounding boxes)

FRAME INFO:
- Timestamp: {timestamp_ms:.2f}ms

CV-DETECTED OBJECTS:
{objects_formatted}

YOUR TASKS:

1. VALIDATE DETECTIONS (CRITICAL - Visual Inspection Required)
   For EACH object, visually inspect using BOTH images and determine:
   - TRUE POSITIVE: The CV detection is correct (e.g., actual lane, real crosswalk, valid vehicle)
   - FALSE POSITIVE: The CV detection is INCORRECT (mark is_false_positive: true)

   IMPORTANT VALIDATION RULES:

   A. TRAFFIC OBJECTS (vehicles, trucks, buses, pedestrians, bicyclists, motorcycles):
      - You can ONLY REFINE these objects (adjust distance, priority, location)
      - You CANNOT mark these as false positives (is_false_positive must be false)
      - Even if partially occluded or at frame edge, keep them as valid detections
      - Only refine their distance, priority, and location attributes

   B. GEOMETRIC OBJECTS (lanes, crosswalks, double_yellow, intersection_boundary):
      - You CAN mark these as false positives if they are incorrect detections
      - Common false positives to REJECT:
        * Crosswalks: Railroad/tram tracks, horizontal building edges, fence lines, painted road markings that aren't crosswalks
        * Double_yellow: Yellow buildings/walls, construction signs, curb paint, yellow vehicles, road surface discoloration
        * Lanes: Curbs, road edges, building edges, snow patches, shadows, cracks, tire marks
        * Intersection_boundary: Random road markings, shadows, surface cracks
      - If you see a geometric detection in the annotated image but cannot visually confirm it's the actual object type in the original image, mark it FALSE

   C. SIGNS AND OTHER OBJECTS:
      - You CAN mark these as false positives if misidentified
      - Be careful with partially visible or distant signs

2. REFINE DISTANCE LABELS
   For each VALID object (not false positive), provide accurate distance based on YOUR VISUAL ANALYSIS:
   - Options: {distance_options}
   - Use your judgment based on real-world appearance, perspective, depth cues
   - Consider: object size in frame, depth perspective, partial occlusion, road context
   - Larger objects in frame = closer to camera
   - Smaller objects = farther away
   - Use "n/a" only for lane markings and road infrastructure

3. REFINE PRIORITY LEVELS
   For each VALID object (not false positive), assign appropriate priority:
   - Options: {priority_options}
   - critical: Immediate danger requiring urgent response
   - high: Plausible hazard requiring close monitoring
   - medium: Worth attention, may require monitoring
   - low: Relevant but unlikely to require action
   - none: Context-only, informational

4. ADD LOCATION DESCRIPTION
   For vehicles and pedestrians ONLY, describe spatial position:
   - Examples: "left lane approaching", "right side of road", "center lane ahead", "crossing from right"
   - Use empty string "" if location cannot be determined or for non-vehicle/pedestrian objects

5. SELF-ASSESS YOUR WORK
   After completing refinements, determine your confidence:
   - "success": You're confident in all your assessments
   - "needs_retry": You need another look at the refined data (we'll re-annotate and call you again)
   - "failed": The frame is too unclear/complex, skip this frame entirely

REQUIRED OUTPUT FORMAT (valid JSON only, no markdown):
{{
  "status": "success|needs_retry|failed",
  "reason": "brief explanation if needs_retry or failed, empty string if success",
  "refined_objects": [
    {{
      "object_index": 0,
      "is_false_positive": false,
      "refined_distance": "close",
      "refined_priority": "high",
      "location_description": "center lane ahead"
    }}
  ]
}}

CRITICAL RULES:
- You MUST provide refined_objects entry for EVERY object (indices 0 to {len(objects) - 1})
- is_false_positive must be true or false (boolean) - mark true if visually incorrect
- refined_distance must be one of: {distance_options}
- refined_priority must be one of: {priority_options}
- location_description required ONLY for vehicles/pedestrians (empty string "" for others)
- Return ONLY valid JSON, no explanations or markdown
- Be STRICT about false positives - when in doubt about a detection, mark it false
- If you mark needs_retry, we will fix the JSON based on your refinements and call you again with updated annotations

Now analyze the frame and return the complete JSON:"""

        return prompt

    def _call_bedrock(self, images_base64: List[str], prompt: str) -> str:
        """Call Bedrock API with images and prompt.

        Args:
            images_base64: List of base64-encoded images
            prompt: Prompt text

        Returns:
            Response text from model

        Raises:
            Exception: If Bedrock call fails
        """
        # Build content array with images and prompt
        content = []

        # Add images
        for image_base64 in images_base64:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64
                }
            })

        # Add text prompt
        content.append({
            "type": "text",
            "text": prompt
        })

        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "top_k": TOP_K,
            "messages": [
                {
                    "role": "user",
                    "content": content
                }
            ]
        }

        try:
            response = self.bedrock_client.invoke_model(
                modelId=BEDROCK_MODEL_ID,
                body=json.dumps(request_body)
            )

            response_body = json.loads(response['body'].read())

            if 'content' in response_body and len(response_body['content']) > 0:
                return response_body['content'][0]['text']
            else:
                raise ValueError("No content in Bedrock response")

        except ClientError as e:
            logger.error(f"Bedrock API error: {e}")
            raise

    def _parse_refiner_response(
        self,
        response_text: str,
        original_objects: List[ObjectLabel]
    ) -> Tuple[List[ObjectLabel], RefinerStatus, str]:
        """Parse LLM refinement response and apply to objects.

        Args:
            response_text: Raw response from LLM
            original_objects: Original CV-detected objects

        Returns:
            Tuple of (refined_objects, status, reason)
        """
        try:
            # Extract JSON from response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')

            if start_idx == -1 or end_idx == -1:
                logger.error("No JSON found in response")
                return original_objects, RefinerStatus.FAILED, "No JSON in response"

            json_text = response_text[start_idx:end_idx+1]
            data = json.loads(json_text)

            # Get status
            status_str = data.get('status', 'failed')
            try:
                status = RefinerStatus(status_str)
            except ValueError:
                logger.warning(f"Invalid status '{status_str}', defaulting to FAILED")
                status = RefinerStatus.FAILED

            reason = data.get('reason', '')

            # If not success, return originals with status
            if status != RefinerStatus.SUCCESS:
                logger.info(f"Refinement status: {status.value} - {reason}")
                return original_objects, status, reason

            # Parse refined objects
            refined_objects_data = data.get('refined_objects', [])
            refined_objects = []

            for orig_obj in original_objects:
                # Find refinement for this object
                obj_index = original_objects.index(orig_obj)
                refinement = None

                for ref in refined_objects_data:
                    if ref.get('object_index') == obj_index:
                        refinement = ref
                        break

                if refinement is None:
                    logger.warning(f"No refinement found for object {obj_index}, keeping original")
                    refined_objects.append(orig_obj)
                    continue

                # Check if marked as false positive
                if refinement.get('is_false_positive', False):
                    logger.info(f"Object {obj_index} marked as false positive: {orig_obj.description}")
                    continue  # Skip this object (don't add to refined list)

                # Apply refinements to valid object
                refined_obj = orig_obj.model_copy(update={
                    'distance': refinement.get('refined_distance', orig_obj.distance),
                    'priority': refinement.get('refined_priority', orig_obj.priority),
                    'location_description': refinement.get('location_description', '')
                })
                refined_objects.append(refined_obj)

            logger.info(f"Refined {len(original_objects)} objects to {len(refined_objects)} "
                       f"(rejected {len(original_objects) - len(refined_objects)} false positives)")

            return refined_objects, RefinerStatus.SUCCESS, ""

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Response text: {response_text[:500]}...")
            return original_objects, RefinerStatus.FAILED, f"JSON parse error: {e}"

        except Exception as e:
            logger.error(f"Error parsing refiner response: {e}", exc_info=True)
            return original_objects, RefinerStatus.FAILED, str(e)

