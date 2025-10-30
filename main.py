import os
import re
import json
import logging
import asyncio
import time

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError, BadMsgNotification
from pyrogram.types import Message
from typing import Dict, List, Optional

# ─── Logging Setup ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(name)s │ %(levelname)s │ %(message)s"
)
logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────────────
API_ID = 27765349
API_HASH = "9df1f705c8047ac0d723b29069a1332b"
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Only these user IDs can trigger /setchannel or upload
ALLOWED_USER_IDS = [1116405290]

app = Client(
    "simple_subject_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    workdir="./",
    sleep_threshold=60  # handle flood waits automatically
)

# ─── Global State ───────────────────────────────────────────────────────────────
active_downloads: Dict[int, bool] = {}
user_data: Dict[int, dict] = {}

# ─── Helper Functions ───────────────────────────────────────────────────────────

async def duration_async(filename: str) -> float:
    """Get video duration using ffprobe."""
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffprobe',
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            filename,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return float(stdout.decode().strip())
        else:
            return 0.0
    except Exception as e:
        logger.error(f"Duration error: {e}")
        return 0.0

async def extract_thumbnail_async(filename: str, timestamp: str = "00:00:10") -> Optional[str]:
    """Generate a thumbnail from the video at the given timestamp."""
    thumbnail_path = f"{filename}.jpg"
    try:
        proc = await asyncio.create_subprocess_exec(
            'ffmpeg',
            '-i', filename,
            '-ss', timestamp,
            '-vframes', '1',
            '-y', thumbnail_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return thumbnail_path if os.path.exists(thumbnail_path) else None
    except Exception as e:
        logger.error(f"Thumbnail error: {e}")
        return None

def extract_subjects(title: str) -> List[str]:
    """
    Extract all "[Subject]" tags from the title string.
    If none are found, returns ["General"].
    """
    subjects = list(set(re.findall(r'\[([^\]]+)\]', title)))
    return subjects if subjects else ["General"]

def clean_title(title: str) -> str:
    """Sanitize title for use as a filename (remove forbidden characters)."""
    return re.sub(r'[^\w\-_. ]', "", title.strip())

async def download_file(url: str, filename: str) -> str:
    """
    Download either .mp4 or .pdf from the given URL using 'appxdl'.
    Returns the path to the downloaded file.
    Raises Exception on failure, and ensures no leftover file remains.
    """
    url_lower = url.lower()
    # build output path based on the filename passed in
    if ".pdf" in url_lower:
        out_path = f"{filename}.pdf"
        cmd = ["appxdl", "-u", url, "-o", out_path]
    else:
        out_path = f"{filename}.mp4"
        cmd = ["appxdl", "-u", url, "-o", out_path]

    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.wait()

    # If download failed or file doesn't exist, remove any partial file and raise
    if proc.returncode != 0 or not os.path.exists(out_path):
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except Exception:
                pass
        raise Exception(f"Download failed for {url}")

    return out_path

async def upload_file_to_channel(
    bot: Client,
    file_path: str,
    caption: str,
    channel_id: int,
    thread_id: int,   # ✅ new optional argument
    status_msg: Message
) -> bool:
    """
    Uploads either .mp4 (with thumbnail) or any other document to the channel.
    Retries up to 3 times on RPCError/FloodWait.
    Ensures that thumbnails are cleaned up after use.
    """
    max_retries = 3

    for attempt in range(max_retries):
        try:
            if file_path.lower().endswith(".mp4"):
                # Extract thumbnail if possible
                thumb = await extract_thumbnail_async(file_path)
                duration = int(await duration_async(file_path))
                try:
                    await bot.send_video(
                        chat_id=channel_id,
                        video=file_path,
                        caption=caption,
                        thumb=thumb,
                        duration=duration,
                        supports_streaming=True
                    )
                    return True
                finally:
                    if thumb and os.path.exists(thumb):
                        os.remove(thumb)
            else:
                # For non‐video files, send as document
                await bot.send_document(
                    chat_id=channel_id,
                    document=file_path,
                    caption=caption
                )
                return True

        except FloodWait as e:
            logger.warning(f"FloodWait during upload: sleeping for {e.value}s")
            await asyncio.sleep(e.value)
            continue

        except RPCError as e:
            logger.error(f"RPCError on upload (attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(2 ** attempt)
            continue

        except Exception as e:
            logger.error(f"Unexpected upload error (attempt {attempt+1}): {e}")
            if attempt == max_retries - 1:
                return False
            await asyncio.sleep(2 ** attempt)

    return False

# ─── Command Handlers ──────────────────────────────────────────────────────────

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user_id = message.from_user.id
    text = (
        "👋 **Welcome to the Subject‐Based Upload Bot!**\n\n"
        "📋 **How to use:**\n"
        "1. Send me a `.txt` file with lines in this format:\n"
        "   `[Subject] Title:URL`\n\n"
        "   - `Subject` (in square brackets) will be used to group uploads.\n"
        "   - `Title` is the human‐readable name (used for filename and caption).\n"
        "   - `URL` is a direct link to the `.mp4` or `.pdf`.\n\n"
        "2. After I process your `.txt`, I'll ask for:\n"
        "   • **Starting line** number\n"
        "   • **Channel ID** (e.g. `-1001234567890`)\n"
        "   • **Batch name** (any text)\n"
        "   • **Downloaded by** (credit text)\n\n"
        "Then I will:\n"
        "  • Read each line from the starting line onward.\n"
        "  • Whenever `[Subject]` changes from the previous one, I'll send a plain message\n"
        "    with that subject and pin it in the channel.\n"
        "  • Upload the corresponding file under that subject with numbered captions.\n"
        "  • Retry failed downloads once before moving to next item.\n\n"
        "🛑 Use `/stop` (in private chat) at any time to halt processing.\n\n"
        f"🆔 Your User ID: `{user_id}`"
    )
    await message.reply_text(text, disable_web_page_preview=True)

@app.on_message(filters.command("stop") & filters.private)
async def stop_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in active_downloads or not active_downloads[user_id]:
        return await message.reply_text("ℹ️ No active process to stop.")
    active_downloads[user_id] = False
    await message.reply_text("⏹️ Processing has been stopped.")

# ─── Check for incoming .txt files ──────────────────────────────────────────────

def is_txt_document(_, __, message: Message) -> bool:
    doc = message.document
    return bool(doc and doc.file_name and doc.file_name.lower().endswith(".txt"))

@app.on_message(filters.document & filters.create(is_txt_document))
async def txt_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        return

    ack = await message.reply_text("📥 Downloading and reading your .txt file...")
    os.makedirs("downloads", exist_ok=True)
    temp_path = f"downloads/temp_{user_id}.txt"

    try:
        await client.download_media(message, file_name=temp_path)
        with open(temp_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except Exception as e:
        logger.error(f"File error: {e}")
        await ack.edit_text("⚠️ Failed to read the file.")
        return
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    if not lines:
        return await ack.edit_text("⚠️ The file is empty.")

    user_data[user_id] = {
        'lines': lines,
        'total': len(lines),
        'step': 'start_number'
    }
    await ack.edit_text(f"📋 Found {len(lines)} items. Please send the starting line number (1–{len(lines)}).")

# ─── Handle subsequent text inputs (start_number → channel_id → batch_name → downloaded_by) ───────

@app.on_message(filters.text & filters.private)
async def input_handler(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_data or (ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS):
        return

    data = user_data[user_id]
    text = message.text.strip()

    if data['step'] == 'start_number':
        try:
            start = int(text)
            if 1 <= start <= data['total']:
                data['start_number'] = start
                data['step'] = 'channel_id'
                await message.reply_text("📝 Got it. Now send the **channel ID** (e.g. `-1001234567890`).")
            else:
                await message.reply_text(f"❌ Please send a number between 1 and {data['total']}.")
        except ValueError:
            await message.reply_text("❌ That's not a valid integer. Please send the starting line number.")

    elif data['step'] == 'channel_id':
        # Validate channel ID format (starts with -100 for supergroups/channels)
        if not text.startswith("-100"):
            return await message.reply_text("❌ Invalid channel ID format. Make sure it starts with `-100`.")
        data['channel_id'] = int(text)
        data['step'] = 'batch_name'
        await message.reply_text("🏷️ Great! Now send the **batch name** (any text).")

    elif data['step'] == 'batch_name':
        data['batch_name'] = text
        data['step'] = 'downloaded_by'
        await message.reply_text("👤 Perfect! Now send the **Downloaded by** credit text.")

    elif data['step'] == 'downloaded_by':
        data['downloaded_by'] = text
        # Everything is set → start processing
        await start_processing(client, message, user_id)

# ─── Main Processing Loop ────────────────────────────────────────────────────────

from pyrogram import Client
from pyrogram.errors import FloodWait, RPCError
import asyncio, os, logging

logger = logging.getLogger(__name__)

# ─── Topic Helper (Fixed Raw API + random_id) ───────────────────────────────────────────────
from pyrogram import raw
from pyrogram.errors import RPCError
import random

async def get_or_create_topic(client, chat_id, topic_name: str):
    """
    Create a forum topic safely for bot accounts using the raw API.
    Includes required random_id to satisfy Telegram's method definition.
    """
    try:
        peer = await client.resolve_peer(chat_id)

        # generate a unique random_id (Telegram requires this)
        random_id = random.randint(1, 2**63 - 1)

        try:
            # Try to create a topic directly (bots can use this)
            new_topic = await client.invoke(
                raw.functions.channels.CreateForumTopic(
                    channel=peer,
                    title=topic_name,
                    random_id=random_id,
                    icon_color=7322096
                )
            )
            topic_id = new_topic.topic.id
            logger.info(f"✅ Created topic: {topic_name} (id={topic_id})")
            return topic_id

        except RPCError as e:
            # If topic already exists or can't be created, fall back to "General"
            logger.warning(f"⚠️ Topic create RPCError for '{topic_name}': {e}")
            return 0  # default thread ID

    except Exception as e:
        logger.error(f"⚠️ Failed to get/create topic '{topic_name}': {e}")
        return 0




# ─── Main Processing Loop ───────────────────────────────────────

async def start_processing(client: Client, message: Message, user_id: int):
    data = user_data[user_id]
    lines = data["lines"]
    start_idx = data["start_number"]
    batch_name = data["batch_name"]
    channel_id = data["channel_id"]
    downloaded_by = data["downloaded_by"]
    total = data["total"]

    active_downloads[user_id] = True
    status_msg = await message.reply_text(
        f"🚀 Starting processing:\n"
        f"• Start line: {start_idx}\n"
        f"• Total items: {total}\n"
        f"• Batch name: {batch_name}\n"
        f"• Channel: {channel_id}\n"
        f"• Downloaded by: {downloaded_by}\n\n"
        f"Completed: 0 / {total}"
    )

    processed = 0
    failed = 0
    last_subject = None
    video_count = 0
    topic_id_cache = {}  # Cache topics to reduce API calls

    for idx, entry in enumerate(lines[start_idx - 1:], start=start_idx):
        if not active_downloads.get(user_id, True):
            logger.info(f"Process stopped by user {user_id} at line {idx}")
            break

        # Each line is "[Subject] Title:URL"
        if ":" not in entry:
            logger.warning(f"Skipping invalid line {idx}: {entry}")
            failed += 1
            continue

        title_part, url = entry.split(":", 1)
        subjects = extract_subjects(title_part)
        subject = subjects[0] if subjects else "General"
        clean_name = clean_title(title_part)

        # Get or create topic (cached)
        if subject not in topic_id_cache:
            try:
                topic_id_cache[subject] = await get_or_create_topic(client, channel_id, subject)
            except Exception as e:
                logger.error(f"Failed to get/create topic for '{subject}': {e}")
                failed += 1
                continue

        topic_id = topic_id_cache[subject]

        video_count += 1
        item_status = await message.reply_text(f"⬇️ [{idx}/{total}] Downloading: {clean_name}")

        file_path = None
        download_success = False

        for attempt in range(2):
            try:
                file_path = await download_file(url.strip(), clean_name)
                download_success = True
                break
            except Exception as e:
                logger.error(f"Download attempt {attempt + 1} failed for line {idx} ({clean_name}): {e}")
                if attempt == 0:
                    await item_status.edit_text(f"⚠️ [{idx}/{total}] Retry download: {clean_name}")
                    await asyncio.sleep(2)
                else:
                    await item_status.edit_text(f"❌ [{idx}/{total}] Failed: {clean_name}")
                    failed += 1

        if not download_success:
            try: await item_status.delete()
            except: pass
            continue

        caption = f"{video_count}\n{title_part.strip()}\n{batch_name}\nDownloaded by {downloaded_by}"
        await item_status.edit_text(f"📤 [{idx}/{total}] Uploading: {clean_name}")

        success = False
        try:
            success = await upload_file_to_channel(
                client,
                file_path,
                caption,
                channel_id,
                item_status,
                thread_id=topic_id  # <── Send inside the topic
            )
        except Exception as e:
            logger.error(f"Upload error '{clean_name}': {e}")
            success = False

        if success:
            processed += 1
            logger.info(f"Uploaded '{clean_name}' to topic '{subject}'.")
        else:
            failed += 1
            video_count -= 1

        if file_path and os.path.exists(file_path):
            try: os.remove(file_path)
            except: pass

        await status_msg.edit_text(
            f"🚀 Processing:\n"
            f"• Current line: {idx}/{total}\n"
            f"• Completed: {processed}\n"
            f"• Failed: {failed}\n"
            f"• Batch: {batch_name}"
        )

        await asyncio.sleep(2)
        try: await item_status.delete()
        except: pass

    user_data.pop(user_id, None)
    active_downloads.pop(user_id, None)

    await status_msg.edit_text(
        f"✅ Completed!\n"
        f"• Uploaded: {processed}\n"
        f"• Failed: {failed}\n"
        f"• Total: {processed + failed}"
    )

# ─── Handle potential bad‐time notifications on startup ────────────────────────

def sync_system_time():
    try:
        import subprocess
        subprocess.run(['ntpdate', '-s', 'pool.ntp.org'], timeout=10, capture_output=True)
        logger.info("System time synced")
    except:
        logger.warning("Could not sync system time; proceeding anyway")

if __name__ == "__main__":
    logger.info("Starting bot...")
    try:
        app.run()
    except BadMsgNotification:
        logger.warning("System time mismatch - continuing anyway")
        app.run()
    except Exception as e:
        logger.exception(f"Fatal error: {e}")

