import logging
from logging.config import dictConfig


def configure_logging(log_level: str) -> None:
    level = (log_level or "INFO").upper()
    fmt = "%(asctime)s | %(levelname).1s | %(name)s | %(message)s"
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"default": {"format": fmt}},
            "handlers": {
                "default": {"class": "logging.StreamHandler", "formatter": "default"},
            },
            "root": {"handlers": ["default"], "level": level},
            "loggers": {
                "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
                "uvicorn.error": {"handlers": ["default"], "level": level, "propagate": False},
                "uvicorn.access": {"handlers": ["default"], "level": level, "propagate": False},
                # Third-party SDKs are chatty at DEBUG; keep them at INFO+.
                "httpx": {"level": "WARNING"},
                "httpcore": {"level": "WARNING"},
                "botocore": {"level": "WARNING"},
                "openai": {"level": "WARNING"},
                "anthropic": {"level": "WARNING"},
            },
        }
    )
    logging.getLogger(__name__).debug("logging configured level=%s", level)
