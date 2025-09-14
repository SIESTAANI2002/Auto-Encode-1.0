import os
import sys
from asyncio import Queue, Lock, create_task, sleep, gather
from re import findall
from math import floor
from time import time

from pyrogram import filters
from pyrogram.types import Message

from bot import bot, Var, LOGS
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import mediainfo, convertBytes, convertTime, editMessage, sendMessage

# -------------------- Queue & Lock -------------------- #
ffQueue = Queue()
ffLock = Lock()
ff_queued = {}
runner_task = None

# -------------------- Queue Runner -------------------- #
async def queue_runner(client):
    global runner_task
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        filename = os.path.basename(encoder.dl_path)
        ff_queued[filename] = encoder
        msg = encoder.msg

        try:
            # Download
            await msg.edit(f"⬇️ Downloading {filename}...")
            await encoder.message.download(encoder.dl_path, progress=encoder.download_progress)

            # Start encoding
            await msg.edit(f"⏳ Encoding {filename}...")
            output_path = await encoder.start_encode()
            if output_path is None:
                await msg.edit(f"❌ Encoding failed: {filename}")
                ff_queued.pop(filename, None)
                ffQueue.task_done()
                continue

            # Upload
            await client.send_document(
                chat_id=Var.MAIN_CHANNEL,
                document=output_path,
                caption=f"✅ Encoded 1080p: {filename}"
            )
            await msg.edit(f"✅ Encoding and upload finished: {filename}")

            # Auto-delete
            if Var.AUTO_DEL:
                for f in [encoder.dl_path, output_path]:
                    if os.path.exists(f):
                        os.remove(f)

        except Exception as e:
            LOGS.error(f"Queue task failed: {filename} | {str(e)}")
            await msg.edit(f"❌ Task failed: {filename}\nError: {str(e)}")

        finally:
            ff_queued.pop(filename, None)
            ffQueue.task_done()

    runner_task = None

# -------------------- Manual Encode Handler -------------------- #
@bot.on_message(filters.document | filters.video)
async def manual_encode(client, message: Message):
    global runner_task

    # Only owner
    if message.from_user.id not in Var.ADMINS:
        await message.reply_text("❌ You are not authorized to use this bot.")
        return

    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"

    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    # FFEncoder: download & encode progress
    encoder = FFEncoder(message, download_path, file_name, "1080")
    encoder.msg = msg

    # Add download progress method
    async def download_progress(current, total):
        percent = round(current/total*100, 2)
        bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
        diff = time() - encoder.__start_time
        speed = current / max(diff, 1)
        eta = (total - current) / max(speed, 0.01)
        progress_str = f"⬇️ Downloading {file_name}\n[{bar}] {percent}%\nSpeed: {convertBytes(speed)}/s | ETA: {convertTime(eta)}"
        await editMessage(msg, progress_str)
    encoder.download_progress = download_progress

    await ffQueue.put(encoder)
    LOGS.info(f"Added {file_name} to queue")

    if runner_task is None or runner_task.done():
        runner_task = create_task(queue_runner(client))

# -------------------- Queue Status -------------------- #
@bot.on_message(filters.command("queue"))
async def queue_status(client, message):
    status_lines = []
    for fname, encoder in ff_queued.items():
        status_lines.append(f"▶️ Encoding: {fname}")
    if not ffQueue.empty():
        for encoder in list(ffQueue._queue):
            filename = os.path.basename(encoder.dl_path)
            status_lines.append(f"⏳ Waiting: {filename}")
    if not status_lines:
        await message.reply_text("📭 No files are currently queued.")
    else:
        await message.reply_text("\n".join(status_lines))

# -------------------- Cancel Command -------------------- #
@bot.on_message(filters.command("cancel"))
async def cancel_encode(client, message):
    try:
        filename = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.reply_text("⚠️ Usage: /cancel <filename>")
        return

    removed = False

    # Running tasks
    if filename in ff_queued:
        encoder = ff_queued[filename]
        encoder.is_cancelled = True
        removed = True
        await message.reply_text(f"🛑 Cancel request sent for {filename}")
        return

    # Waiting queue
    temp_queue = []
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        if os.path.basename(encoder.dl_path) == filename:
            removed = True
            ffQueue.task_done()
        else:
            temp_queue.append(encoder)
            ffQueue.task_done()

    for e in temp_queue:
        await ffQueue.put(e)

    if removed:
        await message.reply_text(f"🗑️ {filename} removed from queue.")
    else:
        await message.reply_text(f"❌ File {filename} not found in queue.")

@bot.on_message(filters.command("restart") & filters.user(Var.ADMINS))
async def restart_bot(client, message):
    await message.reply_text("🔄 Bot is restarting...")
    LOGS.info("Bot restart initiated by owner.")
    # Gracefully stop the bot
    await client.stop()
    # Restart the current process
    os.execv(sys.executable, [sys.executable] + sys.argv)
