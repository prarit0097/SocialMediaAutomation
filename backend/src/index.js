import express from "express";
import cors from "cors";
import dotenv from "dotenv";
import "./db/index.js";
import authRoutes from "./routes/auth.js";
import postRoutes from "./routes/posts.js";
import insightRoutes from "./routes/insights.js";
import { startScheduler } from "./workers/scheduler.js";

dotenv.config();

const app = express();
const port = Number(process.env.PORT || 4000);

app.use(
  cors({
    origin: process.env.APP_URL || "*"
  })
);
app.use(express.json());

app.get("/health", (_req, res) => {
  res.json({ ok: true });
});

app.use("/auth", authRoutes);
app.use("/posts", postRoutes);
app.use("/insights", insightRoutes);

startScheduler();

app.listen(port, () => {
  console.log(`API running on http://localhost:${port}`);
});
