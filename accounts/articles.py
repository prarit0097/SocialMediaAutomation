from dataclasses import dataclass


@dataclass(frozen=True)
class Article:
    title: str
    description: str
    image: str
    image_alt: str
    sections: tuple[tuple[str, str], ...]
    published: str = "2026-11-19"


ARTICLES = {
    "schedule-facebook-instagram-posts": Article(
        "How to schedule one post for Facebook and Instagram",
        "Set up a linked Facebook Page and Instagram account, prepare a shared caption and media, and check delivery on both platforms.",
        "dashboard/landing/scheduler-desktop.webp",
        "Postzyo’s scheduled post form with Facebook and Instagram selected for a sample campaign",
        (
            ("Connect the Page that manages your Instagram account",
             "Start in Accounts. Sign in to Meta as someone who manages the Facebook Page you want to use and grant access to the relevant assets. Postzyo finds the eligible Instagram professional profile linked to that Page. Refresh the list and confirm both destinations are present. If the Instagram account is missing, check the link in Meta before reconnecting."),
            ("Prepare a caption and media that work on both platforms",
             "Draft a caption that makes sense without platform-specific directions. For example, if you are announcing a weekend workshop, put the date and city in the opening lines. Choose an image or supported video for Instagram. If you provide a media URL, verify it opens directly in a signed-out browser. A link to a private folder or preview page may fail."),
            ("Schedule the post in your local time",
             "Open Scheduler, select the connected account and choose Facebook + Instagram. Add your caption and media, then choose the date and time. The composer uses your browser’s local time, so check which timezone your device is set to if you are scheduling while travelling. After submitting, open Scheduled Queue and verify the requested time and each destination."),
            ("Check delivery one destination at a time",
             "Facebook and Instagram have separate queue rows even when they came from one submission. If only one publishes, read the status note for the other destination. A temporary delay can be retried automatically. If the row says Verify outcome, check the destination in Meta before scheduling again so you avoid publishing the same post twice."),
        ),
    ),
    "monthly-social-media-content-calendar": Article(
        "Build a monthly social media content calendar",
        "Choose content pillars, balance formats and move approved Facebook and Instagram ideas from a monthly calendar into the publishing queue.",
        "dashboard/landing/calendar-desktop.webp",
        "Postzyo’s monthly Planning calendar populated with sample social media posts",
        (
            ("Start with a business goal and a realistic cadence",
             "Pick one outcome for the month, such as more workshop enquiries or more conversations with existing customers. Count the time you can actually spend producing photos, video and captions. If you can produce two posts a week, plan eight strong posts for a four-week month before filling extra slots."),
            ("Give each idea a pillar and a publishable brief",
             "For a small studio, the pillars might be customer stories, practical tips and workshop announcements. Add each idea to Planning with a title, platform, intended date and draft caption. Use a pillar for the recurring theme and a tag for a temporary campaign, such as a November class launch. Keep approvals or missing assets in the notes."),
            ("Use draft and approval states to protect the queue",
             "A planned item is a place to work out the idea. Use Draft when copy or media is missing and Review when a teammate needs to check it. Once it is ready, open the calendar item in Scheduler. Review the account, time and media there and submit it. The queue is where you track delivery."),
            ("Leave room to respond and review",
             "Keep a buffer for time-sensitive updates, customer questions or an asset that arrives late. At month end, compare a consistent metric across similar posts, such as saves for tips or comments for questions. Use that comparison to adjust the next month’s mix, and keep drafts that can be reused for a different occasion."),
        ),
    ),
    "why-scheduled-social-post-did-not-publish": Article(
        "Why a scheduled Facebook or Instagram post didn’t publish",
        "Read each destination’s queue status, diagnose Meta connection and media problems, and decide when to wait, retry or verify delivery.",
        "dashboard/landing/scheduler-mobile.webp",
        "Postzyo Scheduler with a sample account, destination choice and caption",
        (
            ("Find the affected destination in Scheduled Queue",
             "Locate the post by its caption and intended time, then check the platform and the status note. A combined submission may publish on Facebook while Instagram is still processing. Record the account name, status and requested time so your next action applies to the right queue row."),
            ("Check the common connection and media problems",
             "If the status points to an expired token or missing permission, reconnect through Accounts with a Meta user who manages the relevant assets. If it points to media, verify that the URL is direct and reachable without signing in, or upload a supported file. For Instagram, check that you attached media and that the profile is still linked to the correct Page."),
            ("Use the queue status to decide what to do next",
             "Retrying means an eligible temporary failure has a next-attempt time. Give the queue time to recover. A known failure may expose a Retry action after you resolve the cause. A row marked Verify outcome needs a different check. Open the destination in Meta and look for a matching post before you reschedule."),
            ("Give support enough information to investigate",
             "If the cause is unclear, record the platform, connected profile, scheduled time and the exact text in the status note. Include whether the post used an upload or a URL. Use the Help form with these details and keep passwords and access tokens out of the request."),
        ),
    ),
}
