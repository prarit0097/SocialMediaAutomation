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


def _to_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        raw = value.strip().replace(",", "")
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _format_number(value: float | None, decimals: int = 2) -> str:
    if value is None:
        return "not available"
    rounded = round(float(value), decimals)
    if abs(rounded - int(rounded)) < 1e-9:
        return str(int(rounded))
    return f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")


def _mentions_both_platforms(text: str) -> bool:
    lowered = str(text or "").lower()
    has_fb = ("facebook" in lowered) or bool(re.search(r"\bfb\b", lowered))
    has_ig = ("instagram" in lowered) or bool(re.search(r"\big\b", lowered))
    return has_fb and has_ig


def _default_posting_strategy(payload: dict[str, Any]) -> dict[str, str]:
    cadence = payload.get("posting_cadence") if isinstance(payload.get("posting_cadence"), dict) else {}
    perf = payload.get("performance_last_7d") if isinstance(payload.get("performance_last_7d"), dict) else {}

    fb_posts_7 = _to_number(cadence.get("facebook_posts_last_7d"))
    ig_posts_7 = _to_number(cadence.get("instagram_posts_last_7d"))
    fb_avg_7 = _to_number(cadence.get("facebook_avg_posts_per_day_last_7d"))
    ig_avg_7 = _to_number(cadence.get("instagram_avg_posts_per_day_last_7d"))
    total_avg_7 = _to_number(cadence.get("avg_posts_per_day_last_7d"))

    if fb_avg_7 is None and fb_posts_7 is not None:
        fb_avg_7 = round(fb_posts_7 / 7.0, 2)
    if ig_avg_7 is None and ig_posts_7 is not None:
        ig_avg_7 = round(ig_posts_7 / 7.0, 2)

    if fb_avg_7 is None and ig_avg_7 is None and total_avg_7 is not None:
        fb_avg_7 = round(total_avg_7 / 2.0, 2)
        ig_avg_7 = round(total_avg_7 / 2.0, 2)

    if fb_posts_7 is None and fb_avg_7 is not None:
        fb_posts_7 = round(fb_avg_7 * 7, 2)
    if ig_posts_7 is None and ig_avg_7 is not None:
        ig_posts_7 = round(ig_avg_7 * 7, 2)

    if fb_avg_7 is None and ig_avg_7 is None:
        current = "Facebook: not available | Instagram: not available"
        recommended = (
            "Facebook: 1 post/day (~7 posts/7d). "
            "Instagram: 1 post/day (~7 posts/7d) with at least 4 reels + 3 static/carousel posts."
        )
        reasoning = (
            "Current platform-wise cadence is not available in the snapshot, so a balanced baseline is recommended. "
            "This gives enough weekly volume to test hooks, creatives, and posting slots on both platforms."
        )
        return {
            "current_posting": current,
            "recommended_posting": recommended,
            "reasoning": reasoning,
        }

    fb_target_avg = max(1.0, round((fb_avg_7 or 0.0) + 0.3, 2))
    ig_target_avg = max(1.0, round((ig_avg_7 or 0.0) + 0.4, 2))
    fb_target_week = max(7, int(round(fb_target_avg * 7)))
    ig_target_week = max(7, int(round(ig_target_avg * 7)))

    current = (
        f"Facebook: {_format_number(fb_posts_7, 0)} posts in last 7 days "
        f"(avg {_format_number(fb_avg_7)}/day) | "
        f"Instagram: {_format_number(ig_posts_7, 0)} posts in last 7 days "
        f"(avg {_format_number(ig_avg_7)}/day)"
    )
    recommended = (
        f"Facebook: target ~{_format_number(fb_target_avg)}/day ({fb_target_week} posts/7d) "
        "with consistent feed posts and at least 2 short videos/week. "
        f"Instagram: target ~{_format_number(ig_target_avg)}/day ({ig_target_week} posts/7d) "
        "with reels-first mix (minimum 4-5 reels/week) plus carousel/static support posts."
    )
    reasoning = (
        "A moderate platform-wise cadence increase should improve reach distribution and give more creative tests per week. "
        f"Last 7-day performance context: views={_format_number(_to_number(perf.get('views')), 0)}, "
        f"likes={_format_number(_to_number(perf.get('likes')), 0)}, "
        f"comments={_format_number(_to_number(perf.get('comments')), 0)}, "
        f"shares={_format_number(_to_number(perf.get('shares')), 0)}. "
        "Separate FB and IG targets reduce under-posting risk on one platform and make weekly optimization measurable."
    )
    return {
        "current_posting": current,
        "recommended_posting": recommended,
        "reasoning": reasoning,
    }


def _default_best_recommendations(payload: dict[str, Any]) -> list[str]:
    perf = payload.get("performance_last_7d") if isinstance(payload.get("performance_last_7d"), dict) else {}
    cadence = payload.get("posting_cadence") if isinstance(payload.get("posting_cadence"), dict) else {}
    profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    profile_name = str(profile.get("page_name") or "this profile").strip()

    views = _format_number(_to_number(perf.get("views")), 0)
    likes = _format_number(_to_number(perf.get("likes")), 0)
    comments = _format_number(_to_number(perf.get("comments")), 0)
    shares = _format_number(_to_number(perf.get("shares")), 0)
    fb_7d = _format_number(_to_number(cadence.get("facebook_posts_last_7d")), 0)
    ig_7d = _format_number(_to_number(cadence.get("instagram_posts_last_7d")), 0)

    return [
        (
            f"For {profile_name}, use a reels-first + strong-hook format on Instagram and test at least 4-5 short videos in 7 days; "
            "prioritize opening 2 seconds and retention-focused edits. "
            "Reference: Instagram Creators / Meta for Creators best practices."
        ),
        (
            f"Keep platform-separated consistency for {profile_name}: Facebook {fb_7d} posts (last 7d) and Instagram {ig_7d} posts (last 7d) "
            "should move toward daily publishing with fixed posting slots. "
            "Reference: Meta Business Suite publishing guidance."
        ),
        (
            f"Build engagement loops for {profile_name} from current baseline (views={views}, likes={likes}, comments={comments}, shares={shares}) "
            "by adding CTA prompts, question captions, and comment reply within first 60 minutes of posting. "
            "Reference: Meta performance/engagement documentation."
        ),
        (
            f"For {profile_name}, run weekly content mix: 60% educational problem-solution posts, 25% proof/results/social trust posts, "
            "15% authority/personal brand posts. Track saves + shares as primary quality signal. "
            "Reference: creator-led content strategy playbooks."
        ),
    ]


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
        "7) In posting_strategy, never give a blended cadence; always split Facebook and Instagram separately.\n"
        "8) Tone must be humanized and practical: like a senior growth mentor talking to a real operator, not robotic.\n"
        "9) Recommendations must explicitly stay profile-wise (for the selected account_id/page_name only).\n"
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
        '  "content_ideas": string[],\n'
        '  "best_recommendations_for_growth": string[]\n'
        "}\n"
        "\n"
        "Output requirements:\n"
        "- executive_summary: 4-7 lines, include strongest issue and top growth lever.\n"
        "- pros/cons/risks/opportunities: each ideally 4-8 clear bullets.\n"
        "- posting_strategy:\n"
        "  - current_posting: MUST report both platforms separately using last-7-days data.\n"
        "    Required format: 'Facebook: <posts_7d> posts in last 7 days (avg <x>/day) | Instagram: <posts_7d> posts in last 7 days (avg <y>/day)'.\n"
        "    Use input keys posting_cadence.facebook_posts_last_7d, posting_cadence.instagram_posts_last_7d, posting_cadence.facebook_avg_posts_per_day_last_7d, posting_cadence.instagram_avg_posts_per_day_last_7d.\n"
        "    If missing, write 'not available' for that platform; do not merge both platforms into one number.\n"
        "  - recommended_posting: MUST give separate FB and IG posting plan (posts/day and posts/7d), with short content-mix guidance.\n"
        "  - reasoning: MUST explain platform-wise why recommendation should work, referencing observed metrics/cadence from input.\n"
        "    Include at least 3 concrete data anchors from provided JSON (for example views, likes, comments, shares, saves, cadence gap).\n"
        "- action_plan_7d: 5-10 prioritized actions; each action should be concrete and measurable.\n"
        "- kpi_growth_plan: include at least these metrics when available: views, reach, likes, comments, shares, saves, interactions, post cadence.\n"
        "  For each metric: give current (or 'not available'), realistic 7-day target, and method.\n"
        "- content_ideas: 8-15 practical ideas aligned to current profile performance.\n"
        "- best_recommendations_for_growth:\n"
        "  - MUST provide 5-10 high-impact recommendations as clear bullets.\n"
        "  - MUST be tied to provided profile data (cadence + performance metrics).\n"
        "  - MUST sound realistic and human (mentor/advisor tone), not robotic or template-like.\n"
        "  - MUST mention selected profile context (page_name or account_id reference) naturally.\n"
        "  - MUST mention trend-aware execution for current social behavior (short-form video hooks, retention, saves/shares, strong CTA loops).\n"
        "  - MUST include credible resource references inside each bullet when possible (for example: Meta for Creators, Instagram Creators, Meta Business Help Center).\n"
        "  - Do not add fake URLs; if specific source link is not known, use source name only.\n"
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
    fallback_posting_strategy = _default_posting_strategy(payload)
    fallback_best_recommendations = _default_best_recommendations(payload)
    parsed_posting_strategy = parsed.get("posting_strategy") if isinstance(parsed.get("posting_strategy"), dict) else {}

    current_posting = str(parsed_posting_strategy.get("current_posting") or "").strip()
    recommended_posting = str(parsed_posting_strategy.get("recommended_posting") or "").strip()
    reasoning = str(parsed_posting_strategy.get("reasoning") or "").strip()

    if not current_posting or not _mentions_both_platforms(current_posting):
        current_posting = fallback_posting_strategy["current_posting"]
    if not recommended_posting or not _mentions_both_platforms(recommended_posting):
        recommended_posting = fallback_posting_strategy["recommended_posting"]
    if not reasoning:
        reasoning = fallback_posting_strategy["reasoning"]
    elif not _mentions_both_platforms(reasoning):
        reasoning = f"{reasoning} {fallback_posting_strategy['reasoning']}".strip()

    normalized = {
        "executive_summary": str(parsed.get("executive_summary") or "").strip(),
        "pros": _normalize_list(parsed.get("pros")),
        "cons": _normalize_list(parsed.get("cons")),
        "risks": _normalize_list(parsed.get("risks")),
        "opportunities": _normalize_list(parsed.get("opportunities")),
        "posting_strategy": {
            "current_posting": current_posting,
            "recommended_posting": recommended_posting,
            "reasoning": reasoning,
        },
        "action_plan_7d": _normalize_plan_rows(parsed.get("action_plan_7d")),
        "kpi_growth_plan": _normalize_kpi_rows(parsed.get("kpi_growth_plan")),
        "content_ideas": _normalize_list(parsed.get("content_ideas")),
        "best_recommendations_for_growth": _normalize_list(parsed.get("best_recommendations_for_growth")),
    }
    if len(normalized["best_recommendations_for_growth"]) < 3:
        normalized["best_recommendations_for_growth"] = fallback_best_recommendations
    return normalized
