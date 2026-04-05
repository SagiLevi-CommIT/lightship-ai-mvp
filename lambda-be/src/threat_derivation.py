"""Priority level derivation from hazard events.

Derives object-level priority from hazard event severity ratings.
Note: This is now deprecated as LLM assigns priority directly.
"""
import logging
from typing import List, Dict, Tuple
from src.schemas import ObjectLabel, HazardEvent
from src.config import HAZARD_SEVERITY_TO_THREAT, HAZARD_SEVERITY_RANK

logger = logging.getLogger(__name__)


def derive_threat_levels(
    all_objects: List[ObjectLabel],
    hazard_events: List[HazardEvent]
) -> List[ObjectLabel]:
    """Derive priority for objects from hazard_events.

    Objects within the temporal window of a hazard event inherit that hazard's severity
    as their priority. Objects not near any hazard get priority='none'.

    Note: This function is deprecated - LLM now assigns priority directly.

    Args:
        all_objects: List of all detected objects
        hazard_events: List of hazard events from LLM

    Returns:
        Updated list of objects with priority set
    """
    logger.info(f"Deriving priority levels for {len(all_objects)} objects from {len(hazard_events)} hazards")

    # Build timestamp -> max severity mapping
    # For each hazard, assign severity to all objects near that timestamp
    TIME_WINDOW_MS = 2000  # Objects within 2s of a hazard inherit its severity

    # Apply threat levels to objects
    updated_objects = []
    for obj in all_objects:
        # Find hazards that overlap with this object's timestamp
        best_severity = "None"

        for hazard in hazard_events:
            time_diff = abs(obj.start_time_ms - hazard.start_time_ms)

            # Check if object is within hazard time window
            if time_diff < TIME_WINDOW_MS:
                # Object is potentially involved in this hazard
                # Check if object type is mentioned in hazard description (simple heuristic)
                if (obj.description in hazard.hazard_description.lower() or
                    hazard.hazard_severity in ['Critical', 'High']):  # High severity affects all nearby objects

                    if HAZARD_SEVERITY_RANK[hazard.hazard_severity] > HAZARD_SEVERITY_RANK[best_severity]:
                        best_severity = hazard.hazard_severity

        # Map severity to priority
        priority = HAZARD_SEVERITY_TO_THREAT[best_severity]

        # Create updated object with priority
        updated_obj = obj.model_copy(update={'priority': priority})
        updated_objects.append(updated_obj)

    # Log statistics
    priority_counts = {}
    for obj in updated_objects:
        priority_counts[obj.priority] = priority_counts.get(obj.priority, 0) + 1

    logger.info(f"Priority distribution: {priority_counts}")

    return updated_objects

