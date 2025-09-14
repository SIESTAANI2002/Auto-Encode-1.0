import os, aiohttp, aiofiles, asyncio, libtorrent as lt
from time import time
from math import floor
from urllib.parse import urlparse
from pyrogram import Client, filters
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage
from bot import Var

bot = Client("manual_encode_bot", api_id=Var.API_ID, api_hash=Var.API_HASH, bot_token=Var.BOT_TOKEN)

OWNER_ID = Var.OWNER_ID  # Make sure this is set in config

# ----------------------------
# /restart command
# ----------------------------
@bot.on_message(filters.command("restart") & filters.user(OWNER_ID))
async def restart_bot(_, message):
    await message.reply("🔄 Restarting bot...")
    os.execv(__file__, ["python3"] + [__file__])

# ----------------------------
# Download helper
# ----------------------------
async def download_url_or_torrent(message, url):
    filename = os.path.basename(urlparse(url).path) if not url.startswith("magnet:") else "magnet_download"
    path = f"downloads/{filename}"
    prog_msg = await message.reply(f"⬇️ Downloading {filename}...")

    if url.startswith("magnet:") or url.endswith(".torrent"):
        # Torrent download
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

        start_time = time()
        while not handle.is_seed():
            s = handle.status()
            current, total = s.total_done, s.total_wanted
            speed = s.download_rate
            percent = round(current / max(total,1)*100,2)
            bar = floor(percent/8)*"█" + (12-floor(percent/8))*"▒"
            eta = (total - current)/max(speed,1)
            await editMessage(prog_msg, f"""⬇️ Downloading {filename}
[{bar}] {percent}%
Size: {convertBytes(current)}/{convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}""")
            await asyncio.sleep(10)
    else:
        # Direct link
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                total = int(resp.headers.get("Content-Length",0))
                downloaded = 0
                start_time = time()
                async with aiofiles.open(path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024*64):
                        await f.write(chunk)
                        downloaded += len(chunk)
                        percent = round(downloaded / max(total,1) * 100, 2)
                        speed = downloaded / max(time() - start_time,1)
                        eta = (total - downloaded)/max(speed,1)
                        bar = floor(percent/8)*"█" + (12-floor(percent/8))*"▒"
                        await editMessage(prog_msg, f"""⬇️ Downloading {filename}
[{bar}] {percent}%
Size: {convertBytes(downloaded)}/{convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}""")
                        await asyncio.sleep(10)

    await prog_msg.edit(f"⬇️ Download Completed: {filename}")
    return path, filename

# ----------------------------
# Manual encode command
# ----------------------------
@bot.on_message(filters.command("encode") & filters.user(OWNER_ID))
async def manual_encode(_, message):
    if not message.reply_to_message:
        return await message.reply("❌ Reply to a file or send a direct/magnet/torrent link.")

    # Determine source
    if message.reply_to_message.document:
        file = await message.reply_to_message.download()
        filename = message.reply_to_message.document.file_name
    else:
        file = message.text.split(None,1)[1]
        filename = os.path.basename(urlparse(file).path) if not file.startswith("magnet:") else "magnet_download"

    # Download if direct/magnet/torrent
    if not os.path.exists(file):
        file, filename = await download_url_or_torrent(message, file)

    # Encoding
    encoder = FFEncoder(message, file, filename, "1080")  # 1080p only
    out_file = await encoder.start_encode()
    await message.reply(f"✅ Encoding Completed: {out_file}")

# ----------------------------
# Run bot
# ----------------------------
if __name__ == "__main__":
    import asyncio
    from pyrogram import idle

    async def main():
        await bot.start()
        print("Bot started successfully...")
        await idle()
        await bot.stop()

    asyncio.run(main())
