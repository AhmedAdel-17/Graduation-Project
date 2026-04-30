import logging
import time
from typing import List, Any, Optional, Dict, Union
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import Field, PrivateAttr

logger = logging.getLogger("tradingagents.llm_failover")

class ReliableChatModel(BaseChatModel):
    """
    A LangChain-compatible ChatModel that supports automatic failover between multiple providers.
    In case of a 429 (Rate Limit) or 413 (Payload Too Large), it automatically tries the next provider.
    """
    
    _providers: List[BaseChatModel] = PrivateAttr(default_factory=list)
    _current_idx: int = PrivateAttr(default=0)
    
    def __init__(self, config: Dict[str, Any], **kwargs):
        super().__init__(**kwargs)
        self._providers = self._initialize_providers(config)
        self._current_idx = 0

    def _initialize_providers(self, config: Dict[str, Any]) -> List[BaseChatModel]:
        providers = []
        priority = config.get("llm_failover_priority", ["google", "openrouter", "openai"])
        
        for p in priority:
            try:
                if p == "google":
                    providers.append(ChatGoogleGenerativeAI(
                        model=config["deep_think_llm"],
                        google_api_key=config.get("GOOGLE_API_KEY"),
                        temperature=0,
                        max_retries=0  # Disable internal retries for fast failover
                    ))
                elif p == "openrouter":
                    providers.append(ChatOpenAI(
                        model=config.get("openrouter_model", "openai/gpt-4o-mini"),
                        openai_api_key=config.get("OPENROUTER_API_KEY"),
                        base_url=config.get("openrouter_url", "https://openrouter.ai/api/v1"),
                        temperature=0,
                        max_retries=0  # Disable internal retries for fast failover
                    ))
                elif p == "openai": # Groq
                    providers.append(ChatOpenAI(
                        model=config["deep_think_llm"] if "llama" in config["deep_think_llm"] else "llama-3.3-70b-versatile",
                        openai_api_key=config.get("GROQ_API_KEY"),
                        base_url=config.get("backend_url"),
                        temperature=0,
                        max_retries=0  # Disable internal retries for fast failover
                    ))
            except Exception as e:
                logger.error(f"Failed to initialize provider {p}: {e}")
        
        if not providers:
             raise ValueError("ReliableChatModel: No valid LLM providers initialized!")
             
        return providers

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[Any] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Core generation logic with failover."""
        
        errors = []
        for _ in range(len(self._providers)):
            current_provider = self._providers[self._current_idx]
            provider_name = current_provider.__class__.__name__
            
            try:
                logger.info(f"Invoking LLM via {provider_name} (index {self._current_idx})...")
                return current_provider._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            
            except Exception as e:
                err_msg = str(e).lower()
                is_retryable = (
                    "rate_limit" in err_msg or 
                    "429" in err_msg or 
                    "payload too large" in err_msg or 
                    "413" in err_msg or
                    "resource_exhausted" in err_msg or
                    "quota exceeded" in err_msg
                )
                
                if is_retryable:
                    logger.warning(f"Provider {provider_name} failed with retryable error: {e}. Switching to next provider.")
                    errors.append(f"{provider_name}: {e}")
                    self._current_idx = (self._current_idx + 1) % len(self._providers)
                    time.sleep(2) # Give it a breath
                    continue
                else:
                    logger.error(f"Provider {provider_name} failed with non-retryable error: {e}")
                    raise e
                    
        raise RuntimeError(f"All LLM providers failed: {'; '.join(errors)}")

    def bind_tools(self, tools: List[Any], **kwargs: Any) -> Any:
        """Ensure tool binding is propagated to all underlying providers."""
        # Note: This is an approximation. LangChain's bind_tools usually returns a RunnableBinding.
        # For simplicity, we wrap the current provider's bind_tools and hope for the best.
        # A more robust implementation would wrap the resulting runnables.
        
        # Actually, the best way is to bind tools to each provider and return a new ReliableChatModel
        # that uses the bound versions.
        
        cloned = self.copy()
        cloned._providers = [p.bind_tools(tools, **kwargs) for p in self._providers]
        return cloned

    @property
    def _llm_type(self) -> str:
        return "reliable_failover_model"
