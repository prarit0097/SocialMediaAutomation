from django import forms


class HelpRequestForm(forms.Form):
    TOPIC_CHOICES = [
        ("account_access", "Account access"),
        ("meta_connection", "Meta account connection"),
        ("scheduler", "Scheduling or publishing"),
        ("analytics", "Insights or AI reports"),
        ("billing", "Billing or subscription"),
        ("other", "Other"),
    ]
    PRIORITY_CHOICES = [
        ("normal", "Normal"),
        ("urgent", "Urgent"),
    ]

    name = forms.CharField(
        max_length=120,
        widget=forms.TextInput(attrs={"placeholder": "Your name", "autocomplete": "name"}),
    )
    email = forms.EmailField(
        max_length=254,
        widget=forms.EmailInput(attrs={"placeholder": "you@example.com", "autocomplete": "email"}),
    )
    topic = forms.ChoiceField(choices=TOPIC_CHOICES)
    priority = forms.ChoiceField(choices=PRIORITY_CHOICES)
    page_url = forms.CharField(
        max_length=500,
        required=False,
        label="Page or issue URL",
        widget=forms.URLInput(attrs={"placeholder": "https://postzyo.com/dashboard/...", "autocomplete": "url"}),
    )
    message = forms.CharField(
        max_length=3000,
        widget=forms.Textarea(
            attrs={
                "placeholder": "Tell us what happened, which account/page was affected, and what you expected.",
                "rows": 7,
            }
        ),
    )
