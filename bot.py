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

YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?(youtube\.com|youtu\.be|m\.youtube\.com)/\S+"
)

# --- Minimal health-check web server (required by Render Web Services) ---
health_app = Flask(__name__)


@health_app.route("/")
def health():
    return "OK", 200


def run_health_server():
    port = int(os.environ.get("PORT", 10000))
    health_app.run(host="0.0.0.0", port=port)


# --- Download logic ---
def download_youtube_video(url: str, out_dir: str) -> str:
    """Try progressively lower quality until the file fits Telegram's 50MB limit."""
    format_attempts = [
        "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best[height<=480]",
        "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]/best[height<=360]",
        "worst",
    ]

    last_error = None
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
        "👋 Send me a YouTube link and I'll download it and upload it here.\n\n"
        "Note: Telegram bots can only upload files up to 50MB, so I'll automatically "
        "lower the video quality if needed to fit."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    match = YOUTUBE_REGEX.search(text)

    if not match:
        await update.message.reply_text("Please send a valid YouTube link.")
        return

    if ALLOWED_USER_IDS and update.effective_user.id not in ALLOWED_USER_IDS:
        await update.message.reply_text("🚫 You're not authorized to use this bot.")
        return

    url = match.group(0)
    status_msg = await update.message.reply_text("⏳ Downloading from YouTube...")

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            filepath = await asyncio.to_thread(download_youtube_video, url, tmp_dir)
        except Exception as e:  # noqa: BLE001
            logger.exception("Download failed")
            await status_msg.edit_text(f"❌ Couldn't download this video.\nReason: {e}")
            return

        try:
            await status_msg.edit_text("⬆️ Uploading to Telegram...")
            with open(filepath, "rb") as f:
                await update.message.reply_video(
                    video=f,
                    caption=os.path.basename(filepath),
                    supports_streaming=True,
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
