# Deploy v1 on Render (free plan) - step by step

This puts the whole app (website + API) in ONE free Render web service with a free Postgres database.
It is a DEMO setup: the free service sleeps when idle, and the free database is deleted after 30 days.
Do not use it for real customers yet.

## 0. Before anything: replace your Gemini key
The old key was pasted in a chat, written into config.py and included in your zip. In Google AI Studio create a NEW key and
delete the old one. The new key goes only into Render's dashboard (step 4), never into a file.

## 1. Prepare the folder
Unzip `chat_support_render.zip` into a NEW folder (not your old working folder). Check that these are NOT inside it:
`.env`, `backend/.env`, `.venv`, `node_modules`, `local.db`. (The package was built without them.)

## 2. Put it on GitHub (private repository)
1. Install Git for Windows (git-scm.com) if you do not have it. Create a free GitHub account.
2. On github.com click New repository. Name it `tradewise-support`, choose **Private**, do NOT add a README, create it.
3. In PowerShell, inside the unzipped folder:
   ```
   git init
   git add .
   git status
   ```
   Read the list. If you see `.env`, `.venv`, `node_modules` or `local.db`, STOP and tell me. Otherwise:
   ```
   git commit -m "v1"
   git branch -M main
   git remote add origin https://github.com/YOUR_NAME/tradewise-support.git
   git push -u origin main
   ```
   (GitHub may ask you to sign in in the browser.)

## 3. Create the service on Render
1. Sign up at render.com (you can sign in with GitHub). 
2. Dashboard -> New -> **Blueprint** -> connect your GitHub account -> select the `tradewise-support` repository.
3. Render reads `render.yaml` and shows one web service and one database. 

## 4. Fill in the two secret values when Render asks
- `GEMINI_API_KEY`: your NEW key.
- `SEED_AGENTS`: your agents, written as `username:Display Name:password`, separated by `;`
  Example: `riya:Riya:Str0ngPassOne;aman:Aman:Str0ngPassTwo`
  (Passwords must not contain `;`. They are created at every start-up, so they survive restarts and database re-creation.)
Click **Apply**. The first build takes about 5-10 minutes.

## 5. Open it
Render shows your address, like `https://tradewise-support-xxxx.onrender.com`.
- Customer chat: that address.   - Agents: that address + `/agent`.   - Health check: `/api/healthz` shows `{"ok":true}`.

## 6. Test checklist
1. Open the customer chat on your phone AND your computer: each should have its OWN empty chat (separate demo customers).
2. Ask an FAQ, then type "check TXN1002".
3. Say "connect me to an agent", pick Chat. Sign in at `/agent` and accept. Try typing indicators both ways.
4. Try a Call between phone and computer (the site is HTTPS, so the microphone works).

## Update an existing deployment

Copy the fixed files into your existing repository, commit, and push to the branch
already connected to Render. In the existing Render web service, use **Manual
Deploy → Deploy latest commit** if auto-deploy is off. Keep your existing
database, JWT secrets, `SEED_AGENTS`, and `GEMINI_API_KEY`. Refresh both browser tabs
and start a new conversation before testing the fixes.

### Verify voice across networks

Test a customer on mobile data and an agent on Wi-Fi. Allow both microphones.
If peer-to-peer audio cannot connect, configure a TURN relay under the service's
**Environment** settings and redeploy:

- `TURN_URL` — provider-supplied URL(s), separated by commas. Include the provider's
  TURN-over-TLS endpoint on TCP 443 if available, such as
  `turns:relay.example.com:443?transport=tcp`.
- `TURN_USERNAME` — relay username.
- `TURN_CREDENTIAL` — relay password or credential accepted by the relay.

Use demo-scoped TURN credentials with provider-side limits. Both browsers must
receive the credentials to use the relay. Render hosts the signaling app and the
TURN provider relays audio when the peers cannot connect directly.

### Verify Gemini handoff

Send two consecutive questions that Gemini cannot answer. A timeout, API error,
empty reply, or request for clarification counts as one unanswered turn. A
successful answer resets the count. The second unanswered turn creates a text
chat request in the agent queue. An explicit request for an agent still shows the
Chat / Call choice.

## If something fails
Render dashboard -> your service -> **Logs** (build logs and run logs). Send me the last 30 lines.
- Bot says "had trouble answering": wrong key or model name. In Render -> Environment add `GEMINI_MODEL` with a model name that exists for your key.
- Site loads but chat does not start: check the logs for a database error.

## What to expect from the free plan
- After 15 minutes with no traffic it sleeps; the next visit takes about a minute to wake. An open agent console keeps it awake.
- Chats are stored in the free database, which expires after 30 days. Back up anything important.
- Voice calls can fail on strict networks unless a TURN relay is configured.
- Anyone with the link can use the chat and your Gemini quota (a global limit of 120 bot messages a minute applies). Share the link only with testers.
