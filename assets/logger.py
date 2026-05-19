import logging
import os
from logging.handlers import RotatingFileHandler

try:
    import colorlog
except ImportError:  # pragma: no cover - fallback for minimal environments
    colorlog = None


LOGS_DIR = os.getenv("BOT_LOGS_DIR", "logs")
LOG_FILE = os.path.join(LOGS_DIR, "bot.log")
FILE_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    os.makedirs(LOGS_DIR, exist_ok=True)

    root_logger = logging.getLogger()
    if root_logger.handlers:
        return logging.getLogger("rare_items_bot")

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))

    console_handler = logging.StreamHandler()
    if colorlog:
        console_handler.setFormatter(
            colorlog.ColoredFormatter(
                "%(log_color)s[%(asctime)s] [%(levelname)s] [%(name)s]%(reset)s %(message)s",
                datefmt=DATE_FORMAT,
                log_colors={
                    "DEBUG": "cyan",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        )
    else:
        console_handler.setFormatter(logging.Formatter(FILE_FORMAT, DATE_FORMAT))

    logging.basicConfig(level=level, handlers=[console_handler, file_handler])
    return logging.getLogger("rare_items_bot")


logger = setup_logging()
