from __future__ import annotations
import anthropic
from config import settings


class ClaudeClient:
    """Shared Claude client with prompt caching enabled."""

    def __init__(self):
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.claude_model

    def create(self, system: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 4096) -> anthropic.types.Message:
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return self.client.messages.create(**kwargs)

    def stream(self, system: str, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 8192):
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "thinking": {"type": "adaptive"},
            "system": [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return self.client.messages.stream(**kwargs)


claude = ClaudeClient()
