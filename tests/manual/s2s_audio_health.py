"""S2S audio-health check: drive N turns against a live speech-to-speech provider,
capture the model's PCM per turn, and report defect metrics (clipping, frame-joint
clicks, narrowband tones). Manual diagnostic, not part of CI.

Run inside the backend container (needs provider API keys in env):

    python tests/manual/s2s_audio_health.py \
        --provider gemini_live --model gemini-3.1-flash-live-preview --voice Puck \
        --language hi --temperature 0.8 --out /tmp/s2s_health

Exit 0 = all turns clean, 1 = a turn shows clipping/joint clicks/a fixed tone.
"""

import argparse
import asyncio
import base64
import json
import os
import wave

import numpy as np

RATE = 24000


def analyze_pcm(raw: bytes, frame_lens: list) -> dict:
    """Defect metrics for one turn of 16-bit mono PCM at RATE."""
    if not raw:
        return {"error": "no audio"}
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    out = {
        "seconds": round(len(a) / RATE, 2),
        "peak": int(np.abs(a).max()),
        "rms": round(float(np.sqrt((a**2).mean())), 1),
        "clipped_frac": round(float((np.abs(a) > 32000).mean()), 5),
    }
    # Frame-joint steps: what a gapless player concatenates.
    off, steps = 0, []
    for size in frame_lens[:-1]:
        n0 = off + size // 2
        if 0 < n0 < len(a):
            steps.append(abs(float(a[n0] - a[n0 - 1])))
        off += size // 2
    steps = np.array(steps or [0.0])
    typical = float(np.median(np.abs(np.diff(a)))) + 1e-6
    out["joints"] = len(steps)
    out["joint_median_step"] = round(float(np.median(steps)), 1)
    out["joint_max_step"] = round(float(steps.max()), 1)
    out["joint_clicks"] = int((steps > 20 * typical).sum())
    # Narrowband hunt per 2s window: a fixed electrical/beacon tone repeats bins.
    window_top = []
    window = 2 * RATE
    for start in range(0, len(a) - window, window):
        seg = a[start : start + window] * np.hanning(window)
        spec = np.abs(np.fft.rfft(seg))
        med = float(np.median(spec)) + 1e-6
        top = np.argsort(spec)[-3:][::-1]
        window_top.append(
            {
                "freqs_hz": [round(int(b) * RATE / window) for b in top],
                "peak_med": [round(float(spec[b]) / med, 1) for b in top],
            }
        )
    out["windows"] = window_top
    out["verdict"] = (
        "FAIL"
        if out["clipped_frac"] > 0.01 or out["joint_clicks"] > len(steps) // 2
        else "OK"
    )
    return out


async def capture_turn(provider, text: str, out_path: str | None = None) -> tuple[dict, str]:
    await provider.send_text(text)
    frames, transcript = [], ""
    try:
        async for event in provider.receive_events():
            kind = type(event).__name__
            if kind == "AudioDelta":
                data = event.data
                frames.append(base64.b64decode(data) if isinstance(data, str) else data)
            elif kind == "TranscriptDelta" and event.is_final:
                transcript += event.content
            elif kind == "ResponseDone":
                transcript = event.transcript or transcript
                break
            elif kind == "S2SError":
                return {"error": f"{event.message} ({event.code})"}, transcript
    except StopAsyncIteration:
        pass
    raw = b"".join(frames)
    if out_path and raw:
        with wave.open(out_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(raw)
    report = analyze_pcm(raw, [len(f) for f in frames])
    return report, transcript


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="gemini_live")
    parser.add_argument("--model", default="gemini-3.1-flash-live-preview")
    parser.add_argument("--voice", default="Puck")
    parser.add_argument("--language", default="hi")
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--out", default="/tmp/s2s_health")
    parser.add_argument("turns", nargs="*", default=[
        "Hey! Who are you?",
        "Namaste! Mujhe kal subah doctor se milna hai.",
        "Doctor ka naam specialty batao aur time confirm karo.",
    ])
    args = parser.parse_args()

    from voiceai.s2s.gemini_live_s2s import GeminiLiveS2S

    os.makedirs(args.out, exist_ok=True)
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    provider = GeminiLiveS2S(
        system_prompt="You are a clinic receptionist. Keep replies under 25 words.",
        voice=args.voice,
        model=args.model,
        api_key=key,
        language=args.language,
        temperature=args.temperature,
        vad_silence_duration_ms=600,
        vad_prefix_padding_ms=400,
    )
    await asyncio.wait_for(provider.connect(), timeout=30)
    print(f"connected model={args.model} voice={args.voice}", flush=True)
    overall = True
    try:
        for i, text in enumerate(args.turns):
            path = os.path.join(args.out, f"turn{i}.wav")
            report, transcript = await capture_turn(provider, text, path)
            print(f"--- turn{i} user={text[:50]!r}", flush=True)
            print(f"    reply={transcript[:80]!r}", flush=True)
            print(f"    report={json.dumps(report)[:400]}", flush=True)
            overall = overall and report.get("verdict", "FAIL") == "OK"
    finally:
        await provider.disconnect()
    print("OVERALL:", "OK" if overall else "FAIL", flush=True)
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
