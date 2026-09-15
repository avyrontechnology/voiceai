from voiceai.otobaai_logger import get_logger

logger = get_logger(__name__)


class BaseAgent:
    def __init__(self):
        self.agent_name = "base-agent"
