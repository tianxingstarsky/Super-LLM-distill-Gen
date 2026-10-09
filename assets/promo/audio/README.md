# Promo soundtrack

Chinese narration: Local Qwen3-TTS-12Hz-1.7B-CustomVoice — Serena. The narration is locally rendered; playback makes no speech-service requests.

Music is an original deterministic composition created by this script with synthesized pads, plucks, bass pulses and filtered noise. No recordings, samples or third-party music are used.

Final master: 60 seconds, stereo, 48 kHz; loudness target −16 LUFS, true-peak target −1.5 dBTP. Sentence-aligned captions are in cues.json; scene and measured voice timings are in subtitle_cues.json. Standalone captions are in subtitles.srt and ../captions.srt.

The chosen voice runs locally in the isolated Python 3.12 environment `.dataforge/promo-tts/runtime`. It uses torch/torchaudio 2.8.0 + CUDA 12.8, qwen-tts 0.1.1, transformers 4.57.3, accelerate 1.12.0, Gradio 5.49.1 and Hub 0.36.2. Public model weights live in ignored `.dataforge/promo-tts/models/Qwen3-TTS-12Hz-1.7B-CustomVoice`; weights and the environment are not repository assets.

Run `.dataforge/promo-tts/runtime/Scripts/python.exe scripts/promo/generate_audio.py --voice qwen`. Use `--qwen-sample` to render the opening question by itself. Cached raw voice files are reused; changing text or the prosody instruction changes the cache identity. Explicit `--voice edge` and `--voice sapi` remain optional fallbacks.

Voice model: [official Qwen3-TTS repository](https://github.com/QwenLM/Qwen3-TTS) and [official 1.7B CustomVoice model card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice), Apache-2.0. The warm Mandarin speaker is Serena; the exact prosody instruction is recorded in audio_report.json.
