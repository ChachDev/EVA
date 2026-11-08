"""One CPU transcription; releasing the process releases the model memory."""
import json
import re
import sys
from pathlib import Path

import av
import numpy as np
from faster_whisper import WhisperModel

RATE = 16_000
MAX_SECONDS = 3600
LANGUAGE = {"RU":"ru", "KZ":"kk", "EN":"en"}


def waveform(path, fmt):
    chunks = []
    samples = 0
    resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
    with av.open(str(path), mode="r", format=fmt) as media:
        for frame in media.decode(audio=0):
            for converted in resampler.resample(frame):
                samples += converted.samples
                if samples > MAX_SECONDS * RATE:
                    raise ValueError("Recording exceeds the transcript contract")
                chunks.append(converted.to_ndarray().reshape(-1))
        for converted in resampler.resample(None):
            samples += converted.samples
            chunks.append(converted.to_ndarray().reshape(-1))
    if not chunks or not 0 < samples <= MAX_SECONDS * RATE:
        raise ValueError("Empty or unsupported recording duration")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def phrases(segments, duration):
    result = []
    def emit(start, end, text):
        start = round(max(0.0,min(start,duration)),3)
        end = round(max(0.0,min(end,duration)),3)
        if end>start and text.strip():
            result.append({"speaker":"unknown","text":text.strip(),"start_seconds":start,"end_seconds":end})
    for seg in segments:
        words = seg.words or []
        if not words:
            emit(seg.start,seg.end,seg.text)
            continue
        part=[]
        for word in words:
            if part and (word.start-part[-1].end>=0.65 or part[-1].word.rstrip().endswith((".","?","!","…"))):
                emit(part[0].start,part[-1].end,"".join(w.word for w in part))
                part=[]
            part.append(word)
        if part:
            emit(part[0].start,part[-1].end,"".join(w.word for w in part))
    return result


def decode(model, audio, language):
    segments,_ = model.transcribe(audio,language=LANGUAGE[language],beam_size=5,
                                  word_timestamps=True,vad_filter=True,condition_on_previous_text=False)
    return phrases(segments,len(audio)/RATE)


def restore_opening(primary, opening):
    """Only prepend speech before the first timestamp, with an overlapping matching phrase."""
    boundary = primary[0]["start_seconds"]
    additions = [seg for seg in opening if seg["end_seconds"] <= boundary]
    if not additions:
        return primary
    def words(seg):
        return {w for w in re.findall(r"\w+",seg["text"].casefold()) if len(w)>2}
    for short in opening:
        for original in primary:
            if original["start_seconds"]>=short["end_seconds"]:
                break
            if short["start_seconds"]<original["end_seconds"] and len(words(short)&words(original))>=2:
                return additions+primary
    return primary


def transcribe(path, fmt, language, model_dir):
    audio = waveform(path,fmt)
    duration = len(audio) / RATE
    model = WhisperModel(str(model_dir), device="cpu", compute_type="int8", cpu_threads=4,
                         num_workers=1, local_files_only=True)
    result=decode(model,audio,language)
    if result and duration>24 and 4<result[0]["start_seconds"]<20:
        result=restore_opening(result,decode(model,audio[:24*RATE],language))
    if not result or len(result)>10000:
        raise ValueError("No usable speech segmentation")
    return result


if __name__=="__main__":
    path,fmt,lang,model_dir = sys.argv[1:5]
    try:
        print(json.dumps(transcribe(Path(path),fmt,lang,Path(model_dir)),ensure_ascii=True))
    except (ValueError, av.FFmpegError, IndexError):
        sys.exit(2)
