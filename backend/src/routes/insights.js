import express from "express";
import db from "../db/index.js";
import { fetchFacebookInsights, fetchInstagramInsights } from "../services/meta.js";

const router = express.Router();

router.get("/:accountId", async (req, res) => {
  try {
    const accountId = Number(req.params.accountId);
    const account = db
      .prepare("SELECT * FROM connected_accounts WHERE id = ?")
      .get(accountId);

    if (!account) {
      return res.status(404).json({ error: "Connected account not found" });
    }

    if (account.platform === "facebook") {
      const insights = await fetchFacebookInsights({
        pageId: account.page_id,
        pageAccessToken: account.access_token
      });
      return res.json({ platform: "facebook", insights });
    }

    const insights = await fetchInstagramInsights({
      igUserId: account.ig_user_id || account.page_id,
      pageAccessToken: account.access_token
    });

    return res.json({ platform: "instagram", insights });
  } catch (error) {
    return res.status(500).json({
      error: "Failed to fetch insights",
      details: error?.response?.data || error.message
    });
  }
});

export default router;
