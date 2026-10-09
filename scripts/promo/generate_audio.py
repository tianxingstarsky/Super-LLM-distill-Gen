"""Generate the local, scene-aligned ShuJian Cube promo soundtrack.

Preferred narration: local Qwen3-TTS-12Hz-1.7B-CustomVoice, Serena, with
natural-language prosody direction. Run in the isolated promo-tts venv:
  .dataforge/promo-tts/runtime/Scripts/python scripts/promo/generate_audio.py --voice qwen
Model weights and the CUDA environment remain in ignored .dataforge/promo-tts.
Edge Xiaoxiao and Windows Huihui remain explicit fallbacks. The music is an
original deterministic composition: no samples or third-party music.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
import wave

import numpy as np
from scipy import signal


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "promo" / "audio"
LOCAL_TTS = ROOT / '.dataforge' / 'promo-tts'
QWEN_MODEL = LOCAL_TTS / 'models' / 'Qwen3-TTS-12Hz-1.7B-CustomVoice'
QWEN_INSTRUCT = (
    '像向朋友介绍一个实用的软件，温暖、清晰、自然，带一点轻微的笑意。'
    '有信心，轻松亲切，语气随句意自然起伏，句间合理停顿。'
    '保持中等稍快的日常交流速度，不拖长句尾，不用新闻播报腔，不夸张表演。'
)
QWEN_SPEAKER = 'Serena'
SR = 48000
DURATION = 60.0
SCENES = [
    (0, 7, '想训练自己的模型，却卡在准备数据这一步？'),
    (7, 15, '把文档、对话，或者一个想法，交给数简立方。选好目标，就能开始。'),
    (15, 25, '沿着这张画布，你可以做预训练语料、问答和偏好数据。每个节点，都能按你的需求调整。'),
    (25, 35, '你能看着回答一点点出现，也能设计多轮对话，让样本带上自己的风格。'),
    (35, 45, '从生成到审核、去重和导出，流程一路衔接。中途停下，下次接着做。'),
    (45, 54, '做研究、落地企业项目，或只是想试试自己的模型，都可以从一小批开始。'),
    (54, 60, '数简立方，让好想法，变成下一次训练的起点。'),
]


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=True, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def ffmpeg_path() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def write_wav(path: Path, samples: np.ndarray) -> None:
    samples = np.asarray(samples)
    if samples.ndim == 1:
        samples = samples[:, None]
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as target:
        target.setnchannels(samples.shape[1])
        target.setsampwidth(2)
        target.setframerate(SR)
        target.writeframes(pcm.tobytes())


def read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as source:
        assert source.getframerate() == SR and source.getsampwidth() == 2
        raw = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
        return raw.astype(np.float64).reshape(-1, source.getnchannels()) / 32768


def sentence_chunks(text: str) -> list[str]:
    sentences = re.findall(r'[^。！？]+[。！？]?', text)
    chunks = []
    for sentence in sentences:
        if len(sentence) > 29:
            # Long spoken sentences get a natural comma boundary for captions.
            commas = [m.end() for m in re.finditer('，', sentence)]
            if commas:
                boundary = min(commas, key=lambda i: abs(i - len(sentence) / 2))
                chunks.extend([sentence[:boundary], sentence[boundary:]])
                continue
        chunks.append(sentence)
    return chunks


def load_qwen_model():
    import torch
    from qwen_tts import Qwen3TTSModel
    if not torch.cuda.is_available():
        raise RuntimeError('Local Qwen narration requires a working CUDA runtime')
    torch.set_num_threads(8)
    torch.manual_seed(6102026)
    print(f'Local Qwen GPU: {torch.cuda.get_device_name(0)}; torch {torch.__version__}', flush=True)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        str(QWEN_MODEL), device_map='cuda:0', dtype=torch.bfloat16,
        attn_implementation='sdpa', local_files_only=True)
    print(f'Local Qwen model loaded in {time.perf_counter() - started:.2f}s', flush=True)
    return model


def qwen_segment(model, text: str, path: Path) -> None:
    import soundfile as sf
    combined, boundaries = [], []
    timings = []
    sample_offset = 0
    for sentence_index, sentence in enumerate(sentence_chunks(text)):
        started = time.perf_counter()
        wavs, rate = model.generate_custom_voice(
            text=sentence, language='Chinese', speaker=QWEN_SPEAKER,
            instruct=QWEN_INSTRUCT, non_streaming_mode=True,
            temperature=.8, top_p=.9, top_k=40, repetition_penalty=1.05,
            max_new_tokens=1024)
        inference_seconds = time.perf_counter() - started
        piece = np.asarray(wavs[0], dtype=np.float64)
        raw = path.with_name(path.stem + f'_sentence_{sentence_index}.wav')
        sf.write(raw, piece, rate)
        divisor = math.gcd(SR, rate)
        piece = signal.resample_poly(piece, SR // divisor, rate // divisor)
        active = np.flatnonzero(np.abs(piece) > .004)
        if not active.size:
            raise RuntimeError(f'Local Qwen produced a silent sentence: {sentence}')
        piece = piece[max(0, active[0] - int(.055 * SR)):min(len(piece), active[-1] + int(.1 * SR))]
        boundaries.append({'type': 'SentenceBoundary', 'offset': round(sample_offset / SR * 1e7),
                           'duration': round(len(piece) / SR * 1e7), 'text': sentence})
        combined.append(piece)
        sample_offset += len(piece)
        if sentence_index < len(sentence_chunks(text)) - 1:
            combined.append(np.zeros(int(.13 * SR)))
            sample_offset += int(.13 * SR)
        timings.append({'text': sentence, 'audio_seconds': round(len(piece)/SR, 3),
                        'inference_seconds': round(inference_seconds, 3),
                        'real_time_factor': round(inference_seconds / (len(piece)/SR), 3)})
        print(f'Qwen sentence {sentence_index + 1}: {len(piece)/SR:.2f}s audio, '
              f'{inference_seconds:.2f}s inference — {sentence}', flush=True)
    write_wav(path, np.concatenate(combined))
    path.with_suffix('.boundaries.jsonl').write_text(
        '\n'.join(json.dumps(boundary, ensure_ascii=False) for boundary in boundaries), encoding='utf-8')
    path.with_suffix('.generation.json').write_text(json.dumps(timings, ensure_ascii=False, indent=2), encoding='utf-8')


async def edge_segment(text: str, path: Path) -> None:
    import edge_tts
    communication = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural", rate="+4%")
    await asyncio.wait_for(communication.save(str(path), str(path.with_suffix('.boundaries.jsonl'))), timeout=40)


def sapi_segment(text: str, path: Path) -> None:
    # The JSON input avoids interpolation of narration text into shell source.
    request = path.with_suffix(".request.json")
    sentences = [sentence + '。' for sentence in text.split('。') if sentence]
    pieces = [{"text": sentence, "output": str(path.with_name(path.stem + f'_sentence_{i}.wav'))}
              for i, sentence in enumerate(sentences)]
    request.write_text(json.dumps({"pieces": pieces}, ensure_ascii=False), encoding="utf-8-sig")
    ps_source = """
param([string]$RequestPath)
Add-Type -AssemblyName System.Speech
$request = Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice.SelectVoice('Microsoft Huihui')
$voice.Rate = 0
$voice.Volume = 100
foreach ($piece in $request.pieces) {
    $voice.SetOutputToWaveFile($piece.output)
    $voice.Speak($piece.text)
    $voice.SetOutputToNull()
}
$voice.Dispose()
"""
    ps = OUT / "sapi_narration.ps1"
    ps.write_text(ps_source, encoding="utf-8-sig")
    run("powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps),
        "-RequestPath", str(request))
    combined = []
    boundaries = []
    sample_offset = 0
    for i, piece in enumerate(pieces):
        with wave.open(piece['output'], 'rb') as original:
            source_rate = original.getframerate()
            data = np.frombuffer(original.readframes(original.getnframes()), dtype='<i2').astype(float) / 32768
        divisor = math.gcd(SR, source_rate)
        data = signal.resample_poly(data, SR // divisor, source_rate // divisor)
        active = np.flatnonzero(np.abs(data) > .006)
        data = data[max(0, active[0] - int(.045 * SR)):min(len(data), active[-1] + int(.1 * SR))]
        boundaries.append({'type': 'SentenceBoundary', 'offset': round(sample_offset / SR * 1e7),
                           'duration': round(len(data) / SR * 1e7), 'text': piece['text']})
        combined.append(data)
        sample_offset += len(data)
        if i < len(pieces) - 1:
            combined.append(np.zeros(int(.18 * SR)))
            sample_offset += int(.18 * SR)
    write_wav(path, np.concatenate(combined))
    path.with_suffix('.boundaries.jsonl').write_text('\n'.join(json.dumps(x, ensure_ascii=False) for x in boundaries), encoding='utf-8')


def atempo_chain(factor: float) -> str:
    parts = []
    while factor > 2:
        parts.append("atempo=2")
        factor /= 2
    while factor < 0.5:
        parts.append("atempo=0.5")
        factor /= 0.5
    parts.append(f"atempo={factor:.8f}")
    return ",".join(parts)


def generate_narration(ffmpeg: str, provider: str) -> tuple[np.ndarray, list[dict], list[dict], str]:
    narration = np.zeros((int(SR * DURATION), 2))
    cues = []
    subtitle_cues = []
    using = provider
    if using == "auto":
        if QWEN_MODEL.is_dir() and importlib.util.find_spec('qwen_tts'):
            using = 'qwen'
        else:
            using = "edge" if importlib.util.find_spec("edge_tts") else "sapi"
    qwen = load_qwen_model() if using == 'qwen' else None
    for index, (start, end, text) in enumerate(SCENES, 1):
        segment_start = start + (0.6 if index == 1 else 0.35)
        cache_key = hashlib.sha256((text + (QWEN_INSTRUCT if using == 'qwen' else using)).encode()).hexdigest()[:10]
        source = OUT / f"voice_{index:02d}_{using}_{cache_key}.{'mp3' if using == 'edge' else 'wav'}"
        if not source.exists() or source.stat().st_size < 100:
            if using == 'qwen':
                qwen_segment(qwen, text, source)
            elif using == "edge":
                try:
                    asyncio.run(edge_segment(text, source))
                except Exception as error:
                    if provider == "edge":
                        raise
                    print(f"Edge voice unavailable ({type(error).__name__}); using installed Huihui.")
                    using = "sapi"
                    source = OUT / f"voice_{index:02d}_sapi_{cache_key}.wav"
                    sapi_segment(text, source)
            else:
                sapi_segment(text, source)
        decoded = OUT / f"voice_{index:02d}_decoded.wav"
        run(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source),
            "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(decoded))
        voice = read_wav(decoded)[:, 0]
        # Remove the TTS service's leading/trailing padding, retaining a soft fade.
        active = np.flatnonzero(np.abs(voice) > 0.006)
        if active.size == 0:
            raise RuntimeError(f"Narration segment {index} is silent")
        lo = max(0, active[0] - int(SR * 0.045))
        hi = min(voice.size, active[-1] + int(SR * 0.11))
        voice = voice[lo:hi]
        natural_duration = len(voice) / SR
        available = end - segment_start - 0.4
        tempo = max(1.0, natural_duration / available)
        if tempo > 1.01:
            trimmed = OUT / f"voice_{index:02d}_trim.wav"
            adjusted = OUT / f"voice_{index:02d}_adjusted.wav"
            write_wav(trimmed, voice)
            run(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(trimmed),
                "-af", atempo_chain(tempo), "-ar", str(SR), "-c:a", "pcm_s16le", str(adjusted))
            voice = read_wav(adjusted)[:, 0]
        voice = voice[:int(available * SR)]
        rms = np.sqrt(np.mean(voice ** 2))
        voice *= min(10 ** (-17 / 20) / max(rms, 1e-9), 0.72 / max(np.max(np.abs(voice)), 1e-9))
        fade = min(int(0.015 * SR), voice.size // 2)
        voice[:fade] *= np.linspace(0, 1, fade)
        voice[-fade:] *= np.linspace(1, 0, fade)
        offset = int(segment_start * SR)
        narration[offset:offset + len(voice)] += voice[:, None]
        cue = {"scene": index, "start": start, "end": end, "text": text,
               "voice_start": round(segment_start, 3),
               "voice_end": round(segment_start + len(voice) / SR, 3),
               "natural_voice_seconds": round(natural_duration, 3), "tempo_factor": round(tempo, 4)}
        cues.append(cue)
        boundary_file = source.with_suffix('.boundaries.jsonl')
        boundaries = [json.loads(line) for line in boundary_file.read_text(encoding='utf-8').splitlines() if line.strip()]
        for boundary in boundaries:
            cue_start = segment_start + max(0, boundary['offset'] / 1e7 - lo / SR) / tempo
            cue_end = segment_start + max(0, (boundary['offset'] + boundary['duration']) / 1e7 - lo / SR) / tempo
            subtitle_cues.append({'scene': index, 'start': round(cue_start, 3),
                                  'end': round(min(cue['voice_end'], cue_end), 3), 'text': boundary['text']})
        print(f"Scene {index}: {cue['voice_start']}–{cue['voice_end']}s, tempo {tempo:.3f}", flush=True)
    # Edge sentence metadata includes a 50ms overlap at punctuation. Clamp
    # adjacent captions so a renderer never chooses two sentences together.
    for current, following in zip(subtitle_cues, subtitle_cues[1:]):
        current['end'] = min(current['end'], following['start'])
    return narration, cues, subtitle_cues, using


def note_frequency(midi: int) -> float:
    return 440 * 2 ** ((midi - 69) / 12)


def original_music() -> np.ndarray:
    """Compose a soft D-major synth bed, 96 BPM, with deterministic timbres."""
    count = int(SR * DURATION)
    music = np.zeros((count, 2))
    rng = np.random.default_rng(6102026)
    # Eight-bar Dmaj9 / Aadd9 / Bm7 / Gmaj9 progression. Airy tones avoid
    # strong midrange content where Chinese narration needs clarity.
    chords = [(50, 57, 61, 66, 69), (45, 52, 59, 61, 64),
              (47, 54, 57, 62, 66), (43, 50, 57, 59, 62)]
    beat = 60 / 96
    chord_duration = beat * 8
    for bar, start in enumerate(np.arange(0, DURATION, chord_duration)):
        chord = chords[bar % len(chords)]
        stop = min(count, int((start + chord_duration + 1.4) * SR))
        offset = int(start * SR)
        t = np.arange(stop - offset) / SR
        envelope = (1 - np.exp(-t / .45)) * np.exp(-np.maximum(t - chord_duration + .55, 0) / .75)
        for pitch, pan in zip(chord, [-.55, .45, -.2, .2, 0]):
            f = note_frequency(pitch + 12)
            phase = rng.uniform(0, 2 * np.pi)
            tone = (.7 * np.sin(2 * np.pi * f * t + phase) +
                    .22 * np.sin(2 * np.pi * (f * 1.0018) * t) +
                    .08 * np.sin(2 * np.pi * 2 * f * t))
            pulse = .93 + .07 * np.sin(2 * np.pi * .13 * t)
            tone *= envelope * pulse * .06
            music[offset:stop, 0] += tone * math.sqrt((1 - pan) / 2)
            music[offset:stop, 1] += tone * math.sqrt((1 + pan) / 2)
    # Rounded eighth-note plucks with ping-pong echo and gentle stereo motion.
    for step, start in enumerate(np.arange(0, DURATION, beat / 2)):
        chord = chords[int(start // chord_duration) % len(chords)]
        pitch = chord[(0, 2, 3, 4, 2, 3, 1, 4)[step % 8]] + 24
        t = np.arange(int(.95 * SR)) / SR
        envelope = (1 - np.exp(-t / .006)) * np.exp(-t / .18)
        tone = (np.sin(2 * np.pi * note_frequency(pitch) * t) +
                .16 * np.sin(2 * np.pi * 2 * note_frequency(pitch) * t)) * envelope * .025
        pan = -.5 if step % 2 else .5
        for delay, level, stereo in [(0, 1, pan), (beat * .75, .28, -pan), (beat * 1.5, .12, pan)]:
            offset = int((start + delay) * SR)
            length = min(len(tone), count - offset)
            if length > 0:
                music[offset:offset + length, 0] += tone[:length] * level * math.sqrt((1 - stereo) / 2)
                music[offset:offset + length, 1] += tone[:length] * level * math.sqrt((1 + stereo) / 2)
    # Subtle 96 BPM pulse, a restrained kick and filtered electronic texture.
    for step, start in enumerate(np.arange(0, DURATION, beat)):
        offset = int(start * SR)
        t = np.arange(int(.3 * SR)) / SR
        kick = np.sin(2 * np.pi * (43 * t + 3.8 * (1 - np.exp(-t / .027)))) * np.exp(-t / .09) * .07
        length = min(len(kick), count - offset)
        music[offset:offset + length] += kick[:length, None]
        if step % 2:
            noise = rng.normal(size=int(.11 * SR))
            noise = signal.lfilter([1, -1], [1], noise)
            noise *= np.exp(-np.arange(len(noise)) / (SR * .025)) * .008
            length = min(len(noise), count - offset)
            music[offset:offset + length] += noise[:length, None]
    timeline = np.arange(count) / SR
    entrance = np.clip(timeline / 1.4, 0, 1)
    ending = np.clip((DURATION - timeline) / 2.0, 0, 1)
    music *= (entrance * ending)[:, None]
    music /= max(1, np.max(np.abs(music)) / .55)
    return music


def timestamp_srt(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--voice", choices=["auto", "edge", "sapi", "qwen"], default="auto")
    parser.add_argument('--qwen-sample', action='store_true', help='Generate a local Serena sample before the master')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_path()
    print(f"FFMPEG={ffmpeg}", flush=True)
    if args.qwen_sample:
        sample = OUT / 'qwen_sample.wav'
        text = SCENES[0][2]
        key = hashlib.sha256((text + QWEN_INSTRUCT).encode()).hexdigest()[:10]
        cached = OUT / f'voice_01_qwen_{key}.wav'
        qwen_segment(load_qwen_model(), text, cached)
        shutil.copyfile(cached, sample)
        shutil.copyfile(cached.with_suffix('.boundaries.jsonl'), sample.with_suffix('.boundaries.jsonl'))
        shutil.copyfile(cached.with_suffix('.generation.json'), sample.with_suffix('.generation.json'))
        print(f'Local Serena sample saved: {sample}', flush=True)
        return
    narration, cues, subtitle_cues, provider = generate_narration(ffmpeg, args.voice)
    music = original_music()
    write_wav(OUT / "narration.wav", narration)
    write_wav(OUT / "music_original.wav", music)
    # Duck music smoothly from the narration activity envelope.
    speech_envelope = np.abs(narration[:, 0])
    # A first-order envelope avoids a long FIR convolution on a 60-second file.
    decay = np.exp(-1 / (.08 * SR))
    smoothed = signal.lfilter([1 - decay], [1, -decay], speech_envelope)
    activity = np.clip(smoothed / .018, 0, 1)
    duck_gain = .64 - .31 * activity
    mixed = narration + music * duck_gain[:, None]
    mixed = .93 * np.tanh(mixed / .93)
    write_wav(OUT / "master_pre.wav", mixed)
    run(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(OUT / "master_pre.wav"),
        "-af", "loudnorm=I=-16:LRA=7:TP=-1.5", "-ar", str(SR), "-ac", "2",
        "-c:a", "pcm_s16le", str(OUT / "master.wav"))
    run(ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(OUT / "master.wav"),
        "-c:a", "libmp3lame", "-b:a", "192k", str(OUT / "master.mp3"))
    (OUT / "subtitle_cues.json").write_text(json.dumps(cues, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "cues.json").write_text(json.dumps(subtitle_cues, ensure_ascii=False, indent=2), encoding="utf-8")
    srt = "\n\n".join(
        f"{i}\n{timestamp_srt(cue['start'])} --> {timestamp_srt(cue['end'])}\n{cue['text']}"
        for i, cue in enumerate(subtitle_cues, 1)) + "\n"
    (OUT / "subtitles.srt").write_text(srt, encoding="utf-8-sig")
    (OUT.parent / "captions.srt").write_text(srt, encoding="utf-8-sig")
    master = read_wav(OUT / "master.wav")
    measurement = run(ffmpeg, "-hide_banner", "-i", str(OUT / "master.wav"),
                      "-af", "ebur128=peak=true", "-f", "null", "-").stderr
    loudness_summary = measurement.split('Summary:')[-1]
    integrated = re.search(r"I:\s+(-?[\d.]+) LUFS", loudness_summary)
    true_peak = re.search(r"Peak:\s+(-?[\d.]+) dBFS", loudness_summary)
    report = {
        "duration_seconds": len(master) / SR,
        "sample_rate": SR,
        "channels": master.shape[1],
        "peak_dbfs": round(20 * np.log10(max(np.max(np.abs(master)), 1e-12)), 2),
        "rms_dbfs": round(20 * np.log10(max(np.sqrt(np.mean(master ** 2)), 1e-12)), 2),
        "integrated_loudness_lufs": float(integrated.group(1)) if integrated else None,
        "true_peak_dbfs": float(true_peak.group(1)) if true_peak else None,
        "voice_provider": {'edge': 'Microsoft Edge TTS', 'sapi': 'Windows SAPI', 'qwen': 'Local Qwen3-TTS-12Hz-1.7B-CustomVoice'}[provider],
        "voice_name": {'edge': 'zh-CN-XiaoxiaoNeural', 'sapi': 'Microsoft Huihui', 'qwen': QWEN_SPEAKER}[provider],
        "voice_instruct": QWEN_INSTRUCT if provider == 'qwen' else None,
        "music": "Original deterministic synth composition; seed 6102026, D major, 96 BPM",
        "ffmpeg": Path(ffmpeg).name,
        "local_model": 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice' if provider == 'qwen' else None,
        "voice_runtime": '.dataforge/promo-tts/runtime' if provider == 'qwen' else None,
        "voice_model_license": 'Apache-2.0' if provider == 'qwen' else None,
        "voice_sources": [
            'https://github.com/QwenLM/Qwen3-TTS',
            'https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice',
        ] if provider == 'qwen' else [],
        "scene_voice_timings": cues,
    }
    assert report["duration_seconds"] == 60
    assert all(cue["voice_end"] <= cue["end"] for cue in cues)
    assert all(cue['end'] <= following['start'] for cue, following in zip(subtitle_cues, subtitle_cues[1:]))
    assert all(np.sqrt(np.mean(master[int(a * SR):int(b * SR)] ** 2)) > .001 for a, b, _ in SCENES)
    (OUT / "audio_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    reproduce = (
        'The chosen voice runs locally in the isolated Python 3.12 environment '
        '`.dataforge/promo-tts/runtime`. It uses torch/torchaudio 2.8.0 + CUDA 12.8, '
        'qwen-tts 0.1.1, transformers 4.57.3, accelerate 1.12.0, Gradio 5.49.1 and Hub 0.36.2. '
        'Public model weights live in ignored '
        '`.dataforge/promo-tts/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`; weights and the environment are not repository assets.\n\n'
        'Run `.dataforge/promo-tts/runtime/Scripts/python.exe scripts/promo/generate_audio.py --voice qwen`. '
        'Use `--qwen-sample` to render the opening question by itself. Cached raw voice files are reused; '
        'changing text or the prosody instruction changes the cache identity. '
        'Explicit `--voice edge` and `--voice sapi` remain optional fallbacks.\n\n'
        'Voice model: [official Qwen3-TTS repository](https://github.com/QwenLM/Qwen3-TTS) and '
        '[official 1.7B CustomVoice model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice), '
        'Apache-2.0. The warm Mandarin speaker is Serena; the exact prosody instruction is recorded in audio_report.json.\n'
    ) if provider == 'qwen' else (
        'Reproduce: `python -m pip install numpy scipy edge-tts imageio-ffmpeg`, then '
        '`python scripts/promo/generate_audio.py`. Use `--voice sapi` for installed Windows Huihui offline. '
        'Existing raw voice files are reused.\n'
    )
    (OUT / "README.md").write_text(
        "# Promo soundtrack\n\n"
        f"Chinese narration: {report['voice_provider']} — {report['voice_name']}. "
        "The narration is locally rendered; playback makes no speech-service requests.\n\n"
        "Music is an original deterministic composition created by this script with synthesized "
        "pads, plucks, bass pulses and filtered noise. No recordings, samples or third-party music are used.\n\n"
        "Final master: 60 seconds, stereo, 48 kHz; loudness target −16 LUFS, true-peak target −1.5 dBTP. "
        "Sentence-aligned captions are in cues.json; scene and measured voice timings are in subtitle_cues.json. "
        "Standalone captions are in subtitles.srt and ../captions.srt.\n\n" + reproduce, encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "scene_voice_timings"},
                     ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
