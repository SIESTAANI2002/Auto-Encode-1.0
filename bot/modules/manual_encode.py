from pyrogram import filters
from bot import bot, Var, LOGS
from bot.core.ffencoder import FFEncoder
from asyncio import Queue, Lock, create_task, sleep
from os import remove, path as ospath
import os
from re import findall
import sys

# -------------------- Queue & Lock -------------------- #
ffQueue = Queue()
ffLock = Lock()
ff_queued = {}       # {filename: encoder_instance}
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
            # Download progress
            await msg.edit(f"⬇️ Downloading {filename}...")
            await encoder.message.download(encoder.dl_path, progress=encoder.download_progress)
            await msg.edit(f"⬇️ Download complete. Starting 1080p encoding...")

            # Encode with progress
            last_percent = -5
            encoder_task = create_task(encoder.start_encode())

            while not encoder_task.done():
                if ospath.exists(encoder._FFEncoder__prog_file):
                    try:
                        async with open(encoder._FFEncoder__prog_file, "r") as f:
                            text = await f.read()
                            if (t := findall(r"out_time_ms=(\d+)", text)):
                                time_done = int(t[-1]) / 1000000
                                total = encoder._FFEncoder__total_time or 1
                                percent = min(round(time_done / total * 100, 2), 100)

                                remaining = max(total - time_done, 0)
                                mins, secs = divmod(int(remaining), 60)
                                eta = f"{mins}m {secs}s"

                                if percent - last_percent >= 5:
                                    progress_bar = "█" * int(percent // 8) + "▒" * (12 - int(percent // 8))
                                    await msg.edit(
                                        f"⏳ Encoding {filename}...\n"
                                        f"[{progress_bar}] {percent}%\n"
                                        f"ETA: {eta}"
                                    )
                                    last_percent = percent
                    except Exception as e:
                        LOGS.error(f"Progress read error: {str(e)}")
                await sleep(8)

            output_path = await encoder_task

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
                    if ospath.exists(f):
                        remove(f)

        except Exception as e:
            LOGS.error(f"Queue task failed: {filename} | {str(e)}")
            await msg.edit(f"❌ Task failed: {filename}")

        finally:
            ff_queued.pop(filename, None)
            ffQueue.task_done()

    runner_task = None

# -------------------- Manual Encode Handler -------------------- #
@bot.on_message(filters.document | filters.video)
async def manual_encode(client, message):
    global runner_task

    # Owner check
    if message.from_user.id != int(os.getenv("OWNER_ID")):
        await message.reply_text("❌ Only the owner can use this bot.")
        return

    file_name = message.document.file_name if message.document else message.video.file_name

    # sanitize filename for FFmpeg safety
    safe_name = file_name.replace("{", "").replace("}", "")
    download_path = f"downloads/{safe_name}"

    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    encoder = FFEncoder(message, download_path, safe_name, "1080")
    encoder.msg = msg

    await ffQueue.put(encoder)
    LOGS.info(f"Added {safe_name} to queue")

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

    # currently encoding
    if filename in ff_queued:
        encoder = ff_queued[filename]
        encoder.is_cancelled = True
        removed = True
        await message.reply_text(f"🛑 Cancel request sent for {filename}")
        return

    # waiting queue
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
@bot.on_message(filters.command("restart"))
async def restart_bot(client, message):
    if message.from_user.id != int(os.getenv("OWNER_ID")):
        await message.reply_text("❌ Only the owner can restart the bot.")
        return

    await message.reply_text("♻️ Bot is restarting...")
    LOGS.info("Bot restart triggered by owner.")
    os.execv(sys.executable, ['python'] + sys.argv)
