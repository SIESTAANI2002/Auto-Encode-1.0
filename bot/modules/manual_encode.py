from pyrogram import filters
from asyncio import create_task
from bot import bot, Var, ffQueue, ffLock, ff_queued, LOGS
from bot.core.ffencoder import FFEncoder
from os import remove, path as ospath
import sys, time
import asyncio

# -------------------- Queue & Lock -------------------- #
runner_task = None

# -------------------- Queue Runner -------------------- #
async def queue_runner(client):
    global runner_task
    while not ffQueue.empty():
        encoder = await ffQueue.get()
        filename = ospath.basename(encoder.dl_path)
        ff_queued[filename] = encoder        # mark as running
        msg = encoder.msg  # bot message for progress

        try:
            # Download progress
            async def download_progress(current, total):
                percent = round(current/total*100, 2)
                bar = "█"*int(percent/8) + "▒"*(12-int(percent/8))
                speed = current / max(time.time() - encoder.start_download, 0.01)
                eta = (total-current)/max(speed, 0.01)
                text = f"""⬇️ Downloading {filename}
<code>[{bar}]</code> {percent}%
‣ {convert_bytes(speed)}/s
‣ Time Left: {convert_time(eta)}"""
                await editMessage(msg, progress_str)
                
            encoder.start_download = time.time()
            await encoder.message.download(encoder.dl_path, progress=download_progress)

            # Encode with optimized progress
            await msg.edit(f"⏳ Encoding {filename}...")
            output_path = await encoder.start_encode()

            # Upload
            await client.send_document(
                chat_id=Var.MAIN_CHANNEL,
                document=output_path,
                caption=f"✅ Encoded 1080p: {filename}"
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

    runner_task = None

# -------------------- Manual Encode -------------------- #
@bot.on_message(filters.document | filters.video)
async def manual_encode(client, message):
    global runner_task
    if message.from_user.id not in Var.ADMINS:
        return await message.reply_text("❌ Only bot owner can use this bot.")

    file_name = message.document.file_name if message.document else message.video.file_name
    download_path = f"downloads/{file_name}"

    msg = await message.reply_text(f"⏳ Queued: {file_name}")

    encoder = FFEncoder(message, download_path, file_name, "1080")
    encoder.msg = msg

    await ffQueue.put(encoder)
    LOGS.info(f"Added {file_name} to queue")

    if runner_task is None or runner_task.done():
        runner_task = create_task(queue_runner(client))

# -------------------- Queue Status Command -------------------- #
@bot.on_message(filters.command("queue") & filters.user(Var.ADMINS))
async def queue_status(client, message):
    status_lines = []

    for fname, encoder in ff_queued.items():
        status_lines.append(f"▶️ Encoding: {fname}")

    if not ffQueue.empty():
        for encoder in list(ffQueue._queue):
            filename = ospath.basename(encoder.dl_path)
            status_lines.append(f"⏳ Waiting: {filename}")

    if not status_lines:
        await message.reply_text("📭 No files are currently queued.")
    else:
        await message.reply_text("\n".join(status_lines))

# -------------------- Cancel Command -------------------- #
@bot.on_message(filters.command("cancel") & filters.user(Var.ADMINS))
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

# -------------------- Restart Command -------------------- #
@bot.on_message(filters.command("restart") & filters.user(Var.ADMINS))
async def restart_bot(client, message):
    await message.reply_text("♻️ Restarting bot...")
    await bot.stop()
    sys.exit(0)


# -------------------- Helper Functions -------------------- #
def convert_bytes(size):
    # convert to human readable
    for unit in ['B','KB','MB','GB','TB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"

def convert_time(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"
