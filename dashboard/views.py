from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def home(request):
    return render(request, "dashboard/home.html")


@login_required
def accounts_page(request):
    return render(request, "dashboard/accounts.html")


@login_required
def scheduler_page(request):
    return render(request, "dashboard/scheduler.html")


@login_required
def insights_page(request):
    return render(request, "dashboard/insights.html")
