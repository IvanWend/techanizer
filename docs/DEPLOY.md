# DEPLOY — runbook for the Aliyun HK VM (techanizer.tech)

Target: Aliyun HK trial ECS 2C/2G (47.86.2.120), root ssh verified, Docker 29 +
compose preinstalled. Going live additionally needs the domain serverHold
cleared and the staged A-records (@/www → 47.86.2.120) resolving.

Every command runs where it says: **dev** = the dev box, **VM** =
`ssh root@47.86.2.120`. v2rayA tproxy stays OFF while working on the VM.
A local `sudo -i` shell looks identical to an ssh one — when output surprises
you, `whoami; hostname` before anything else.

## 1. VM prep (once)

```bash
# VM — 2G RAM is tight for the Docker build; swap first.
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
sysctl vm.swappiness=10

# Containers must come back after a VM reboot.
systemctl enable --now docker
```

## 2. Code + .env

```bash
# VM
git clone https://github.com/IvanWend/techanizer.git /opt/techanizer
cd /opt/techanizer

# Two secrets generated fresh for prod — don't reuse dev values.
openssl rand -hex 32   # → POSTGRES_PASSWORD
openssl rand -hex 32   # → ASK_API_KEY
```

Write `/opt/techanizer/.env` — the three vars compose reads (values are **raw**:
no quotes around the hex — compose strips quotes when interpolating, but a
`grep` of the value for smoke curls doesn't, and the mismatch 401s):

```dotenv
POSTGRES_PASSWORD=<generated>
ASK_API_KEY=<generated>
ALIBABA_API_KEY=<the LOW-QUOTA Bailian key>
# optional override, default is 10:
# RATE_LIMIT_MAX=10
```

`ASK_API_KEY` is baked into the frontend bundle at build time, so it is
browser-visible — by design (DECISIONS "Live deployment cut"). The spend guard
is the low-quota DashScope key + the rate limit, not key secrecy.

## 3. Data: dump live → restore

```bash
# DEV — role and db passed explicitly, so the empty-$DATABASE_URL pg_dump
# trap (AGENTS "Watch out") can't bite; --no-owner/--no-privileges keep the
# dump free of dev-cluster roles (techanizer_ro) that don't exist on the VM.
cd ~/Projects/techanizer
docker exec techanizer-db-1 pg_dump -U techanizer --no-owner --no-privileges \
  techanizer > /tmp/opencode/live_$(date +%F).sql
scp /tmp/opencode/live_*.sql root@47.86.2.120:/opt/techanizer/
```

```bash
# VM — db up first (init/ creates the extensions); --wait because a fresh
# container runs initdb for a few seconds before accepting connections;
# then restore.
cd /opt/techanizer
docker compose -f compose.prod.yml up -d --wait db
docker compose -f compose.prod.yml exec -T db \
  psql -U techanizer -v ON_ERROR_STOP=1 -d techanizer < live_*.sql
# sanity: counts must match the dev box (there is no `postings` table —
# the corpus is raw_postings + structured_postings)
docker compose -f compose.prod.yml exec db \
  psql -U techanizer -d techanizer -t \
  -c 'SELECT count(*) FROM raw_postings;' \
  -c 'SELECT count(*) FROM structured_postings;'
```

## 4. Build + start

```bash
# VM — the build takes minutes on 2C2G; the npm/uv cache mounts make
# rebuilds cheap. Reads .env for the VITE_ASK_API_KEY build arg.
docker compose -f compose.prod.yml build
docker compose -f compose.prod.yml up -d
docker compose -f compose.prod.yml ps   # db healthy → app healthy → caddy up
```

## 5. Smoke

DNS is not live yet, so `--resolve` pins the hostname to the VM itself and
`-k` accepts the `tls internal` self-signed cert:

```bash
# VM
ASK=$(grep -oP '(?<=^ASK_API_KEY=).*' .env)

curl -k --resolve techanizer.tech:443:127.0.0.1 https://techanizer.tech/health
# → {"status":"ok"}

# UI served when this returns HTML (a <div id="root"> page), not an error:
curl -k --resolve techanizer.tech:443:127.0.0.1 https://techanizer.tech/ | head -5
```

Four canned queries (2 EN, 2 RU) — each must stream SSE frames and finish with
an answer:

```bash
ask() { curl -sN -k --resolve techanizer.tech:443:127.0.0.1 \
  -X POST https://techanizer.tech/ask \
  -H "Authorization: Bearer $ASK" -H 'Content-Type: application/json' \
  -d "{\"question\":\"$1\",\"lang\":\"$2\"}"; }

ask "What are the most frequently mentioned programming languages in remote job postings?" en
ask "What is the median salary for senior backend roles, and how many postings is that based on?" en
ask "Какие навыки чаще всего встречаются в удалённых вакансиях для фронтенд-разработчиков?" ru
ask "Найди вакансии, связанные с Rust и распределёнными системами." ru
```

From your laptop once DNS is live: the same curls without `--resolve`/`-k`, and
the browser at https://techanizer.tech.

## 6. TLS flip (when the serverHold clears)

```bash
# VM
dig +short techanizer.tech          # 1. must answer 47.86.2.120
# 2. delete both `tls internal` lines from Caddyfile
docker compose -f compose.prod.yml restart caddy   # 3.
docker compose -f compose.prod.yml logs -f caddy   # wait: "certificate obtained"
curl -I https://techanizer.tech/    # valid cert — no -k
```

## 7. Scheduler cron (weekly refresh)

The prod db publishes no port (§4), so the weekly load → extract → embed runs
inside the app container — compose already injects DATABASE_URL and
ALIBABA_API_KEY there. Cron reads the VM clock: HK time = China time (UTC+8),
but if `timedatectl` shows UTC the schedule below is Sunday-morning UTC —
use `7 16 * * 0` instead (same instant).

```bash
# VM, as root
timedatectl                                    # Asia/Shanghai or Asia/Hong_Kong
docker compose -f compose.prod.yml exec app \
  python -m techanizer.ops.scheduler --dry-run # smoke: pending counts, no API cost
mkdir -p logs
crontab -e                                     # add the line below
```

```cron
# Monday 00:07 China time — weekly load -> extract -> embed
7 0 * * 1 cd /opt/techanizer && flock -n /tmp/techanizer-weekly.lock docker compose -f compose.prod.yml exec -T app python -m techanizer.ops.scheduler >> logs/weekly.log 2>&1
```

- `cd` first: cron has no cwd, and compose needs compose.prod.yml + .env in it.
- `-T` = no TTY under cron; `flock -n` makes an overlapping slow run no-op.
- Check after the first Monday: `grep "weekly ingest done" logs/weekly.log`;
  truncate when big: `truncate -s 0 logs/weekly.log`.

## Day-2 ops

```bash
docker compose -f compose.prod.yml logs -f app   # tail the API
docker compose -f compose.prod.yml restart app   # after .env change
docker compose -f compose.prod.yml down          # stop; data survives (volume)
```

- **Code change:** `git pull && docker compose -f compose.prod.yml build app &&
  docker compose -f compose.prod.yml up -d` — the `techanizer_pgdata_prod`
  volume is untouched.
- **Economy-mode Stop releases the public IP** (trial ECS): after any stop,
  re-check the IP, update the A-records, and re-run the TLS flip if it changed.
- Scheduler: weekly refresh via cron — §7 (log: `logs/weekly.log`).
