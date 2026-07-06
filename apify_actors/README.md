# Apify Actors — Job Market Intelligence

Two actors, both thin proxies over our own `/api/jobs` endpoint (see [/api/docs](https://jobs.mujtaba0085.opior.com/api/docs)
on the live site for the full API reference):

- **`pakistan-jobs/`** — everything from the `pakistan_jobs_all` market.
- **`global-tech-jobs/`** — everything else (currently AI/ML Global + Software Engineering & Backend Global).

Neither actor scrapes anything itself — collection, cleaning, and dedup already happen on our own
pipeline. Each actor just authenticates with an API key, pages through `/api/jobs`, and pushes the
results into that run's Apify dataset, so the data is consumable through Apify's platform too
(dataset export, scheduling, webhooks, integrations).

---

## 1. One-time setup (you haven't done this before, so start here)

1. **Create an Apify account** — go to [apify.com](https://apify.com), sign up (free tier is enough
   to publish and run these actors; it just meters usage on compute-unit hours).
2. **Install Node.js** if you don't have it — the Apify CLI itself is Node-based even though these
   actors are Python. Get it from [nodejs.org](https://nodejs.org) (LTS version).
3. **Install the Apify CLI:**
   ```bash
   npm install -g apify-cli
   ```
4. **Log in:**
   ```bash
   apify login
   ```
   This opens a browser to authorize the CLI against your new Apify account.

## 2. Get a Job Market Intelligence API key

Log into the site and go to **[/auth/me/keys](https://jobs.mujtaba0085.opior.com/auth/me/keys)** —
no admin approval needed, it's self-service. Create a key with the `jobs:read` scope. You'll paste
this into each actor's input when you run it (Apify keeps it encrypted; it's marked `isSecret` in
both actors' input schemas so it never shows up in run logs).

## 3. Push each actor

From inside each actor's own directory (they're independent projects — push them separately):

```bash
cd apify_actors/pakistan-jobs
apify push
```

```bash
cd apify_actors/global-tech-jobs
apify push
```

`apify push` builds the Docker image on Apify's infrastructure and creates (or updates) the actor
under your account. First push may take a couple of minutes while it builds.

## 4. Test a run

Either from the Apify Console (after pushing, open the actor there → **Start**, fill in the `apiKey`
field) or locally before pushing:

```bash
cd apify_actors/pakistan-jobs
apify run --input='{"apiKey": "jmi_your_key_here", "maxItems": 20}'
```

Check the run's **Dataset** tab (or `apify run`'s local output) for the pushed job records.

## 5. Optional: schedule it

In the Apify Console, open the actor → **Schedules** → create one (e.g. daily) so it stays in sync
with our pipeline automatically, instead of running it by hand each time.

---

## Input fields (same for both actors)

| Field | Required | Default | Description |
|---|---|---|---|
| `apiKey` | Yes | — | Your Job Market Intelligence API key (`jobs:read` scope). Kept secret. |
| `maxItems` | No | 1000 | Stop after this many jobs. `0` = fetch everything available. |
| `baseUrl` | No | `https://jobs.mujtaba0085.opior.com` | Only change if the site moves. |

## A note on `source_name`

If the site's admin has the "show source names" display setting turned off, you'll see generic
labels like `"Source A"` instead of the real source (Himalayas, Arbeitnow, Pakistan Jobs Bank, etc.)
in the `source_name` field. This is intentional — the underlying job data (title, company, location,
URL, etc.) is unaffected either way.
