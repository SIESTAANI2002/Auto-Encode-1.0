import asyncio
import os
from math import floor
from time import time
from urllib.parse import urlparse
from bot.core.func_utils import mediainfo, convertBytes, convertTime, editMessage, sendMessage
from bot.core.ffencoder import FFEncoder
from bot import Var, bot_loop
import aiofiles
import aiohttp
import libtorrent as lt
from pyrogram import Client, filters

ffQueue = asyncio.Queue()
ffLock = asyncio.Lock()

class ManualEncode:
    def __init__(self, bot):
        self.bot = bot

    async def handle(self, message):
        if message.from_user.id != Var.OWNER_ID:
            return await message.reply_text("❌ You are not authorized to use this bot.")

        # Determine source: Telegram file, direct link, or magnet/torrent
        if message.document:
            path = await self.download_telegram_file(message)
            filename = message.document.file_name
        elif message.text and (message.text.startswith("http") or message.text.startswith("magnet:")):
            path, filename = await self.download_url_or_torrent(message, message.text)
        else:
            return await message.reply_text("Send a file, magnet link, or direct link to encode.")

        # Queue task
        await ffQueue.put((message, path, filename))
        if ffLock.locked():
            await message.reply_text("⏳ Task queued to encode...")
        else:
            asyncio.create_task(self.process_queue())

    async def process_queue(self):
        async with ffLock:
            while not ffQueue.empty():
                message, path, filename = await ffQueue.get()
                await self.start_encoding(message, path, filename)

    async def download_telegram_file(self, message):
        msg = await message.reply_text(f"⬇️ Downloading {message.document.file_name}...")
        path = f"downloads/{message.document.file_name}"
        await message.download(path)
        await msg.edit(f"⬇️ Download Completed: {message.document.file_name}")
        return path

    async def download_url_or_torrent(self, message, url):
        if url.startswith("magnet:") or url.endswith(".torrent"):
            return await self.download_torrent(message, url)
        else:
            return await self.download_direct_link(message, url)

    async def download_direct_link(self, message, url):
        filename = os.path.basename(urlparse(url).path)
        path = f"downloads/{filename}"
        msg = await message.reply_text(f"⬇️ Downloading {filename}...")
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                start_time = time()
                async with aiofiles.open(path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024*64):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        percent = round(downloaded / max(total,1) * 100, 2)
                        speed = downloaded / max(time() - start_time,1)
                        eta = (total - downloaded) / max(speed,1)
                        bar = floor(percent/8)*"█" + (12-floor(percent/8))*"▒"
                        await editMessage(msg, f"""⬇️ Downloading {filename}
[{bar}] {percent}%
Size: {convertBytes(downloaded)}/{convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}""")
                        await asyncio.sleep(10)
        await msg.edit(f"⬇️ Download Completed: {filename}")
        return path, filename

    async def download_torrent(self, message, url):
        filename = url.split("/")[-1] if url.endswith(".torrent") else "magnet_download"
        msg = await message.reply_text(f"⬇️ Downloading torrent {filename}...")
        ses = lt.session()
        ses.listen_on(6881, 6891)
        params = {"save_path": "downloads/", "storage_mode": lt.storage_mode_t.storage_mode_sparse}

        if url.startswith("magnet:"):
            handle = lt.add_magnet_uri(ses, url, params)
            while not handle.has_metadata():
                await asyncio.sleep(1)
        else:
            info = lt.torrent_info(url)
            handle = ses.add_torrent({"ti": info, "save_path": "downloads/"})

        await asyncio.sleep(1)
        info = handle.get_torrent_info()
        if info.num_files() == 1:
            filename = info.files()[0].path
        path = f"downloads/{filename}"
        start_time = time()

        while not handle.is_seed():
            s = handle.status()
            current, total = s.total_done, s.total_wanted
            speed = s.download_rate
            percent = round(current / max(total, 1) * 100, 2)
            bar = floor(percent/8)*"█" + (12-floor(percent/8))*"▒"
            eta = (total - current) / max(speed,1)
            await editMessage(msg, f"""⬇️ Downloading {filename}
[{bar}] {percent}%
Size: {convertBytes(current)}/{convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}""")
            await asyncio.sleep(10)

        await msg.edit(f"⬇️ Torrent Download Completed: {filename}")
        return path, filename

    async def start_encoding(self, message, path, filename):
        anime_name = filename
        qual = "1080"
        encoder = FFEncoder(message, path, anime_name, qual)
        out_path = await encoder.start_encode()
        await message.reply_text(f"✅ Encoding Completed: {anime_name}\nPath: {out_path}")

# /restart command
@Client.on_message(filters.command("restart") & filters.user(Var.OWNER_ID))
async def restart_bot(client, message):
    await message.reply_text("🔄 Restarting bot...")
    os.execv(sys.executable, [sys.executable] + sys.argv)
