from django.contrib.auth import login, logout
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render


class AdminLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True


def landing_page(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")
    return render(request, "accounts/landing.html")


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard:home")

    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("dashboard:home")
    else:
        form = UserCreationForm()

    return render(request, "accounts/signup.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("login")
