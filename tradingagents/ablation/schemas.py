"""
Ablation experiment logging schema.

Every (experiment_id, ticker, trade_date) triple produces one AblationRecord.
Records are appended to a JSONL file for post-hoc analysis.
"""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional
import json
import time


@dataclass
class LLMCallLog:
    """One LLM API call."""
    agent_name: str               # e.g. "market_analyst", "bull_researcher"
    model: str                    # e.g. "deepseek-chat"
    input_tokens: int             # prompt tokens
    output_tokens: int            # completion tokens
    latency_ms: float             # wall-clock time for this call
    is_tool_call: bool            # True if response contained tool_calls
    attempt: int = 1              # retry attempt number


@dataclass
class AgentLog:
    """Aggregated metrics for one agent within a single run."""
    agent_name: str
    variant: str                  # "llm", "deterministic", "hybrid", "skip"
    wall_time_ms: float           # total wall time for this agent
    llm_calls: int                # number of LLM calls made
    total_input_tokens: int
    total_output_tokens: int
    output_decision: Optional[str]       # the signal/direction this agent produced
    output_confidence: Optional[float]   # confidence score if applicable
    structured_output_parsed: bool       # did JSON extraction succeed?
    error: Optional[str] = None          # any exception message


@dataclass
class AblationRecord:
    """One complete pipeline run for a (experiment, ticker, date) triple."""
    # Identity
    experiment_id: str            # e.g. "BASELINE", "EXP-A1"
    ticker: str
    trade_date: str
    run_timestamp: str            # ISO 8601

    # Pipeline output
    final_decision: str           # BUY / HOLD / SELL
    overall_confidence: float
    risk_vetoed: bool

    # Timing
    total_wall_time_ms: float
    agent_logs: List[AgentLog] = field(default_factory=list)

    # Accuracy (filled post-hoc by evaluator)
    forward_return_1d: Optional[float] = None   # next-day return
    forward_return_5d: Optional[float] = None   # 5-day return
    forward_return_10d: Optional[float] = None  # 10-day return
    decision_correct: Optional[bool] = None     # BUY and return>0, SELL and return<0, etc.

    # Cost
    total_llm_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    estimated_api_cost_usd: float = 0.0

    # LLM call detail (optional, for deep debugging)
    llm_call_details: List[LLMCallLog] = field(default_factory=list)

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_jsonl(cls, line: str) -> "AblationRecord":
        return cls(**json.loads(line))


# ---------------------------------------------------------------------------
# Instrumented LLM wrapper
# ---------------------------------------------------------------------------

class InstrumentedLLM:
    """
    Wraps a LangChain ChatModel to capture per-call token counts and latency.
    Drop-in replacement: same .invoke() / .bind_tools() interface.
    """

    def __init__(self, llm, agent_name: str, call_log: List[LLMCallLog]):
        self._llm = llm
        self._agent_name = agent_name
        self._call_log = call_log

    def invoke(self, *args, **kwargs):
        t0 = time.perf_counter()
        result = self._llm.invoke(*args, **kwargs)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        # Extract token usage from response metadata if available
        usage = getattr(result, "usage_metadata", None) or {}
        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
        else:
            input_tokens = getattr(usage, "input_tokens", 0)
            output_tokens = getattr(usage, "output_tokens", 0)

        is_tool = bool(getattr(result, "tool_calls", None))

        self._call_log.append(LLMCallLog(
            agent_name=self._agent_name,
            model=getattr(self._llm, "model_name", str(self._llm)),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=elapsed_ms,
            is_tool_call=is_tool,
        ))
        return result

    def bind_tools(self, tools, **kwargs):
        """Return a RunnableLambda that is both composable (prompt | llm.bind_tools(...))
        and still appends to call_log on every invocation."""
        from langchain_core.runnables import RunnableLambda

        bound = self._llm.bind_tools(tools, **kwargs)
        call_log = self._call_log
        agent_name = self._agent_name
        model_name = getattr(self._llm, "model_name", str(self._llm))

        def _invoke_and_log(input_val):
            t0 = time.perf_counter()
            result = bound.invoke(input_val)
            elapsed_ms = (time.perf_counter() - t0) * 1000

            usage = getattr(result, "usage_metadata", None) or {}
            if isinstance(usage, dict):
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
            else:
                input_tokens = getattr(usage, "input_tokens", 0)
                output_tokens = getattr(usage, "output_tokens", 0)

            is_tool = bool(getattr(result, "tool_calls", None))
            call_log.append(LLMCallLog(
                agent_name=agent_name,
                model=model_name,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=elapsed_ms,
                is_tool_call=is_tool,
            ))
            return result

        return RunnableLambda(_invoke_and_log)

    def __getattr__(self, name):
        return getattr(self._llm, name)
