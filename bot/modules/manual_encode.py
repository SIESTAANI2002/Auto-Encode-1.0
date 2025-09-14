from pyrogram import Client, filters
from asyncio import Queue, Lock, create_task, sleep, Event
from os import path as ospath, remove, makedirs
from time import time
from math import floor
from re import findall

from bot.core.ffencoder import FFEncoder, convertBytes, convertTime
from bot.core.func_utils import editMessage
from bot.config import Var

ffQueue = Queue()
ffLock = Lock()
ff_queued = {}
runner_task = None

# Ensure downloads/encode folders exist
makedirs("downloads", exist_ok=True)
makedirs("encode", exist_ok=True)

# ------------------- DOWNLOAD FUNCTION ------------------- #
async def download_file(message, file_path):
    total_size = message.document.file_size if message.document else message.video.file_size
    downloaded_size = 0
    chunk_size = 256 * 1024  # 256KB per chunk

    # create empty file
    with open(file_path, "wb") as f:
        pass

    msg = await message.reply_text(f"⬇️ Downloading {os.path.basename(file_path)}…\n[▒▒▒▒▒▒▒▒▒▒▒▒] 0% | 0 KB/s | ETA: --:--")

    start_time = time()
    last_update = 0

    async for chunk in message.download(file_path, chunk_size=chunk_size, progress=None):
        downloaded_size += len(chunk)
        elapsed = time() - start_time
        speed = downloaded_size / max(elapsed, 0.01)  # bytes/sec
        percent = min(downloaded_size / total_size * 100, 100)
        eta = (total_size - downloaded_size) / max(speed, 0.01)

        bar_len = 12
        filled_len = int(bar_len * percent // 100)
        bar = "█" * filled_len + "▒" * (bar_len - filled_len)

        # update every 3 sec
        if int(elapsed) - last_update >= 3:
            last_update = int(elapsed)
            progress_str = f"""⬇️ Downloading {os.path.basename(file_path)}…
[{bar}] {percent:.2f}% | {convertBytes(speed)}/s | ETA: {convertTime(eta)}"""
            await editMessage(msg, progress_str)

    await editMessage(msg, f"✅ Download Completed: {os.path.basename(file_path)}")
    return file_path, msg


# ------------------- QUEUE RUNNER ------------------- #
async def queue_runner(client):
    global runner_task
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        filename = ospath.basename(encoder.dl_path)
        ff_queued[filename] = encoder
        msg = encoder.msg

        try:
            # Download
            await editMessage(msg, f"⬇️ Downloading {filename}…")
            encoder.dl_path, _ = await download_file(encoder.message, encoder.dl_path)

            # Encode
            await editMessage(msg, f"⏳ Encoding {filename}…")
            output_path = await encoder.start_encode()

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
                    if ospath.exists(f):
                        remove(f)

        except Exception as e:
            await editMessage(msg, f"❌ Task failed: {filename}\nError: {str(e)}")
        finally:
            ff_queued.pop(filename, None)
            ffQueue.task_done()

    runner_task = None


# ------------------- MANUAL ENCODE HANDLER ------------------- #
@Client.on_message(filters.document | filters.video)
async def manual_encode(client, message):
    global runner_task

    if message.from_user.id not in Var.ADMINS:
        await message.reply_text("❌ You are not allowed to use this bot.")
        return

    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"
    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    encoder = FFEncoder(message, download_path, file_name, "1080")
    encoder.msg = msg
    await ffQueue.put(encoder)

    if runner_task is None or runner_task.done():
        runner_task = create_task(queue_runner(client))


# ------------------- QUEUE STATUS ------------------- #
@Client.on_message(filters.command("queue"))
async def queue_status(client, message):
    status_lines = []

    for fname, encoder in ff_queued.items():
        status_lines.append(f"▶️ Encoding: {fname}")

    if not ffQueue.empty():
        for encoder in list(ffQueue._queue):
            filename = ospath.basename(encoder.dl_path)
            status_lines.append(f"⏳ Waiting: {filename}")

    if not status_lines:
        await message.reply_text("📭 No files are currently queued.")
    else:
        await message.reply_text("\n".join(status_lines))


# ------------------- CANCEL TASK ------------------- #
@Client.on_message(filters.command("cancel"))
async def cancel_encode(client, message):
    try:
        filename = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.reply_text("⚠️ Usage: /cancel <filename>")
        return

    removed = False

    # Running task
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
        if ospath.basename(encoder.dl_path) == filename:
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


# ------------------- RESTART BOT ------------------- #
@Client.on_message(filters.command("restart"))
async def restart_bot(client, message):
    if message.from_user.id not in Var.ADMINS:
        await message.reply_text("❌ You are not allowed.")
        return
    await message.reply_text("🔄 Restarting bot...")
    os.execv(sys.executable, ['python'] + sys.argv)
