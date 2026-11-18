import os
import httpx
from enum import Enum

class TaskTier(str, Enum):
    HYDROGEN = "hydrogen"  # High-volume mechanical tasks: RSS/Reddit triage, subtitle chunking, hashtag formatting, deduplication
    CARBON = "carbon"      # High-impact tasks: viral 1st-second hook crafting, fact/compliance reasoning, campaign strategy, learning loop analysis

class TieredModelRouter:
    def __init__(self):
        self.hydrogen_provider = os.getenv("HYDROGEN_PROVIDER", "gemini").lower()
        self.hydrogen_model = os.getenv("HYDROGEN_MODEL", "gemini-2.5-flash-lite")
        
        self.carbon_provider = os.getenv("CARBON_PROVIDER", "gemini").lower()
        self.carbon_model = os.getenv("CARBON_MODEL", "gemini-2.5-pro")

    async def complete(
        self,
        prompt: str,
        system: str = "",
        tier: TaskTier = TaskTier.HYDROGEN,
        temperature: float = 0.4
    ) -> str | None:
        provider = self.hydrogen_provider if tier == TaskTier.HYDROGEN else self.carbon_provider
        model = self.hydrogen_model if tier == TaskTier.HYDROGEN else self.carbon_model

        if provider == "gemini":
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return None
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            
            contents = []
            if system:
                contents.append({"role": "user", "parts": [{"text": f"System Instructions:\n{system}\n\nUser Request:\n{prompt}"}]})
            else:
                contents.append({"role": "user", "parts": [{"text": prompt}]})

            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": temperature
                }
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.post(url, json=payload)
                    if resp.status_code != 200:
                        return None
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        return None
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        return None
                    return parts[0].get("text")
                except Exception:
                    return None

        elif provider in ("openai", "groq"):
            if provider == "openai":
                api_key = os.getenv("OPENAI_API_KEY")
                url = "https://api.openai.com/v1/chat/completions"
            else:
                api_key = os.getenv("GROQ_API_KEY")
                url = "https://api.groq.com/openai/v1/chat/completions"

            if not api_key:
                return None

            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": model,
                "messages": messages,
                "temperature": temperature
            }
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            async with httpx.AsyncClient(timeout=30.0) as client:
                try:
                    resp = await client.post(url, json=payload, headers=headers)
                    if resp.status_code != 200:
                        return None
                    data = resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        return None
                    return choices[0].get("message", {}).get("content")
                except Exception:
                    return None

        return None
