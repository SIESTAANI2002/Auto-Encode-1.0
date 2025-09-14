import os
import asyncio
import aiohttp
import libtorrent as lt
from math import floor
from time import time
from re import findall
from aiofiles import open as aiopen, os as aioos
from pyrogram import filters
from bot import bot, Var, LOGS
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage, sendMessage

ffQueue = asyncio.Queue()
ff_queued = {}
runner_task = None

# ---------- OWNER CHECK ---------- #
def owner_only(func):
    async def wrapper(client, message):
        if message.from_user.id != Var.OWNER_ID:
            await message.reply_text("⚠️ Only the owner can use this bot!")
            return
        await func(client, message)
    return wrapper

# ---------- DOWNLOAD HELPERS ---------- #
async def download_file(message):
    file_name = message.document.file_name if message.document else message.video.file_name
    path = f"downloads/{file_name}"
    msg = await message.reply_text(f"⬇️ Downloading {file_name}...")
    await message.download(path)
    await msg.edit(f"⬇️ Download Completed: {file_name}")
    return path, msg, file_name

async def download_direct(url, message):
    file_name = url.split("/")[-1]
    path = f"downloads/{file_name}"
    msg = await message.reply_text(f"⬇️ Downloading {file_name} from link...")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            total = int(resp.headers.get("Content-Length", 1))
            downloaded = 0
            start = time()
            async with aiopen(path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024*64):
                    await f.write(chunk)
                    downloaded += len(chunk)
                    percent = round(downloaded/total*100,2)
                    bar = floor(percent/8)*"█" + (12-floor(percent/8))*"▒"
                    speed = downloaded / max(time()-start,1)
                    eta = (total-downloaded)/max(speed,1)
                    if int(time()) % 10 == 0:
                        await editMessage(msg, f"""⬇️ Downloading {file_name}
[{bar}] {percent}%
Size: {convertBytes(downloaded)}/{convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}""")
    await msg.edit(f"⬇️ Download Completed: {file_name}")
    return path, msg, file_name

async def download_torrent(url, message):
    filename = url.split("/")[-1] if url.endswith(".torrent") else "magnet_download"
    msg = await message.reply_text(f"⬇️ Downloading torrent {filename}...")

    ses = lt.session()
    ses.listen_on(6881, 6891)
    params = {"save_path": "downloads/", "storage_mode": lt.storage_mode_t.storage_mode_sparse}

    if url.startswith("magnet:"):
        handle = lt.add_magnet_uri(ses, url, params)
        while not handle.has_metadata():
            await asyncio.sleep(2)
    else:
        info = lt.torrent_info(url)
        handle = ses.add_torrent({"ti": info, "save_path": "downloads/"})

    info = handle.get_torrent_info()
    filename = info.files()[0].path if info.num_files() == 1 else filename
    path = f"downloads/{filename}"

    while not handle.is_seed():
        s = handle.status()
        current, total = s.total_done, s.total_wanted
        speed = s.download_rate
        percent = round(current/max(total,1)*100,2)
        bar = floor(percent/8)*"█" + (12-floor(percent/8))*"▒"
        eta = (total-current)/max(speed,1)
        await editMessage(msg, f"""⬇️ Downloading {filename}
[{bar}] {percent}%
Size: {convertBytes(current)}/{convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}""")
        await asyncio.sleep(10)

    await msg.edit(f"⬇️ Torrent Download Completed: {filename}")
    return path, msg, filename

# ---------- QUEUE RUNNER ---------- #
async def queue_runner(client):
    global runner_task
    while not ffQueue.empty():
        task = await ffQueue.get()
        path, msg, filename = task["path"], task["msg"], task["name"]
        ff_queued[filename] = msg

        try:
            await msg.edit(f"⏳ Encoding {filename}...")
            encoder = FFEncoder(msg, path, filename, "1080")
            out_path = await encoder.start_encode()
            await msg.edit(f"✅ Encoding Completed: {filename}")

            if Var.AUTO_DEL:
                for f in [path, out_path]:
                    if os.path.exists(f):
                        os.remove(f)
        except Exception as e:
            LOGS.error(f"Queue task failed: {filename} | {str(e)}")
            await msg.edit(f"❌ Task failed: {filename}")
        finally:
            ff_queued.pop(filename, None)
            ffQueue.task_done()
    runner_task = None

# ---------- MANUAL ENCODE HANDLER ---------- #
@bot.on_message(filters.command("encode") | filters.document | filters.video)
@owner_only
async def manual_encode(client, message):
    global runner_task
    file_url = message.text.split(maxsplit=1)[1] if len(message.text.split())>1 else None

    if message.document or message.video:
        path, msg, filename = await download_file(message)
    elif file_url and file_url.startswith(("http://","https://")):
        path, msg, filename = await download_direct(file_url, message)
    elif file_url and file_url.startswith("magnet:"):
        path, msg, filename = await download_torrent(file_url, message)
    else:
        await message.reply_text("⚠️ Send a Telegram file, direct link, or magnet link.")
        return

    await ffQueue.put({"path": path, "msg": msg, "name": filename})
    if runner_task is None or runner_task.done():
        runner_task = asyncio.create_task(queue_runner(client))

# ---------- QUEUE STATUS ---------- #
@bot.on_message(filters.command("queue"))
@owner_only
async def queue_status(client, message):
    lines = []
    for f, msg in ff_queued.items():
        lines.append(f"▶️ Encoding: {f}")
    if not ffQueue.empty():
        for t in list(ffQueue._queue):
            lines.append(f"⏳ Waiting: {t['name']}")
    await message.reply_text("\n".join(lines) if lines else "📭 No files queued.")

# ---------- CANCEL TASK ---------- #
@bot.on_message(filters.command("cancel"))
@owner_only
async def cancel_encode(client, message):
    try:
        filename = message.text.split(maxsplit=1)[1]
    except IndexError:
        await message.reply_text("⚠️ Usage: /cancel <filename>")
        return

    removed = False
    if filename in ff_queued:
        # Cancel running
        encoder_msg = ff_queued[filename]
        # We can't kill FFEncoder here, needs external handling
        removed = True
        await message.reply_text(f"🛑 Cancel request sent for {filename}")
    else:
        temp_queue = []
        while not ffQueue.empty():
            t = await ffQueue.get()
            if t['name'] == filename:
                removed = True
                ffQueue.task_done()
            else:
                temp_queue.append(t)
                ffQueue.task_done()
        for t in temp_queue:
            await ffQueue.put(t)
        if removed:
            await message.reply_text(f"🗑️ {filename} removed from queue.")
        else:
            await message.reply_text(f"❌ File {filename} not found.")

# ---------- RESTART BOT ---------- #
@bot.on_message(filters.command("restart"))
@owner_only
async def restart_bot(client, message):
    await message.reply_text("🔄 Restarting bot...")
    os.execv(sys.executable, ['python3'] + sys.argv)
