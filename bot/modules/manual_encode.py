import os
from re import findall
from math import floor
from time import time
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, rename as aiorename
from asyncio import sleep as asleep, gather, create_task, create_subprocess_shell
from asyncio.subprocess import PIPE

from bot import LOGS, ffpids_cache
from bot.core.func_utils import mediainfo, convertBytes, convertTime, editMessage, sendMessage
from bot import Var


class ManualEncoder:
    def __init__(self, message, dl_path):
        self.__proc = None
        self.is_cancelled = False
        self.message = message
        self.dl_path = dl_path
        self.__total_time = None
        self.__prog_file = "prog.txt"
        self.__start_time = time()
        # Keep original filename
        self.out_path = os.path.join("encode", os.path.basename(self.dl_path))
        self.__last_percent = 0.0

    async def progress(self):
        """Encoding progress bar"""
        self.__total_time = await mediainfo(self.dl_path, get_duration=True)
        if isinstance(self.__total_time, str):
            self.__total_time = 1.0

        while not (self.__proc is None or self.is_cancelled):
            async with aiopen(self.__prog_file, "r+") as p:
                text = await p.read()
            if text:
                time_done = (
                    floor(int(t[-1]) / 1000000)
                    if (t := findall(r"out_time_ms=(\d+)", text))
                    else 1
                )
                ensize = (
                    int(s[-1]) if (s := findall(r"total_size=(\d+)", text)) else 0
                )

                diff = time() - self.__start_time
                speed = ensize / diff if diff > 0 else 0
                percent = round((time_done / self.__total_time) * 100, 2)
                tsize = ensize / (max(percent, 0.01) / 100)
                eta = (tsize - ensize) / max(speed, 0.01)

                # Only update every 5% progress
                if percent - self.__last_percent >= 5 or percent == 100:
                    self.__last_percent = percent

                    bar = floor(percent / 8) * "█" + (12 - floor(percent / 8)) * "▒"
                    progress_str = f"""<blockquote>⬇️ <b>File :</b> <i>{os.path.basename(self.dl_path)}</i></blockquote>
<blockquote>⏳ <b>Status :</b> <i>Encoding</i>
    <code>[{bar}]</code> {percent}%</blockquote> 
<blockquote>   ‣ <b>Size :</b> {convertBytes(ensize)} / ~ {convertBytes(tsize)}
    ‣ <b>Speed :</b> {convertBytes(speed)}/s
    ‣ <b>Time Took :</b> {convertTime(diff)}
    ‣ <b>ETA :</b> {convertTime(eta)}</blockquote>"""

                    await editMessage(self.message, progress_str)

                if (prog := findall(r"progress=(\w+)", text)) and prog[-1] == "end":
                    break

            await asleep(10)

    async def start_encode(self):
        """Start encoding"""
        if os.path.exists(self.__prog_file):
            await aioremove(self.__prog_file)

        async with aiopen(self.__prog_file, "w+"):
            LOGS.info("Progress Temp Generated !")

        dl_npath = os.path.join("encode", "ffmanualin.mkv")
        out_npath = os.path.join("encode", "ffmanualout.mkv")
        await aiorename(self.dl_path, dl_npath)

        ffcode = Var.FFCODE_1080.format(dl_npath, self.__prog_file, out_npath)
        LOGS.info(f"FFCode: {ffcode}")

        self.__proc = await create_subprocess_shell(ffcode, stdout=PIPE, stderr=PIPE)
        proc_pid = self.__proc.pid
        ffpids_cache.append(proc_pid)

        _, return_code = await gather(
            create_task(self.progress()), self.__proc.wait()
        )
        ffpids_cache.remove(proc_pid)

        await aiorename(dl_npath, self.dl_path)

        if self.is_cancelled:
            return

        if return_code == 0 and os.path.exists(out_npath):
            await aiorename(out_npath, self.out_path)
            return self.out_path
        else:
            err = (await self.__proc.stderr.read()).decode().strip()
            LOGS.error(f"Manual Encode Failed: {err}")
            await sendMessage(self.message, f"❌ Encoding Failed\n\n{err}")
            return None

    async def cancel_encode(self):
        self.is_cancelled = True
        if self.__proc is not None:
            try:
                self.__proc.kill()
            except:
                pass
