from pyrogram import filters
from asyncio import Queue, Lock, create_task, sleep
from time import time
from os import path as ospath, remove

from bot import bot, Var, LOGS
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage

# -------------------- Queue & Lock -------------------- #
ffQueue = Queue()
ffLock = Lock()
ff_queued = {}
runner_task = None

# -------------------- Download Progress -------------------- #
async def download_progress(current, total, msg, filename):
    percent = round(current / total * 100, 2)
    bar = "█" * int(percent // 8) + "▒" * (12 - int(percent // 8))
    elapsed = max(time() - download_progress.start_time, 0.01)
    speed = current / elapsed
    eta = (total - current) / speed
    await msg.edit(
        f"⬇️ Downloading {filename}...\n"
        f"[{bar}] {percent}%\n"
        f"Downloaded: {convertBytes(current)} / {convertBytes(total)}\n"
        f"Speed: {convertBytes(speed)}/s\n"
        f"ETA: {int(eta // 60)}m {int(eta % 60)}s"
    )

# -------------------- Queue Runner -------------------- #
async def queue_runner(client):
    global runner_task
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        filename = ospath.basename(encoder.dl_path)
        ff_queued[filename] = encoder
        msg = encoder.msg

        try:
            # Download
            download_progress.start_time = time()
            await msg.edit(f"⬇️ Downloading {filename}...")
            await encoder.message.download(
                file_name=encoder.dl_path,
                progress=lambda cur, tot: create_task(download_progress(cur, tot, msg, filename))
            )

            await msg.edit(f"⏳ Download completed. Starting 1080p encoding...")

            # Encode
            encoder_task = create_task(encoder.start_encode())
            last_percent = -5
            while not encoder_task.done():
                if ospath.exists(encoder._FFEncoder__prog_file):
                    try:
                        async with open(encoder._FFEncoder__prog_file, 'r') as f:
                            text = await f.read()
                        if text:
                            t_done = [int(x)/1000000 for x in findall(r"out_time_ms=(\d+)", text)]
                            time_done = t_done[-1] if t_done else 1
                            total_time = encoder._FFEncoder__total_time or 1
                            percent = round(time_done/total_time*100,2)
                            if percent - last_percent >=5:
                                # progress string
                                bar = "█" * int(percent//8) + "▒"*(12-int(percent//8))
                                eta = (total_time - time_done)
                                mins, secs = divmod(int(eta), 60)
                                await msg.edit(
                                    f"⏳ Encoding {filename}...\n"
                                    f"[{bar}] {percent}%\n"
                                    f"Time Left: {mins}m {secs}s"
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

# -------------------- Manual Encode -------------------- #
@bot.on_message(filters.document | filters.video)
async def manual_encode(client, message):
    if message.from_user.id not in Var.ADMINS:
        return await message.reply_text("⚠️ You are not allowed to use this bot!")

    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"

    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    # FFEncoder: original message for download, bot reply for progress
    encoder = FFEncoder(message, download_path, file_name, "1080")
    encoder.msg = msg
    await ffQueue.put(encoder)
    LOGS.info(f"Added {file_name} to queue")

    # Start runner
    global runner_task
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
            status_lines.append(f"⏳ Waiting: {ospath.basename(encoder.dl_path)}")

    if not status_lines:
        await message.reply_text("📭 No files are currently queued.")
    else:
        await message.reply_text("\n".join(status_lines))

# -------------------- Cancel -------------------- #
@bot.on_message(filters.command("cancel"))
async def cancel_encode(client, message):
    try:
        filename = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.reply_text("⚠️ Usage: /cancel <filename>")
        return

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

# -------------------- Restart Bot -------------------- #
@bot.on_message(filters.command("restart"))
async def restart_bot(client, message):
    if message.from_user.id not in Var.ADMINS:
        return await message.reply_text("⚠️ Only owner can restart!")
    await message.reply_text("♻️ Restarting bot...")
    import sys, os
    os.execv(sys.executable, ['python3'] + sys.argv)
