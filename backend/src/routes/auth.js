import express from "express";
import dotenv from "dotenv";
import db from "../db/index.js";
import { exchangeCodeForToken, getManagedPages } from "../services/meta.js";

dotenv.config();

const router = express.Router();

router.get("/meta/start", (_req, res) => {
  const params = new URLSearchParams({
    client_id: process.env.META_APP_ID,
    redirect_uri: process.env.META_REDIRECT_URI,
    scope:
      "pages_show_list,pages_read_engagement,pages_manage_posts,instagram_basic,instagram_content_publish,instagram_manage_insights,pages_read_user_content",
    response_type: "code"
  });

  const authUrl = `https://www.facebook.com/v21.0/dialog/oauth?${params.toString()}`;
  return res.json({ authUrl });
});

router.get("/meta/callback", async (req, res) => {
  try {
    const { code } = req.query;
    if (!code) {
      return res.status(400).json({ error: "Missing code" });
    }

    const tokenData = await exchangeCodeForToken({
      code,
      redirectUri: process.env.META_REDIRECT_URI,
      appId: process.env.META_APP_ID,
      appSecret: process.env.META_APP_SECRET
    });

    const userAccessToken = tokenData?.access_token;
    const pages = await getManagedPages(userAccessToken);

    const upsert = db.prepare(`
      INSERT INTO connected_accounts (platform, page_id, page_name, ig_user_id, access_token, updated_at)
      VALUES (@platform, @page_id, @page_name, @ig_user_id, @access_token, CURRENT_TIMESTAMP)
      ON CONFLICT(platform, page_id)
      DO UPDATE SET
        page_name = excluded.page_name,
        ig_user_id = excluded.ig_user_id,
        access_token = excluded.access_token,
        updated_at = CURRENT_TIMESTAMP
    `);

    const tx = db.transaction((items) => {
      for (const page of items) {
        upsert.run({
          platform: "facebook",
          page_id: page.id,
          page_name: page.name,
          ig_user_id: page.instagram_business_account?.id || null,
          access_token: page.access_token
        });

        if (page.instagram_business_account?.id) {
          upsert.run({
            platform: "instagram",
            page_id: page.instagram_business_account.id,
            page_name: `${page.name} (IG)`,
            ig_user_id: page.instagram_business_account.id,
            access_token: page.access_token
          });
        }
      }
    });

    tx(pages);

    return res.send("Accounts connected successfully. You can close this tab.");
  } catch (error) {
    return res.status(500).json({
      error: "Failed to connect Meta accounts",
      details: error?.response?.data || error.message
    });
  }
});

router.get("/accounts", (_req, res) => {
  const rows = db
    .prepare(
      "SELECT id, platform, page_id, page_name, ig_user_id, created_at, updated_at FROM connected_accounts ORDER BY created_at DESC"
    )
    .all();

  return res.json(rows);
});

export default router;
