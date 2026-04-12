"""Hazard Assessor module using temporal LLM for hazard event detection.

Sends JSON object sequences + middle frame image to Bedrock LLM to identify hazards.
"""
import base64
import json
import logging
import time
from typing import List, Dict, Tuple
import boto3
from botocore.exceptions import ClientError, EndpointConnectionError, ConnectionError, ReadTimeoutError, ConnectionClosedError
from botocore.config import Config

from src.schemas import ObjectLabel, HazardEvent
from src.config import (
    AWS_REGION,
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    BEDROCK_MODEL_ID,
    TEMPERATURE,
    MAX_TOKENS,
    TOP_P,
    TOP_K,
    HAZARD_LLM_MODE,
    WINDOW_SIZE,
    WINDOW_OVERLAP,
    MERGE_POLICY,
    HAZARD_SEVERITY_RANK
)

logger = logging.getLogger(__name__)


class WindowInfo:
    """Information about a temporal window."""
    def __init__(self, frame_indices: List[int], middle_frame_idx: int):
        self.frame_indices = frame_indices
        self.middle_frame_idx = middle_frame_idx


class HazardAssessor:
    """Assesses hazards using temporal LLM with JSON + image input."""

    def __init__(self):
        """Initialize HazardAssessor with Bedrock client."""
        # Configure boto3 with longer timeouts for LLM processing
        config = Config(
            read_timeout=600,  # 10 minutes for LLM to process large requests
            connect_timeout=10,  # 10 seconds to establish connection
            retries={'max_attempts': 0}  # We handle retries manually with exponential backoff
        )

        self.bedrock_client = boto3.client(
            service_name='bedrock-runtime',
            region_name=AWS_REGION,
            config=config
        )
        logger.info(f"HazardAssessor initialized with model: {BEDROCK_MODEL_ID}")

    def assess_hazards_only(
        self,
        frame_objects: Dict[int, List[ObjectLabel]],
        frame_images: Dict[int, str],
        video_metadata: Dict
    ) -> Tuple[List[HazardEvent], List[ObjectLabel], Dict]:
        """Assess hazards from PRE-REFINED objects (from per-frame refinement).

        This method is called after per-frame LLM refinement, so objects are already validated.
        Focus is on temporal hazard detection and video metadata inference.

        Args:
            frame_objects: Dict mapping frame_idx to list of ALREADY REFINED objects
            frame_images: Dict mapping frame_idx to ANNOTATED image path (with refined bboxes)
            video_metadata: Video metadata dict

        Returns:
            Tuple of (hazard_events, objects_list, inferred_video_metadata)
        """
        logger.info("Assessing hazards from pre-refined objects (hazard events only)")

        # For now, use the existing assess_hazards method
        # The objects are already refined, so LLM will mostly confirm them
        # TODO: In future, create a simplified prompt that only asks for hazard events and metadata
        # without redundant object validation/refinement tasks

        return self.assess_hazards(frame_objects, frame_images, video_metadata)

    def assess_hazards(
        self,
        frame_objects: Dict[int, List[ObjectLabel]],
        frame_images: Dict[int, str],
        video_metadata: Dict
    ) -> Tuple[List[HazardEvent], List[ObjectLabel], Dict]:
        """Assess hazards from object sequences and frame images.

        Args:
            frame_objects: Dict mapping frame_idx to list of objects
            frame_images: Dict mapping frame_idx to image path
            video_metadata: Video metadata dict

        Returns:
            Tuple of (hazard_events, all_objects_with_refined_priority, inferred_video_metadata)
        """
        logger.info(f"Assessing hazards using {HAZARD_LLM_MODE} mode")

        if HAZARD_LLM_MODE == "full_video":
            return self._assess_full_video(frame_objects, frame_images, video_metadata)
        else:
            return self._assess_sliding_windows(frame_objects, frame_images, video_metadata)

    def _assess_full_video(
        self,
        frame_objects: Dict[int, List[ObjectLabel]],
        frame_images: Dict[int, str],
        video_metadata: Dict
    ) -> Tuple[List[HazardEvent], List[ObjectLabel], Dict]:
        """Assess hazards for full video at once.

        Args:
            frame_objects: Dict mapping frame_idx to list of objects
            frame_images: Dict mapping frame_idx to image path
            video_metadata: Video metadata dict

        Returns:
            Tuple of (hazard_events, refined_objects, inferred_video_metadata)
        """
        # Get all frame images (sorted by frame index)
        frame_indices = sorted(frame_objects.keys())
        image_paths = [frame_images[idx] for idx in frame_indices if idx in frame_images]

        # Collect all objects
        all_objects_flat = []
        for frame_idx in sorted(frame_objects.keys()):
            all_objects_flat.extend(frame_objects[frame_idx])

        # Call LLM with all images
        hazard_events, refined_objects, inferred_metadata = self._call_llm_for_hazards(
            objects_list=all_objects_flat,
            image_paths=image_paths,
            video_metadata=video_metadata
        )

        # Apply refinements to objects
        refined_object_list = self._apply_refinements(frame_objects, refined_objects)

        return hazard_events, refined_object_list, inferred_metadata

    def _assess_sliding_windows(
        self,
        frame_objects: Dict[int, List[ObjectLabel]],
        frame_images: Dict[int, str],
        video_metadata: Dict
    ) -> Tuple[List[HazardEvent], List[ObjectLabel], Dict]:
        """Assess hazards using sliding windows.

        Args:
            frame_objects: Dict mapping frame_idx to list of objects
            frame_images: Dict mapping frame_idx to image path
            video_metadata: Video metadata dict

        Returns:
            Tuple of (hazard_events, all_objects, inferred_video_metadata)
        """
        frame_indices = sorted(frame_objects.keys())

        # Build windows
        windows = self._build_windows(frame_indices)
        logger.info(f"Created {len(windows)} windows with size={WINDOW_SIZE}, overlap={WINDOW_OVERLAP}")

        # Process each window
        all_window_hazards = []

        for i, window in enumerate(windows):
            logger.info(f"Processing window {i+1}/{len(windows)}: frames {window.frame_indices}")

            # Collect objects for this window
            window_objects = []
            for frame_idx in window.frame_indices:
                if frame_idx in frame_objects:
                    window_objects.extend(frame_objects[frame_idx])

            # Get all frame images for this window
            window_image_paths = []
            for frame_idx in window.frame_indices:
                if frame_idx in frame_images:
                    window_image_paths.append(frame_images[frame_idx])

            if not window_image_paths or not window_objects:
                logger.warning(f"Skipping window {i+1}: missing data")
                continue

            # Build frame_objects dict for this window
            window_frame_objects = {}
            for frame_idx in window.frame_indices:
                if frame_idx in frame_objects:
                    window_frame_objects[frame_idx] = frame_objects[frame_idx]

            # Call LLM for this window with all images
            try:
                hazards, refined_objects, window_metadata = self._call_llm_for_hazards(
                    objects_list=window_objects,
                    image_paths=window_image_paths,
                    video_metadata=video_metadata
                )
                all_window_hazards.extend(hazards)

                # Store metadata from first window (most representative)
                if i == 0:
                    inferred_video_metadata = window_metadata

                # Apply refinements for this window
                refined_window_objects = self._apply_refinements(window_frame_objects, refined_objects)
                # Store refined objects (will be collected later)
                for refined_obj in refined_window_objects:
                    # Update in original frame_objects dict
                    for frame_idx in window.frame_indices:
                        if frame_idx in frame_objects:
                            for j, orig_obj in enumerate(frame_objects[frame_idx]):
                                if (orig_obj.start_time_ms == refined_obj.start_time_ms and
                                    orig_obj.description == refined_obj.description and
                                    orig_obj.center == refined_obj.center):
                                    frame_objects[frame_idx][j] = refined_obj
                                    break

            except Exception as e:
                logger.error(f"Error processing window {i+1}: {e}")
                continue

        # Merge overlapping hazards
        merged_hazards = self._merge_hazards(all_window_hazards)

        # Collect all objects
        all_objects_flat = []
        for frame_idx in sorted(frame_objects.keys()):
            all_objects_flat.extend(frame_objects[frame_idx])

        # Return inferred metadata (or empty dict if no windows processed)
        if 'inferred_video_metadata' not in locals():
            inferred_video_metadata = {}

        return merged_hazards, all_objects_flat, inferred_video_metadata

    def _build_windows(self, frame_indices: List[int]) -> List[WindowInfo]:
        """Build sliding windows from frame indices.

        Args:
            frame_indices: List of frame indices

        Returns:
            List of WindowInfo objects
        """
        windows = []
        step = WINDOW_SIZE - WINDOW_OVERLAP

        for i in range(0, len(frame_indices), step):
            window_frames = frame_indices[i:i + WINDOW_SIZE]

            if len(window_frames) >= 2:  # Need at least 2 frames for temporal analysis
                middle_idx_pos = len(window_frames) // 2
                middle_frame = window_frames[middle_idx_pos]

                windows.append(WindowInfo(
                    frame_indices=window_frames,
                    middle_frame_idx=middle_frame
                ))

        return windows

    def _call_llm_for_hazards(
        self,
        objects_list: List[ObjectLabel],
        image_paths: List[str],
        video_metadata: Dict
    ) -> Tuple[List[HazardEvent], Dict, Dict]:
        """Call LLM to identify hazards, refine objects, and infer video metadata.

        Args:
            objects_list: List of objects from window
            image_paths: List of paths to frame images (typically 3 frames)
            video_metadata: Video metadata

        Returns:
            Tuple of (hazard_events, refined_objects_dict, inferred_video_metadata)
            refined_objects_dict maps (frame_idx, object_index) -> refinement data
        """
        # Read and encode all images
        images_base64 = []
        for image_path in image_paths:
            with open(image_path, 'rb') as f:
                image_bytes = f.read()
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            images_base64.append(image_base64)

        logger.debug(f"Encoded {len(images_base64)} images for LLM call")

        # Build prompt
        prompt = self._build_hazard_prompt(objects_list, video_metadata)

        # Call Bedrock
        try:
            response = self._call_bedrock(images_base64, prompt)
            # Validate and repair if needed
            hazard_events, refined_objects, inferred_metadata = self._validate_and_repair_response(
                response, images_base64, prompt, retry_count=0
            )
            logger.info(f"Identified {len(hazard_events)} hazard events and {len(refined_objects)} refined objects")
            return hazard_events, refined_objects, inferred_metadata
        except Exception as e:
            logger.error(f"Error calling LLM for hazards (after retries): {e}")
            raise  # Re-raise instead of returning empty - let caller handle

    def _build_hazard_prompt(
        self,
        objects_list: List[ObjectLabel],
        video_metadata: Dict
    ) -> str:
        """Build prompt for hazard detection and object refinement.

        Args:
            objects_list: List of objects
            video_metadata: Video metadata

        Returns:
            Prompt string
        """
        # Group objects by timestamp with frame indexing
        objects_by_time = {}
        for obj in objects_list:
            timestamp = obj.start_time_ms
            if timestamp not in objects_by_time:
                objects_by_time[timestamp] = []
            objects_by_time[timestamp].append(obj)

        # Create frame_idx mapping (sorted timestamps become frame indices)
        sorted_timestamps = sorted(objects_by_time.keys())
        timestamp_to_frame_idx = {ts: idx for idx, ts in enumerate(sorted_timestamps)}

        # Format objects for prompt with frame_idx and object_index
        objects_text = []
        for timestamp in sorted_timestamps:
            frame_idx = timestamp_to_frame_idx[timestamp]
            objs = objects_by_time[timestamp]
            objects_text.append(f"\n**FRAME {frame_idx} at {timestamp:.0f}ms:**")
            for obj_index, obj in enumerate(objs):
                obj_str = (
                    f"  Object {obj_index}: {obj.description} - "
                    f"CV distance: {obj.distance}, "
                    f"bbox: ({obj.x_min:.0f}, {obj.y_min:.0f}) to ({obj.x_max:.0f}, {obj.y_max:.0f})"
                )
                if obj.center:
                    obj_str += f", center: ({obj.center.x}, {obj.center.y})"
                objects_text.append(obj_str)

        objects_formatted = "\n".join(objects_text)

        prompt = f"""You are analyzing dashcam footage for autonomous vehicle hazard detection. You will receive {len(sorted_timestamps)} sequential frames with their detected objects.

VIDEO INFO:
- Camera: {video_metadata.get('camera', 'unknown')}
- FPS: {video_metadata.get('fps', 10)}
- Duration: {video_metadata.get('duration_ms', 0):.0f}ms

DETECTED OBJECTS (from CV system):
{objects_formatted}

YOUR TASKS:

1. VALIDATE DETECTIONS (REJECT FALSE POSITIVES)
   Visually inspect EACH detected object and determine if it's a TRUE or FALSE positive:
   - TRUE: The object is correctly identified (e.g., actual lane marking, real crosswalk, valid double_yellow)
   - FALSE: The detection is incorrect (e.g., railroad tracks labeled as crosswalk, yellow building as double_yellow, curb as lane)

   Common false positives to reject:
   - Crosswalks: Railroad/tram tracks, horizontal building edges, fence lines
   - Double_yellow: Yellow buildings, construction signs, curb paint
   - Lanes: Curbs, road edges, building edges, snow patches, shadows

   Mark is_false_positive: true for incorrect detections (they will be removed from final output)

2. REFINE DISTANCE LABELS
   For EACH valid object (not false positive), provide accurate distance based on YOUR VISUAL ANALYSIS:
   - Options: dangerously_close, very_close, close, moderate, far, very_far, n/a
   - Use your judgment based on real-world appearance, perspective, depth cues, and object type
   - DO NOT use mechanical percentage calculations
   - Consider: object size in frame, depth perspective, partial occlusion, road context

3. REFINE PRIORITY LEVELS
   For EACH valid object (not false positive), assign appropriate priority level:
   - critical: Immediate danger requiring urgent response
   - high: Plausible hazard requiring close monitoring
   - medium: Worth attention, may require monitoring
   - low: Relevant but unlikely to require action
   - none: Context-only, informational

4. ADD LOCATION DESCRIPTION
   For vehicles and pedestrians ONLY, describe their spatial position:
   - Examples: "left lane approaching", "right side of road", "center lane ahead", "crossing from right to left"
   - Use empty string "" if location cannot be determined
   - DO NOT include for: traffic signals, signs, lane markings

5. IDENTIFY HAZARDS
   Detect safety-critical events across the frame sequence.

6. INFER VIDEO METADATA
   Based on the visual analysis, provide these video characteristics:
   - description: Brief summary of what happens in the video (1-2 sentences)
   - traffic: Traffic density (light/moderate/heavy/unknown)
   - lighting: Lighting conditions (daylight/dusk/night/unknown)
   - weather: Weather conditions (clear/rain/snow/fog/unknown)
   - collision: Collision type if any (e.g., "intersection,pedestrian" or "none")
   - speed: Approximate vehicle speed (e.g., "<=40mph", ">40mph", "unknown")

REQUIRED OUTPUT FORMAT (valid JSON only, no markdown):
{{
  "video_metadata": {{
    "description": "<brief_summary>",
    "traffic": "<light|moderate|heavy|unknown>",
    "lighting": "<daylight|dusk|night|unknown>",
    "weather": "<clear|rain|snow|fog|unknown>",
    "collision": "<type_or_none>",
    "speed": "<speed_range_or_unknown>"
  }},
  "refined_objects": [
    {{
      "frame_idx": <frame_number>,
      "object_index": <index_in_frame>,
      "is_false_positive": <true_or_false>,
      "refined_distance": "<distance_label>",
      "refined_priority": "<priority_level>",
      "location_description": "<description_or_empty_string>"
    }}
  ],
  "hazard_events": [
    {{
      "start_time_ms": <timestamp>,
      "hazard_type": "<category>",
      "hazard_description": "<detailed_description>",
      "hazard_severity": "<Critical|High|Medium|Low|None>",
      "road_conditions": "<conditions>",
      "duration_ms": <duration>
    }}
  ]
}}

CRITICAL RULES:
- You MUST provide refined_objects entry for EVERY object in EVERY frame
- Use frame_idx and object_index exactly as shown in the input (e.g., FRAME 0, Object 0)
- is_false_positive must be true or false (boolean) - mark true if detection is incorrect based on visual inspection
- refined_distance must be one of: dangerously_close, very_close, close, moderate, far, very_far, n/a
- refined_priority must be one of: critical, high, medium, low, none
- location_description required only for vehicles/pedestrians (empty string "" for others)
- Return ONLY valid JSON, no explanations or markdown
- If no hazards, use "hazard_events": []

Now analyze the frames and return the complete JSON:"""

        return prompt

    def _call_bedrock(self, images_base64: List[str], prompt: str) -> str:
        """Call Bedrock API with multiple images and prompt, with retry logic for transient errors.

        Args:
            images_base64: List of base64-encoded images
            prompt: Prompt text

        Returns:
            Response text from model
        """
        # Build content array with all images followed by text prompt
        content = []

        # Add all images
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

        # Retry logic for transient errors
        MAX_RETRIES = 3
        INITIAL_BACKOFF = 2  # seconds

        for attempt in range(MAX_RETRIES):
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

            except (EndpointConnectionError, ConnectionError, ReadTimeoutError, ConnectionClosedError) as e:
                # Transient connection/timeout errors - retry with exponential backoff
                error_type = "Timeout" if isinstance(e, ReadTimeoutError) else "Connection"
                if attempt < MAX_RETRIES - 1:
                    wait_time = INITIAL_BACKOFF * (2 ** attempt)
                    logger.warning(f"{error_type} error on attempt {attempt + 1}/{MAX_RETRIES}: {e}")
                    logger.info(f"Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"{error_type} error after {MAX_RETRIES} attempts: {e}")
                    raise
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                # Retry on throttling or service unavailable
                if error_code in ['ThrottlingException', 'ServiceUnavailable', 'TooManyRequestsException']:
                    if attempt < MAX_RETRIES - 1:
                        wait_time = INITIAL_BACKOFF * (2 ** attempt)
                        logger.warning(f"Throttling/service error on attempt {attempt + 1}/{MAX_RETRIES}: {error_code}")
                        logger.info(f"Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"Service error after {MAX_RETRIES} attempts: {e}")
                        raise
                else:
                    # Non-retryable error (auth, permissions, etc.)
                    logger.error(f"Bedrock API error: {e}")
                    raise

    def _validate_and_repair_response(
        self,
        response_text: str,
        images_base64: List[str],
        prompt: str,
        retry_count: int = 0
    ) -> Tuple[List[HazardEvent], Dict, Dict]:
        """Validate LLM response and auto-repair or retry with LLM if needed.

        Args:
            response_text: Raw response from LLM
            images_base64: List of base64-encoded images (for retry)
            prompt: Original prompt (for retry)
            retry_count: Current retry attempt number

        Returns:
            Tuple of (hazard_events, refined_objects_dict, video_metadata_dict)
        """
        MAX_RETRIES = 2

        # Try to parse the response
        try:
            hazard_events, refined_objects, video_metadata = self._parse_hazard_response(response_text)

            # Validate that we got refined_objects
            if len(refined_objects) == 0:
                raise ValueError("No refined_objects found in response")

            logger.info("Response validated successfully")
            return hazard_events, refined_objects, video_metadata

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parsing failed: {e}")

            # Try auto-fix
            fixed_text = self._auto_fix_json(response_text)
            if fixed_text != response_text:
                logger.info("Auto-fix applied, retrying parse...")
                try:
                    hazard_events, refined_objects, video_metadata = self._parse_hazard_response(fixed_text)
                    if len(refined_objects) > 0:
                        logger.info("Auto-fix successful")
                        return hazard_events, refined_objects, video_metadata
                except:
                    pass

            # Auto-fix failed, try LLM repair
            if retry_count < MAX_RETRIES:
                logger.info(f"Auto-fix failed, calling LLM repair (attempt {retry_count + 1}/{MAX_RETRIES})")
                return self._call_llm_repair(response_text, str(e), images_base64, prompt, retry_count)
            else:
                logger.error(f"Max retries ({MAX_RETRIES}) exceeded, giving up")
                raise

        except Exception as e:
            logger.warning(f"Response validation failed: {e}")

            # Try LLM repair for schema issues
            if retry_count < MAX_RETRIES:
                logger.info(f"Calling LLM repair for schema issue (attempt {retry_count + 1}/{MAX_RETRIES})")
                return self._call_llm_repair(response_text, str(e), images_base64, prompt, retry_count)
            else:
                logger.error(f"Max retries ({MAX_RETRIES}) exceeded, giving up")
                raise

    def _auto_fix_json(self, text: str) -> str:
        """Attempt to auto-fix common JSON syntax issues.

        Args:
            text: Potentially malformed JSON text

        Returns:
            Fixed JSON text (or original if no fix applied)
        """
        fixed = text

        # Remove markdown code blocks
        if "```json" in fixed:
            fixed = fixed.split("```json")[1].split("```")[0].strip()
        elif "```" in fixed:
            fixed = fixed.split("```")[1].split("```")[0].strip()

        # Extract JSON if surrounded by text
        start_idx = fixed.find('{')
        end_idx = fixed.rfind('}')
        if start_idx != -1 and end_idx != -1:
            fixed = fixed[start_idx:end_idx+1]

        # Fix trailing commas before closing brackets
        import re
        fixed = re.sub(r',(\s*[}\]])', r'\1', fixed)

        # Try to balance brackets
        open_braces = fixed.count('{')
        close_braces = fixed.count('}')
        if open_braces > close_braces:
            fixed += '}' * (open_braces - close_braces)

        open_brackets = fixed.count('[')
        close_brackets = fixed.count(']')
        if open_brackets > close_brackets:
            fixed += ']' * (open_brackets - close_brackets)

        if fixed != text:
            logger.debug("Auto-fix applied to JSON")

        return fixed

    def _call_llm_repair(
        self,
        invalid_response: str,
        error_message: str,
        images_base64: List[str],
        original_prompt: str,
        retry_count: int
    ) -> Tuple[List[HazardEvent], Dict, Dict]:
        """Call LLM to repair invalid response.

        Args:
            invalid_response: The invalid response from LLM
            error_message: Error message describing the issue
            images_base64: List of base64-encoded images
            original_prompt: Original prompt
            retry_count: Current retry attempt

        Returns:
            Tuple of (hazard_events, refined_objects_dict, video_metadata_dict)
        """
        repair_prompt = f"""Your previous response had issues and could not be parsed correctly.

ORIGINAL RESPONSE:
{invalid_response[:1000]}...

ERROR ENCOUNTERED:
{error_message}

Please fix your response and return ONLY valid JSON matching this exact schema:
{{
  "video_metadata": {{
    "description": "<string>",
    "traffic": "<light|moderate|heavy|unknown>",
    "lighting": "<daylight|dusk|night|unknown>",
    "weather": "<clear|rain|snow|fog|unknown>",
    "collision": "<string or none>",
    "speed": "<string or unknown>"
  }},
  "refined_objects": [
    {{
      "frame_idx": <number>,
      "object_index": <number>,
      "is_false_positive": <true_or_false>,
      "refined_distance": "<dangerously_close|very_close|close|moderate|far|very_far|n/a>",
      "refined_priority": "<critical|high|medium|low|none>",
      "location_description": "<string or empty>"
    }}
  ],
  "hazard_events": [
    {{
      "start_time_ms": <number>,
      "hazard_type": "<string>",
      "hazard_description": "<string>",
      "hazard_severity": "<Critical|High|Medium|Low|None>",
      "road_conditions": "<string>",
      "duration_ms": <number or null>
    }}
  ]
}}

CRITICAL:
- You MUST include refined_objects array with entries for ALL objects
- Return ONLY the JSON, no explanations or markdown
- Ensure all field names and values match the schema exactly

Return the corrected JSON now:"""

        logger.info("Calling Bedrock for repair...")
        try:
            response = self._call_bedrock(images_base64, repair_prompt)
            # Recursively validate (with incremented retry count)
            return self._validate_and_repair_response(response, images_base64, original_prompt, retry_count + 1)
        except Exception as e:
            logger.error(f"LLM repair failed: {e}")
            raise

    def _parse_hazard_response(self, response_text: str) -> Tuple[List[HazardEvent], Dict, Dict]:
        """Parse LLM response into HazardEvent instances, refined objects, and video metadata.

        Args:
            response_text: Raw response text from LLM

        Returns:
            Tuple of (hazard_events, refined_objects_dict, video_metadata_dict)
            refined_objects_dict maps (frame_idx, object_index) -> refinement data
        """
        try:
            # Extract JSON from response
            start_idx = response_text.find('{')
            end_idx = response_text.rfind('}')

            if start_idx == -1 or end_idx == -1:
                logger.error("No JSON found in response")
                return [], {}, {}

            json_text = response_text[start_idx:end_idx+1]
            data = json.loads(json_text)

            # Parse video metadata
            video_metadata = data.get('video_metadata', {})

            # Parse hazard events
            hazard_events = []
            for hazard_data in data.get('hazard_events', []):
                try:
                    hazard = HazardEvent(
                        start_time_ms=float(hazard_data['start_time_ms']),
                        hazard_type=hazard_data['hazard_type'],
                        hazard_description=hazard_data['hazard_description'],
                        hazard_severity=hazard_data['hazard_severity'],
                        road_conditions=hazard_data.get('road_conditions', 'Unknown'),
                        duration_ms=hazard_data.get('duration_ms')
                    )
                    hazard_events.append(hazard)
                except Exception as e:
                    logger.warning(f"Failed to parse hazard event: {e}. Data: {hazard_data}")
                    continue

            # Parse refined objects
            refined_objects = {}
            for ref_obj in data.get('refined_objects', []):
                try:
                    frame_idx = int(ref_obj['frame_idx'])
                    object_index = int(ref_obj['object_index'])
                    key = (frame_idx, object_index)

                    refined_objects[key] = {
                        'is_false_positive': ref_obj.get('is_false_positive', False),
                        'refined_distance': ref_obj['refined_distance'],
                        'refined_priority': ref_obj['refined_priority'],
                        'location_description': ref_obj.get('location_description', '')
                    }
                except Exception as e:
                    logger.warning(f"Failed to parse refined object: {e}. Data: {ref_obj}")
                    continue

            logger.debug(f"Parsed {len(hazard_events)} hazard events, {len(refined_objects)} refined objects, and video metadata")
            return hazard_events, refined_objects, video_metadata

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.debug(f"Response text: {response_text}")
            return [], {}, {}
        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return [], {}, {}

    def _apply_refinements(
        self,
        frame_objects: Dict[int, List[ObjectLabel]],
        refined_objects: Dict
    ) -> List[ObjectLabel]:
        """Apply LLM refinements to objects.

        Args:
            frame_objects: Dict mapping frame_idx to list of objects
            refined_objects: Dict mapping (frame_idx, object_index) -> refinement data

        Returns:
            List of refined ObjectLabel instances
        """
        refined_list = []

        # Group objects by timestamp to create frame_idx mapping
        objects_by_time = {}
        for frame_idx, objs in frame_objects.items():
            if len(objs) > 0:
                timestamp = objs[0].start_time_ms
                objects_by_time[timestamp] = (frame_idx, objs)

        # Create frame_idx mapping (sorted timestamps)
        sorted_timestamps = sorted(objects_by_time.keys())
        timestamp_to_frame_idx = {ts: idx for idx, ts in enumerate(sorted_timestamps)}

        # Apply refinements
        for frame_idx, objs in frame_objects.items():
            if not objs:
                continue

            # Get the logical frame_idx (position in sorted sequence)
            timestamp = objs[0].start_time_ms
            logical_frame_idx = timestamp_to_frame_idx[timestamp]

            for object_index, obj in enumerate(objs):
                key = (logical_frame_idx, object_index)

                if key in refined_objects:
                    # Check if LLM marked this as false positive
                    refinement = refined_objects[key]

                    if refinement.get('is_false_positive', False):
                        # LLM identified this as false positive - skip it (remove from output)
                        logger.info(f"LLM rejected false positive: frame {logical_frame_idx}, object {object_index} ({obj.description})")
                        continue

                    # Apply LLM refinements (not a false positive)
                    refined_obj = obj.model_copy(update={
                        'distance': refinement['refined_distance'],
                        'priority': refinement['refined_priority'],
                        'location_description': refinement.get('location_description', '')
                    })
                    refined_list.append(refined_obj)
                    logger.debug(f"Applied refinement to frame {logical_frame_idx}, object {object_index}")
                else:
                    # No refinement from LLM - keep original (assume valid)
                    logger.warning(f"No refinement for frame {logical_frame_idx}, object {object_index} - keeping original")
                    refined_list.append(obj)

        return refined_list

    def _merge_hazards(self, hazards: List[HazardEvent]) -> List[HazardEvent]:
        """Merge overlapping hazards from different windows.

        Args:
            hazards: List of hazard events from all windows

        Returns:
            Merged list of hazard events
        """
        if not hazards:
            return []

        # Group hazards by type and approximate time
        TIME_TOLERANCE_MS = 2000  # Consider hazards within 2s as potentially same

        merged = []
        used = set()

        for i, hazard in enumerate(hazards):
            if i in used:
                continue

            # Find similar hazards
            similar_group = [hazard]
            used.add(i)

            for j, other in enumerate(hazards[i+1:], start=i+1):
                if j in used:
                    continue

                # Check if similar (same type, close in time)
                time_diff = abs(hazard.start_time_ms - other.start_time_ms)
                if (hazard.hazard_type == other.hazard_type and
                    time_diff < TIME_TOLERANCE_MS):
                    similar_group.append(other)
                    used.add(j)

            # Merge group based on policy
            if MERGE_POLICY == "max_severity":
                # Keep the highest severity
                best_hazard = max(
                    similar_group,
                    key=lambda h: HAZARD_SEVERITY_RANK.get(h.hazard_severity, 0)
                )
            else:  # latest_window
                # Keep the one with latest timestamp
                best_hazard = max(similar_group, key=lambda h: h.start_time_ms)

            merged.append(best_hazard)

        # Sort by timestamp
        merged.sort(key=lambda h: h.start_time_ms)

        logger.info(f"Merged {len(hazards)} hazards into {len(merged)} unique events")
        return merged

