import os
import asyncio
import shutil
import libtorrent as lt
from time import time, sleep
from math import floor
from re import findall
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, rename as aiorename
from asyncio.subprocess import PIPE, create_subprocess_shell
from pyrogram import Client, filters
from pyrogram.types import Message

from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import mediainfo, convertBytes, convertTime, editMessage, sendMessage
from bot import Var
from bot import bot_loop, LOGS

# -------------------
# TELEGRAM FILE DOWNLOAD
# -------------------
async def download_telegram_file(message: Message, out_path: str):
    await message.download(file_name=out_path)
    return out_path

# -------------------
# MAGNET LINK DOWNLOAD
# -------------------
async def download_magnet(magnet: str, out_path: str, message: Message=None):
    def blocking_download():
        ses = lt.session()
        ses.listen_on(6881, 6891)
        params = {'save_path': os.path.dirname(out_path)}
        handle = lt.add_magnet_uri(ses, magnet, params)
        while not handle.has_metadata():
            sleep(1)
        info = handle.get_torrent_info()
        fname = info.files()[0].path
        total = info.total_size()
        while handle.status().state != lt.torrent_status.seeding:
            s = handle.status()
            percent = s.progress * 100
            speed = s.download_rate
            eta = (total - s.total_done)/max(speed,1)
            if message:
                asyncio.run_coroutine_threadsafe(
                    editMessage(message, f"⬇️ Downloading: {fname}\n[{floor(percent/8)*'█'+(12-floor(percent/8))*'▒'}] {percent:.2f}%\nSpeed: {convertBytes(speed)}/s ETA: {convertTime(eta)}"),
                    asyncio.get_event_loop()
                )
            sleep(10)
        shutil.move(os.path.join(os.path.dirname(out_path), fname), out_path)
        return out_path

    return await asyncio.to_thread(blocking_download)

# -------------------
# MANUAL ENCODE HANDLER
# -------------------
@Client.on_message(filters.command("manual") & filters.user(Var.OWNER_ID))
async def manual_encode_handler(bot, message: Message):
    # Determine input type
    if message.document or message.video:
        out_file = f"downloads/{message.document.file_name if message.document else message.video.file_name}"
        await download_telegram_file(message, out_file)
    elif message.text and message.text.startswith("magnet:"):
        fname = message.text.split("&dn=")[-1].split("&")[0]
        out_file = f"downloads/{fname}.mkv"
        await download_magnet(message.text, out_file, message)
    else:
        await message.reply_text("❌ Unsupported input. Send Telegram file or magnet link.")
        return

    # Start encoding 1080p
    encoder = FFEncoder(message=message, path=out_file, name=os.path.basename(out_file), qual="1080")
    out_path = await encoder.start_encode()
    if out_path:
        await message.reply_text(f"✅ Encoding completed!\nSaved: {out_path}")
    else:
        await message.reply_text("❌ Encoding failed.")

# -------------------
# RESTART BOT COMMAND
# -------------------
@Client.on_message(filters.command("restart") & filters.user(Var.OWNER_ID))
async def restart_bot(_, message: Message):
    await message.reply_text("♻️ Restarting bot...")
    os.execv(sys.executable, ['python3'] + sys.argv)
