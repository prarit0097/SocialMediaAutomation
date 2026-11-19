from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core.throttle import is_ip_rate_limited

from .assistant import answer_question
from .forms import HomepageFollowUpForm, HomepageQuestionForm
from .models import HomepageLead


LINK_LABELS = {
    "help": "Open Help",
    "pricing": "See pricing",
    "signup": "Start free trial",
    "login": "Log in",
    "privacy_policy": "Read the Privacy Policy",
    "data_deletion": "View data deletion instructions",
    "terms": "Read the Terms of Service",
}


@require_POST
@never_cache
def ask(request):
    if is_ip_rate_limited(request, "homepage_assistant_ask", "30/m"):
        return JsonResponse({"error": "Too many questions at once. Please try again in a minute."}, status=429)

    form = HomepageQuestionForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Enter a question of up to 300 characters."}, status=400)

    topic = answer_question(form.cleaned_data["question"])
    route = topic.route if topic else "help"
    return JsonResponse({
        "answer": topic.answer if topic else
            "I couldn’t find a clear answer. Try a specific Postzyo question or ask the team through Help.",
        "link": reverse(route),
        "link_label": LINK_LABELS[route],
    })


@require_POST
@never_cache
def follow_up(request):
    accepted = {"message": "Your request was saved. The Postzyo team can follow up by email."}
    if request.POST.get("website", "").strip():
        return JsonResponse(accepted)

    form = HomepageFollowUpForm(request.POST)
    if not form.is_valid():
        return JsonResponse({
            "error": "Enter your name and a valid email address, then confirm you want a follow-up.",
        }, status=400)
    if is_ip_rate_limited(request, "homepage_assistant_follow_up", "5/h"):
        return JsonResponse({
            "error": "Too many requests. Try again later or send the team a message through Help.",
        }, status=429)

    HomepageLead.objects.get_or_create(
        email=form.cleaned_data["email"],
        defaults={"name": form.cleaned_data["name"]},
    )
    return JsonResponse(accepted)
