# EventTracker
Tracks events for Whatsapp group

## BotC Events — Deployment

### What's in the repo

```
botc/
├── app/
│   ├── main.py          ← FastAPI (all routes + serves frontend)
│   ├── models.py        ← SQLAlchemy models
│   ├── auth.py          ← JWT + bcrypt
│   ├── database.py      ← SQLite async engine
│   ├── config.py        ← Reads .env
│   ├── requirements.txt
│   ├── Dockerfile
│   └── static/
│       └── index.html   ← Full frontend SPA
├── docker-compose.yml
├── .env.example
└── nginx-host.conf
```

One container. SQLite database stored in a folder you point at.
No Postgres, no extra containers.

---

## Deploy

```bash
# 1. Clone the repo
git clone https://github.com/CarryingHim/EventTracker.git
cd EventTracker

# 2. Create your .env
cp .env.example .env
nano .env
#   → paste a real JWT_SECRET (run: openssl rand -hex 32)
#   → set ADMIN_PASSWORD to something only you know
#   → set DATA_PATH to wherever you want the database to live

# 3. Build and start
docker compose up -d --build

# 4. Hook up nginx
cp nginx-host.conf /etc/nginx/sites-available/botc
nano /etc/nginx/sites-available/botc   # set your domain/hostname
ln -s /etc/nginx/sites-available/botc /etc/nginx/sites-enabled/botc
nginx -t && nginx -s reload
```

Done. App is live.

---

## Default admin login

- Username: `admin`  
- Password: `admin123` (or whatever you set in `.env`)

Change via the Account tab after first login.

---

## Useful commands

```bash
# Logs
docker compose logs -f

# Restart
docker compose restart botc

# Stop (data is safe)
docker compose down

# Rebuild after updating files
docker compose up -d --build

# Backup the database
cp /mnt/tank/apps/botc/data/botc.db /mnt/tank/backups/botc-$(date +%Y%m%d).db
```

---

## Updating

Pull changes and rebuild:

```bash
git pull origin main
docker compose up -d --build
```

The database in `DATA_PATH` is untouched.
