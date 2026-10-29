# Bots: named helpers that work on their own

A **bot** is a durable helper with a name, a picture, a job, its own conversation and memory, and
routines it runs without being asked. You message it like a colleague; it comes back with results,
and with approvals when something needs you. Every tool call a bot makes goes through Foyelle's
policy, each bot uses only the services it is given, and nothing that sends, deletes or changes
the computer runs while nobody watches.

## What a bot is

A bot is a **Hermes profile** (`<HERMES_HOME>/profiles/<id>/`): its own persona (SOUL.md, written
from the bot's job), memory, skills, sessions and cron store. One `hermes serve` process serves
every profile; the canvas passes `profile` on each call. Foyelle itself is the default profile and
is always there.

What Foyelle adds, kept by the Shell helper in `bots.json` (its state directory, which the policy
plugin can read):

| Field | Meaning |
| --- | --- |
| `id` | the profile name (`travel`, `household`; lowercase, from the name) |
| `name`, `picture` | shown in the sidebar: a name and an emoji or a colour initial |
| `job` | one line ("Watches flight prices for our trips"), also the persona's first line |
| `services` | the connectors this bot may use; nothing else exists for it |
| `shareOnline` | per service: may this bot's routines send what it reads to the online model |

## Fail-closed policy for every profile

Hermes loads plugins per profile, so a new profile would run without Foyelle's policy. Two things
close that:

1. The policy plugin ships inside the harness package as a bundled plugin, and a Foyelle patch
   makes `HERMES_REQUIRED_PLUGINS` (set to `foyelle-policy` by the harness service) enabled in
   every profile whatever its config says.
2. The same patch blocks every tool call in a profile where a required plugin did not load.

Each profile's config gets Foyelle's enforced keys (toolsets, MCP servers, approvals) from the
config merge at start and when a bot is created.

## Services per bot

The policy refuses a connector the bot was not given, and the per-conversation tool filter (the
`llm_request` middleware) never shows it to the model. A new bot starts with none; the person
picks them when creating it or later on the bot's details. Foyelle (the default profile) keeps
every connected service.

## Routines

A routine is a Hermes cron job in the bot's profile. `hermes serve` does not run the cron
scheduler unless it is the Desktop app's own backend; a Foyelle patch starts the same ticker when
`HERMES_SERVE_CRON=1`, so runs happen inside the harness process, under the policy plugin.

- **Made by asking** the bot ("every morning at 7:30, tell me what's on today"). The `cronjob`
  toolset is on; `cronjob_manage` is a system-tier tool, so creating, changing or removing a
  routine shows an approval card that says the schedule and the prompt in words. Nothing is
  scheduled before that yes.
- **Shown on the bot's details**: each routine with its schedule, last run and pause/delete.
- **Results**: every run is a `cron` session in the bot's profile. The canvas shows each run in
  the bot's conversation as a note ("Morning brief · 07:30") that opens the run.

## The policy for background runs

A cron run (`HERMES_CRON_SESSION`) has the origin **background**:

1. Only the bot's services exist (as for every run of that bot).
2. Read tools run. A private connector's content goes to the online model only when the person
   allowed it for that bot (`shareOnline`); otherwise the call is refused and the note says why. A
   local model needs no such consent.
3. Reversible, outbound and system tools never run in the background. The call is turned into a
   **waiting item** ("Waiting for you: add 'Pay the invoice' to Todoist"); the run is told it is
   waiting and says so in its result. The person approves or drops it from the bot's
   conversation; approving runs it with the person as the origin, through the usual approval card.
4. Untrusted content still taints: a tainted run can't send anything out, waiting item or not.
5. Every background call is audited with the origin `background` and the job id.

Heartbeat turns (`/heartbeat`, a recurring prompt in one conversation) start with Hermes's
"[Heartbeat — recurring instruction" marker; their calls are treated like background calls.

Waiting items live in the Shell helper's state (`waiting.json`), written by the policy plugin;
they expire after a day.

## The canvas

- **Sidebar of bots** (left): Foyelle first, then the person's bots; each row with picture, name,
  the last line from it, and a dot when it has unseen results or something waiting.
  "+ New bot" at the top.
- **The conversation** (middle): the selected bot's chat, with routine results as notes and
  waiting items as cards (Approve / Drop). The header shows the bot's picture, name and job; a
  click opens its **details**: job, services, routines.
- **Cards** (right): Foyelle's surfaces, in Foyelle's own look (themes, fonts and the ember).
- **New bot**: name, picture, job, services. Creating it makes the profile (cloned from the default
  profile's config, persona from the job) and its Shell entry.

A card's call goes through the conversation it opened in, and a harness patch runs
`surface.call_tool` in that session's profile (after the MCP discovery a routine run does first),
so a card in a bot's conversation is decided as that bot's. Approve on a waiting item runs as the
bot whose routine left it. The canvas's own controls (settings, accounts, the bots, what waits) go
through Foyelle's session.
