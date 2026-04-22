FACEBOOK = "facebook"
INSTAGRAM = "instagram"

PLATFORM_CHOICES = [
    (FACEBOOK, "Facebook"),
    (INSTAGRAM, "Instagram"),
]

POST_STATUS_PENDING = "pending"
POST_STATUS_PROCESSING = "processing"
POST_STATUS_PUBLISHED = "published"
POST_STATUS_FAILED = "failed"

POST_STATUS_CHOICES = [
    (POST_STATUS_PENDING, "Pending"),
    (POST_STATUS_PROCESSING, "Processing"),
    (POST_STATUS_PUBLISHED, "Published"),
    (POST_STATUS_FAILED, "Failed"),
]

META_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "read_insights",
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_insights",
]
