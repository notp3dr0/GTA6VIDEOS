# 5-Day Away Autonomous Kickoff & Unattended Operations Plan

## 1. Mission Objective (Days 1–5 Unattended)
Operate the **GTA 6 Fast Clips** pipeline and **Real Oficial (`gta-vi-youtube`)** YouTube Shorts Championship safely for **5 consecutive days** with zero manual intervention required, zero copyright/leak violations, and zero unapproved credit spend:
- **20 YouTube Shorts** (`4/day` at `10:00`, `14:00`, `18:00`, `21:30` BRT) compliant with `gta-vi-youtube` rules (`#GTA6 #GTAVI #Shorts`, `10–58s`, `-14 LUFS`, burned captions, `🎥 @RockstarGames`).
- **10 Instagram Reels** (`2/day` at `12:00`, `19:30` BRT) + **15 TikToks** (`3/day` at `11:45`, `17:00`, `21:00` BRT) via Buffer/native APIs or ready-to-post manifest queue.
- **30 X Updates** (`6/day`, zero hashtags, verified facts + `🎥 @creator`).

---

## 2. Pre-Departure 15-Minute Checklist (Before Leaving)
1. **Credentials & Tokens (`.env`)**:
   - Verify `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, and `YOUTUBE_REFRESH_TOKEN` are active and scoped to the public Shorts channel.
   - Verify `REAL_OFICIAL_API_KEY` / `REAL_OFICIAL_MCP_TOKEN` and `REAL_OFICIAL_CAMPAIGN_SLUG=gta-vi-youtube`.
   - Set `DRY_RUN=false` and `AUTOPILOT_MODE=semi_auto` once a single dry-run test clip passes end-to-end.
2. **Financial & Credit Locks**:
   - Keep `REAL_OFICIAL_ALLOW_SPEND_CREDITS=false` (uses free local FFmpeg + Edge-TTS pipeline and free `submit_championship_video` submission; prevents unattended paid cut generation).
   - Keep `REAL_OFICIAL_DAILY_CREDIT_LIMIT=150` hard cap enforced in SQLite `credit_spend`.
3. **Compliance Locks (`config/settings.yaml`)**:
   - `require_approval_for_rumors: true` (holds any rumor in `pending_approvals` instead of posting).
   - `require_approval_outside_pillars: true`
   - `min_interval_minutes_same_platform: 150`
   - `auto_publish_verified_clips: true` (auto-publishes ONLY `AUTO_APPROVED` official/whitelisted clips).
4. **Alerts**:
   - Confirm `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` or `DISCORD_WEBHOOK_URL` receives daily digest notifications.

---

## 3. Day-by-Day Autonomous Content Matrix

### Day 1 (`2026-11-18` — Final Pre-Launch Eve)
- **YT Shorts (`gta-vi-youtube`)**:
  1. `10:00` — Trailer 1 (`#0-26`): Lucia Caminos introduction & Leonida correctional detail
  2. `14:00` — Trailer 2 (`#0-26`): Jason Duval & Leonida Keys opening atmosphere
  3. `18:00` — Spotlight: Vice City neon reflections & traffic density breakdown
  4. `21:30` — Countdown Recap: Everything officially confirmed by Rockstar before launch
- **Reels / TikTok / X**: Pre-launch settings prep (`tips_tutorials`) + Los Santos farewell (`gta5_nostalgia`).

### Day 2 (`2026-11-19` — Official Launch Day Pivot)
- **Automatic Launch Mode Trigger (`gta6_launch_date: 2026-11-19`)**:
  - `ScoutAgent` blocks street-date leaks/piracy and prioritizes official Rockstar launch footage & verified whitelisted gameplay.
  - `ComplianceFactAgent` enforces `⚠️ SPOILER` tags on any narrative flag.
- **YT Shorts (`gta-vi-youtube`)**:
  1. `10:00` — Trailer 1 (`#26-52`): Vice Beach crowd AI & water physics showcase
  2. `14:00` — Trailer 2 (`#26-52`): Jason & Lucia dynamic duo mechanics breakdown
  3. `18:00` — Tips: Day-1 driving & camera settings for Leonida highways
  4. `21:30` — Day 1 in Leonida Recap: Top verified open-world details

### Day 3 (`2026-11-20` — Leonida World Regions)
- **YT Shorts (`gta-vi-youtube`)**:
  1. `10:00` — Trailer 1 (`#52-78`): Grassrivers airboat & wildlife ecosystem detail
  2. `14:00` — Trailer 2 (`#52-78`): Vice City night lighting & vehicle reflections
  3. `18:00` — Spotlight: Leonida Keys bridges, boats & coastal weather
  4. `21:30` — Nostalgia: GTA V (2013) vs GTA VI (2026) water & vehicle physics

### Day 4 (`2026-11-21` — Mechanics, Vehicles & Exploration)
- **YT Shorts (`gta-vi-youtube`)**:
  1. `10:00` — Trailer 2 (`#78-104`): Muscle car suspension & tire smoke physics
  2. `14:00` — Spotlight: Port Gellhorn & Ambrosia industrial backcountry details
  3. `18:00` — Tips: Mastering high-speed cornering & heavy vehicle braking
  4. `21:30` — Whitelisted Gameplay Breakdown (`@GTASeriesVideos` / `@Broughy1322` credited)

### Day 5 (`2026-11-22` — Weekend Peak Traffic & Recap)
- **YT Shorts (`gta-vi-youtube`)**:
  1. `10:00` — Trailer 2 (`#104-130`): Mount Kalaga wilderness & draw-distance showcase
  2. `14:00` — Spotlight: Lucia & Jason character animation micro-expressions
  3. `18:00` — Tips: 3 hidden open-world interaction mechanics most players miss
  4. `21:30` — First Week in Leonida Verified Highlights Recap

---

## 4. Resilience, Failure Modes & Emergency Controls
- **RSS / Reddit Outage**: `ScoutAgent` seamlessly falls back to its built-in offline library of verified Rockstar Trailer segments, Leonida regional spotlights, GTA V nostalgia comparisons, and gameplay tips — pipeline never stalls.
- **LLM API Outage / Quota Limit**: `ScriptCopyAgent` automatically falls back to deterministic `pt-BR` / `en-US` templates compliant with Section 7 and `ComplianceFactAgent`.
- **Real Oficial Submission Guard**: Every published YouTube Short is logged in SQLite `championship_entries`; if MCP/REST submission returns transient 5xx or requires manual confirmation, entry is safely preserved as `pending_submission` / `manual_action_needed` with exact YouTube URL.
- **Day-6 Return Routine (10 Minutes)**:
  1. Review `pending_approvals` table and approve/discard held items.
  2. Verify all 20 YouTube Shorts in `championship_entries` show `submitted` and check validated views on Real Oficial dashboard.
  3. Log top 3 retention winners into `memory/learning_loop.md`.
