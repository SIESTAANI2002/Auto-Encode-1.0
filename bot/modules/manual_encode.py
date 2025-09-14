import os
import sys
import time
from asyncio import sleep as asleep, create_task
from pyrogram import Client, filters
from bot.core.ffencoder import FFEncoder
from bot.core.func_utils import convertBytes, convertTime, editMessage, sendMessage
from bot import Var

OWNER_ID = Var.OWNER_ID  # make sure this is in config
ENC_QUEUE = []  # Queue to track tasks
CURRENT_TASK = None  # Currently encoding

@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(bot, message):
    await message.reply_text(Var.START_MSG.format(first_name=message.from_user.first_name))

@Client.on_message(filters.command("restart") & filters.user(OWNER_ID))
async def restart_cmd(bot, message):
    await message.reply_text("♻️ Restarting bot...")
    os.execv(sys.executable, ['python3'] + sys.argv)

@Client.on_message(filters.command("queue") & filters.user(OWNER_ID))
async def queue_cmd(bot, message):
    if not ENC_QUEUE:
        await message.reply_text("📭 Queue is empty!")
    else:
        text = "📝 Pending tasks:\n\n" + "\n".join(f"{i+1}. {os.path.basename(f)}" for i, f in enumerate(ENC_QUEUE))
        await message.reply_text(text)

@Client.on_message(filters.command("cancel") & filters.user(OWNER_ID))
async def cancel_cmd(bot, message):
    global CURRENT_TASK
    if CURRENT_TASK:
        await CURRENT_TASK.cancel_encode()
        CURRENT_TASK = None
        await message.reply_text("❌ Current encoding cancelled!")
    else:
        await message.reply_text("⚠️ No encoding task running.")

async def download_progress(current, total, message, start_time):
    diff = time.time() - start_time
    percent = (current / max(total, 1)) * 100
    speed = current / max(diff, 0.01)
    eta = (total - current) / max(speed, 0.01)

    bar_len = 12
    filled_len = int(bar_len * percent / 100)
    bar = "█" * filled_len + "▒" * (bar_len - filled_len)

    progress_str = f"""⬇️ Downloading {message.document.file_name}
<code>[{bar}]</code> {percent:.2f}%
Size: {convertBytes(current)} / {convertBytes(total)}
Speed: {convertBytes(speed)}/s
ETA: {convertTime(eta)}"""
    
    await editMessage(message, progress_str)

@Client.on_message(filters.document & filters.private)
async def manual_encode(bot, message):
    global CURRENT_TASK
    if message.from_user.id != OWNER_ID:
        await message.reply_text("❌ You are not authorized to use this bot.")
        return

    start_msg = await sendMessage(message.chat.id, f"⌛ Preparing download for {message.document.file_name}...")

    # Track download start time
    start_time = time.time()
    file_path = await bot.download_media(
        message,
        file_name=os.path.join("downloads", message.document.file_name),
        progress=lambda cur, tot: create_task(download_progress(cur, tot, start_msg, start_time))
    )

    await editMessage(start_msg, f"✅ Download Complete: {message.document.file_name}\n⏳ Starting Encoding...")

    # Rename file like auto_encode
    anime_name = message.document.file_name
    encoded_name = f"[{Var.SECOND_BRAND}]{anime_name.split(']')[-1].strip()}"

    # Add to queue
    ENC_QUEUE.append(file_path)

    while ENC_QUEUE:
        next_file = ENC_QUEUE.pop(0)
        CURRENT_TASK = FFEncoder(start_msg, next_file, encoded_name, "1080")
        out_path = await CURRENT_TASK.start_encode()

        if out_path:
            await editMessage(start_msg, f"✅ Encoding Complete!\nFile: {encoded_name}")
        else:
            await editMessage(start_msg, f"❌ Encoding Failed: {encoded_name}")

        CURRENT_TASK = None
        await asleep(2)  # small pause before next task
