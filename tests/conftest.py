"""Shared fixtures for the test suite."""
import os
import pytest
from dataclasses import dataclass, field
from typing import Optional, List

# Set dummy API keys so imports don't fail at module load time
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")


@dataclass
class MockMessage:
    content: str
    tool_calls: Optional[List] = field(default=None)
    usage_metadata: Optional[dict] = field(default=None)


class MockLLM:
    """Drop-in replacement for LangChain ChatModels in tests.

    bind_tools() returns a RunnableLambda so it can be composed with
    ChatPromptTemplate via the `|` operator (used in tool-calling paths).
    """

    def __init__(self, response_text: str, tool_calls=None):
        self._response = response_text
        self._tool_calls = tool_calls or []

    def invoke(self, *args, **kwargs):
        return MockMessage(
            content=self._response,
            tool_calls=self._tool_calls,
            usage_metadata={"input_tokens": 100, "output_tokens": 50},
        )

    def bind_tools(self, tools, **kwargs):
        """Return a LangChain-compatible Runnable wrapping this mock's response."""
        from langchain_core.runnables import RunnableLambda

        response = MockMessage(
            content=self._response,
            tool_calls=self._tool_calls,
            usage_metadata={"input_tokens": 100, "output_tokens": 50},
        )
        return RunnableLambda(lambda x: response)

    @property
    def model_name(self):
        return "mock-llm"

    @property
    def temperature(self):
        return 0.0
