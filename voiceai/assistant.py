from voiceai.enums import TelephonyProvider
from voiceai.models import Task, ToolsChainModel, ToolsConfig
from voiceai.agent_manager import AssistantManager


class Assistant:
    def __init__(self, name="trial_agent"):
        self.name = name
        self.tasks = []

    def _as_dict(self, value):
        dump = getattr(value, "model_dump", None)
        if callable(dump):
            return dump()
        return value

    def add_task(
        self,
        task_type,
        llm_agent,
        input_queue=None,
        output_queue=None,
        transcriber=None,
        synthesizer=None,
        enable_textual_input=False,
    ):
        tools_config_args: dict = {}
        tools_config_args["llm_agent"] = self._as_dict(llm_agent)
        # Real, registry-known IO providers (TelephonyProvider.DEFAULT == "default").
        tools_config_args["input"] = {"format": "wav", "provider": TelephonyProvider.DEFAULT.value}
        tools_config_args["output"] = {"format": "wav", "provider": TelephonyProvider.DEFAULT.value}
        # Gate the pipeline on configured tools: a pipeline may only reference tools that exist
        # in tools_config, otherwise agent_config validation (and the engine) rejects the task.
        pipelines: list = []
        if transcriber is not None:
            tools_config_args["transcriber"] = self._as_dict(transcriber)
            pipeline = ["transcriber", "llm"]
            if synthesizer is not None:
                tools_config_args["synthesizer"] = self._as_dict(synthesizer)
                pipeline.append("synthesizer")
            pipelines.append(pipeline)
        else:
            if synthesizer is not None:
                tools_config_args["synthesizer"] = self._as_dict(synthesizer)
                pipelines.append(["llm", "synthesizer"])
            else:
                pipelines.append(["llm"])

        if enable_textual_input and ["llm"] not in pipelines:
            pipelines.append(["llm"])

        toolchain = ToolsChainModel(execution="parallel", pipelines=pipelines)
        task = Task(tools_config=ToolsConfig(**tools_config_args), toolchain=toolchain, task_type=task_type)
        self.tasks.append(task.model_dump(mode="json"))

    async def execute(self):
        agent_config = {"agent_name": self.name, "tasks": self.tasks}
        self.manager = AssistantManager(agent_config, ws=None, input_queue=None, output_queue=None)
        async for index, task_output in self.manager.run():
            yield task_output
