import os
import sys
from time import time, sleep
from math import floor
from pyrogram import Client, filters
from asyncio import Queue, Lock, create_task
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage
from bot import Var, LOGS

# -------------------- Queue & Lock -------------------- #
ffQueue = Queue()
ffLock = Lock()
ff_queued = {}
runner_task = None

# -------------------- Download with Progress -------------------- #
async def download_with_progress(msg, file_path, media_message):
    start_time = time()
    last_percent = -5

    async def progress_callback(current, total):
        nonlocal last_percent
        elapsed = time() - start_time
        speed = current / max(elapsed, 0.01)
        percent = round(current / max(total, 1) * 100, 2)
        eta = (total - current) / max(speed, 0.01)
        bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"

        if percent - last_percent >= 5 or percent == 100:
            progress_str = f"""<b>⬇️ Downloading :</b> <b>{os.path.basename(file_path)}</b>
<code>[{bar}] {percent}%</code>
Size: {convertBytes(current)} / {convertBytes(total)}
Speed: {convertBytes(speed)}/s
Time Left: {convertTime(eta)}"""
            await editMessage(msg, progress_str)
            last_percent = percent

    await media_message.download(file_path, progress=progress_callback)
    await editMessage(msg, f"⬇️ Download completed: {os.path.basename(file_path)}\n⏳ Starting encoding...")

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
            await download_with_progress(msg, encoder.dl_path, encoder.message)

            # Encode
            encoder_task = create_task(encoder.start_encode())
            while not encoder_task.done():
                await sleep(8)
            output_path = await encoder_task

            # Upload
            await client.send_document(
                chat_id=Var.MAIN_CHANNEL,
                document=output_path,
                caption=f"✅ Encoded 1080p: {filename}"
            )
            await editMessage(msg, f"✅ Encoding and upload finished: {filename}")

            # Auto-delete
            if Var.AUTO_DEL:
                for f in [encoder.dl_path, output_path]:
                    if os.path.exists(f):
                        os.remove(f)

        except Exception as e:
            LOGS.error(f"Queue task failed: {filename} | {str(e)}")
            await editMessage(msg, f"❌ Task failed: {filename}\nError: {str(e)}")
        finally:
            ff_queued.pop(filename, None)
            ffQueue.task_done()
    runner_task = None

# -------------------- Manual Encode -------------------- #
@Client.on_message(filters.document | filters.video)
async def manual_encode(client, message):
    global runner_task

    if message.from_user.id != int(Var.OWNER_ID):
        return await message.reply_text("❌ Only owner can use this bot!")

    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"
    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    encoder = FFEncoder(message, download_path, file_name, "1080")
    encoder.msg = msg

    await ffQueue.put(encoder)
    LOGS.info(f"Added {file_name} to queue")

    if runner_task is None or runner_task.done():
        runner_task = create_task(queue_runner(client))

# -------------------- Queue Status -------------------- #
@Client.on_message(filters.command("queue"))
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
@Client.on_message(filters.command("cancel"))
async def cancel_encode(client, message):
    try:
        filename = message.text.split(maxsplit=1)[1]
    except IndexError:
        return await message.reply_text("⚠️ Usage: /cancel <filename>")

    removed = False

    if filename in ff_queued:
        encoder = ff_queued[filename]
        encoder.is_cancelled = True
        removed = True
        await message.reply_text(f"🛑 Cancel request sent for {filename}")
        return

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

# -------------------- Restart Command -------------------- #
@Client.on_message(filters.command("restart"))
async def restart_bot(client, message):
    if message.from_user.id != int(Var.OWNER_ID):
        return await message.reply_text("❌ Only owner can restart the bot!")

    await message.reply_text("♻️ Restarting bot...")
    # Gracefully stop ongoing tasks
    for encoder in ff_queued.values():
        encoder.is_cancelled = True
    while not ffQueue.empty():
        await ffQueue.get()
        ffQueue.task_done()
    await sleep(2)
    os.execv(sys.executable, [sys.executable] + sys.argv)
