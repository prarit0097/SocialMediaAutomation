import cron from "node-cron";
import db from "../db/index.js";
import {
  createInstagramMediaContainer,
  publishInstagramMedia,
  publishToFacebookPage
} from "../services/meta.js";

async function processPost(post) {
  const account = db
    .prepare("SELECT * FROM connected_accounts WHERE id = ?")
    .get(post.account_id);

  if (!account) {
    throw new Error("Account not found");
  }

  if (post.platform === "facebook") {
    if (!post.message) {
      throw new Error("Facebook post requires message");
    }

    const result = await publishToFacebookPage({
      pageId: account.page_id,
      pageAccessToken: account.access_token,
      message: post.message
    });

    return result.id;
  }

  if (!post.media_url) {
    throw new Error("Instagram publishing requires mediaUrl");
  }

  const creationId = await createInstagramMediaContainer({
    igUserId: account.ig_user_id || account.page_id,
    pageAccessToken: account.access_token,
    imageUrl: post.media_url,
    caption: post.message || ""
  });

  const publishResult = await publishInstagramMedia({
    igUserId: account.ig_user_id || account.page_id,
    pageAccessToken: account.access_token,
    creationId
  });

  return publishResult.id;
}

export function startScheduler() {
  cron.schedule("* * * * *", async () => {
    const duePosts = db
      .prepare(
        `SELECT * FROM scheduled_posts
         WHERE status = 'pending'
           AND datetime(scheduled_for) <= datetime('now')
         ORDER BY scheduled_for ASC
         LIMIT 20`
      )
      .all();

    for (const post of duePosts) {
      try {
        db.prepare("UPDATE scheduled_posts SET status = 'processing', updated_at = CURRENT_TIMESTAMP WHERE id = ?")
          .run(post.id);

        const externalPostId = await processPost(post);

        db.prepare(
          "UPDATE scheduled_posts SET status = 'published', external_post_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        ).run(externalPostId, post.id);
      } catch (error) {
        db.prepare(
          "UPDATE scheduled_posts SET status = 'failed', error_message = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
        ).run(error?.response?.data ? JSON.stringify(error.response.data) : error.message, post.id);
      }
    }
  });
}
