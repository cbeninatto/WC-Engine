# WC Engine — Project Brief & Current-State Design

A hand-off document for a designer. Part 1 explains *what the product is and who
uses it*. Part 2–4 document the *layout and visual language as currently built*, so
the current state can be explained before a new design system is introduced.

> Source of truth for this doc: [webapp/index.html](../webapp/index.html) (the entire UI)
> and [app.py](../app.py) (the JSON the UI renders). It is a single-page app.

---

## Part 1 — Project Brief

### One-liner
A **World Cup 2026 prediction dashboard and control panel**. It forecasts match
outcomes with a transparent statistical model, grades those forecasts against real
results, and lets the user play a fantasy-style scoreline game *against the engine*.

### What it does
- **Predicts** every fixture: a Home / Draw / Away probability split plus a projected
  scoreline (e.g. 2–1), derived from a "power rating" computed for each team.
- **Tracks results**: pulls finished scores from a real data feed, updates the group
  standings and re-rates teams.
- **Grades itself**: shows how accurate the forecasts have been (Brier / log-loss / RPS
  vs a naive ⅓-guess baseline) on the Performance tab.
- **You vs the engine**: the user enters their own predicted scorelines; both the user's
  and the engine's picks are scored by a fantasy point ladder (exact / win+margin /
  win+goals / winner / miss) and compared head-to-head.
- **Human-in-the-loop changes**: background "agents" propose changes to the model
  (re-tuning, schedule-strength updates, team-form rebuilds). These land in a
  **Proposals** queue where the user approves or rejects each one. Nothing about the
  model changes without an explicit click.

### Who uses it
A single power-user (the owner) running it locally. It is a personal analyst's
cockpit, not a consumer product — so density and information richness are valued over
hand-holding. There is no auth, no multi-user, no onboarding.

### The mental model a designer should hold
1. **Power rating → probabilities.** Each team has a number ("power"). The gap between
   two teams' numbers becomes a win/draw/loss probability and an expected score.
2. **Prior vs live.** A team starts on a pre-tournament "prior" rating and drifts as
   real results come in. The dashboard shows both and the delta (Δ).
3. **Agents propose, the user approves.** This is the product's core trust principle and
   it should remain visible in any redesign — the Proposals queue and the
   "agents propose, you approve" footer are load-bearing, not decoration.
4. **Two scoreboards.** "Engine accuracy" (is the model good?) and "fantasy points"
   (am I beating the model?) are different questions shown in the same Performance tab.

### Tech context that constrains design
- **Dark mode only**, hard-coded (`<html class="dark">`). No light theme exists.
- **Tailwind CSS via CDN**, configured inline in the HTML head. No build step, no
  component library, no design tokens file yet — all styling is utility classes written
  directly in markup and in JS template strings.
- **Default system font stack** (Tailwind's default). No custom typeface is loaded.
- The whole UI is **one `index.html`** (~580 lines) rendered from a single `/api/state`
  JSON payload plus a `/api/scoreboard` payload for the Performance tab. A redesign that
  keeps this single-file, no-build approach will be fastest to implement.

---

## Part 2 — Information Architecture

Single page, three tabs (client-side switch, no routing):

```
WC Engine
├── Dashboard      ← default. The "now" view: standings, upcoming odds, ratings, results
├── 🎯 Performance ← scorekeeping: engine accuracy + you-vs-engine fantasy + pick editor
└── ⚑ Proposals    ← approval queue (badge shows count of pending items)
```

A persistent **header** (title + date + Refresh) and **footer** sit outside the tabs.

---

## Part 3 — Screen-by-Screen Layout (as built)

All content is centered in a `max-w-6xl` column (~1152px) with `p-5` padding.

### Header (persistent)
```
┌──────────────────────────────────────────────────────────────────────┐
│ ⚽ WC Engine                                            [ ↻ Refresh ]  │
│ World Cup 2026 prediction dashboard · today 2026-06-16                 │
├────────────────────────────────────────────────────────────────────── │
│ [Dashboard]  🎯 Performance   ⚑ Proposals(0)        ← tab bar         │
└──────────────────────────────────────────────────────────────────────┘
```
Active tab = sky underline + white text; inactive = grey, no underline. The Proposals
tab carries a **rose count badge** when items are pending (hidden at 0).

### Tab 1 — Dashboard
```
[ ⤓ Run results monitor ]  [ ↻ Recompute predictions ]   <status message>

┌─────────┬─────────┬─────────┬─────────┐   ← 4 stat cards (2-col on mobile)
│Results  │Pending  │Overdue  │Proposals│      Overdue turns amber if >0
│  in  N  │   N     │   N     │   N     │      Proposals turns rose + is clickable
└─────────┴─────────┴─────────┴─────────┘

Group standings                              ← heading
┌──────────────┐ ┌──────────────┐ ┌──────────────┐   (1 / 2 / 3 cols responsive)
│ Group A      │ │ Group B      │ │ Group C      │
│ Team P W D L │ │ ...          │ │ ...          │   top-2 rows tinted green
│ GD Pts       │ │              │ │              │   (qualification zone)
└──────────────┘ └──────────────┘ └──────────────┘

Upcoming — model probabilities               ← heading
┌──────────────────────────────────────────────────────────┐
│ 18:00 · Group A   Brazil vs Serbia              proj 2–1  │
│ ┌─────────┐ ┌─────────┐ ┌─────────┐                       │
│ │● Brazil │ │● Draw   │ │● Serbia │   ← 3 prob pills,      │
│ │  62%    │ │  22%    │ │  16%    │     favorite is ringed │
│ └─────────┘ └─────────┘ └─────────┘                       │
│ ▓▓▓▓▓▓▓▓░░░░  ← stacked bar (sky / slate / rose)          │
└──────────────────────────────────────────────────────────┘   (up to 12 cards)

┌───────────────────────────┐ ┌───────────────────────────┐   ← 2-col on desktop
│ Power ratings             │ │ Results                   │
│ #  Team        Prior Pwr Δ│ │ 18:00·A Brazil 2–1 Serbia │   (scrollable list,
│ 1  Brazil (..)  88.1 90.2 │ │ ...                       │    source tag at right)
│ ...                       │ │                           │
└───────────────────────────┘ └───────────────────────────┘

Local engine · reads wc.db · agents propose, you approve   ← footer
```

### Tab 2 — 🎯 Performance
```
Fantasy points — you vs the engine
Your scoreline picks scored by league rules, alongside the engine's own pick.
┌──────────┐ ┌──────────┐ ┌──────────┐    ← 3 summary cards
│ You      │ │ Engine   │ │ Your lead│      big numbers, colored by who's ahead
│ 47 pts   │ │ 39 pts   │ │ +8 pts   │
└──────────┘ └──────────┘ └──────────┘
┌──────────────────────────────────────────────────────────┐
│ Match              You          Final   Engine            │
│ Brazil v Serbia    2–1 [9·Win]  2–1     2–0 [7·Win+goals] │  ← category chips
│ ...                                                       │
└──────────────────────────────────────────────────────────┘

Engine forecast accuracy
How W/D/L probabilities grade vs a naive ⅓-guess. Lower Brier/log-loss/RPS is better.
┌────────┬────────┬────────┬────────┬────────┬────────┐  ← 6 metric cards
│Winner  │Exact   │Brier   │Log-loss│ RPS    │Draws   │
│acc 71% │scores 3│0.182   │0.531   │0.141   │ 4/6    │     green/red vs baseline
└────────┴────────┴────────┴────────┴────────┴────────┘

Your picks — upcoming fixtures
Enter a scoreline for each match; saved instantly. "proj" is the engine's suggestion.
┌──────────────────────────────────────────────────────────┐
│ 18:00     Brazil v Serbia    [ 2 ]–[ 1 ]  proj 2–1  [Save]│  ← number inputs
│ Grp A                                                     │     + inline save state
└──────────────────────────────────────────────────────────┘
```

### Tab 3 — ⚑ Proposals
```
⚑ Proposals awaiting approval
Agent-proposed changes. Approve to commit, reject to discard.
<status message line>
┌──────────────────────────────────────────────────────────────────┐
│ [MODEL TUNING]  Retune model parameters (v4)        [ Approve ]   │  ← colored
│ Optimize the power formula for log-loss on 64 past…  [ Reject  ]   │    kind badge
│ ┌─────┬─────┬─────┐                                                │
│ │Brier│Log  │RPS  │  ← before → after, green if improved          │
│ │.19→ │.55→ │.14→ │                                                │
│ └─────┴─────┴─────┘                                                │
│ ▸ New knob values (7)        ← collapsible details                 │
│ tuner · 2026-06-16 10:22                                           │
└──────────────────────────────────────────────────────────────────┘
```
Proposal kinds (each with its own badge color): **Model tuning** (amber),
**Schedule strength** (sky), **Team form** (violet), **Data audit** (slate). Repeat
runs of the same agent get an "older run" tag.

---

## Part 4 — Current Visual Language (design tokens as built)

These are the values literally in the code today — the baseline the new design system
will replace or formalize.

### Color
| Role | Token / class | Hex | Used for |
|------|---------------|-----|----------|
| App background | `ink` | `#0b0f17` | page background |
| Surface | `panel` | `#131a26` | cards, tables, inputs |
| Border | `edge` | `#22304a` | all borders, dividers |
| Primary accent | `sky-500/600` | Tailwind | primary buttons, active tab, home-win, links |
| Positive | `emerald-400/600` | Tailwind | approve, improvements, qualification zone, "exact" |
| Negative | `rose-400/500/600` | Tailwind | away-win, regressions, badges, lead-behind |
| Warning | `amber-300/400/600` | Tailwind | overdue, model-tuning badge, "older run" |
| Engine / team-form | `violet-300/500` | Tailwind | engine fantasy, team-form proposals |
| Draw / neutral | `slate-300/400/500/600` | Tailwind | draw outcome, muted text, reject button |

Text hierarchy: `white` (emphasis) › `slate-200` (body) › `slate-400` (labels) ›
`slate-500/600` (meta, faint). Numbers use `tabular-nums`.

### Typography
- **Family:** Tailwind default sans (system UI stack). No web font loaded.
- **Scale in use:** `text-2xl` bold (page title), `text-lg` semibold (section
  headings), `text-sm` (body, the default), `text-xs` / `text-[10px]/[11px]` (meta,
  chips, badges). Big stat numbers: `text-3xl`/`text-4xl` bold.

### Surfaces, spacing, shape
- Cards/panels: `bg-panel border border-edge` with `rounded-xl` (12px); smaller
  elements `rounded-lg`/`rounded-md`. Padding usually `p-4`.
- Vertical rhythm: sections separated by `mb-5`/`mb-6`; grids gap `3`/`4`/`6`.
- Container: `max-w-6xl` centered.

### Components present (informal — no library)
Buttons (primary filled / secondary outline), tab bar, stat card, metric card,
points card, data tables (standings, ratings, fantasy), probability pill, stacked
probability bar, category/kind chips, collapsible `<details>` blocks, number-input
score editor, status-message inline text, count badge.

### Interaction & states
- Hover: borders shift to `sky-500` on interactive cards/buttons.
- Active tab: sky underline + white text.
- Inline async feedback everywhere (`Working…` → `✓ saved` / `✗ error`), green/red.
- Favorite outcome gets a colored `ring-1`; qualification rows get a green tint;
  overdue items get an amber border + ⏳.
- Emoji are used as iconography (⚽ ↻ ⤓ 🎯 ⚑ ⏳ ✓ ✗). There is no icon set.

---

## Part 5 — Notes & Opportunities for the Designer

Honest read of the current state — these are candidates, not requirements.

- **No formal design tokens.** Colors/spacing/type are repeated inline across markup and
  JS strings. A tokenized system (CSS variables or a Tailwind config block) would make a
  redesign maintainable. Note ~half the colors (sky/emerald/rose/amber/violet) are raw
  Tailwind defaults, only three (`ink`/`panel`/`edge`) are custom.
- **Dark-only.** If a light theme is wanted, it's net-new; nothing today is theme-aware.
- **Emoji-as-icons.** Inconsistent rendering across platforms; a real icon set is an easy win.
- **Density vs. hierarchy.** The Dashboard packs five sections; the visual weight between
  "glance" data (stat cards, odds) and "reference" data (full ratings table) is fairly
  flat. Prioritization is an opportunity.
- **System font.** A typeface choice would do a lot for perceived quality, especially for
  the heavy use of numbers (a font with good `tabular-nums` matters here).
- **Color carries meaning.** sky=home, slate=draw, rose=away, and green=better/positive
  recur throughout (odds, metrics, proposals). Keep this semantic mapping coherent in any
  new palette — it's the product's visual grammar.
- **Mobile.** Layouts use responsive grids but the experience is desktop-first (dense
  tables, multi-column). Worth deciding how much mobile matters.
```
