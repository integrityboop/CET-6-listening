# -*- coding: utf-8 -*-
"""
分析 CET-6 听力音频，自动切分出 7 个题目段（2 长对话 + 2 篇章 + 3 讲座）。

原理：听力录音里每题后有约 13.4 秒的答题停顿，25 题 = 25 个停顿。
停顿按题目分组：4,4,3,4,3,3,4（对应 SectionA 8题 / SectionB 7题 / SectionC 10题）。
组与组之间的间隔是整段音频（140~210 秒），组内的间隔只是朗读下一题的题干（约 5 秒）。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent

# 每个文件里 7 个题目段的定义（组内题数）
ITEMS = [
    ("A", "Conversation One", 4),
    ("A", "Conversation Two", 4),
    ("B", "Passage One", 3),
    ("B", "Passage Two", 4),
    ("C", "Lecture One", 3),
    ("C", "Lecture Two", 3),
    ("C", "Lecture Three", 4),
]

PAUSE_MIN, PAUSE_MAX = 9.0, 20.0   # 答题停顿的时长范围
GROUP_GAP = 30.0                   # 组内间隔上限（超过即认为是下一题）


def silence_pairs(path):
    """返回 [(start, end), ...]，单位为秒。"""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", "silencedetect=noise=-32dB:d=2.0", "-f", "null", "-"],
        stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
    )
    text = proc.stderr.decode("utf-8", "ignore")
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", text)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", text)]
    return list(zip(starts, ends))


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, check=True,
    ).stdout.decode().strip()
    return float(out)


def segment(path):
    """把一条录音切成 7 段，返回 (segments, total_duration)。"""
    pairs = silence_pairs(path)
    pauses = [(s, e) for s, e in pairs if PAUSE_MIN <= e - s <= PAUSE_MAX]

    groups, cur = [], []
    for s, e in pauses:
        if cur and s - cur[-1][1] > GROUP_GAP:
            groups.append(cur)
            cur = []
        cur.append((s, e))
    if cur:
        groups.append(cur)

    if len(groups) != len(ITEMS):
        raise SystemExit(
            "%s: 分组异常，得到 %d 组（应为 %d），题数 %s"
            % (path.name, len(groups), len(ITEMS), [len(g) for g in groups])
        )

    total = duration(path)
    segs = []
    for i, (section, name, _) in enumerate(ITEMS):
        start = 0.0 if i == 0 else groups[i - 1][-1][1]
        end = groups[i][-1][1] if i < len(ITEMS) - 1 else total
        segs.append({
            "section": section,
            "name": name,
            "start": round(start, 2),
            "end": round(end, 2),
            "count": len(groups[i]),
        })
    return segs, total, [len(g) for g in groups]


def main():
    files = sorted(HERE.glob("cet6_*.m4a"))
    if not files:
        raise SystemExit("没有找到 cet6_*.m4a")

    out = {}
    ok = True
    for f in files:
        try:
            segs, total, counts = segment(f)
        except SystemExit as exc:
            print("[失败] " + str(exc))
            ok = False
            continue
        m = re.match(r"cet6_(\d{4})_(\d{2})_(\d)", f.stem)
        year, month, setno = m.group(1), m.group(2), m.group(3)
        key = "%s_%s_%s" % (year, month, setno)
        out[key] = {
            "year": year, "month": month, "set": setno,
            "file": f.name, "duration": round(total, 2),
            "questionCounts": counts,
            "segments": segs,
        }
        print("%s  %6.1fs  题数%s  " % (f.name, total, counts)
              + " ".join("%.1f-%.1f" % (s["start"], s["end"]) for s in segs))

    if not ok:
        sys.exit(1)

    (HERE / "segments.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n已写出 segments.json，共 %d 个文件 / %d 段"
          % (len(out), sum(len(v["segments"]) for v in out.values())))


if __name__ == "__main__":
    main()
