__version__ = "0.11.0"

from voiceai.core.environment import set_env
from voiceai.otobaai_logger import get_logger

logger = get_logger(__name__)


def setenv(variables):
    """
    Set environment variables
    """
    for key, value in variables.items():
        logger.info(f"Setting environment variable: {key}")
        set_env(key, value)
