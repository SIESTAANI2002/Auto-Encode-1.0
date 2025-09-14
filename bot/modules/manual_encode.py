from re import findall
from math import floor
from time import time
from os import path as ospath
from aiofiles import open as aiopen
from aiofiles.os import remove as aioremove, rename as aiorename
from asyncio import sleep as asleep, gather, create_subprocess_shell, create_task
from asyncio.subprocess import PIPE

from bot import Var, ffpids_cache, LOGS
from .func_utils import mediainfo, convertBytes, convertTime, editMessage, sendMessage
from .reporter import rep


# FFmpeg arguments (only 1080p supported here)
ffargs = {
    '1080': Var.FFCODE_1080,
}


class ManualFFEncoder:
    def __init__(self, message, path, name, user_id, qual="1080"):
        self.__proc = None
        self.is_cancelled = False
        self.message = message
        self.__name = name
        self.__qual = qual
        self.dl_path = path
        self.user_id = user_id
        self.__total_time = None
        self.out_path = ospath.join("encode", name)
        self.__prog_file = 'prog.txt'
        self.__start_time = time()

    async def progress(self):
        self.__total_time = await mediainfo(self.dl_path, get_duration=True)
        if isinstance(self.__total_time, str):
            self.__total_time = 1.0

        while not (self.__proc is None or self.is_cancelled):
            async with aiopen(self.__prog_file, 'r+') as p:
                text = await p.read()

            if text:
                time_done = floor(int(t[-1]) / 1000000) if (t := findall(r"out_time_ms=(\d+)", text)) else 1
                ensize = int(s[-1]) if (s := findall(r"total_size=(\d+)", text)) else 0

                diff = time() - self.__start_time
                speed = ensize / diff if diff > 0 else 0
                percent = round((time_done / self.__total_time) * 100, 2)
                tsize = ensize / (max(percent, 0.01) / 100)
                eta = (tsize - ensize) / max(speed, 0.01)

                # Progress bar (12 blocks wide)
                bar = floor(percent / 8) * "█" + (12 - floor(percent / 8)) * "▒"

                progress_str = f"""‣ <b>Anime Name :</b> <b><i>{self.__name}</i></b>
‣ <b>Status :</b> <i>Encoding</i>
    <code>[{bar}]</code> {percent}%
‣ <b>Size :</b> {convertBytes(ensize)} out of ~ {convertBytes(tsize)}
‣ <b>Speed :</b> {convertBytes(speed)}/s
‣ <b>Time Took :</b> {convertTime(diff)}
‣ <b>Time Left :</b> {convertTime(eta)}
‣ <b>File(s) Encoded:</b> <code>1 / 1</code>"""

                await editMessage(self.message, progress_str)

                if (prog := findall(r"progress=(\w+)", text)) and prog[-1] == 'end':
                    break

            await asleep(10)  # update interval 10s

    async def start_encode(self):
        # OWNER-only restriction
        if str(self.user_id) != str(Var.OWNER_ID):
            await sendMessage(self.message, "🚫 You are not authorized to use manual encoding.")
            return None

        # remove old progress file if exists
        if ospath.exists(self.__prog_file):
            await aioremove(self.__prog_file)

        async with aiopen(self.__prog_file, 'w+'):
            LOGS.info("Progress Temp Generated !")

        dl_npath, out_npath = ospath.join("encode", "manual_in.mkv"), ospath.join("encode", "manual_out.mkv")
        await aiorename(self.dl_path, dl_npath)

        ffcode = ffargs[self.__qual].format(dl_npath, self.__prog_file, out_npath)
        LOGS.info(f'FFCode: {ffcode}')

        self.__proc = await create_subprocess_shell(ffcode, stdout=PIPE, stderr=PIPE)
        proc_pid = self.__proc.pid
        ffpids_cache.append(proc_pid)

        _, return_code = await gather(create_task(self.progress()), self.__proc.wait())
        ffpids_cache.remove(proc_pid)

        await aiorename(dl_npath, self.dl_path)

        if self.is_cancelled:
            return None

        if return_code == 0:
            if ospath.exists(out_npath):
                await aiorename(out_npath, self.out_path)
            return self.out_path
        else:
            error_text = (await self.__proc.stderr.read()).decode().strip()
            await rep.report(error_text, "manual encode error")

    async def cancel_encode(self):
        self.is_cancelled = True
        if self.__proc is not None:
            try:
                self.__proc.kill()
            except:
                pass
