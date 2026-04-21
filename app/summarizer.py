from __future__ import annotations

import os
from collections import Counter
from typing import Any

import httpx


class Summarizer:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")

    async def summarize(self, title: str, content: str) -> str:
        text = content.strip()
        if not text:
            return "No content available to summarize."
        if self.api_key:
            try:
                summary = await self._summarize_with_llm(title, text)
                if summary:
                    return summary
            except Exception:
                pass
        return self._fallback_summary(text)

    async def _summarize_with_llm(self, title: str, content: str) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "You summarize blog posts in 3 to 4 concise bullet points.",
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Title: {title}\n\nContent:\n{content}",
                        }
                    ],
                },
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(f"{self.base_url}/responses", headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        return self._extract_output_text(data).strip()

    def _extract_output_text(self, payload: dict[str, Any]) -> str:
        if isinstance(payload.get("output_text"), str):
            return payload["output_text"]

        chunks: list[str] = []
        for item in payload.get("output", []):
            for content in item.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    chunks.append(content["text"])
        return "\n".join(chunks)

    def _fallback_summary(self, content: str) -> str:
        sentences = [part.strip() for part in content.replace("\n", " ").split(".") if part.strip()]
        if not sentences:
            return "No meaningful sentences found to summarize."

        stop_words = {
            "the", "a", "an", "and", "or", "to", "of", "in", "is", "it", "for", "on",
            "that", "this", "with", "as", "are", "be", "by", "from", "at", "was", "were",
        }
        words = [
            token.strip(" ,!?;:-").lower()
            for token in content.split()
            if token.strip(" ,!?;:-")
        ]
        counts = Counter(word for word in words if word not in stop_words and len(word) > 3)
        ranked = sorted(
            sentences,
            key=lambda sentence: sum(counts.get(token.strip(" ,!?;:-").lower(), 0) for token in sentence.split()),
            reverse=True,
        )[:3]
        return "\n".join(f"- {sentence.strip()}." for sentence in ranked)
