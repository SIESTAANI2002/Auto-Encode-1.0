from time import time, sleep
from traceback import format_exc
from math import floor
from os import path as ospath
from pyrogram.errors import FloodWait

from bot import bot, Var
from .func_utils import editMessage, convertBytes, convertTime
from .reporter import rep
from .gdrive_uploader import upload_to_drive  # ✅ Import Drive uploader


class TgUploader:
    def __init__(self, message):
        self.cancelled = False
        self.message = message
        self.__name = ""
        self.__qual = ""
        self.__client = bot
        self.__start = time()
        self.__updater = time()

    async def upload(self, path, qual):
        self.__name = ospath.basename(path)
        self.__qual = qual
        try:
            if Var.AS_DOC:
                msg = await self.__client.send_document(
                    chat_id=Var.FILE_STORE,
                    document=path,
                    thumb="thumb.jpg" if ospath.exists("thumb.jpg") else None,
                    caption=f"<i>{self.__name}</i>",
                    force_document=True,
                    progress=self.progress_status
                )
            else:
                msg = await self.__client.send_video(
                    chat_id=Var.FILE_STORE,
                    video=path,
                    thumb="thumb.jpg" if ospath.exists("thumb.jpg") else None,
                    caption=f"<i>{self.__name}</i>",
                    progress=self.progress_status
                )

            await rep.report("[INFO] Succesfully Uploaded File into Tg...", "info")

            # ✅ After Telegram upload → Upload to Drive
            drive_link = await upload_to_drive(path)

            if drive_link:
                await self.__client.send_message(
                    chat_id=Var.LOG_CHANNEL,
                    text=f"✅ <b>{self.__name}</b> also uploaded to <b>Google Drive</b>\n\n🔗 {drive_link}"
                )

            return msg

        except FloodWait as e:
            sleep(e.value * 1.5)
            return await self.upload(path, qual)
        except Exception as e:
            await rep.report(format_exc(), "error")
            raise e
        # ❌ Removed file deletion here (TokyoTosho still needs it!)

    async def progress_status(self, current, total):
        if self.cancelled:
            self.__client.stop_transmission()
        now = time()
        diff = now - self.__start
        if (now - self.__updater) >= 7 or current == total:
            self.__updater = now
            percent = round(current / total * 100, 2)
            speed = current / diff
            eta = round((total - current) / speed)
            bar = floor(percent / 8) * "█" + (12 - floor(percent / 8)) * "▒"
            progress_str = f"""‣ <b>Anime Name :</b> <b><i>{self.__name}</i></b>

‣ <b>Status :</b> <i>Uploading</i>
    <code>[{bar}]</code> {percent}%
    
    ‣ <b>Size :</b> {convertBytes(current)} out of ~ {convertBytes(total)}
    ‣ <b>Speed :</b> {convertBytes(speed)}/s
    ‣ <b>Time Took :</b> {convertTime(diff)}
    ‣ <b>Time Left :</b> {convertTime(eta)}

‣ <b>File(s) Encoded:</b> <code>{Var.QUALS.index(self.__qual)} / {len(Var.QUALS)}</code>"""
            await editMessage(self.message, progress_str)
