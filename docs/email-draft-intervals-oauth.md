# Email Draft: Intervals.icu OAuth App Registration

**To:** david@intervals.icu
**Subject:** OAuth App Registration — EndurAI (AI Triathlon Coach)

---

Hi David,

I'd like to register an OAuth application for Intervals.icu.

**App name:** EndurAI

**Description:** AI-powered triathlon coaching assistant. EndurAI syncs wellness, activities, and workout data from Intervals.icu, analyzes recovery (HRV, RHR, CTL/ATL/TSB), and provides personalized training recommendations via Telegram bot and a web dashboard. It also adapts scheduled workouts based on daily readiness and pushes AI-generated workouts back to the athlete's Intervals.icu calendar.

**What we use from the API:**
- Read: wellness, activities (including FIT files), scheduled workouts/events, athlete settings (zones, thresholds)
- Write: create/update events (AI-adapted workouts) with `external_id` for idempotent upserts

**Website URL:** https://endurai.me

**Logo:** *(attach endurai-icon-B.png — 512×512 square)*

**Privacy Policy URL:** https://endurai.me/privacy

**Redirect URI(s):**
- `https://endurai.me/api/intervals/auth/callback` *(production)*
- `http://localhost:8000/api/intervals/auth/callback` *(development)*

Currently we have a working multi-tenant system with 2 athletes, using API keys. OAuth would make onboarding much smoother — athletes could connect their account with one click instead of manually copying API keys.

Thank you!
Radik Khaziev
https://endurai.me

---

## Notes (do not include in email)
- Attach `docs/logos/endurai-icon-B.png` as the logo image
- `http://localhost/` is always allowed by default, but specifying port is safer
