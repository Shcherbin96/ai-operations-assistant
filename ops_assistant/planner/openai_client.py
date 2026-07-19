"""Live LLM client over any OpenAI-compatible endpoint (Gemini, OpenRouter, …).

I/O only — the planning logic is tested against a fake, so this stays a thin
wrapper. Provider is chosen by base_url; the model by name."""

from __future__ import annotations

from openai import OpenAI


class OpenAILLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._temperature = temperature

    def complete(self, *, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            temperature=self._temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        # Providers (OpenRouter, Gemini) can return an empty choices list on a 200
        # (e.g. a safety block). Degrade to "" so the planner fails closed.
        if not response.choices:
            return ""
        message = response.choices[0].message
        return (message.content or "") if message is not None else ""
