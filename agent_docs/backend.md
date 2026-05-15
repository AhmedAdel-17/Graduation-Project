# ⚙️ Backend & API Guidelines

## Overview
The backend provides the API layer, orchestration, and interface logic for the Trading Agents system. It handles user requests, serves data to the frontend dashboard, and triggers the multi-agent pipeline executions.

## Tech Stack & Libraries
- **FastAPI**: Core framework for the API endpoints.
- **Uvicorn**: High-performance ASGI server.
- **Pydantic**: Critical for request/response validation and configuration serialization.
- **LangGraph**: Used for stateful multi-agent orchestration and loop logic.

## Implementation Guidelines
1. **Endpoint Modularity**: Keep routers strictly isolated based on domain logic (e.g., `routers/backtest.py`, `routers/agents.py`, `routers/data.py`).
2. **Asynchronous Execution**: Always utilize `async def` and non-blocking I/O. For heavy, blocking agent computations (like deep technical indicator calculations), dispatch them to background workers or threads via `asyncio.to_thread`.
3. **Structured Errors**: Use standardized `HTTPException` formats. Catch deep pipeline errors and wrap them cleanly. Never expose raw Python errors or LLM keys to the client.
4. **Immutability of State**: The LangGraph state object should be treated carefully. Always ensure nodes pass completely isolated state updates to the coordinator without destructively modifying shared dictionaries unexpectedly.
5. **Logging**: Maintain verbose, asynchronous structured logging for every run, as backtraces of agent chains are notoriously difficult to debug.
