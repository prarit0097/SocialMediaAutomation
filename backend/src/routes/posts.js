import express from "express";
import { z } from "zod";
import db from "../db/index.js";

const router = express.Router();

const scheduleSchema = z.object({
  accountId: z.number().int().positive(),
  platform: z.enum(["facebook", "instagram"]),
  message: z.string().max(2200).optional(),
  mediaUrl: z.string().url().optional(),
  scheduledFor: z.string().datetime()
});

router.post("/schedule", (req, res) => {
  const parsed = scheduleSchema.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: parsed.error.flatten() });
  }

  const { accountId, platform, message, mediaUrl, scheduledFor } = parsed.data;

  const account = db.prepare("SELECT id FROM connected_accounts WHERE id = ?").get(accountId);
  if (!account) {
    return res.status(404).json({ error: "Connected account not found" });
  }

  const info = db
    .prepare(
      `INSERT INTO scheduled_posts (account_id, platform, message, media_url, scheduled_for)
       VALUES (?, ?, ?, ?, ?)`
    )
    .run(accountId, platform, message || null, mediaUrl || null, scheduledFor);

  return res.status(201).json({ id: info.lastInsertRowid, status: "pending" });
});

router.get("/scheduled", (_req, res) => {
  const rows = db
    .prepare(
      `SELECT sp.id, sp.platform, sp.message, sp.media_url, sp.scheduled_for, sp.status, sp.error_message,
              ca.page_name
       FROM scheduled_posts sp
       JOIN connected_accounts ca ON ca.id = sp.account_id
       ORDER BY sp.scheduled_for DESC`
    )
    .all();

  return res.json(rows);
});

export default router;
