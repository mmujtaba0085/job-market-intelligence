"""
Apify Actor: Global Tech Jobs
──────────────────────────────
Pulls every job EXCEPT the Pakistan feed (exclude_market=pakistan_jobs_all -
currently AI/ML Global + Software Engineering & Backend Global) from the Job
Market Intelligence API and pushes them to this run's Apify dataset.

The API itself does all the work (collection, cleaning, dedup) - this actor
is a thin, paginated proxy so the data is discoverable/consumable through
Apify's platform (dataset export, scheduling, integrations) as well.
"""

import requests
from apify import Actor

_MARKET_PARAMS = {"exclude_market": "pakistan_jobs_all"}
_PAGE_SIZE = 100


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        api_key = actor_input.get("apiKey")
        if not api_key:
            raise ValueError(
                "apiKey is required - generate one at "
                "https://jobs.mujtaba0085.opior.com/auth/me/keys"
            )

        base_url = (actor_input.get("baseUrl") or "https://jobs.mujtaba0085.opior.com").rstrip("/")
        max_items = actor_input.get("maxItems", 1000)

        headers = {"X-API-Key": api_key}
        offset = 0
        pushed = 0

        while True:
            if max_items and pushed >= max_items:
                Actor.log.info(f"Reached maxItems ({max_items}), stopping.")
                break

            limit = _PAGE_SIZE if not max_items else min(_PAGE_SIZE, max_items - pushed)
            params = {**_MARKET_PARAMS, "limit": limit, "offset": offset}

            try:
                resp = requests.get(f"{base_url}/api/jobs", headers=headers, params=params, timeout=30)
            except requests.RequestException as exc:
                Actor.log.error(f"Request failed: {exc}")
                break

            if resp.status_code == 401:
                raise ValueError("API key was rejected (401) - check it's correct and not revoked.")
            if resp.status_code == 403:
                raise ValueError("API key doesn't have the jobs:read scope (403).")
            if resp.status_code == 429:
                Actor.log.warning("Rate limited (429) by the API - stopping this run early.")
                break
            if resp.status_code != 200:
                Actor.log.error(f"API error {resp.status_code}: {resp.text[:300]}")
                break

            jobs = resp.json().get("jobs", [])
            if not jobs:
                Actor.log.info("No more jobs - reached the end of the feed.")
                break

            await Actor.push_data(jobs)
            pushed += len(jobs)
            offset += limit
            Actor.log.info(f"Pushed {pushed} jobs so far...")

        Actor.log.info(f"Done. Total jobs pushed: {pushed}")
