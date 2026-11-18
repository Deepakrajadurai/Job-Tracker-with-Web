"""Free-tier AI client (Google Gemini by default, Groq as an alternative).

Every HTTP request counts against ai.max_calls_per_run, results are cached on disk,
and listing pages are sent in batches, so a daily run stays far below free-tier limits.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class AIUnavailable(RuntimeError):
    """No key, disabled, or the per-run call budget is used up."""


def parse_json_text(text: str):
    """Parse model output that should be JSON but may be wrapped in prose or code fences."""
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        return json.loads(text)
    except ValueError:
        pass
    starts = [i for i in (text.find("["), text.find("{")) if i != -1]
    if not starts:
        raise ValueError("no JSON found in model output")
    start = min(starts)
    end = max(text.rfind("]"), text.rfind("}"))
    return json.loads(text[start:end + 1])


class AIClient:
    def __init__(self, ai_cfg: dict, cache_path: str, session=None, sleep=time.sleep):
        self.cfg = ai_cfg
        self.cache_path = Path(cache_path)
        self.session = session or requests.Session()
        self.sleep = sleep
        self.calls_made = 0
        self.api_key = os.environ.get(ai_cfg.get("api_key_env", ""), "")
        self._cache = self._load_cache()

    # ----------------------------------------------------------------- state
    @property
    def available(self) -> bool:
        return bool(self.cfg.get("enabled")) and bool(self.api_key)

    @property
    def calls_left(self) -> int:
        return max(0, int(self.cfg.get("max_calls_per_run", 0)) - self.calls_made)

    def _load_cache(self) -> dict:
        try:
            return json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cache(self) -> None:
        ttl = float(self.cfg.get("cache_ttl_days", 7)) * 86400
        now = time.time()
        self._cache = {k: v for k, v in self._cache.items() if now - v.get("ts", 0) < ttl}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")

    def _cached(self, key: str):
        entry = self._cache.get(key)
        ttl = float(self.cfg.get("cache_ttl_days", 7)) * 86400
        if entry and time.time() - entry.get("ts", 0) < ttl:
            return entry["value"]
        return None

    # ------------------------------------------------------------------ HTTP
    def _post(self, prompt: str, grounded: bool = False) -> str:
        provider = self.cfg.get("provider", "gemini")
        timeout = self.cfg.get("timeout", 60)
        if provider == "gemini":
            body: dict = {"contents": [{"parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": 0}}
            if grounded:
                body["tools"] = [{"google_search": {}}]
            else:
                body["generationConfig"]["responseMimeType"] = "application/json"
            response = self.session.post(
                GEMINI_URL.format(model=self.cfg["model"]), json=body, timeout=timeout,
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
            response.raise_for_status()
            parts = response.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        body = {"model": self.cfg["model"], "temperature": 0,
                "messages": [{"role": "user", "content": prompt}]}
        response = self.session.post(
            GROQ_URL, json=body, timeout=timeout,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    def complete_json(self, prompt: str, grounded: bool = False):
        """Return parsed JSON for the prompt, from cache when possible."""
        if not self.available:
            raise AIUnavailable("AI is disabled or the API key environment variable is empty")
        key = hashlib.sha256(
            f"{self.cfg.get('provider')}|{self.cfg.get('model')}|{grounded}|{prompt}".encode()).hexdigest()
        cached = self._cached(key)
        if cached is not None:
            logger.info("AI cache hit", extra={"source": "ai"})
            return cached
        last_error: Exception | None = None
        for attempt in range(3):
            if self.calls_left <= 0:
                raise AIUnavailable("AI call cap for this run reached (ai.max_calls_per_run)")
            self.calls_made += 1
            try:
                value = parse_json_text(self._post(prompt, grounded=grounded))
            except requests.HTTPError as exc:
                last_error = exc
                status = exc.response.status_code if exc.response is not None else 0
                if status == 429 or status >= 500:
                    self.sleep(min(60, 5 * 2 ** attempt))
                    continue
                raise
            except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
                last_error = exc
                self.sleep(2 * (attempt + 1))
                continue
            self._cache[key] = {"ts": time.time(), "value": value}
            self._save_cache()
            return value
        raise RuntimeError(f"AI request failed after retries: {last_error}")

    # ----------------------------------------------------------------- tasks
    def discover_companies(self, search_cfg: dict, field: str = "", exclude=None,
                           limit: int | None = None) -> list[dict]:
        """One call: companies hiring for the configured roles and area, with career pages."""
        limit = min(int(limit or self.cfg.get("max_companies", 60)), int(self.cfg.get("max_companies", 60)))
        exclude = sorted({str(n) for n in exclude or []})
        field = str(field or "").strip()
        kind = f"employers in the field of {field}" if field else "employers"
        prompt = (
            "You help with a job search. List up to "
            f"{limit} {kind} that are currently hiring for roles like "
            f"{json.dumps(search_cfg['queries'], ensure_ascii=False)} in or near "
            f"{json.dumps(search_cfg['locations'], ensure_ascii=False)}. "
            "Mix large companies, small and medium sized companies and young startups. "
            + (f"Do NOT include any of these, they are already known: {json.dumps(exclude, ensure_ascii=False)}. "
               if exclude else "")
            + "For each company give the URL of its official careers page that lists open jobs. "
            "Prefer the direct job board URL (for example boards.greenhouse.io/..., jobs.lever.co/..., "
            "jobs.ashbyhq.com/..., *.jobs.personio.de, jobs.smartrecruiters.com/...) when you know it. "
            "Only include URLs you are confident exist. "
            'Answer with a JSON array only: [{"name": "...", "careers_url": "https://..."}]'
        )
        data = self.complete_json(prompt, grounded=bool(self.cfg.get("use_search_grounding")))
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        companies, seen, known = [], set(), {n.lower() for n in exclude}
        for entry in data if isinstance(data, list) else []:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            url = str(entry.get("careers_url") or entry.get("url") or "").strip()
            parts = urlsplit(url)
            if not name or parts.scheme not in ("http", "https") or not parts.netloc:
                continue
            key = (parts.netloc.lower(), parts.path.rstrip("/").lower())
            if key in seen:
                continue
            seen.add(key)
            if name.lower() in known:
                continue
            companies.append({"name": name, "url": url})
        return companies[:limit]

    def suggest_profile(self, description: str) -> dict:
        """One call: turn a plain description of the wanted jobs into search terms and keywords."""
        prompt = (
            "A job seeker in Germany or the EU describes the jobs they want as: "
            f"{json.dumps(description, ensure_ascii=False)}. "
            "Return JSON with two lists. \"search_terms\": 3 to 4 short phrases to type into a job "
            "site search box, mixing German and English where both are common. \"keywords\": 10 to 18 "
            "words or word stems that typically appear in matching job titles, German and English, "
            "specific to this field (no generic words like Manager, Engineer, Job, m/w/d). "
            'Answer with JSON only: {"search_terms": ["..."], "keywords": ["..."]}'
        )
        data = self.complete_json(prompt)

        def clean(values, limit):
            out = []
            for value in values if isinstance(values, list) else []:
                value = " ".join(str(value).split())
                if 2 <= len(value) <= 40 and value.lower() not in (v.lower() for v in out):
                    out.append(value)
            return out[:limit]

        data = data if isinstance(data, dict) else {}
        keywords = clean(data.get("keywords"), 20)
        if not keywords:
            raise RuntimeError("the AI model returned no usable keywords")
        return {"keywords": keywords, "search_terms": clean(data.get("search_terms"), 4) or keywords[:4]}

    def extract_jobs(self, pages: list[dict], search_cfg: dict) -> list[dict]:
        """Batched: pick the job postings out of the link lists of several pages.

        pages: [{"url", "source", "anchors": [{"text", "href"}]}]. Returned links are
        checked against the links that were really on the page, so the model cannot invent any.
        """
        results: list[dict] = []
        size = max(1, int(self.cfg.get("pages_per_batch", 5)))
        for i in range(0, len(pages), size):
            batch = pages[i:i + size]
            payload = [{"page": n, "page_url": p["url"],
                        "links": [[a["text"][:120], a["href"]] for a in p["anchors"][:150]]}
                       for n, p in enumerate(batch)]
            prompt = (
                "Below are the links found on job listing pages. Pick the links that are individual "
                "job postings relevant to these searches: "
                f"{json.dumps(search_cfg['queries'], ensure_ascii=False)}. Ignore navigation, "
                "categories, legal pages and unrelated roles. If the link text contains a "
                "publication date, return it as YYYY-MM-DD, otherwise use an empty string. "
                'Answer with a JSON array only: [{"page": 0, "url": "...", "title": "...", "date": ""}]\n\n'
                + json.dumps(payload, ensure_ascii=False)
            )
            data = self.complete_json(prompt)
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [])
            for entry in data if isinstance(data, list) else []:
                try:
                    page = batch[int(entry.get("page", -1))]
                except (AttributeError, IndexError, TypeError, ValueError):
                    continue
                allowed = {a["href"] for a in page["anchors"]}
                url, title = entry.get("url"), entry.get("title")
                if url in allowed and isinstance(title, str) and title.strip():
                    results.append({"link": url, "position": title, "date": entry.get("date") or "",
                                    "source": page.get("source", "career_pages"),
                                    "company": page.get("company", ""), "apply_filter": False})
        return results
