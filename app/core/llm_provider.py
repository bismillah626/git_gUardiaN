"""
LLM Provider adapter for CodeGuardian AI.

Provides a unified interface for calling Groq (primary) and Claude (fallback)
with built-in retry/backoff for rate-limit resilience.
"""

import logging
from typing import Optional, List

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

from app.core.config import settings

logger = logging.getLogger(__name__)


class LLMProvider:
    """Unified LLM interface with automatic retry and provider fallback.
    
    Primary: Groq (fast, free-tier)
    Fallback: Claude (trial credits) — only used if Groq is persistently rate-limited
    """

    def __init__(self):
        self._groq = None
        self._claude = None

    @property
    def groq(self) -> ChatGroq:
        if self._groq is None:
            if not settings.groq_api_key:
                raise ValueError("GROQ_API_KEY is required")
            self._groq = ChatGroq(
                api_key=settings.groq_api_key,
                model=settings.groq_model,
                temperature=0.1,  # Low temperature for deterministic analysis
                max_tokens=4096,
            )
        return self._groq

    @property
    def claude(self):
        """Lazy-init Claude as fallback. Returns None if no API key."""
        if self._claude is None and settings.claude_api_key:
            try:
                from langchain_community.chat_models import ChatAnthropic
                self._claude = ChatAnthropic(
                    api_key=settings.claude_api_key,
                    model=settings.claude_model,
                    temperature=0.1,
                    max_tokens=4096,
                )
            except ImportError:
                logger.warning("langchain anthropic adapter not installed; Claude fallback unavailable")
        return self._claude

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=1, max=60),
        retry=retry_if_exception_type((Exception,)),
        before_sleep=lambda retry_state: logger.warning(
            f"LLM call failed (attempt {retry_state.attempt_number}), retrying: {retry_state.outcome.exception()}"
        ),
    )
    async def invoke(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        use_fallback: bool = True,
    ) -> str:
        """Send a prompt to the LLM and return the response text.
        
        Tries Groq first. If rate-limited and use_fallback=True, tries Claude.
        """
        messages = []
        if system_prompt:
            messages.append(SystemMessage(content=system_prompt))
        messages.append(HumanMessage(content=prompt))

        try:
            response = await self.groq.ainvoke(messages)
            return response.content
        except Exception as e:
            error_str = str(e).lower()
            if ("rate" in error_str or "429" in error_str) and use_fallback and self.claude:
                logger.warning(f"Groq rate-limited, falling back to Claude: {e}")
                response = await self.claude.ainvoke(messages)
                return response.content
            raise  # Let tenacity retry

    async def invoke_batch(
        self,
        prompts: List[str],
        system_prompt: Optional[str] = None,
    ) -> List[str]:
        """Process multiple prompts sequentially (to respect rate limits)."""
        results = []
        for prompt in prompts:
            result = await self.invoke(prompt, system_prompt=system_prompt)
            results.append(result)
        return results


# Singleton
llm_provider = LLMProvider()
