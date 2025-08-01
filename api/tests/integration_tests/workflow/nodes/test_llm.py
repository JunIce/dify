import json
import time
import uuid
from collections.abc import Generator
from unittest.mock import MagicMock, patch

from core.app.entities.app_invoke_entities import InvokeFrom
from core.llm_generator.output_parser.structured_output import _parse_structured_output
from core.workflow.entities.variable_pool import VariablePool
from core.workflow.entities.workflow_node_execution import WorkflowNodeExecutionStatus
from core.workflow.graph_engine.entities.graph import Graph
from core.workflow.graph_engine.entities.graph_init_params import GraphInitParams
from core.workflow.graph_engine.entities.graph_runtime_state import GraphRuntimeState
from core.workflow.nodes.event import RunCompletedEvent
from core.workflow.nodes.llm.node import LLMNode
from core.workflow.system_variable import SystemVariable
from extensions.ext_database import db
from models.enums import UserFrom
from models.workflow import WorkflowType

"""FOR MOCK FIXTURES, DO NOT REMOVE"""


def init_llm_node(config: dict) -> LLMNode:
    graph_config = {
        "edges": [
            {
                "id": "start-source-next-target",
                "source": "start",
                "target": "llm",
            },
        ],
        "nodes": [{"data": {"type": "start"}, "id": "start"}, config],
    }

    graph = Graph.init(graph_config=graph_config)

    # Use proper UUIDs for database compatibility
    tenant_id = "9d2074fc-6f86-45a9-b09d-6ecc63b9056b"
    app_id = "9d2074fc-6f86-45a9-b09d-6ecc63b9056c"
    workflow_id = "9d2074fc-6f86-45a9-b09d-6ecc63b9056d"
    user_id = "9d2074fc-6f86-45a9-b09d-6ecc63b9056e"

    init_params = GraphInitParams(
        tenant_id=tenant_id,
        app_id=app_id,
        workflow_type=WorkflowType.WORKFLOW,
        workflow_id=workflow_id,
        graph_config=graph_config,
        user_id=user_id,
        user_from=UserFrom.ACCOUNT,
        invoke_from=InvokeFrom.DEBUGGER,
        call_depth=0,
    )

    # construct variable pool
    variable_pool = VariablePool(
        system_variables=SystemVariable(
            user_id="aaa",
            app_id=app_id,
            workflow_id=workflow_id,
            files=[],
            query="what's the weather today?",
            conversation_id="abababa",
        ),
        user_inputs={},
        environment_variables=[],
        conversation_variables=[],
    )
    variable_pool.add(["abc", "output"], "sunny")

    node = LLMNode(
        id=str(uuid.uuid4()),
        graph_init_params=init_params,
        graph=graph,
        graph_runtime_state=GraphRuntimeState(variable_pool=variable_pool, start_at=time.perf_counter()),
        config=config,
    )

    # Initialize node data
    if "data" in config:
        node.init_node_data(config["data"])

    return node


def test_execute_llm():
    node = init_llm_node(
        config={
            "id": "llm",
            "data": {
                "title": "123",
                "type": "llm",
                "model": {
                    "provider": "openai",
                    "name": "gpt-3.5-turbo",
                    "mode": "chat",
                    "completion_params": {},
                },
                "prompt_template": [
                    {
                        "role": "system",
                        "text": "you are a helpful assistant.\ntoday's weather is {{#abc.output#}}.",
                    },
                    {"role": "user", "text": "{{#sys.query#}}"},
                ],
                "memory": None,
                "context": {"enabled": False},
                "vision": {"enabled": False},
            },
        },
    )

    db.session.close = MagicMock()

    # Mock the _fetch_model_config to avoid database calls
    def mock_fetch_model_config(**_kwargs):
        from decimal import Decimal
        from unittest.mock import MagicMock

        from core.model_runtime.entities.llm_entities import LLMResult, LLMUsage
        from core.model_runtime.entities.message_entities import AssistantPromptMessage

        # Create mock model instance
        mock_model_instance = MagicMock()
        mock_usage = LLMUsage(
            prompt_tokens=30,
            prompt_unit_price=Decimal("0.001"),
            prompt_price_unit=Decimal(1000),
            prompt_price=Decimal("0.00003"),
            completion_tokens=20,
            completion_unit_price=Decimal("0.002"),
            completion_price_unit=Decimal(1000),
            completion_price=Decimal("0.00004"),
            total_tokens=50,
            total_price=Decimal("0.00007"),
            currency="USD",
            latency=0.5,
        )
        mock_message = AssistantPromptMessage(content="Test response from mock")
        mock_llm_result = LLMResult(
            model="gpt-3.5-turbo",
            prompt_messages=[],
            message=mock_message,
            usage=mock_usage,
        )
        mock_model_instance.invoke_llm.return_value = mock_llm_result

        # Create mock model config
        mock_model_config = MagicMock()
        mock_model_config.mode = "chat"
        mock_model_config.provider = "openai"
        mock_model_config.model = "gpt-3.5-turbo"
        mock_model_config.parameters = {}

        return mock_model_instance, mock_model_config

    # Mock fetch_prompt_messages to avoid database calls
    def mock_fetch_prompt_messages_1(**_kwargs):
        from core.model_runtime.entities.message_entities import SystemPromptMessage, UserPromptMessage

        return [
            SystemPromptMessage(content="you are a helpful assistant. today's weather is sunny."),
            UserPromptMessage(content="what's the weather today?"),
        ], []

    with (
        patch.object(LLMNode, "_fetch_model_config", mock_fetch_model_config),
        patch.object(LLMNode, "fetch_prompt_messages", mock_fetch_prompt_messages_1),
    ):
        # execute node
        result = node._run()
        assert isinstance(result, Generator)

        for item in result:
            if isinstance(item, RunCompletedEvent):
                if item.run_result.status != WorkflowNodeExecutionStatus.SUCCEEDED:
                    print(f"Error: {item.run_result.error}")
                    print(f"Error type: {item.run_result.error_type}")
                assert item.run_result.status == WorkflowNodeExecutionStatus.SUCCEEDED
                assert item.run_result.process_data is not None
                assert item.run_result.outputs is not None
                assert item.run_result.outputs.get("text") is not None
                assert item.run_result.outputs.get("usage", {})["total_tokens"] > 0


def test_execute_llm_with_jinja2():
    """
    Test execute LLM node with jinja2
    """
    node = init_llm_node(
        config={
            "id": "llm",
            "data": {
                "title": "123",
                "type": "llm",
                "model": {"provider": "openai", "name": "gpt-3.5-turbo", "mode": "chat", "completion_params": {}},
                "prompt_config": {
                    "jinja2_variables": [
                        {"variable": "sys_query", "value_selector": ["sys", "query"]},
                        {"variable": "output", "value_selector": ["abc", "output"]},
                    ]
                },
                "prompt_template": [
                    {
                        "role": "system",
                        "text": "you are a helpful assistant.\ntoday's weather is {{#abc.output#}}",
                        "jinja2_text": "you are a helpful assistant.\ntoday's weather is {{output}}.",
                        "edition_type": "jinja2",
                    },
                    {
                        "role": "user",
                        "text": "{{#sys.query#}}",
                        "jinja2_text": "{{sys_query}}",
                        "edition_type": "basic",
                    },
                ],
                "memory": None,
                "context": {"enabled": False},
                "vision": {"enabled": False},
            },
        },
    )

    # Mock db.session.close()
    db.session.close = MagicMock()

    # Mock the _fetch_model_config method
    def mock_fetch_model_config(**_kwargs):
        from decimal import Decimal
        from unittest.mock import MagicMock

        from core.model_runtime.entities.llm_entities import LLMResult, LLMUsage
        from core.model_runtime.entities.message_entities import AssistantPromptMessage

        # Create mock model instance
        mock_model_instance = MagicMock()
        mock_usage = LLMUsage(
            prompt_tokens=30,
            prompt_unit_price=Decimal("0.001"),
            prompt_price_unit=Decimal(1000),
            prompt_price=Decimal("0.00003"),
            completion_tokens=20,
            completion_unit_price=Decimal("0.002"),
            completion_price_unit=Decimal(1000),
            completion_price=Decimal("0.00004"),
            total_tokens=50,
            total_price=Decimal("0.00007"),
            currency="USD",
            latency=0.5,
        )
        mock_message = AssistantPromptMessage(content="Test response: sunny weather and what's the weather today?")
        mock_llm_result = LLMResult(
            model="gpt-3.5-turbo",
            prompt_messages=[],
            message=mock_message,
            usage=mock_usage,
        )
        mock_model_instance.invoke_llm.return_value = mock_llm_result

        # Create mock model config
        mock_model_config = MagicMock()
        mock_model_config.mode = "chat"
        mock_model_config.provider = "openai"
        mock_model_config.model = "gpt-3.5-turbo"
        mock_model_config.parameters = {}

        return mock_model_instance, mock_model_config

    # Mock fetch_prompt_messages to avoid database calls
    def mock_fetch_prompt_messages_2(**_kwargs):
        from core.model_runtime.entities.message_entities import SystemPromptMessage, UserPromptMessage

        return [
            SystemPromptMessage(content="you are a helpful assistant. today's weather is sunny."),
            UserPromptMessage(content="what's the weather today?"),
        ], []

    with (
        patch.object(LLMNode, "_fetch_model_config", mock_fetch_model_config),
        patch.object(LLMNode, "fetch_prompt_messages", mock_fetch_prompt_messages_2),
    ):
        # execute node
        result = node._run()

        for item in result:
            if isinstance(item, RunCompletedEvent):
                assert item.run_result.status == WorkflowNodeExecutionStatus.SUCCEEDED
                assert item.run_result.process_data is not None
                assert "sunny" in json.dumps(item.run_result.process_data)
                assert "what's the weather today?" in json.dumps(item.run_result.process_data)


def test_extract_json():
    llm_texts = [
        '<think>\n\n</think>{"name": "test", "age": 123',  # resoning model (deepseek-r1)
        '{"name":"test","age":123}',  # json schema model (gpt-4o)
        '{\n    "name": "test",\n    "age": 123\n}',  # small model (llama-3.2-1b)
        '```json\n{"name": "test", "age": 123}\n```',  # json markdown (deepseek-chat)
        '{"name":"test",age:123}',  # without quotes (qwen-2.5-0.5b)
    ]
    result = {"name": "test", "age": 123}
    assert all(_parse_structured_output(item) == result for item in llm_texts)
