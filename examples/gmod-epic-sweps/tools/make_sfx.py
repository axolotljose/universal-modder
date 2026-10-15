#!/usr/bin/env python3
"""Generate the sound effects that ship with Epic SWEPs, from nothing but the
Python standard library (no ffmpeg, no samples, no licensing questions).

Run:  python3 examples/gmod-epic-sweps/tools/make_sfx.py
Out:  examples/gmod-epic-sweps/epic_sweps/sound/epic_sweps/*.wav  (44.1 kHz, mono, 16-bit)

The voice lines are not generated here - they are the TTS takes committed next to
these. If you replace them, keep the file names and Epic SWEPs will use them.
"""

import math
import os
import random
import struct
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "epic_sweps", "sound", "epic_sweps")
SR = 44100


# --------------------------------------------------------------------- filters
def lowpass(x, cutoff_hz):
    """One-pole IIR lowpass, cheap and good enough for noise shaping."""
    a = math.exp(-2.0 * math.pi * cutoff_hz / SR)
    y, prev = [], 0.0
    for v in x:
        prev = v * (1.0 - a) + prev * a
        y.append(prev)
    return y


def highpass(x, cutoff_hz):
    rc = 1.0 / (2.0 * math.pi * cutoff_hz)
    a = rc / (rc + 1.0 / SR)
    y, prev_in, prev_out = [], 0.0, 0.0
    for v in x:
        prev_out = a * (prev_out + v - prev_in)
        prev_in = v
        y.append(prev_out)
    return y


def brown(n, seed=0):
    rnd = random.Random(seed)
    out, v = [], 0.0
    for _ in range(n):
        v += rnd.uniform(-1, 1) * 0.02
        v *= 0.995
        out.append(v)
    m = max(abs(x) for x in out) or 1.0
    return [x / m for x in out]


def white(n, seed=1):
    rnd = random.Random(seed)
    return [rnd.uniform(-1, 1) for _ in range(n)]


def adsr(n, a, d, s, r, peak=1.0, sustain=0.6):
    env = []
    na, nd, nr = max(1, int(n * a)), max(1, int(n * d)), max(1, int(n * r))
    ns = max(0, n - na - nd - nr)
    for i in range(na):
        env.append((i / na) * peak)
    for i in range(nd):
        env.append(peak - (peak - sustain * peak) * (i / nd))
    env.extend([sustain * peak] * ns)
    for i in range(nr):
        env.append(sustain * peak * (1 - i / nr))
    return env[:n] if len(env) >= n else env + [0.0] * (n - len(env))


def normalise(x, peak=0.97):
    m = max((abs(v) for v in x), default=0.0)
    if m < 1e-9:
        return x
    return [v / m * peak for v in x]


def mix(*tracks):
    n = max(len(t) for t in tracks)
    out = [0.0] * n
    for t in tracks:
        for i, v in enumerate(t):
            out[i] += v
    return out


def write_wav(name, samples, bit=16):
    os.makedirs(OUT, exist_ok=True)
    data = normalise(samples)
    path = os.path.join(OUT, name)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        frames = bytearray()
        for v in data:
            frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32767))
        w.writeframes(bytes(frames))
    print("wrote %-22s %6.2fs" % (name, len(samples) / SR))


# ---------------------------------------------------------------------- sounds
def launch(seed=0, dur=0.5):
    """Missile leave-the-tube: sub thump + filtered noise whoosh + descending chirp."""
    n = int(SR * dur)
    t = [i / SR for i in range(n)]

    noise = lowpass(white(n, seed + 11), 2600)
    env = adsr(n, 0.03, 0.35, 0, 0.6, sustain=0.35)
    whoosh = [v * e for v, e in zip(noise, env)]

    sub = []
    for i, x in enumerate(t):
        f = 95 * math.exp(-x * 5.5)
        sub.append(math.sin(2 * math.pi * f * x) * math.exp(-x * 7.0))
    sub = [v * 1.4 for v in sub]

    chirp = []
    phase = 0.0
    for x in t:
        f = 1400 * math.exp(-x * 6.0) + 180
        phase += 2 * math.pi * f / SR
        chirp.append(math.sin(phase) * math.exp(-x * 9.0) * 0.35)

    # crackle on the way out
    cr = random.Random(seed + 3)
    crack = [0.0] * n
    for _ in range(int(n * 0.0015)):
        i = cr.randrange(n)
        w = int(SR * 0.004)
        for k in range(w):
            if i + k < n:
                crack[i + k] += cr.uniform(-1, 1) * (1 - k / w) * 0.5
    crack = highpass(crack, 900)

    return mix(whoosh, sub, chirp, crack)


def explosion(seed=0, dur=2.6):
    """Big blast: transient, low boom, long rumble tail, debris crackle."""
    n = int(SR * dur)
    t = [i / SR for i in range(n)]

    # transient
    tr = highpass(white(n, seed + 5), 400)
    tre = adsr(n, 0.0005, 0.09, 0, 0.05, sustain=0.05)
    transient = [v * e * 1.1 for v, e in zip(tr, tre)]

    # boom: pitch-dropping sine + ring
    boom = []
    phase = 0.0
    for i, x in enumerate(t):
        f = 52 + 60 * math.exp(-x * 9.0)
        phase += 2 * math.pi * f / SR
        boom.append(math.sin(phase) * math.exp(-x * 2.4) * 1.5)

    rumble = lowpass(brown(n, seed + 9), 180)
    re = [math.exp(-(i / SR) * 1.15) for i in range(n)]
    rumble = [v * e * 0.9 for v, e in zip(rumble, re)]

    # debris crackle
    cr = random.Random(seed + 21)
    crack = [0.0] * n
    for _ in range(int(n * 0.0009)):
        i = int(n * (0.05 + 0.95 * cr.random() ** 2))
        w = int(SR * 0.012)
        for k in range(w):
            if 0 <= i + k < n:
                crack[i + k] += cr.uniform(-1, 1) * (1 - k / w) * 0.35
    crack = highpass(crack, 1200)

    return mix(transient, boom, rumble, crack)


def whine(seed=0, dur=3.0):
    """Incoming whistle: falling tone with tremolo. Loops: edges near zero."""
    n = int(SR * dur)
    t = [i / SR for i in range(n)]
    out = []
    phase = 0.0
    for x in t:
        f = 1150 - 700 * (x / dur) + 40 * math.sin(2 * math.pi * 5.5 * x)
        phase += 2 * math.pi * f / SR
        trem = 0.65 + 0.35 * math.sin(2 * math.pi * 17 * x)
        edge = math.sin(math.pi * min(1.0, x / 0.25)) if x < 0.25 else 1.0
        edge *= math.sin(math.pi * min(1.0, (dur - x) / 0.35)) if x > dur - 0.35 else 1.0
        out.append((math.sin(phase) * 0.7 + math.sin(phase * 2) * 0.22) * trem * edge)

    air = lowpass(white(n, seed + 31), 1400)
    ae = [math.sin(math.pi * i / n) for i in range(n)]
    air = [v * e * 0.22 for v, e in zip(air, ae)]
    return mix(out, air)


def siren(seed=0, dur=2.4):
    """Two-tone air-raid whoop. Loopable: starts and ends at the same tone."""
    n = int(SR * dur)
    t = [i / SR for i in range(n)]
    out = []
    phase = 0.0
    for i, x in enumerate(t):
        k = (i % int(SR * 0.8)) / 0.8 / SR
        f = 520 + 260 * math.sin(2 * math.pi * 1.25 * x)
        phase += 2 * math.pi * f / SR
        env = 0.72 + 0.28 * math.sin(2 * math.pi * 2.5 * x)
        out.append((math.sin(phase) * 0.75 + math.sin(phase * 3) * 0.12) * env)
    fade = int(SR * 0.02)
    for i in range(fade):
        out[i] *= i / fade
        out[-(i + 1)] *= i / fade
    return out


def hum(seed=0, dur=2.2):
    """Eerie portal hum for the zombie breach. Loopable."""
    n = int(SR * dur)
    t = [i / SR for i in range(n)]
    out = []
    for x in t:
        v = math.sin(2 * math.pi * 62 * x) * 0.6
        v += math.sin(2 * math.pi * 93.5 * x) * 0.4
        v *= 0.55 + 0.45 * math.sin(2 * math.pi * 0.9 * x)
        v += math.sin(2 * math.pi * 247 * x + 3 * math.sin(2 * math.pi * 0.5 * x)) * 0.18
        out.append(v)

    air = lowpass(brown(n, seed + 77), 700)
    air = [v * 0.35 for v in air]
    out = mix(out, air)

    fade = int(SR * 0.06)
    for i in range(fade):
        out[i] *= i / fade
        out[-(i + 1)] *= i / fade
    return out


def main():
    print("Epic SWEPs - generating sounds into", OUT)

    for i, seed in enumerate([0, 1, 2, 3]):
        name = "launch.wav" if i == 0 else "launch_%d.wav" % i
        write_wav(name, launch(seed))

    for i, seed in enumerate([0, 1, 2, 3]):
        name = "explosion.wav" if i == 0 else "explosion_%d.wav" % i
        write_wav(name, explosion(seed, dur=2.4 if i else 2.9))

    write_wav("whine.wav", whine(dur=3.0))
    write_wav("siren.wav", siren(dur=2.4))
    write_wav("portal_hum.wav", hum(dur=2.2))


if __name__ == "__main__":
    main()
