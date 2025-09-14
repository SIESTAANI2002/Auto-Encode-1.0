from bot import bot, Var, LOGS
from pyrogram import filters
from asyncio import Queue, Lock, create_task, sleep
from bot.core.ffencoder import FFEncoder
from os import remove, path as ospath
import os
from re import findall

# -------------------- Queue & Lock -------------------- #
ffQueue = Queue()        # waiting tasks
ffLock = Lock()          # ensures only one runner at a time
ff_queued = {}           # currently running tasks {filename: encoder_instance}
runner_task = None       # reference to the queue runner task

# -------------------- Queue Runner -------------------- #
async def queue_runner(client):
    global runner_task
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        filename = os.path.basename(encoder.dl_path)
        ff_queued[filename] = encoder        # mark as running
        msg = encoder.msg  # bot message for progress

        try:
            # Download
            await msg.edit(f"⬇️ Downloading {filename}...")
            await encoder.message.download(encoder.dl_path)
            await msg.edit(f"⬇️ Download completed. Starting 720p encoding...")

            # Encode with optimized progress + ETA
            progress_file = encoder._FFEncoder__prog_file
            encoder_task = create_task(encoder.start_encode())

            last_percent = -5  # track last updated percent
            while not encoder_task.done():
                if ospath.exists(progress_file):
                    try:
                        with open(progress_file, "r") as f:
                            text = f.read()
                            if (t := findall(r"out_time_ms=(\d+)", text)):
                                time_done = int(t[-1]) / 1000000
                                total = encoder._FFEncoder__total_time or 1
                                percent = min(round(time_done / total * 100, 2), 100)

                                # Calculate remaining time
                                remaining = max(total - time_done, 0)
                                mins, secs = divmod(int(remaining), 60)
                                eta = f"{mins}m {secs}s"

                                # Update Telegram only if percent changed by 5%
                                if percent - last_percent >= 5:
                                    await msg.edit(f"⏳ Encoding {filename}... {percent}% | ETA: {eta}")
                                    last_percent = percent
                    except Exception as e:
                        LOGS.error(f"Progress read error: {str(e)}")
                await sleep(10)

            output_path = await encoder_task

            # Upload
            await client.send_document(
                chat_id=Var.MAIN_CHANNEL,
                document=output_path,
                caption=f"✅ Encoded 720p: {filename}"
            )
            await msg.edit(f"✅ Encoding and upload finished: {filename}")

            # Auto-delete if enabled
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

    runner_task = None  # mark runner as stopped when queue is empty

# -------------------- Manual Encode Handler -------------------- #
@bot.on_message(filters.document | filters.video)
async def manual_encode(client, message):
    global runner_task
    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"

    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    # FFEncoder: original message for download, bot reply for progress
    encoder = FFEncoder(message, download_path, file_name, "720")
    encoder.msg = msg  # bot reply

    await ffQueue.put(encoder)
    LOGS.info(f"Added {file_name} to queue")

    # Start runner only if not already running
    if runner_task is None or runner_task.done():
        runner_task = create_task(queue_runner(client))

# -------------------- Queue Status Command -------------------- #
@bot.on_message(filters.command("queue"))
async def queue_status(client, message):
    status_lines = []

    # Currently running task
    for fname, encoder in ff_queued.items():
        status_lines.append(f"▶️ Encoding: {fname}")

    # Waiting tasks
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

    # Check currently encoding
    if filename in ff_queued:
        encoder = ff_queued[filename]
        encoder.is_cancelled = True
        removed = True
        await message.reply_text(f"🛑 Cancel request sent for {filename}")
        return

    # Check waiting queue
    temp_queue = []
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        if os.path.basename(encoder.dl_path) == filename:
            removed = True
            LOGS.info(f"Removed {filename} from waiting queue")
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
