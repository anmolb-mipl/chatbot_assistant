# Render (free demo) deployment
See RENDER.md for the one-container Render setup (Dockerfile, render.yaml).

# TradeWise Support: production-style stack

FastAPI + PostgreSQL + Redis + React (TypeScript) + Docker. Customer chat with a Gemini bot (tool use),
shared agent queue ("first agent to accept wins"), live WebSockets, past chats, audit log.

## Run it (needs Docker)
```
cp .env.example .env          # then put your GEMINI_API_KEY in .env (never commit it)
docker compose up --build
docker compose exec backend python -m app.seed riya Riya 'choose-a-strong-password'
docker compose exec backend python -m app.seed aman Aman 'another-strong-password'
```
- Customer chat:  http://localhost:8080
- Agent console:  http://localhost:8080/agent   (sign in with a seeded agent)
- API docs (dev only): http://localhost:8080/api/docs

Try: ask "check TXN1002", then "talk to a person"; sign in as two agents in two browsers and accept the request.
Demo IDs: TXN1001 success, TXN1002 pending, TXN1003 failed + debited (goes straight to an agent), TXN1004 rejected,
TXN2001 belongs to someone else (shows "not found").

## Run WITHOUT Docker (SQLite, no Redis, one process)
Needs Python 3.11+ and Node.js 20+ (LTS from nodejs.org). Windows commands:
```
cd backend
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements-local.txt
copy .env.local.example .env        (then put your GEMINI_API_KEY in backend\.env)
python -m app.seed riya Riya "choose-a-password"
python -m app.seed aman Aman "another-password"
uvicorn app.main:app --port 8000
```
In a second terminal:
```
cd frontend
npm install
npm run dev
```
Customer chat http://localhost:5173 , agent console http://localhost:5173/agent .
This mode is for development only (SQLite file `backend/local.db`, events kept inside one process).

## Layout
```
backend/app/   main.py (app, WebSockets) · routes_customer.py · routes_agent.py · services.py (atomic accept)
               ai.py (Gemini + tools) · tools.py (transaction lookup, audit) · realtime.py (Redis pub/sub)
               models.py · security.py (argon2, JWT) · knowledge.md (EDIT: bot answers only from this)
backend/tests/ pytest: password hashing, token types, one-agent-accepts, ownership check
frontend/src/  CustomerChat.tsx · AgentConsole.tsx · useSocket.ts (auto-reconnect)
```

## How the key parts work
- **First agent wins:** `UPDATE conversations SET status='live', agent_id=? WHERE id=? AND status='waiting'`.
  Postgres lets only one agent's update match, so two agents can never both get the chat.
- **Live updates:** every event goes through Redis pub/sub, so it works with several backend workers or servers.
- **Customer identity:** the bot only looks up transactions for the user in the signed token, never an ID typed in chat.
  In production YOUR app's backend signs that token (HS256, claim `typ=customer`, `sub=<user id>`, secret `CUSTOMER_JWT_SECRET`)
  and opens the chat with `/?token=...`. In dev, `/api/dev/customer-token` issues a demo one.
- **One live chat per agent:** an agent who is chatting cannot accept another request until they end that chat
  (`MAX_ACTIVE_CHATS_PER_AGENT`, default 1). The check is atomic, so two quick clicks cannot bypass it.
- **Handoff rules:** the server (not the model) decides when an agent is allowed: user asks for a person,
  a lookup says an agent is needed, or two failed attempts.

## Before real users (not done yet)
1. **Replace demo data:** `tools.py` uses a fake table; call your real payments/broker system there.
2. **Real secrets:** set `ENV=prod`, long random `AGENT_JWT_SECRET` and `CUSTOMER_JWT_SECRET`
   (the app refuses to start in prod with the defaults), keep secrets in your cloud's secret manager.
3. **Migrations:** switch `AUTO_CREATE_TABLES=false` and add Alembic migrations.
4. **HTTPS:** put a TLS terminator / load balancer in front (the nginx here is plain HTTP).
5. **Team routing:** agents have a `team` field, but every agent currently sees every request.
6. **Waiting timeout:** nothing happens yet if no agent accepts; add a "we'll call you back" fallback.
7. **Hardening:** monitoring (Sentry, logs, metrics), backups, data-retention rules, PII masking in chats,
   a security review, and a compliance check for your market. The login tokens use query-string WebSockets; avoid logging URLs.
