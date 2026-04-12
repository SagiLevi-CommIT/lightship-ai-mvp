"""Lambda entry-point.

Dual-mode handler:
  1. WORKER MODE  – invoked asynchronously (InvocationType=Event) by /process-video.
                    event["action"] == "process_worker"
                    Runs the full CV+LLM pipeline, updates DynamoDB, uploads results to S3.

  2. API MODE     – invoked synchronously by ALB (HTTP requests from the frontend).
                    Routed through Mangum → FastAPI for all REST endpoints.
"""
import sys
import logging

# Ensure src/ is importable when running as a Lambda package
sys.path.insert(0, "/var/task/src")

# Set log level before any imports so module-level loggers inherit it
logging.getLogger().setLevel(logging.INFO)

from api_server import app, process_video_worker  # noqa: E402
from mangum import Mangum  # noqa: E402

# Module-level: initialised once on cold start, reused across warm invocations
_http_handler = Mangum(app, lifespan="off")


def lambda_handler(event, context):
    """Route Lambda invocations to the correct handler.

    Worker events (action=process_worker) bypass HTTP routing entirely and
    call process_video_worker() directly.  All other events are assumed to be
    ALB/API-Gateway HTTP payloads and are handled by Mangum → FastAPI.
    """
    action = event.get("action") if isinstance(event, dict) else None

    if action == "process_worker":
        # Async self-invocation from POST /process-video
        logging.getLogger(__name__).info(
            f"🔧 Worker mode: job_id={event.get('job_id')} file={event.get('filename')}"
        )
        return process_video_worker(event)

    # Normal HTTP request via ALB
    return _http_handler(event, context)
