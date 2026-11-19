from dataclasses import dataclass


@dataclass(frozen=True)
class Article:
    title: str
    description: str
    image: str
    image_alt: str
    sections: tuple[tuple[str, str], ...]
    published: str = "2026-11-19"
    image_caption: str = "Postzyo product preview with sample data"


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
    "check-media-url-before-scheduling": Article(
        "How to check a media URL before scheduling a social post",
        "Check that a linked image or video opens without signing in, stays available until publishing, and matches the intended Facebook or Instagram post.",
        "dashboard/blog/media-link-preflight.webp",
        "A laptop displaying a product photo next to cards with a link icon and check mark",
        (
            ("Copy the link to the actual file",
             "A team can approve an image in a shared folder or design document, but a scheduler needs access to the image or video file itself. Ask for the final export and check that its link displays the media without a sign-in prompt, permission request or preview page. If your team cannot provide a direct link, use the Scheduler file upload for an eligible file."),
            ("Use a signed-out browser for the first check",
             "Open the link in a private window that is signed out of the storage service. Confirm that you see the intended image or can play or download the intended video. An HTTP 200 can still be a login or document preview page. If you run a technical check, look at the response type as well as the status: an HTML response normally is not the media file you intended to schedule."),
            ("Check how long the file will stay available",
             "A link can work when you schedule a post and expire before publishing. Check whether the link has an expiry or depends on a workspace session. Keep the approved file available through the scheduled time and any retry window. If its distribution permissions should stay restricted, choose a supported upload flow and share only the media your team has approved for publication."),
            ("Keep a record of what was approved",
             "For a campaign photo, record which exported file you used, the intended caption and the destination. After submitting, check each row in the Scheduled Queue. If a status note identifies inaccessible media, resolve the link or upload first. If delivery is uncertain, check the destination before trying to publish a second copy."),
        ),
        image_caption="Editorial illustration of checking an image link before publishing",
    ),
    "schedule-posts-across-timezones": Article(
        "How to schedule social posts across timezones",
        "Turn a launch brief into a timezone-aware schedule and give teammates a clear time to check each Facebook and Instagram destination.",
        "dashboard/landing/scheduler-desktop.webp",
        "Postzyo’s scheduled post form showing both Facebook and Instagram selected",
        (
            ("Write the launch time with a date and location",
             "Ask the campaign owner to name the timezone as well as the date and time. For example, a launch at 9 a.m. on November 20 in London is at 2:30 p.m. in India on that date. Use the launch date when converting the time. A region can change its clock offset between the time you draft a post and the day it goes out."),
            ("Translate the time for the device you are using",
             "Postzyo’s composer accepts the local time shown by your browser and stores the execution time in UTC. If the brief says 9 a.m. London time and your browser is set to India, enter the converted India time for that date. Recheck your device timezone if you travel or use a remote desktop. For a shared brief, include the audience timezone and the UTC equivalent."),
            ("Check the date as well as the hour",
             "Conversions near midnight can move a post to the previous or following day. A brief that just says Friday evening can be interpreted differently by teammates in several regions. Ask one person to independently confirm the exact execution date and time, then compare the selected time and destinations with the Scheduled Queue."),
            ("Hand off the delivery check",
             "Leave the owner with the campaign name, account, planned time and the date they should check. A combined Facebook and Instagram post produces separate destinations. Review those separately after the scheduled time. If you move a draft in the Planning calendar after it has been scheduled, check the queued destination too so the calendar and publishing time stay aligned."),
        ),
    ),
    "meta-account-handoff-checklist": Article(
        "Facebook and Instagram account handoff checklist",
        "Confirm which Page and Instagram profile a teammate manages, review access in Meta, and check the publishing workspace before their first handoff.",
        "dashboard/landing/calendar-mobile.webp",
        "Postzyo’s mobile Planning form for a sample campaign item",
        (
            ("Name each destination before inviting a teammate",
             "Write down the exact Facebook Page and Instagram profile used for each campaign. If your business manages several locations, note the city or account name beside each one. Confirm the Instagram professional profile is linked to the correct Facebook Page. Similar names and profile pictures can make it easy for a new operator to pick the wrong destination."),
            ("Grant access in Meta using the teammate’s own sign-in",
             "Use Meta’s access controls to give the teammate the responsibilities they need. Keep the owner or administrator details separate from publishing instructions. Your handoff document can include account URLs, campaign owners and the approval process without containing passwords, access tokens or recovery codes."),
            ("Refresh the list and compare it with the handoff",
             "Connect through the Accounts section using the Meta user who manages those assets. Refresh the connected list and confirm the Page and linked Instagram profile appear. If the profile is missing, verify the relationship in Meta before reconnecting. If an old entry is marked stale, use the current connection before choosing it as a publishing destination."),
            ("Give the first campaign a named reviewer",
             "Open the calendar and check the caption, approved visual, platform, intended time and who will confirm delivery. The initial handoff is also a chance to agree on what a blocked or uncertain result should trigger. Name the person who can correct account access, and record which destination to check before retrying."),
        ),
    ),
    "plan-social-posts-for-a-time-limited-offer": Article(
        "How to plan social posts for a time-limited offer",
        "Plan the announcement, reminder and closing posts for a short promotion, and keep the schedule accurate if the offer changes.",
        "dashboard/landing/calendar-desktop.webp",
        "Postzyo’s monthly calendar showing sample social posts on several dates",
        (
            ("Put the offer dates and conditions in the brief",
             "Write the date and time the offer begins and ends, the timezone, any eligibility conditions and the destination URL. For a bakery taking orders until Saturday at 4 p.m. local time, spell out whether that is the order cutoff or the pickup cutoff. Confirm that the landing page shows the same details before the first post goes out."),
            ("Give the announcement and reminder different jobs",
             "Use the first post to explain what’s available and who it’s for. Use the reminder to answer a question that might prevent someone from ordering, such as pickup hours or which menu items qualify. If you plan to announce that the offer has ended, prepare the copy without scheduling it until the closing time is confirmed."),
            ("Schedule from the approved calendar items",
             "Add the posts to Planning and give each one a date, intended platform and draft caption. Give the team a single place to note approved artwork and wording. Open the approved items in Scheduler, check the selected destinations and times, then compare the queue with the offer brief."),
            ("Check the queue when the details change",
             "If stock runs out or the closing time changes, find any reminders still in the publish queue and review them there. Update the offer page and record which posts already went live so the campaign owner knows where a correction may be needed. After the campaign, compare posts with the outcome you chose, such as visits to the ordering page."),
        ),
    ),
    "utm-links-for-facebook-and-instagram-campaigns": Article(
        "How to tag Facebook and Instagram campaign links with UTM parameters",
        "Use consistent UTM tags to identify visits from Facebook and Instagram and test the exact links before publishing.",
        "dashboard/blog/media-link-preflight.webp",
        "Illustration of a laptop, a link icon and a check mark",
        (
            ("Agree on the campaign name first",
             "Choose a short, stable name the team can reuse. For a spring workshop campaign, use utm_campaign=spring_workshop on the eligible links. Set utm_medium=social for your unpaid social links and use facebook or instagram for utm_source. Keep a small naming sheet so different teammates use the same spelling and case."),
            ("Tag the link people can actually open",
             "A Facebook post can point readers directly to a booking page. For an Instagram feed caption, use a clear instruction if the clickable link is in the profile. Tag the URL installed at that clickable destination. If the profile link is shared by several posts during a campaign, reporting can show visits from the profile link without identifying which caption prompted each visit."),
            ("Test the complete URL before it goes live",
             "If the destination URL already contains a query string, add parameters to that query rather than adding a second question mark. Use a URL builder for values that need encoding. Open the final link on a phone and follow any redirects. Check the page loads, then confirm your analytics receives the expected campaign values. Keep customer names and other personal information out of tags."),
            ("Compare traffic with the action you asked for",
             "Use your site analytics to compare eligible visits and relevant actions, such as booking requests, against the tagged links. For two distinct links on one platform, use utm_content values such as launch and reminder if you need to tell them apart. Visits can be undercounted when consent choices or app behavior prevent measurement, so treat the tags as evidence about recorded traffic."),
        ),
        image_caption="Editorial illustration of checking a link before a social campaign goes live",
    ),
    "weekly-facebook-and-instagram-insights-review": Article(
        "How to run a weekly Facebook and Instagram Insights review",
        "Review recent post performance, account for incomplete Meta reporting, and choose one change to test in next week’s calendar.",
        "dashboard/landing/calendar-mobile.webp",
        "Postzyo’s mobile Planning form for recording the next content item",
        (
            ("Choose one question for the week",
             "A retailer might ask whether product demonstrations get more saves than product photos on Instagram. A workshop studio might ask which Facebook announcements generate more comments about upcoming sessions. Write down the question and the reporting dates so everyone compares the same set of posts."),
            ("Document which data was available",
             "Open Insights for the connected account and check the recent Published Posts table. The view can include stored snapshots and may warn when Meta only returns partial data. Record when you checked and mark unavailable metrics as missing. If you need a newer view and have access to the account, try a refresh and check which rows were updated."),
            ("Compare like posts at a comparable age",
             "Group posts by platform and format. Compare a seven-day-old post with another post of a similar age where possible. If you only have one example of a format, keep the observation and collect more examples before changing the whole calendar. When a Facebook metric uses a different definition from Instagram’s, report the two separately."),
            ("Put the next test in Planning",
             "Choose one change for the coming week and write down what would count as improvement. For example, keep the Instagram format and posting window similar while testing a more specific opening line. Add the next post to Planning and note when you will review it so the next weekly report can connect the result to the change."),
        ),
    ),
}
