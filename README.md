# YouTube → Telegram Downloader Bot

A Telegram bot that downloads a YouTube video when you send it a link, and
uploads the video file back to you in Telegram. Runs on Render as a Docker
web service.

## What's in this project

- `bot.py` — the bot (Telegram polling + a tiny health-check web server)
- `Dockerfile` — installs Python, ffmpeg, and dependencies
- `requirements.txt` — Python dependencies
- `render.yaml` — Render deployment blueprint
- `.env.example` — example environment variables
- `.gitignore`

## Important limitation (read this first)

Telegram **bots** can only upload files up to **50MB** via the standard Bot
API (this is a Telegram limit, not something this code can bypass). The bot
automatically retries at 480p, then 360p, then lowest quality to try to fit
under 50MB. If a video still can't fit, it'll tell you instead of failing
silently.

Also note: downloading YouTube videos may violate YouTube's Terms of
Service depending on the content and your use case. Use this responsibly —
e.g. for your own content, permitted downloads, or personal/fair use in
your jurisdiction.

---

## Step 1 — Create your Telegram bot

1. Open Telegram and message **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a name and a username
   ending in `bot`).
3. BotFather will give you a **token** like
   `123456789:AAExampleTokenGoesHere`. Save it — you'll need it below.

## Step 2 — (Recommended) Get your Telegram user ID

To stop random strangers from using your bot and burning your bandwidth:

1. Message **@userinfobot** on Telegram.
2. It replies with your numeric user ID, e.g. `987654321`.
3. You'll set this as `ALLOWED_USER_IDS` below (comma-separate multiple
   IDs if you want more than one person to use it).

## Step 3 — Put this project on GitHub

1. Create a new GitHub repository.
2. Upload all the files in this project to it (or `git init`, `git add .`,
   `git commit -m "init"`, `git push`).

## Step 4 — Deploy on Render

1. Go to [render.com](https://render.com) and sign in / sign up.
2. Click **New +** → **Web Service**.
3. Connect the GitHub repo you just created.
4. Render will detect the `Dockerfile` automatically (Environment: Docker).
   If it asks, you can also just point it at `render.yaml` via **New +** →
   **Blueprint** and it will read the config automatically.
5. Choose an instance plan. **Note:** Render's free tier for web services
   spins down after ~15 minutes of inactivity, which will kill the bot's
   connection until traffic hits your health-check URL again. For a bot
   that should respond instantly at any time, use at least the **Starter**
   paid plan (this is already set in `render.yaml`).
6. Under **Environment Variables**, add:
   - `TELEGRAM_BOT_TOKEN` = the token from BotFather
   - `ALLOWED_USER_IDS` = your Telegram user ID (optional but recommended)
7. Click **Create Web Service** / **Apply**. Render will build the Docker
   image (this installs ffmpeg + Python deps) and start the bot.
8. Watch the **Logs** tab — you should see `Bot starting (polling)...`.

That's it — no ports or webhooks to configure manually; the bot uses
polling and only exposes a `/` health-check route so Render is happy.

## Step 5 — Use it

1. Open Telegram, find your bot by the username you gave it in BotFather.
2. Send `/start` to see the welcome message.
3. Send any YouTube link, e.g. `https://youtu.be/dQw4w9WgXcQ`.
4. Wait — you'll see "⏳ Downloading..." then "⬆️ Uploading..." then the
   video will appear in the chat.

## Running it locally (optional, for testing before deploying)

```bash
# 1. Install ffmpeg (required by yt-dlp)
#    macOS: brew install ffmpeg
#    Ubuntu/Debian: sudo apt-get install ffmpeg
#    Windows: https://ffmpeg.org/download.html

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Set environment variables
cp .env.example .env
# edit .env and fill in your real TELEGRAM_BOT_TOKEN

# 4. Export them into your shell (Linux/macOS)
export $(cat .env | xargs)

# 5. Run the bot
python bot.py
```

Then message your bot on Telegram as described in Step 5 above.

## Customizing

- **Change max quality attempts**: edit `format_attempts` in `bot.py` to
  change the resolutions tried (e.g. add a 720p attempt first if you don't
  mind more failures before it falls back).
- **Restrict/open access**: set or clear `ALLOWED_USER_IDS`.
- **Audio-only downloads**: you could add a command like `/audio <link>`
  that uses format `bestaudio/best` and `reply_audio` instead of
  `reply_video` — ask if you'd like this added.
