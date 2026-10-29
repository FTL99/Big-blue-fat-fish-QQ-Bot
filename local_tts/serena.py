from pathlib import Path
import os
import argparse
import time

ROOT = Path(__file__).resolve().parent
for folder in ("outputs", "temp/gradio", "cache/huggingface", "cache/torch"):
    (ROOT / folder).mkdir(parents=True, exist_ok=True)
for key, value in {
    "HF_HOME": ROOT / "cache" / "huggingface",
    "TORCH_HOME": ROOT / "cache" / "torch",
    "GRADIO_TEMP_DIR": ROOT / "temp" / "gradio",
    "TEMP": ROOT / "temp",
    "TMP": ROOT / "temp",
}.items():
    os.environ[key] = str(value)
os.environ["GRADIO_ANALYTICS_ENABLED"] = "False"
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["HF_HUB_OFFLINE"] = "1"

import numpy as np
import soundfile as sf
import torch
from qwen_tts import Qwen3TTSModel

parser = argparse.ArgumentParser(description="Local Qwen3-TTS Serena")
parser.add_argument("--web", action="store_true")
parser.add_argument("--no-browser", action="store_true")
parser.add_argument("--text", default="晚上好呀。忙了一天，先休息一下吧，我在这里陪着你。")
parser.add_argument("--instruct", default="用温柔、放松、亲切的语气说。")
args = parser.parse_args()
if args.web:
    (ROOT / "web.pid").write_text(str(os.getpid()), encoding="ascii")

print("Loading Serena on CUDA...", flush=True)
model = Qwen3TTSModel.from_pretrained(
    str(ROOT / "models" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"),
    device_map="cuda:0", dtype=torch.bfloat16, attn_implementation="sdpa",
)
assert "serena" in [s.lower() for s in model.get_supported_speakers()]

def synthesize(text, instruct):
    if not text.strip():
        raise ValueError("请输入要朗读的文字。")
    started = time.perf_counter()
    wavs, sr = model.generate_custom_voice(
        text=text, language="Chinese", speaker="Serena", instruct=instruct,
    )
    wav = np.asarray(wavs[0])
    assert wav.size > 0 and np.isfinite(wav).all() and np.max(np.abs(wav)) > 1e-5
    output = ROOT / "outputs" / f"serena_{time.time_ns()}.wav"
    sf.write(str(output), wav, sr)
    print(f"Saved {output} | audio={len(wav)/sr:.2f}s | generation={time.perf_counter()-started:.2f}s | peak_cuda={torch.cuda.max_memory_allocated()/1024**3:.2f}GiB", flush=True)
    return str(output)

if args.web:
    import gradio as gr
    demo = gr.Interface(
        fn=synthesize,
        inputs=[gr.Textbox(label="朗读文字", value=args.text, lines=4),
                gr.Textbox(label="语气指令", value=args.instruct)],
        outputs=gr.Audio(label="Serena 语音", type="filepath"),
        title="Serena · 本地中文语音", flagging_mode="never",
    )
    demo.queue(default_concurrency_limit=1).launch(server_name="127.0.0.1", server_port=7865, inbrowser=not args.no_browser)
else:
    synthesize(args.text, args.instruct)


