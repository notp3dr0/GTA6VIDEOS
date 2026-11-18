# Performance & Creative Learning Loop

## 1. North-Star KPIs by Platform

| Platform | Primary Retention KPI | Engagement KPI | Target Threshold | Action if Below Target |
|----------|-----------------------|----------------|------------------|------------------------|
| **YouTube Shorts** | Viewed vs. Swiped Away | Average % Viewed (APV) | `>= 72%` Viewed / `>= 95%` APV | Shorten clip from `28s` to `18–22s`; tighten 0–2s visual hook banner. |
| **Real Oficial (`gta-vi-youtube`)** | Validated Campaign Views | Effective CPM (`R$/1k`) | `100%` rule pass / `>= R$ 1.25` CPM | Audit `#GTA6 #GTAVI #Shorts` tags, `-14 LUFS` audio, and acervo segment novelty. |
| **Instagram Reels** | Average Watch Time | Saves + Shares / Reach | `>= 2.5%` Save+Share rate | Increase `tips_tutorials` and `spotlights` frame-by-frame comparisons. |
| **TikTok** | 2-Second & 6-Second Hold | Comments per 1k views | `>= 65%` 2s hold / `>= 8` comments/1k | End voiceover with a concrete A/B question that loops seamlessly into frame 1. |
| **X (Twitter)** | Video 50%+ View Rate | Bookmarks + Replies | `>= 1.5%` engagement rate | Keep headline `<= 75` chars, use 3 crisp bullets, zero hashtags. |

## 2. Creative Winning Patterns & Guardrails

### High-Performing Hook Archetypes (`pt-BR` & `en-US`)
1. **Micro-Detail Zoom (`spotlights` / `official_news`)**:
   - `pt-BR`: *"99% ignoraram esse reflexo em Vice City no Trailer 2"*
   - `en-US`: *"Almost everyone missed this Vice City reflection in Trailer 2"*
2. **Physics & Evolution Contrast (`gta5_nostalgia`)**:
   - `pt-BR`: *"13 anos de evolução: GTA 5 vs GTA 6 na mesma cena"*
   - `en-US`: *"13 years of Rockstar physics: GTA V vs GTA VI side by side"*
3. **Actionable Mechanic (`tips_tutorials`)**:
   - `pt-BR`: *"Ajuste isso antes de dirigir em alta velocidade em Leonida"*
   - `en-US`: *"Change this driving habit before hitting Leonida highways"*

### Retired / Low-Retention Patterns (Do Not Repeat)
- Slow fade-in intros or Rockstar logo cold opens (`> 1.0s` without motion) — causes >45% immediate swipe-away.
- Generic hooks (*"Veja esse vídeo incrível de GTA 6"*) — low curiosity gap; always name the specific mechanic, character, or region.
- Captions exceeding 5 hashtags on Reels/Shorts — dilutes topic indexing and looks spammy.

## 3. Weekly Experiment & Iteration Log

| Week / Window | Hypothesis Tested | Pillar / Segment | Result Status | System Parameter Adjustment |
|---------------|-------------------|------------------|---------------|-----------------------------|
| **Baseline (2026-W47)** | `20–26s` multi-segment Trailer 1 & 2 cuts with `pt-BR` voiceover + burned ASS captions outperform `45s+` cuts on Shorts APV. | `real_oficial_campaign` | Active Baseline | `default_clip_duration_sec: 26`, `hook_duration_sec: 2.5`, `loudnorm_lufs: -14.0`. |
| **Exp-01 (5-Day Kickoff)** | Compare Leonida Regional Spotlights (`Vice City` vs `Grassrivers`) at `14:00` vs `21:30` BRT. | `spotlights` | Queued | Re-weight top 2 regions in `ScoutAgent` virality scoring after Day 5 audit. |
| **Exp-02 (Launch Pivot)** | Compare `GTA 5 vs GTA 6` nostalgia hooks against pure `tips_tutorials` on Reels save rate. | `gta5_nostalgia` / `tips_tutorials` | Queued | Shift Reels Slot 1 (`12:00 BRT`) toward highest save-rate pillar. |

## 4. Automated Deduplication & Freshness Rules
- **Source Ledger (`source_ledger.jsonl` + `seen_sources`)**: Never reuse the exact same third-party clip URL; for official campaign acervo trailers, track granular `#start-end` segment keys so every Short covers a distinct scene.
- **7-Day Caption Deduplication**: `ComplianceFactAgent` compares every generated caption against `StateStore.recent_captions(days=7)` and blocks near-duplicate copy (`>= 88%` normalized similarity) before rendering or publishing.
## [2026-11-18 14:02:52 -03] Learning Cycle Insight
- **Total Publications**: 8 (`x`: 2, `tiktok`: 2, `instagram_reels`: 2, `youtube_shorts`: 2)
- **Pillar Distribution**: `real_oficial_campaign`: 8
- **Real Oficial (`gta-vi-youtube`)**: 2 entries | **0** validated views | **R$ 0.00** est. earnings (avg CPM R$ 1.25)
- **Credits Today**: 0/150 | **Pending Approvals**: 0
- **Next-Cycle Adjustments**:
  - Prioritize YouTube Shorts cadence (2/4 posted so far) at BRT peak windows (10:00, 14:00, 18:00, 21:30) for 'gta-vi-youtube'.
  - Diversify upcoming slots with underrepresented pillars: official_news, tips_tutorials, gameplay_clips.

