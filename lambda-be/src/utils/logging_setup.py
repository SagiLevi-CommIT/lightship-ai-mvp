"""Logging configuration using dictConfig.

Follows the repo logging standard with console and timed rotating file handlers.
"""
import logging
import logging.config
import os
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_dir: str = None, log_file: str = "app.log") -> None:
    """Set up logging with console and file handlers.
    
    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files (defaults to /tmp/.logs in Lambda, .logs locally)
        log_file: Name of the log file
    """
    # Use /tmp for Lambda (read-only /var/task), .logs locally
    if log_dir is None:
        if os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            log_dir = "/tmp/.logs"
        else:
            log_dir = ".logs"
    
    # Create log directory if it doesn't exist
    try:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
    except OSError:
        log_dir = "/tmp/.logs"
        Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = os.path.join(log_dir, log_file)
    
    logging_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            },
            "detailed": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S"
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "standard",
                "stream": "ext://sys.stdout"
            },
            "file": {
                "class": "logging.handlers.TimedRotatingFileHandler",
                "level": log_level,
                "formatter": "detailed",
                "filename": log_path,
                "when": "midnight",
                "interval": 1,
                "backupCount": 7,
                "encoding": "utf-8"
            }
        },
        "root": {
            "level": log_level,
            "handlers": ["console", "file"]
        }
    }
    
    logging.config.dictConfig(logging_config)
    logging.info(f"Logging initialized. Log file: {log_path}")

