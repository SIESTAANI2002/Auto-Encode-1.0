# manual_encode.py
import os
import time
from re import findall
from math import floor
from asyncio import sleep as asleep, create_task, gather
from aiofiles import open as aiopen
from aiofiles.os import rename as aiorename
from pyrogram import filters
from pyrogram.types import Message

from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage, sendMessage
from bot import bot, Var, ffQueue, ffLock, ffpids_cache, LOGS

runner_task = None

# Owner-only check
def owner_only(func):
    async def wrapper(client, message: Message):
        if message.from_user.id not in Var.ADMINS:
            await message.reply_text("❌ Only bot owner can use this bot.")
            return
        return await func(client, message)
    return wrapper

# Queue runner
async def queue_runner(client):
    while not ffQueue.empty():
        encoder: FFEncoder = await ffQueue.get()
        await ffLock.acquire()
        try:
            await encoder.start_encode()
        except Exception as e:
            LOGS.error(f"Queue task failed: {e}")
        ffLock.release()

# ------------------ Manual Encode ------------------ #
@bot.on_message(filters.document | filters.video)
@owner_only
async def manual_encode(client, message: Message):
    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"

    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    encoder = FFEncoder(msg, download_path, file_name, "1080")
    encoder.msg = msg

    last_percent_download = 0
    last_percent_encode = 0
    encoder.start_download = time.time()

    # Download progress
    async def download_progress(current, total):
        nonlocal last_percent_download
        percent = round(current / total * 100, 2)
        if percent - last_percent_download >= 5 or percent == 100:
            last_percent_download = percent
            bar = "█"*int(percent/8) + "▒"*(12-int(percent/8))
            speed = current / max(time.time() - encoder.start_download, 0.01)
            eta = (total-current)/max(speed, 0.01)
            text = f"""⬇️ Downloading {file_name}
<code>[{bar}]</code> {percent}%
‣ {convertBytes(speed)}/s
‣ Time Left: {convertTime(eta)}"""
            await msg.edit(text)

    # Start downloading
    await message.download(download_path, progress=download_progress)

    # Encode progress
    original_progress = encoder.progress
    async def encode_progress_override():
        nonlocal last_percent_encode
        encoder._FFEncoder__total_time = await encoder.mediainfo(encoder.dl_path, get_duration=True)
        if isinstance(encoder._FFEncoder__total_time, str):
            encoder._FFEncoder__total_time = 1.0
        while not (encoder._FFEncoder__proc is None or encoder.is_cancelled):
            async with aiopen(encoder._FFEncoder__prog_file, 'r+') as p:
                text = await p.read()
            if text:
                time_done = floor(int(t[-1]) / 1000000) if (t := findall(r"out_time_ms=(\d+)", text)) else 1
                percent = round((time_done/encoder._FFEncoder__total_time)*100, 2)
                if percent - last_percent_encode >= 5 or percent == 100:
                    last_percent_encode = percent
                    ensize = int(s[-1]) if (s := findall(r"total_size=(\d+)", text)) else 0
                    diff = time.time() - encoder._FFEncoder__start_time
                    speed = ensize / diff
                    tsize = ensize / (max(percent, 0.01)/100)
                    eta = (tsize-ensize)/max(speed, 0.01)
                    bar = "█"*int(percent/8) + "▒"*(12-int(percent/8))
                    progress_str = f"""⏳ Encoding {file_name}
<code>[{bar}]</code> {percent}%
‣ {convertBytes(speed)}/s
‣ Time Left: {convertTime(eta)}"""
                    await msg.edit(progress_str)
            await asleep(10)

    encoder.progress = encode_progress_override

    # Add to queue
    await ffQueue.put(encoder)
    LOGS.info(f"Added {file_name} to queue")

    if runner_task is None or runner_task.done():
        create_task(queue_runner(client))

# ------------------ Restart Command ------------------ #
@bot.on_message(filters.command("restart") & filters.user(Var.ADMINS))
async def restart_bot(client, message: Message):
    await message.reply_text("♻️ Restarting bot...")
    os.execv(sys.executable, ['python'] + sys.argv)
