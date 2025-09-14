import asyncio, os, re, aiohttp, libtorrent as lt, shutil
from math import floor
from time import time
from aiofiles import open as aiopen
from aiofiles.os import rename as aiorename, remove as aioremove
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage, sendMessage
from bot import Var
from pyrogram import Client, filters
from pyrogram.types import Message

bot = Client("AutoBot", api_id=Var.API_ID, api_hash=Var.API_HASH, bot_token=Var.BOT_TOKEN)

# -------------------- DOWNLOAD HELPERS -------------------- #

async def download_telegram_file(message: Message, out_path: str):
    start_time = time()
    downloaded = 0
    total = message.document.file_size if message.document else message.video.file_size
    async with aiopen(out_path, 'wb') as f:
        async for chunk in message.download(in_memory=True, chunk_size=1024*1024):
            await f.write(chunk)
            downloaded += len(chunk)
            percent = downloaded/total*100
            speed = downloaded/(time()-start_time)
            eta = (total-downloaded)/max(speed,0.01)
            bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
            progress_str = f"""⬇️ <b>Downloading:</b> {message.document.file_name if message.document else message.video.file_name}
<code>[{bar}]</code> {percent:.2f}%
Speed: {convertBytes(speed)}/s | ETA: {convertTime(eta)}"""
            await editMessage(message, progress_str)
            await asyncio.sleep(10)
    return out_path

async def download_url_file(url: str, out_path: str, message: Message=None):
    start_time = time()
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            total = int(resp.headers.get('content-length', 0))
            downloaded = 0
            async with aiopen(out_path, 'wb') as f:
                async for chunk in resp.content.iter_chunked(1024*1024):
                    await f.write(chunk)
                    downloaded += len(chunk)
                    if message:
                        percent = downloaded/total*100 if total else 0
                        speed = downloaded/(time()-start_time)
                        eta = (total-downloaded)/max(speed,0.01) if total else 0
                        bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
                        progress_str = f"""⬇️ <b>Downloading:</b> {os.path.basename(out_path)}
<code>[{bar}]</code> {percent:.2f}%
Speed: {convertBytes(speed)}/s | ETA: {convertTime(eta)}"""
                        await editMessage(message, progress_str)
                        await asyncio.sleep(10)
    return out_path

async def download_magnet(magnet: str, out_path: str, message: Message=None):
    ses = lt.session()
    ses.listen_on(6881, 6891)
    params = {'save_path': os.path.dirname(out_path)}
    handle = lt.add_magnet_uri(ses, magnet, params)
    while not handle.has_metadata():
        await asyncio.sleep(1)
    info = handle.get_torrent_info()
    fname = info.files()[0].path
    total = info.total_size()
    start_time = time()
    while handle.status().state != lt.torrent_status.seeding:
        s = handle.status()
        percent = s.progress * 100
        speed = s.download_rate
        eta = (total - s.total_done)/max(speed,1)
        bar = floor(percent/8)*"█" + (12 - floor(percent/8))*"▒"
        if message:
            progress_str = f"""⬇️ <b>Downloading:</b> {fname}
<code>[{bar}]</code> {percent:.2f}%
Speed: {convertBytes(speed)}/s | ETA: {convertTime(eta)}"""
            await editMessage(message, progress_str)
        await asyncio.sleep(10)
    # Move file to out_path
    src = os.path.join(os.path.dirname(out_path), fname)
    shutil.move(src, out_path)
    return out_path

# -------------------- COMMAND HANDLERS -------------------- #

@bot.on_message(filters.command("manual") & filters.user(Var.OWNER_ID))
async def manual_encode_handler(client, message):
    text = message.text.split(maxsplit=1)
    if len(text) < 2:
        await sendMessage(message, "Send a file, magnet or URL after /manual")
        return
    link_or_file = text[1]
    filename = None

    # Determine type
    if message.document or message.video:
        filename = f"./downloads/{message.document.file_name if message.document else message.video.file_name}"
        await download_telegram_file(message, filename)
    elif link_or_file.startswith("magnet:"):
        filename = f"./downloads/{link_or_file.split('dn=')[-1].split('&')[0]}.mkv"
        await download_magnet(link_or_file, filename, message)
    elif link_or_file.startswith("http"):
        filename = f"./downloads/{os.path.basename(link_or_file)}"
        await download_url_file(link_or_file, filename, message)
    else:
        await sendMessage(message, "Unsupported input!")
        return

    await sendMessage(message, f"⬆️ Download complete: {os.path.basename(filename)}\n⏳ Starting Encoding…")

    # Start Encoding
    encoder = FFEncoder(message, filename, os.path.basename(filename), "1080")
    out_path = await encoder.start_encode()
    if out_path:
        await sendMessage(message, f"✅ Encoding complete: {os.path.basename(out_path)}")

# -------------------- RESTART COMMAND -------------------- #

@bot.on_message(filters.command("restart") & filters.user(Var.OWNER_ID))
async def restart_bot(client, message):
    await sendMessage(message, "🔄 Restarting Bot…")
    os.execv(__file__, ["python3"] + sys.argv)

# -------------------- START BOT -------------------- #

if __name__ == "__main__":
    os.makedirs("./downloads", exist_ok=True)
    bot.run()
