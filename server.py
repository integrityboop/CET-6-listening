# -*- coding: utf-8 -*-
"""
CET-6 听力分节播放器 —— 本地小服务器。

作用：让网页能「导出音频片段」。浏览器直接双击打开 file:// 时做不到这一点，
      所以需要这个小服务在后台调用 ffmpeg 帮忙剪切。
只监听本机 127.0.0.1，不对外网开放；关掉黑窗口即停止。

用法：双击  启动.bat  （或在本目录运行  python server.py）
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import webbrowser
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
PAGE = "index.html"

# 与 analyze_audio.py 保持一致
PAUSE_MIN, PAUSE_MAX, GROUP_GAP = 9.0, 20.0, 30.0
NSEG = 7

# 7 段的固定顺序（与网页一致）
SEG_NAMES = [
    ("A", "ConversationOne"), ("A", "ConversationTwo"),
    ("B", "PassageOne"),      ("B", "PassageTwo"),
    ("C", "LectureOne"),      ("C", "LectureTwo"), ("C", "LectureThree"),
]


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for p in (r"D:\anaconda3\Library\bin\ffmpeg.exe",
              r"C:\anaconda3\Library\bin\ffmpeg.exe",
              r"C:\ffmpeg\bin\ffmpeg.exe"):
        if os.path.isfile(p):
            return p
    return "ffmpeg"


FFMPEG = find_ffmpeg()
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=NO_WINDOW)


# ----------------------------------------------------------------------
# 用 ffmpeg 静音检测重新核对分段
# ----------------------------------------------------------------------
def analyze_starts(path):
    """与 analyze_audio.py 同口径地算出 7 个段起点（段 0 从 0 开始）。"""
    proc = run([FFMPEG, "-hide_banner", "-i", path, "-af",
                "silencedetect=noise=-32dB:d=2.0", "-f", "null", "-"])
    text = proc.stderr.decode("utf-8", "ignore")
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", text)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", text)]
    pauses = [(s, e) for s, e in zip(starts, ends) if PAUSE_MIN <= e - s <= PAUSE_MAX]

    groups, cur = [], []
    for s, e in pauses:
        if cur and s - cur[-1][1] > GROUP_GAP:
            groups.append(cur)
            cur = []
        cur.append((s, e))
    if cur:
        groups.append(cur)
    if len(groups) != NSEG:
        return None, None, "检测到 %d 组（应为 %d 组）" % (len(groups), NSEG)

    # 段 i 的起点 = 上一组最后一次停顿的结束；第 0 段从 0 开始
    seg_starts = [0.0] + [round(g[-1][1], 2) for g in groups[:-1]]
    return seg_starts, [len(g) for g in groups], None


# ----------------------------------------------------------------------
# 剪切
# ----------------------------------------------------------------------
def cut(src, dst, start, end, fmt):
    dur = max(0.05, end - start)
    cmd = [FFMPEG, "-hide_banner", "-y", "-ss", "%.3f" % start, "-i", src,
           "-t", "%.3f" % dur, "-vn"]
    if fmt == "copy":
        cmd += ["-c", "copy"]                  # 源本来就是 mp3，直接切，不重新编码
    else:
        cmd += ["-c:a", "libmp3lame", "-b:a", "64k"]
    cmd.append(dst)
    proc = run(cmd)
    if proc.returncode != 0 or not os.path.isfile(dst) or os.path.getsize(dst) == 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "ignore")[-400:])


def safe_name(s, fallback="audio"):
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", s or "")
    return s.strip() or fallback


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    # ---- 静态文件 ----
    def guess_type(self, path):
        if path.lower().endswith(".mp3"):
            return "audio/mpeg"                    # 最通用的格式，所有手机浏览器都认
        if path.lower().endswith(".m4a"):
            return "audio/mp4"
        if path.lower().endswith((".html", ".htm")):
            return "text/html"
        return super().guess_type(path)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        rng = self.headers.get("Range")
        if not rng:
            return super().send_head()
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()
        try:
            unit, _, spec = rng.partition("=")
            if unit.strip() != "bytes":
                return super().send_head()
            first, _, last = spec.split(",")[0].partition("-")
            size = os.path.getsize(path)
            if first:
                start, end = int(first), int(last) if last else size - 1
            else:
                start, end = max(0, size - int(last)), size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
        except (ValueError, IndexError):
            return super().send_head()

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self._range = (start, end - start + 1)
        return open(path, "rb")

    def copyfile(self, source, outputfile):
        rng = getattr(self, "_range", None)
        self._range = None
        if not rng:
            return super().copyfile(source, outputfile)
        start, remaining = rng
        source.seek(start)
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    # ---- 接口 ----
    def do_GET(self):
        if self.path.startswith("/api/"):
            try:
                self.handle_api()
            except Exception as exc:               # 出错也要给前端一个明确回应
                self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        super().do_GET()

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, data, name, ctype="application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition",
                         "attachment; filename*=UTF-8''" + quote(name))
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self):
        q = parse_qs(urlparse(self.path).query)
        one = lambda k, d="": q.get(k, [d])[0]

        if self.path.startswith("/api/ping"):
            return self.send_json({"ok": True, "api": True, "ffmpeg": FFMPEG})

        if self.path.startswith("/api/analyze"):
            src = self.source_path(one("file"))
            segs, counts, err = analyze_starts(src)
            if err:
                return self.send_json({"ok": False, "error": err})
            return self.send_json({"ok": True, "starts": segs, "counts": counts})

        if self.path.startswith("/api/export"):
            fmt = "copy" if one("fmt") == "copy" else "mp3"
            ext = "mp3"
            name = safe_name(one("name"), "clip")

            paper = one("paper")
            if paper:                              # 整卷打包成 zip
                src = self.source_path("cet6_%s.mp3" % paper)
                starts = [float(x) for x in json.loads(one("starts", "[]"))]
                if len(starts) != NSEG:
                    raise RuntimeError("缺少分段数据")
                total = float(one("dur", "0") or 0)
                buf = io.BytesIO()
                with tempfile.TemporaryDirectory() as td:
                    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
                        for i, (sec, nm) in enumerate(SEG_NAMES):
                            a = starts[i]
                            b = starts[i + 1] if i + 1 < NSEG else total
                            out = os.path.join(td, "%d.%s" % (i, ext))
                            cut(src, out, a, b, fmt)
                            z.write(out, "%d_Section%s_%s.%s" % (i + 1, sec, nm, ext))
                return self.send_file(buf.getvalue(), name + ".zip", "application/zip")

            src = self.source_path(one("file"))
            with tempfile.TemporaryDirectory() as td:
                out = os.path.join(td, "clip." + ext)
                cut(src, out, float(one("start", "0")), float(one("end", "0")), fmt)
                with open(out, "rb") as fh:
                    data = fh.read()
            ctype = "audio/mpeg"
            return self.send_file(data, name + "." + ext, ctype)

        self.send_json({"ok": False, "error": "未知接口"}, 404)

    def source_path(self, fname):
        fname = os.path.basename(fname or "")
        if not re.match(r"^cet6_\d{4}_\d{2}_\d\.mp3$", fname):
            raise RuntimeError("文件名不合法：%s" % fname)
        p = os.path.join(HERE, fname)
        if not os.path.isfile(p):
            raise RuntimeError("找不到文件：%s" % fname)
        return p

    def log_message(self, fmt, *args):
        pass                                        # 黑窗口保持干净


def free_port(start):
    for port in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main():
    if FFMPEG == "ffmpeg" and not shutil.which("ffmpeg"):
        print("！没有找到 ffmpeg，导出功能会不可用（播放不受影响）。")

    port = free_port(PORT)
    url = "http://127.0.0.1:%d/%s" % (port, PAGE.replace(" ", "%20"))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)

    print("=" * 52)
    print("  CET-6 听力分节播放器")
    print("  地址：%s" % url)
    print("  浏览器会自动打开，用完直接关掉这个窗口。")
    print("=" * 52)
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
