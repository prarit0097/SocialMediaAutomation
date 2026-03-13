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
        "You are an elite Facebook + Instagram growth strategist, performance analyst, and content systems advisor. "
        "Your job is to produce a high-signal, execution-ready diagnosis for one profile using ONLY the provided JSON data.\n"
        "\n"
        "Non-negotiable rules:\n"
        "1) Never invent numbers, events, or trends.\n"
        "2) If a metric is missing, uncertain, or not in input, explicitly write 'not available'.\n"
        "3) Prioritize practical recommendations that can be executed in the next 7 days.\n"
        "4) Recommendations must connect to observed data patterns (cadence, engagement, post-level outcomes, platform mix).\n"
        "5) Keep language concise, specific, and business-usable; avoid generic fluff.\n"
        "6) Return strict JSON only, no markdown, no code fences, no explanations outside JSON.\n"
        "\n"
        "Quality bar:\n"
        "- Give concrete, operator-level guidance for improving views, reach, likes, comments, shares, saves, interactions, and reel/video plays.\n"
        "- Mention both strengths and weaknesses clearly.\n"
        "- The 7-day action plan should be prioritized and realistic.\n"
    )
    user_prompt = (
        "Create a profile growth report from the provided profile data.\n"
        "\n"
        "Required output schema (JSON object):\n"
        "{\n"
        '  "executive_summary": string,\n'
        '  "pros": string[],\n'
        '  "cons": string[],\n'
        '  "risks": string[],\n'
        '  "opportunities": string[],\n'
        '  "posting_strategy": {\n'
        '    "current_posting": string,\n'
        '    "recommended_posting": string,\n'
        '    "reasoning": string\n'
        "  },\n"
        '  "action_plan_7d": [\n'
        '    {"action": string, "why": string, "expected_impact": string, "timeline": string}\n'
        "  ],\n"
        '  "kpi_growth_plan": [\n'
        '    {"metric": string, "current": string, "target_7d": string, "how": string}\n'
        "  ],\n"
        '  "content_ideas": string[]\n'
        "}\n"
        "\n"
        "Output requirements:\n"
        "- executive_summary: 4-7 lines, include strongest issue and top growth lever.\n"
        "- pros/cons/risks/opportunities: each ideally 4-8 clear bullets.\n"
        "- posting_strategy:\n"
        "  - current_posting: infer cadence from input posting stats; if unavailable, write 'not available'.\n"
        "  - recommended_posting: explicit per-day or per-week plan for FB and IG.\n"
        "  - reasoning: explain why this cadence should improve distribution/engagement.\n"
        "- action_plan_7d: 5-10 prioritized actions; each action should be concrete and measurable.\n"
        "- kpi_growth_plan: include at least these metrics when available: views, reach, likes, comments, shares, saves, interactions, post cadence.\n"
        "  For each metric: give current (or 'not available'), realistic 7-day target, and method.\n"
        "- content_ideas: 8-15 practical ideas aligned to current profile performance.\n"
        "\n"
        "Important reasoning constraints:\n"
        "- Use profile-level and post-level evidence from input.\n"
        "- If profile is combined FB+IG, compare platform behavior and suggest platform-specific actions.\n"
        "- If one platform underperforms, explicitly call it out and suggest a correction strategy.\n"
        "- Avoid motivational language; focus on analysis and execution.\n"
        "\n"
        f"User focus preference: {focus_text or 'general profile growth'}.\n"
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
