from .models import UserProfile


SUBSCRIPTION_PLAN_CONFIG = {
    "monthly": {
        "plan_key": "monthly",
        "title": "Monthly Growth Plan",
        "profile_label": UserProfile.SUBSCRIPTION_PLAN_MONTHLY,
        "price_label": "INR 6,000 / month",
        "amount_paise": 600000,
        "billing_cycle": "monthly",
        "tagline": "Perfect for fast monthly execution and iteration.",
    },
    "yearly": {
        "plan_key": "yearly",
        "title": "Yearly Scale Plan",
        "profile_label": UserProfile.SUBSCRIPTION_PLAN_YEARLY,
        "price_label": "INR 70,000 / year",
        "amount_paise": 7000000,
        "billing_cycle": "yearly",
        "tagline": "Best value for long-term growth operations.",
    },
}
