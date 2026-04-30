import asyncio
import ast
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from app.config.settings import get_settings
from app.service.deep_research.state import ResearchState, to_serializable


logger = logging.getLogger(__name__)

CODE_LIKE_FIELDS = {"code", "fixed_code", "revised_content"}


class BaseAgent(ABC):
    """Base class for all Deep Research agents."""

    def __init__(
        self,
        name: str,
        role: str,
        llm_api_key: str | None = None,
        llm_base_url: str | None = None,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.name = name
        self.role = role
        self.logger = logging.getLogger(f"Agent.{name}")

        if client is not None:
            self.client = client
            self.model = model or get_settings().llm_model
            return

        settings = get_settings()
        api_key = llm_api_key
        if api_key is None and settings.openai_api_key is not None:
            api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        self.model = model or settings.llm_model
        self.client = OpenAI(
            api_key=api_key,
            base_url=llm_base_url or settings.openai_base_url,
        )

    @abstractmethod
    async def process(self, state: ResearchState) -> ResearchState:
        """Run this agent against the shared Deep Research state."""

    async def call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = True,
        temperature: float = 0.3,
        max_tokens: int = 16000,
    ) -> str:
        """Call the configured LLM without blocking the event loop."""

        started_at = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                **kwargs,
            )
        except Exception:
            self.logger.exception("LLM call failed.")
            raise

        choices = getattr(response, "choices", None)
        if not choices:
            raise RuntimeError("LLM completion returned no choices.")
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", None)
        if not content:
            raise RuntimeError("LLM completion returned empty content.")

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self.logger.info(
            "LLM call completed in %sms, response length: %s",
            duration_ms,
            len(content),
        )
        return str(content)

    def parse_json_response(self, response: str) -> dict[str, Any]:
        """Parse LLM JSON output defensively, including common malformed shapes."""

        candidates = [response]
        candidates.extend(self._extract_markdown_json_blocks(response))

        brace_candidate = self._extract_outer_json_object(response)
        if brace_candidate:
            candidates.append(brace_candidate)

        for candidate in candidates:
            parsed = self._try_parse_json_object(candidate)
            if parsed is not None:
                return parsed

        literal_candidate = brace_candidate or response
        parsed_literal = self._try_parse_python_literal(literal_candidate)
        if parsed_literal is not None:
            return parsed_literal

        self.logger.error("Could not parse JSON response.")
        self.logger.warning("Raw response preview: %s", response[:800])
        return {}

    def _try_parse_json_object(self, payload: str) -> dict[str, Any] | None:
        payload = self._strip_bom(payload.strip())
        if not payload:
            return None

        for candidate in (payload, self._repair_json_payload(payload)):
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return self._fix_escaped_values(parsed)
        return None

    def _repair_json_payload(self, payload: str) -> str:
        repaired = self._strip_bom(payload.strip())
        repaired = re.sub(r"(?<!\\)\\(?![\"\\/bfnrtu])", "", repaired)
        repaired = re.sub(r"//.*?$", "", repaired, flags=re.MULTILINE)
        repaired = re.sub(r"/\*.*?\*/", "", repaired, flags=re.DOTALL)
        repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
        repaired = re.sub(r"([}\]])(\s*)([{\[])", r"\1,\2\3", repaired)
        repaired = re.sub(r"([,{])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', repaired)
        return repaired

    def _try_parse_python_literal(self, payload: str) -> dict[str, Any] | None:
        normalized = payload.strip()
        if not normalized:
            return None
        normalized = re.sub(r"\btrue\b", "True", normalized)
        normalized = re.sub(r"\bfalse\b", "False", normalized)
        normalized = re.sub(r"\bnull\b", "None", normalized)
        try:
            parsed = ast.literal_eval(normalized)
        except (SyntaxError, ValueError):
            return None
        if isinstance(parsed, dict):
            return self._fix_escaped_values(parsed)
        return None

    def _extract_markdown_json_blocks(self, response: str) -> list[str]:
        return re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", response)

    def _extract_outer_json_object(self, response: str) -> str | None:
        start = response.find("{")
        end = response.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return response[start : end + 1]

    def _strip_bom(self, payload: str) -> str:
        if payload.startswith("\ufeff"):
            return payload[1:]
        return payload

    def _fix_escaped_values(self, value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {
                dict_key: self._fix_escaped_values(dict_value, key=str(dict_key))
                for dict_key, dict_value in value.items()
            }
        if isinstance(value, list):
            return [self._fix_escaped_values(item, key=key) for item in value]
        if isinstance(value, str):
            if key in CODE_LIKE_FIELDS:
                return value
            fixed = value
            fixed = fixed.replace("\\\\n", "\n")
            fixed = fixed.replace("\\n", "\n")
            fixed = fixed.replace("\\\\r", "\r")
            fixed = fixed.replace("\\r", "\r")
            fixed = fixed.replace("\\\\t", "\t")
            fixed = fixed.replace("\\t", "\t")
            return fixed
        return value

    def add_message(
        self,
        state: ResearchState,
        event_type: str,
        content: Any,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a Deep Research agent event and optionally push it to the SSE queue."""

        event = {
            "type": event_type,
            "agent": self.name,
            "phase": state.get("phase"),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": to_serializable(content),
            "metadata": to_serializable(metadata or {}),
        }
        state.setdefault("agent_events", []).append(event)

        message_queue = state.get("_message_queue")  # type: ignore[typeddict-item]
        if message_queue is not None:
            try:
                message_queue.put_nowait(event)
                queue_size = (
                    message_queue.qsize()
                    if hasattr(message_queue, "qsize")
                    else "unknown"
                )
                self.logger.info(
                    "Queued Deep Research event: %s queue_size=%s",
                    event_type,
                    queue_size,
                )
            except Exception:
                self.logger.warning(
                    "Failed to queue Deep Research event: %s",
                    event_type,
                    exc_info=True,
                )
        return event

    def add_log(
        self,
        state: ResearchState,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        duration_ms: int = 0,
        tokens_used: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append one agent execution log to shared Deep Research state."""

        log = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": self.name,
            "action": action,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "duration_ms": duration_ms,
            "tokens_used": tokens_used,
            "metadata": to_serializable(metadata or {}),
        }
        state.setdefault("logs", []).append(log)
        return log


class AgentRegistry:
    """In-memory registry for Deep Research agent instances."""

    _agents: dict[str, BaseAgent] = {}

    @classmethod
    def register(cls, agent: BaseAgent) -> None:
        cls._agents[agent.name] = agent

    @classmethod
    def get(cls, name: str) -> BaseAgent | None:
        return cls._agents.get(name)

    @classmethod
    def all(cls) -> dict[str, BaseAgent]:
        return cls._agents.copy()

    @classmethod
    def clear(cls) -> None:
        cls._agents.clear()
