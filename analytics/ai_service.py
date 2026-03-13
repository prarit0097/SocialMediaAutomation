import json
import re
from typing import Any

import requests
from django.conf import settings


class AIInsightsError(Exception):
    pass


def _json_from_text(value: str) -> dict[str, Any]:
    raw = (value or "").strip()
    if not raw:
        raise AIInsightsError("OpenAI returned an empty response.")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```json\s*(\{.*?\})\s*```", raw, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    object_match = re.search(r"(\{.*\})", raw, flags=re.DOTALL)
    if object_match:
        try:
            parsed = json.loads(object_match.group(1))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise AIInsightsError("OpenAI response was not valid JSON.")


def _normalize_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            rows.append(text)
    return rows


def _normalize_plan_rows(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    for row in value:
        if isinstance(row, dict):
            action = str(row.get("action") or "").strip()
            why = str(row.get("why") or "").strip()
            impact = str(row.get("expected_impact") or "").strip()
            timeline = str(row.get("timeline") or "").strip()
        else:
            action = str(row or "").strip()
            why = ""
            impact = ""
            timeline = ""
        if not action:
            continue
        normalized.append(
            {
                "action": action,
                "why": why,
                "expected_impact": impact,
                "timeline": timeline,
            }
        )
    return normalized


def _normalize_kpi_rows(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    for row in value:
        if isinstance(row, dict):
            metric = str(row.get("metric") or "").strip()
            current = str(row.get("current") or "").strip()
            target = str(row.get("target_7d") or "").strip()
            how = str(row.get("how") or "").strip()
        else:
            metric = str(row or "").strip()
            current = ""
            target = ""
            how = ""
        if not metric:
            continue
        rows.append(
            {
                "metric": metric,
                "current": current,
                "target_7d": target,
                "how": how,
            }
        )
    return rows


def generate_profile_ai_insights(payload: dict[str, Any], focus: str | None = None) -> dict[str, Any]:
    api_key = (settings.OPENAI_API_KEY or "").strip()
    if not api_key:
        raise AIInsightsError("OPENAI_API_KEY is missing. Add it in .env and restart Django.")

    model = (settings.OPENAI_MODEL or "").strip() or "gpt-4o-mini"
    timeout = int(settings.OPENAI_TIMEOUT_SECONDS or 45)
    focus_text = str(focus or "").strip()

    system_prompt = (
        "You are a senior social media growth analyst for Facebook and Instagram business profiles. "
        "Use only provided data. Do not invent metrics. "
        "Return strict JSON only."
    )
    user_prompt = (
        "Analyze this profile and return JSON with keys:\n"
        "executive_summary (string),\n"
        "pros (array of strings),\n"
        "cons (array of strings),\n"
        "risks (array of strings),\n"
        "opportunities (array of strings),\n"
        "posting_strategy (object with keys current_posting, recommended_posting, reasoning),\n"
        "action_plan_7d (array of objects: action, why, expected_impact, timeline),\n"
        "kpi_growth_plan (array of objects: metric, current, target_7d, how),\n"
        "content_ideas (array of strings).\n"
        "If a number is unavailable, state 'not available'.\n"
        f"Focus preference from user: {focus_text or 'general profile growth'}.\n"
        f"Profile data JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "temperature": 0.25,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise AIInsightsError(f"OpenAI request failed: {exc}") from exc

    if response.status_code >= 400:
        try:
            err_payload = response.json()
        except ValueError:
            err_payload = {"error": {"message": response.text}}
        message = str((err_payload.get("error") or {}).get("message") or "OpenAI API returned an error.")
        raise AIInsightsError(message)

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001
        raise AIInsightsError("OpenAI response format was unexpected.") from exc

    parsed = _json_from_text(content)
    normalized = {
        "executive_summary": str(parsed.get("executive_summary") or "").strip(),
        "pros": _normalize_list(parsed.get("pros")),
        "cons": _normalize_list(parsed.get("cons")),
        "risks": _normalize_list(parsed.get("risks")),
        "opportunities": _normalize_list(parsed.get("opportunities")),
        "posting_strategy": {
            "current_posting": str((parsed.get("posting_strategy") or {}).get("current_posting") or "").strip(),
            "recommended_posting": str((parsed.get("posting_strategy") or {}).get("recommended_posting") or "").strip(),
            "reasoning": str((parsed.get("posting_strategy") or {}).get("reasoning") or "").strip(),
        },
        "action_plan_7d": _normalize_plan_rows(parsed.get("action_plan_7d")),
        "kpi_growth_plan": _normalize_kpi_rows(parsed.get("kpi_growth_plan")),
        "content_ideas": _normalize_list(parsed.get("content_ideas")),
    }
    return normalized
