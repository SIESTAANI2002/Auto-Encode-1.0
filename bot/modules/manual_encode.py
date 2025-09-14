import os
import asyncio
import aiohttp
import libtorrent as lt
from math import floor
from re import findall
from time import time
from os import path as ospath
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, rename as aiorename
from pyrogram import filters
from pyrogram.types import Message

from bot import bot, Var, LOGS
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage, sendMessage

# -------------------- Queue -------------------- #
ffQueue = asyncio.Queue()
ff_queued = {}
runner_task = None

# -------------------- Download functions -------------------- #
async def download_telegram_file(message: Message, download_path: str):
    msg = await message.reply_text(f"⬇️ Downloading {message.document.file_name}...")
    start_time = time()
    async def download_progress(current, total):
        diff = time() - start_time
        speed = current / max(diff, 0.01)
        percent = round(current / max(total, 1) * 100, 2)
        bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
        eta = (total-current)/max(speed,0.01)
        progress_str = f"""⬇️ Downloading {message.document.file_name}
    [{bar}] {percent}%
    Size: {convertBytes(current)} / {convertBytes(total)}
    Speed: {convertBytes(speed)}/s
    ETA: {convertTime(eta)}"""
        await editMessage(msg, progress_str)

    await message.download(file_name=download_path, progress=download_progress, progress_args=())
    await msg.edit(f"⬇️ Download completed: {os.path.basename(download_path)}")
    return download_path, msg

async def download_direct_link(url: str, message: Message):
    filename = url.split("/")[-1]
    download_path = f"downloads/{filename}"
    msg = await message.reply_text(f"⬇️ Downloading {filename}...")
    start_time = time()

    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            current = 0
            async with aiopen(download_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024*1024):
                    await f.write(chunk)
                    current += len(chunk)
                    if time() - start_time > 10:  # update every 10s
                        speed = current / max(time()-start_time,0.01)
                        percent = round(current / max(total,1)*100,2)
                        bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
                        eta = (total-current)/max(speed,0.01)
                        progress_str = f"""⬇️ Downloading {filename}
    [{bar}] {percent}%
    Size: {convertBytes(current)} / {convertBytes(total)}
    Speed: {convertBytes(speed)}/s
    ETA: {convertTime(eta)}"""
                        await editMessage(msg, progress_str)
    await msg.edit(f"⬇️ Download completed: {filename}")
    return download_path, msg

async def download_torrent(torrent_url: str, message: Message):
    filename = torrent_url.split("/")[-1] if torrent_url.endswith(".torrent") else "magnet_download"
    download_path = f"downloads/{filename}"
    msg = await message.reply_text(f"⬇️ Downloading torrent {filename}...")
    start_time = time()

    ses = lt.session()
    params = {
        "save_path": "downloads/",
        "storage_mode": lt.storage_mode_t.storage_mode_sparse
    }
    if torrent_url.startswith("magnet:"):
        handle = lt.add_magnet_uri(ses, torrent_url, params)
    else:
        info = lt.torrent_info(torrent_url)
        handle = ses.add_torrent({"ti": info, "save_path": "downloads/"})

    while not handle.is_seed():
        s = handle.status()
        current, total = s.total_done, s.total_wanted
        speed = s.download_rate
        percent = round(current/max(total,1)*100,2)
        bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
        eta = (total-current)/max(speed,1)
        progress_str = f"""⬇️ Downloading torrent {filename}
[{bar}] {percent}%
Size: {convertBytes(current)} / {convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}"""
        await editMessage(msg, progress_str)
        await asyncio.sleep(10)
    await msg.edit(f"⬇️ Torrent Download Completed: {filename}")
    return download_path, msg

# -------------------- Queue Runner -------------------- #
async def queue_runner():
    global runner_task
    while not ffQueue.empty():
        task = await ffQueue.get()
        download_path, message, filename = task
        ff_queued[filename] = task
        msg = await message.reply_text(f"⏳ Encoding {filename}...")
        encoder = FFEncoder(msg, download_path, filename, "1080")
        out_path = await encoder.start_encode()
        await message.reply_document(out_path, caption=f"✅ Encoded 1080p: {filename}")
        ff_queued.pop(filename, None)
        ffQueue.task_done()
    runner_task = None

# -------------------- Manual Encode Handler -------------------- #
@bot.on_message(filters.command("encode") & filters.private)
async def manual_encode(client, message: Message):
    if message.from_user.id != Var.OWNER_ID:
        await message.reply_text("❌ Only owner can use this bot.")
        return

    text = message.text.split(maxsplit=1)[-1] if len(message.text.split()) > 1 else None

    # Telegram file
    if message.document or message.video:
        filename = message.document.file_name if message.document else message.video.file_name
        download_path = f"downloads/{filename}"
        download_path, msg = await download_telegram_file(message, download_path)

    # Direct link
    elif text and text.startswith(("http://","https://")):
        download_path, msg = await download_direct_link(text, message)
        filename = os.path.basename(download_path)

    # Torrent/magnet
    elif text and (text.startswith("magnet:") or text.endswith(".torrent")):
        download_path, msg = await download_torrent(text, message)
        filename = os.path.basename(download_path)
    else:
        await message.reply_text("❌ Please provide a Telegram file, direct link, or torrent/magnet link.")
        return

    # Add to queue
    await ffQueue.put((download_path, message, filename))
    global runner_task
    if runner_task is None or runner_task.done():
        runner_task = asyncio.create_task(queue_runner())
