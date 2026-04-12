"""Scene labeler module using AWS Bedrock LLM for object detection and labeling.

Sends frame images to Claude Sonnet via Bedrock with detailed prompts for object
detection, distance estimation, and threat level assessment.
"""
import base64
import json
import logging
from typing import List, Dict, Any
import boto3
from botocore.exceptions import ClientError
from src.schemas import ObjectLabel, Center, PolygonPoint
from src.config import (
    AWS_REGION,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    BEDROCK_MODEL_ID,
    TEMPERATURE,
    MAX_TOKENS,
    TOP_P,
    TOP_K,
    DISTANCE_ENUM,
    DISTANCE_DESCRIPTIONS,
    OBJECT_LABELS,
    THREAT_LEVEL_ENUM,
    THREAT_LEVEL_GUIDELINES
)

logger = logging.getLogger(__name__)


class SceneLabeler:
    """Labels objects in frames using Bedrock Claude Sonnet."""

    def __init__(self):
        """Initialize SceneLabeler with Bedrock client."""
        self.bedrock_client = boto3.client(
            service_name='bedrock-runtime',
            region_name=AWS_REGION
        )
        logger.info(f"SceneLabeler initialized with model: {BEDROCK_MODEL_ID}")

    def label_frame(
        self,
        frame_path: str,
        timestamp_ms: float,
        video_width: int,
        video_height: int
    ) -> List[ObjectLabel]:
        """Label objects in a single frame.

        Args:
            frame_path: Path to frame image file
            timestamp_ms: Timestamp of frame in milliseconds
            video_width: Video width in pixels
            video_height: Video height in pixels

        Returns:
            List of ObjectLabel instances for detected objects
        """
        logger.info(f"Labeling frame: {frame_path} at {timestamp_ms:.2f}ms")

        # Read and encode image
        with open(frame_path, 'rb') as f:
            image_bytes = f.read()
        image_base64 = base64.b64encode(image_bytes).decode('utf-8')

        # Construct prompt with exact dimensions
        prompt = self._build_prompt(video_width, video_height)

        # Call Bedrock API
        try:
            response = self._call_bedrock(image_base64, prompt)
            objects = self._parse_response(response, timestamp_ms, video_width, video_height)
            logger.info(f"Detected {len(objects)} objects in frame")
            return objects
        except Exception as e:
            logger.error(f"Error labeling frame {frame_path}: {e}")
            return []

    def _build_prompt(self, video_width: int, video_height: int) -> str:
        """Build detailed prompt for object labeling.

        Args:
            video_width: Video width in pixels
            video_height: Video height in pixels

        Returns:
            Formatted prompt string
        """
        prompt = f"""You are analyzing a dashcam video frame for driver safety training. Your task is to identify ALL relevant objects, hazards, and scene elements that would be important for understanding driving context and potential risks.

**VIDEO SPECIFICATIONS:**
- Resolution: {video_width}x{video_height} pixels
- Coordinate system: (0,0) is top-left corner
- X coordinates must be between 0 and {video_width}
- Y coordinates must be between 0 and {video_height}

**CRITICAL: All bounding box coordinates MUST be within the image bounds!**

**YOUR TASK:**
For each object/element you identify, provide:
1. A description using the object labels below
2. Distance category
3. Threat level assessment
4. Spatial location (center point and/or bounding box)

**OBJECT LABELS TO USE:**
{', '.join(OBJECT_LABELS)}

**DISTANCE CATEGORIES:**
{self._format_enum_list(DISTANCE_ENUM, DISTANCE_DESCRIPTIONS)}

**THREAT LEVELS:**
{self._format_enum_list(THREAT_LEVEL_ENUM, THREAT_LEVEL_GUIDELINES)}

**THREAT LEVEL ASSIGNMENT GUIDELINES:**
- Consider: object type, proximity, movement trajectory, context
- VRUs (pedestrians, bicyclists) near the vehicle path: HIGH or CRITICAL
- Vehicles cutting in or close proximity: MEDIUM to HIGH
- Traffic controls and lane markings: NONE to LOW (informational)
- Parked vehicles far from path: LOW
- Obstructions or construction in/near path: MEDIUM to HIGH
- Emergency vehicles: MEDIUM to HIGH regardless of distance

**OUTPUT FORMAT:**
Return a JSON array of objects. Each object MUST have:
- "description": string (use labels above; add modifiers like "(parked)", "(group)" if needed)
- "distance": string (one of: {', '.join(DISTANCE_ENUM)})
- "threat_level": string (one of: {', '.join(THREAT_LEVEL_ENUM)})
- "center": object with "x" and "y" integer coordinates (MUST be within 0-{video_width} and 0-{video_height})
- "x_min", "y_min", "x_max", "y_max": bounding box coordinates in pixels (MUST be within image bounds!)
- "width", "height": bounding box dimensions (calculated as x_max - x_min, y_max - y_min)

**CRITICAL REQUIREMENTS:**
- ALL coordinates must be within the image bounds ({video_width}x{video_height})
- x direction is from left to right
- y direction is from top to bottom
- x_min must be >= 0 and <= {video_width}
- y_min must be >= 0 and <= {video_height}
- x_max must be >= x_min and <= {video_width}
- y_max must be >= y_min and <= {video_height}
- Measure bounding boxes PRECISELY from the visible edges of each object
- Do NOT place bounding boxes outside the image
- Return ONLY the JSON array, no additional text

**IMPORTANT:**
- Identify ALL relevant objects (vehicles, VRUs, signs, lane markings, obstacles, etc.)
- Be thorough but accurate
- Use pixel coordinates that precisely match the object's location in the {video_width}x{video_height} frame
- Ensure bounding boxes tightly fit around each object

**Example output structure:**
[
  {{
    "description": "pedestrian",
    "distance": "close",
    "threat_level": "high",
    "center": {{"x": 640, "y": 430}},
    "x_min": 590.0,
    "y_min": 380.0,
    "x_max": 700.0,
    "y_max": 510.0,
    "width": 110,
    "height": 130
  }},
  {{
    "description": "vehicle",
    "distance": "moderate",
    "threat_level": "medium",
    "center": {{"x": 320, "y": 400}},
    "x_min": 250.0,
    "y_min": 350.0,
    "x_max": 390.0,
    "y_max": 450.0,
    "width": 140,
    "height": 100
  }}
]

Now analyze the dashcam frame and return the JSON array of detected objects."""

        return prompt

    def _format_enum_list(self, enum_list: List[str], descriptions: Dict[str, str]) -> str:
        """Format enum list with descriptions.

        Args:
            enum_list: List of enum values
            descriptions: Dictionary mapping values to descriptions

        Returns:
            Formatted string
        """
        lines = []
        for value in enum_list:
            desc = descriptions.get(value, "")
            if desc:
                lines.append(f"  - {value}: {desc}")
            else:
                lines.append(f"  - {value}")
        return "\n".join(lines)

    def _call_bedrock(self, image_base64: str, prompt: str) -> str:
        """Call Bedrock API with image and prompt.

        Args:
            image_base64: Base64-encoded image
            prompt: Prompt text

        Returns:
            Response text from model

        Raises:
            ClientError: If API call fails
        """
        # Construct request body for Claude
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": MAX_TOKENS,
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "top_k": TOP_K,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_base64
                            }
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }
            ]
        }

        try:
            response = self.bedrock_client.invoke_model(
                modelId=BEDROCK_MODEL_ID,
                body=json.dumps(request_body)
            )

            response_body = json.loads(response['body'].read())

            # Extract text from response
            if 'content' in response_body and len(response_body['content']) > 0:
                return response_body['content'][0]['text']
            else:
                raise ValueError("No content in Bedrock response")

        except ClientError as e:
            logger.error(f"Bedrock API error: {e}")
            raise

    def _parse_response(self, response_text: str, timestamp_ms: float, video_width: int, video_height: int) -> List[ObjectLabel]:
        """Parse LLM response into ObjectLabel instances.

        Args:
            response_text: Raw response text from LLM
            timestamp_ms: Timestamp to assign to objects
            video_width: Video width for bbox validation
            video_height: Video height for bbox validation

        Returns:
            List of ObjectLabel instances
        """
        try:
            # Extract JSON array from response
            # LLM might include extra text, so find the JSON array
            start_idx = response_text.find('[')
            end_idx = response_text.rfind(']')

            if start_idx == -1 or end_idx == -1:
                logger.error("No JSON array found in response")
                return []

            json_text = response_text[start_idx:end_idx+1]
            objects_data = json.loads(json_text)

            # Convert to ObjectLabel instances
            objects = []
            for obj_data in objects_data:
                try:
                    # Add timestamp
                    obj_data['start_time_ms'] = timestamp_ms

                    # Validate and clip bounding box coordinates
                    if 'x_min' in obj_data and obj_data['x_min'] is not None:
                        obj_data['x_min'] = max(0, min(obj_data['x_min'], video_width))
                    if 'y_min' in obj_data and obj_data['y_min'] is not None:
                        obj_data['y_min'] = max(0, min(obj_data['y_min'], video_height))
                    if 'x_max' in obj_data and obj_data['x_max'] is not None:
                        obj_data['x_max'] = max(0, min(obj_data['x_max'], video_width))
                    if 'y_max' in obj_data and obj_data['y_max'] is not None:
                        obj_data['y_max'] = max(0, min(obj_data['y_max'], video_height))

                    # Validate center coordinates
                    if 'center' in obj_data and obj_data['center']:
                        center = obj_data['center']
                        if 'x' in center:
                            center['x'] = int(max(0, min(center['x'], video_width)))
                        if 'y' in center:
                            center['y'] = int(max(0, min(center['y'], video_height)))
                        obj_data['center'] = Center(**center)

                    # Parse polygon (if present and not empty) and validate coordinates
                    if 'polygon' in obj_data and obj_data['polygon']:
                        validated_polygon = []
                        for pt in obj_data['polygon']:
                            pt['x'] = max(0, min(pt['x'], video_width))
                            pt['y'] = max(0, min(pt['y'], video_height))
                            validated_polygon.append(PolygonPoint(**pt))
                        obj_data['polygon'] = validated_polygon
                    else:
                        obj_data['polygon'] = []

                    # Recalculate width and height from validated bbox
                    if all(k in obj_data and obj_data[k] is not None for k in ['x_min', 'y_min', 'x_max', 'y_max']):
                        obj_data['width'] = obj_data['x_max'] - obj_data['x_min']
                        obj_data['height'] = obj_data['y_max'] - obj_data['y_min']

                    # Create ObjectLabel
                    obj = ObjectLabel(**obj_data)
                    objects.append(obj)

                except Exception as e:
                    logger.warning(f"Failed to parse object: {e}. Data: {obj_data}")
                    continue

            return objects

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Response text: {response_text}")
            return []
        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return []

