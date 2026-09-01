import os
import re
import logging
import tempfile
import asyncio
import threading

from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import yt_dlp

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")

# Optional: comma-separated Telegram user IDs allowed to use the bot.
# Leave empty to allow anyone (not recommended for a public bot).
_allowed_raw = os.environ.get("ALLOWED_USER_IDS", "").strip()
ALLOWED_USER_IDS = {int(x) for x in _allowed_raw.split(",") if x.strip()} if _allowed_raw else set()

MAX_TELEGRAM_UPLOAD_MB = 50

# Matches any http(s) URL — yt-dlp itself decides whether the site is supported.
URL_REGEX = re.compile(r"https?://\S+")

# Optional: paste the full contents of a cookies.txt (Netscape format) file here
# as an env var to get past YouTube's "Sign in to confirm you're not a bot" check.
# See README for how to export this from your browser.
_youtube_cookies = os.environ.get("YOUTUBE_COOKIES", "").strip()
COOKIES_FILE_PATH = None
if _youtube_cookies:
    COOKIES_FILE_PATH = os.path.join(tempfile.gettempdir(), "youtube_cookies.txt")
    with open(COOKIES_FILE_PATH, "w", encoding="utf-8") as _f:
        _f.write(_youtube_cookies)
    logger.info("Loaded YouTube cookies from YOUTUBE_COOKIES env var")

# --- Minimal health-check web server (required by Render Web Services) ---
health_app = Flask(__name__)


@health_app.route("/")
def health():
    return "OK", 200


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    health_app.run(host="0.0.0.0", port=port)


# --- Download logic ---
def _is_youtube_url(url: str) -> bool:
    return bool(re.search(r"(youtube\.com|youtu\.be)", url, re.IGNORECASE))


def download_media(url: str, out_dir: str) -> str:
    """Download from any site yt-dlp supports, trying progressively lower
    quality until the file fits Telegram's 50MB limit.

    For YouTube specifically, also tries alternate "player clients" (android,
    then web) since YouTube sometimes blocks the default client with a
    bot-check error on datacenter IPs (like Render's). Cookies (YOUTUBE_COOKIES)
    are applied for YouTube URLs if set.
    """
    format_attempts = [
        "bestvideo[height<=480]+bestaudio/best[height<=480]",
        "bestvideo[height<=360]+bestaudio/best[height<=360]",
        "worst",
    ]

    is_youtube = _is_youtube_url(url)
    # Non-YouTube sites don't need/understand the youtube player_client trick,
    # so just run through the format attempts once with client=None.
    player_clients = ["android", "web"] if is_youtube else [None]

    last_error = None
    for player_client in player_clients:
        for fmt in format_attempts:
            ydl_opts = {
                "format": fmt,
                "outtmpl": os.path.join(out_dir, "%(id)s.%(ext)s"),
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "restrictfilenames": True,
            }
            if is_youtube:
                ydl_opts["extractor_args"] = {"youtube": {"player_client": [player_client]}}
                if COOKIES_FILE_PATH:
                    ydl_opts["cookiefile"] = COOKIES_FILE_PATH
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    filepath = ydl.prepare_filename(info)
                    # merge_output_format may change the extension to mp4
                    mp4_path = os.path.splitext(filepath)[0] + ".mp4"
                    if os.path.exists(mp4_path):
                        filepath = mp4_path

                    size_mb = os.path.getsize(filepath) / (1024 * 1024)
                    if size_mb <= MAX_TELEGRAM_UPLOAD_MB:
                        return filepath
                    os.remove(filepath)
                    last_error = RuntimeError(
                        f"Best available at this quality was {size_mb:.1f}MB (over the limit)"
                    )
            except Exception as e:  # noqa: BLE001
                last_error = e
                continue

    raise last_error or RuntimeError("Could not download video under the size limit")


# --- Telegram handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Send me a link from YouTube or any other site yt-dlp supports "
        "(Twitter/X, TikTok, Instagram, Reddit, Vimeo, SoundCloud, etc.) and "
        "I'll download it and upload it here.\n\n"
        "Note: Telegram bots can only upload files up to 50MB, so I'll automatically "
        "lower the quality if needed to fit."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    match = URL_REGEX.search(text)

    if not match:
        await update.message.reply_text("Please send a link.")
        return

    if ALLOWED_USER_IDS and update.effective_user.id not in ALLOWED_USER_IDS:
        await update.message.reply_text("🚫 You're not authorized to use this bot.")
        return

    url = match.group(0)
    status_msg = await update.message.reply_text("⏳ Downloading...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            filepath = await asyncio.to_thread(download_media, url, tmp_dir)
        except Exception as e:  # noqa: BLE001
            logger.exception("Download failed")
            await status_msg.edit_text(
                f"❌ Couldn't download this.\nReason: {e}\n\n"
                "This could mean the site isn't supported by yt-dlp, the link is "
                "private, or the content needs login cookies."
            )
            return

        try:
            await status_msg.edit_text("⬆️ Uploading to Telegram...")
            ext = os.path.splitext(filepath)[1].lower()
            with open(filepath, "rb") as f:
                if ext in (".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac"):
                    await update.message.reply_audio(
                        audio=f,
                        title=os.path.basename(filepath),
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=60,
                    )
                elif ext in (".mp4", ".mkv", ".webm", ".mov"):
                    await update.message.reply_video(
                        video=f,
                        caption=os.path.basename(filepath),
                        supports_streaming=True,
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=60,
                    )
                else:
                    await update.message.reply_document(
                        document=f,
                        filename=os.path.basename(filepath),
                        read_timeout=180,
                        write_timeout=180,
                        connect_timeout=60,
                    )
            await status_msg.delete()
        except Exception as e:  # noqa: BLE001
            logger.exception("Upload failed")
            await status_msg.edit_text(f"❌ Downloaded, but upload to Telegram failed: {e}")


def main():
    # Start health-check server in the background so Render sees an open port
    threading.Thread(target=run_health_server, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot starting (polling)...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
