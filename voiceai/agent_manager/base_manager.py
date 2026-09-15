from voiceai.otobaai_logger import get_logger

logger = get_logger(__name__)


class BaseManager:
    def __init__(self):
        self.agent = "voiceai-agent"
