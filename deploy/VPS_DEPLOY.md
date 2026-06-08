# VPS Deployment Guide — Job Market Intelligence

## Overview

| Layer | Tool | Role |
|---|---|---|
| App | Gunicorn + Flask | Business logic, auth, APIs |
| Reverse proxy | Caddy | TLS (auto HTTPS), headers |
| Container | Docker + docker-compose | Isolation, restarts |
| Auth DB | SQLite (`data/auth.sqlite`) | Users, API keys, access logs |

---

## 1. Prerequisites on VPS

```bash
# Ubuntu 22+ / Debian 12+
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git curl

# Install Caddy
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy.gpg
echo "deb [signed-by=/usr/share/keyrings/caddy.gpg] https://dl.cloudsmith.io/public/caddy/stable/debian.bullseye main" | sudo tee /etc/apt/sources.list.d/caddy.list
sudo apt update && sudo apt install caddy
```

---

## 2. Prepare directories

```bash
sudo mkdir -p /opt/jobmarket/{data,outputs,logs,secrets}
sudo chown -R $USER:$USER /opt/jobmarket
```

---

## 3. Configure environment

```bash
cp .env.example /opt/jobmarket/.env
nano /opt/jobmarket/.env
```

**Critical values to set:**

```bash
# Generate a strong secret key:
python3 -c "import secrets; print(secrets.token_hex(32))"

FLASK_SECRET_KEY=<paste output above>
ADMIN_PASSWORD=<choose a strong password>
SESSION_COOKIE_SECURE=true
TRUST_PROXY_HEADERS=true
JOBMARKET_HOME=/opt/jobmarket
```

---

## 4. Configure Caddy

Edit `deploy/Caddyfile` — replace `jobs.example.com` with your real domain:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
# Edit:
sudo nano /etc/caddy/Caddyfile
# Replace jobs.example.com → your domain

sudo systemctl reload caddy
```

> **DNS**: Point your domain's A record to your VPS IP before starting Caddy.

---

## 5. Build and run

```bash
# From your project root:
docker compose build
docker compose up -d

# Check status:
docker compose ps
docker compose logs -f web
```

---

## 6. First login

1. Open `https://your-domain.com` → redirects to `/auth/login`
2. Sign in with:
   - **Username:** `admin`
   - **Password:** the `ADMIN_PASSWORD` you set in `.env`
3. **Immediately** go to `/admin/auth/users` and:
   - Change the admin password
   - Create user accounts for anyone who needs access

---

## 7. Issue API keys

- Admin: `/admin/auth/keys` → New API Key → assign to user → set rate limit
- User (self-service): `/auth/me/keys` → New Key

API key usage:
```bash
# Header (recommended)
curl -H "X-API-Key: jmi_<your-key>" https://your-domain.com/api/dashboard/kpis

# Bearer token
curl -H "Authorization: Bearer jmi_<your-key>" https://your-domain.com/api/jobs

```

API keys are read-only, accepted only in headers, and limited by their assigned scopes.

---

## 8. Monitor access

- **Access logs:** `/admin/auth/logs`
- **User accounts:** `/admin/auth/users` (enable/disable, reset passwords)
- **API keys:** `/admin/auth/keys` (revoke, adjust rate limits)

---

## 9. Backups

```bash
# Creates consistent SQLite snapshots for jobs.sqlite and auth.sqlite:
bash deploy/scripts/backup_sqlite.sh
```

Example cron schedule:

```cron
15 2 * * * /opt/jobmarket/deploy/cron/backup_daily.sh
30 2 * * * /opt/jobmarket/deploy/cron/ingest_daily.sh
0 4 * * 1 /opt/jobmarket/deploy/cron/report_weekly.sh
```

---

## 10. Useful commands

```bash
# Restart the app
docker compose restart web

# View app logs
docker compose logs -f web

# Run the data pipeline
docker compose --profile jobs run --rm pipeline

# Build and validate a conservative shadow warehouse before promotion
docker compose --profile jobs run --rm pipeline python scripts/warehouse_rollout.py

# Open a shell inside the container
docker compose exec web bash

# Rebuild after code changes
docker compose build web && docker compose up -d web
```

---

## Security checklist

- [ ] `FLASK_SECRET_KEY` is a random 32+ byte hex string
- [ ] `ADMIN_PASSWORD` is strong and changed after first login
- [ ] Domain A record points to VPS
- [ ] Caddy is running and HTTPS is working
- [ ] `SESSION_COOKIE_SECURE=true` and `TRUST_PROXY_HEADERS=true`
- [ ] `/opt/jobmarket/.env` is readable only by root / deploy user
- [ ] Firewall: only ports 80, 443, and 22 (SSH) are open
- [ ] Regular DB backups scheduled (`cron` or `systemd timer`)
