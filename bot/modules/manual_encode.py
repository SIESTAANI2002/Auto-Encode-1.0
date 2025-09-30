import os
import time
import asyncio
from asyncio import Queue, Lock, create_task
from os import remove, path as ospath
import aiofiles
from re import findall
import psutil
import datetime

from pyrogram import filters
from bot import bot, Var, LOGS
from bot.core.ffencoder import FFEncoder
from bot.core import gdrive_uploader
from bot.core.func_utils import convertBytes  # ensure exists

# -------------------- Queue & Lock -------------------- #
ffQueue = Queue()
ffLock = Lock()
ff_queued = {}
runner_task = None

# -------------------- FF-style Progress -------------------- #
async def update_progress(msg, file_name, percent, start_time, ensize=0, total_size=0, status="Processing"):
    elapsed = time.time() - start_time
    speed = ensize / max(elapsed, 1)
    eta = (total_size - ensize) / max(speed, 0.01)

    bar_len = 12
    filled = int(bar_len * percent / 100)
    empty = bar_len - filled
    bar = f"[{'█'*filled}{'▒'*empty}] {percent:.2f}%"

    mins_eta, secs_eta = divmod(int(eta), 60)
    el_m, el_s = divmod(int(elapsed), 60)

    # --- System Stats ---
    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent
    free = psutil.disk_usage('/').free / (1024**3)  # GB
    uptime_seconds = time.time() - psutil.boot_time()
    uptime_str = str(datetime.timedelta(seconds=int(uptime_seconds)))

    progress_text = f"""<blockquote>‣ <b>Anime Name :</b> <b><i>{file_name}</i></b></blockquote>
<blockquote>‣ <b>Status :</b> <i>{status}</i>
    {bar}</blockquote>
<blockquote>   ‣ <b>Size :</b> {convertBytes(ensize)} out of ~ {convertBytes(total_size)}
    ‣ <b>Speed :</b> {convertBytes(speed)}/s
    ‣ <b>Time Took :</b> {el_m}m {el_s}s
    ‣ <b>Time Left :</b> {mins_eta}m {secs_eta}s</blockquote>
<blockquote>‣ <b>System Stats:</b> CPU: {cpu}% | RAM: {ram}% | FREE: {free:.2f}GB | UPTIME: {uptime_str}</blockquote>"""
    await msg.edit(progress_text)

# -------------------- Download Helper -------------------- #
async def download_file(message, path, msg):
    start_time = time.time()
    last_update = 0

    async def progress(current, total):
        nonlocal last_update
        percent = (current / total) * 100
        now = time.time()
        if now - last_update >= 10:  # update every 10s
            await update_progress(
                msg,
                message.document.file_name if message.document else message.video.file_name,
                percent,
                start_time,
                current,
                total,
                status="Downloading"
            )
            last_update = now

    if message.document:
        await message.download(file_name=path, progress=progress)
    else:
        await message.download(file_name=path, progress=progress)

# -------------------- Upload Helper -------------------- #
async def upload_file(client, chat_id, path, msg, caption):
    start_time = time.time()
    last_update = 0
    total = ospath.getsize(path)

    async def progress(current, total_size):
        nonlocal last_update
        percent = (current / total_size) * 100
        now = time.time()
        if now - last_update >= 10:
            await update_progress(
                msg,
                os.path.basename(path),
                percent,
                start_time,
                current,
                total_size,
                status="Uploading"
            )
            last_update = now

    await client.send_document(
        chat_id=chat_id,
        document=path,
        caption=caption,
        progress=progress
    )

# -------------------- Queue Runner -------------------- #
async def queue_runner(client):
    global runner_task
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        filename = os.path.basename(encoder.dl_path)
        ff_queued[filename] = encoder
        msg = encoder.msg

        try:
            # -------------------- Download -------------------- #
            await msg.edit(f"⏳ **Downloading {filename}...**")
            await download_file(encoder.message, encoder.dl_path, msg)
            await msg.edit(f"⬇️ **Download completed. Starting 720p encoding...**")

            # -------------------- Encoding -------------------- #
            encode_task = create_task(encoder.start_encode())

            while not ospath.exists(encoder._FFEncoder__prog_file):
                await asyncio.sleep(1)

            start_time = time.time()
            last_update = 0

            while not encode_task.done():
                try:
                    async with aiofiles.open(encoder._FFEncoder__prog_file, "r") as f:
                        text = await f.read()
                        if text:
                            t = [int(x) for x in findall(r"out_time_ms=(\d+)", text)]
                            s = [int(x) for x in findall(r"total_size=(\d+)", text)]
                            time_done = t[-1]/1000000 if t else 0
                            ensize = s[-1] if s else 0
                            total_size = ensize * (encoder._FFEncoder__total_time / max(time_done, 1))
                            percent = min((time_done/encoder._FFEncoder__total_time)*100, 100)

                            now = time.time()
                            if now - last_update >= 10:
                                await update_progress(msg, filename, percent, start_time, ensize, total_size, status="Encoding")
                                last_update = now
                except Exception as e:
                    LOGS.error(f"Progress read error: {str(e)}")
                await asyncio.sleep(1)

            output_path = await encode_task

            # -------------------- Upload -------------------- #
            await upload_file(
                client,
                Var.MAIN_CHANNEL,
                output_path or encoder.dl_path,
                msg,
                f"✅ **Encoded 720p: {filename}**"
            )

            try:
                await gdrive_uploader.upload_to_drive(output_path or encoder.dl_path)
            except Exception as e:
                LOGS.error(f"GDrive upload failed for {filename}: {str(e)}")

            await msg.edit(f"✅ **Processing finished: {filename}**")

            # Auto-delete
            if Var.AUTO_DEL:
                for f in [encoder.dl_path, output_path]:
                    if f and ospath.exists(f):
                        remove(f)

        except Exception as e:
            LOGS.error(f"Queue task failed: {filename} | {str(e)}")
            await msg.edit(f"❌ **Task failed: {filename}**")

        finally:
            ff_queued.pop(filename, None)
            ffQueue.task_done()

    runner_task = None

# -------------------- Manual Encode Handler -------------------- #
@bot.on_message(filters.document | filters.video)
async def manual_encode(client, message):
    global runner_task
    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"

    msg = await message.reply_text(f"⏳ **Queued: {file_name}**")

    encoder = FFEncoder(message, download_path, file_name, "720")
    encoder.msg = msg

    await ffQueue.put(encoder)
    LOGS.info(f"Added {file_name} to queue")

    if runner_task is None or runner_task.done():
        runner_task = create_task(queue_runner(client))

# -------------------- Queue Status Command -------------------- #
@bot.on_message(filters.command("queue"))
async def queue_status(client, message):
    status_lines = []

    for fname in ff_queued.keys():
        status_lines.append(f"▶️ **Encoding: {fname}**")

    if not ffQueue.empty():
        for encoder in list(ffQueue._queue):
            filename = os.path.basename(encoder.dl_path)
            status_lines.append(f"⏳ **Waiting: {filename}**")

    if not status_lines:
        await message.reply_text("📭 **No files are currently queued.**")
    else:
        await message.reply_text("\n".join(status_lines))

# -------------------- Cancel Command -------------------- #  
@bot.on_message(filters.command("cancel"))  
async def cancel_encode(client, message):  
    try:  
        filename = message.text.split(maxsplit=1)[1]  
    except IndexError:  
        await message.reply_text("⚠️ **Usage: /cancel <filename>**")  
        return  
  
    removed = False  
  
    if filename in ff_queued:  
        encoder = ff_queued[filename]  
        encoder.is_cancelled = True  
        removed = True  
        await message.reply_text(f"🛑 **Cancel request sent for {filename}**")  
        return  
  
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
        await message.reply_text(f"🗑️ **{filename} removed from queue.**")  
    else:  
        await message.reply_text(f"❌ **File {filename} not found in queue.**")  
