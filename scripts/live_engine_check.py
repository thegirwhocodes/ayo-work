"""Live check of Ayo's voice engine through the real bridge prompt builder and outbound resampler.

Needs OPENAI_API_KEY + ELEVENLABS_API_KEY (env or .env) and a recorded clip (never commit clips).
Usage: VOICE_ENGINE=openai .venv/bin/python scripts/live_engine_check.py CLIP.wav [--prompt private/ayo_prompt.md]
Writes the reply to eval_out/<clip>.wav (git-ignored) and prints what was heard/said and latency.
"""
import argparse, asyncio, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.voice.audio import Downsample24kTo8k, pcm16_to_mulaw
from app.voice.gemini_live import LiveConfig
from app.voice.openai_realtime import OpenAIRealtimeSession
from app.voice.twilio_stream import TwilioMediaBridge
from harness.tools.declarations import RELAY_DECLARATION


def clip_pcm16(path: str) -> bytes:
    return subprocess.run(["ffmpeg", "-loglevel", "error", "-i", path, "-f", "s16le", "-ar", "8000", "-ac", "1", "-"],
                          capture_output=True, check=True).stdout


async def run(clip: str, prompt: str, country: str) -> None:
    bridge = TwilioMediaBridge(None, prompt)
    bridge.handle_start({"customParameters": {"sabi_country": country}})
    out, turns, first = bytearray(), [], {}
    resampler = Downsample24kTo8k()

    async def on_audio(pcm24k):
        first.setdefault("t", time.monotonic())
        out.extend(pcm16_to_mulaw(resampler.resample(pcm24k)))

    async def on_interrupted(): print("  (barge-in)")
    async def on_turn(caller, model): turns.append((caller, model))
    async def on_tool(name, args): print("  tool:", name, args); return {"status": "pending"}

    s = OpenAIRealtimeSession(LiveConfig(system_prompt=bridge.build_system_prompt(), input_rate=8000, tools=[RELAY_DECLARATION]),
                              on_audio=on_audio, on_interrupted=on_interrupted, on_turn_complete=on_turn, on_tool_call=on_tool)
    await s.connect()
    rx = asyncio.create_task(s.receive_forever())
    pcm = clip_pcm16(clip) + bytes(16000 * 2)  # clip + 2 s of silence, paced like Twilio (20 ms frames)
    for i in range(0, len(pcm), 320):
        await s.send_audio(pcm[i:i + 320]); await asyncio.sleep(0.02)
    stopped = time.monotonic() - 2.0
    for _ in range(80):
        await asyncio.sleep(0.25)
        if turns and not s._is_speaking():
            break
    await asyncio.sleep(0.5); rx.cancel(); await s.close()
    Path("eval_out").mkdir(exist_ok=True)
    name = Path(clip).stem
    Path(f"eval_out/{name}.ulaw").write_bytes(bytes(out))
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "mulaw", "-ar", "8000", "-ac", "1", "-i", f"eval_out/{name}.ulaw",
                    f"eval_out/{name}.wav"], check=True)
    latency = f"{first['t'] - stopped:.2f}s" if "t" in first else "n/a"
    print(f"{name}: heard={turns[0][0]!r}\n   said={turns[0][1]!r}\n   first audio {latency} after the child stopped; {len(out)/8000:.1f}s of reply")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("clip"); ap.add_argument("--prompt", default="app/prompts/sabi_safe_prompt.md"); ap.add_argument("--country", default="NG")
    a = ap.parse_args()
    asyncio.run(run(a.clip, Path(a.prompt).read_text(encoding="utf-8"), a.country))
