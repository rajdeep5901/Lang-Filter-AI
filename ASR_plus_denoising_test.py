'''import sounddevice as sd
import numpy as np
import logging
import threading
import queue
import time
from collections import deque
from faster_whisper import WhisperModel

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
TARGET_LANGUAGE = "en"
SAMPLE_RATE = 16000
BLOCK_DURATION_MS = 30               # small frame for I/O
BLOCK_SAMPLES = int(SAMPLE_RATE * BLOCK_DURATION_MS / 1000)
DETECTION_INTERVAL = 0.5            # seconds between detection
BUFFER_DURATION_S = 1.0             # accumulate 1s of audio
BUFFER_FRAMES = int(BUFFER_DURATION_S * 1000 / BLOCK_DURATION_MS)
#CONFIDENCE_THRESHOLD = 0.3
CONFIDENCE_THRESHOLD = 0.5
VOTE_THRESHOLD = 0.7
HISTORY_WINDOW = int(2.0 * 1000 / BLOCK_DURATION_MS)  # 2s window

# --- Queues and State ---
audio_input_q = queue.Queue(maxsize=100)
audio_output_q = queue.Queue(maxsize=100)
detection_q   = queue.Queue(maxsize=50)

state_lock = threading.Lock()
current_language = "unknown"
language_conf = 0.0
history = deque(maxlen=HISTORY_WINDOW)
running = True

latest_status = {
    "language": "unknown",
    "confidence": 0.0,
    "decision": "MUTE"
}

# --- Model Initialization ---
model = None

def load_model():
    global model
    logging.info("Loading faster-whisper model (tiny/base-int8)...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    logging.info("Model loaded.")

# --- Detection Worker ---
def detection_worker():
    global current_language, language_conf, running
    buffer = deque(maxlen=BUFFER_FRAMES)
    last_detect = 0
    while running:
        try:
            frame = detection_q.get(timeout=0.1)
            buffer.append(frame)
        except queue.Empty:
            continue

        now = time.time()
        if len(buffer) >= BUFFER_FRAMES and (now - last_detect) >= DETECTION_INTERVAL:
            audio = np.concatenate(list(buffer))
            try:
                # Whisper expects float32 normalized
                audio_f32 = audio.astype(np.float32)
                _, info = model.transcribe(
                    audio_f32, language=None, beam_size=1,
                    condition_on_previous_text=False,
                    vad_filter=True,
                    vad_parameters=dict(min_silence_duration_ms=300)
                )
                lang = info.language
                conf = info.language_probability
            except Exception as e:
                logging.warning(f"Detection error: {e}")
                lang, conf = "unknown", 0.0

            with state_lock:
                language_conf = conf
                history.append(lang if conf >= CONFIDENCE_THRESHOLD else "unk")
                #history.append(lang)
                # vote
                votes = Counter(history)
                lang_vote, _ = votes.most_common(1)[0]
                current_language = lang_vote
                
                #latest_status["language"] = current_language
                #latest_status["confidence"] = float(language_conf)
                #latest_status["decision"] = "PLAY" if current_language == TARGET_LANGUAGE else "MUTE"
                
                 # ✅ UPDATE BLOCK
                latest_status["language"] = current_language
                latest_status["confidence"] = float(language_conf)

                #if current_language == TARGET_LANGUAGE and language_conf >= 0.1:
                if current_language == TARGET_LANGUAGE and language_conf >= CONFIDENCE_THRESHOLD:
                    latest_status["decision"] = "PLAY"
                else:
                    latest_status["decision"] = "MUTE"

            buffer.clear()
            last_detect = now
            logging.info(f"Detected language: {current_language} (conf={language_conf:.2f})")

# --- Routing Worker ---
def routing_worker():
    global running
    current_vol = 0.0
    fade_rate = 0.1
    while running:
        try:
            frame = audio_input_q.get(timeout=0.05)
        except queue.Empty:
            continue
        # Determine forwarding
        with state_lock:
            #ok = (current_language == TARGET_LANGUAGE) 
            ok = (current_language == TARGET_LANGUAGE and language_conf >= CONFIDENCE_THRESHOLD)
        target_vol = 1.0 if ok else 0.0
        # smooth fade
        current_vol += (target_vol - current_vol) * fade_rate
        out_frame = frame * current_vol
        try:
            audio_output_q.put_nowait(out_frame)
        except queue.Full:
            pass

# --- Audio Callback ---
def audio_callback(indata, outdata, frames, time_info, status):
    if status:
        logging.warning(f"Audio status: {status}")
    mono = indata.flatten()
    # enqueue for detection and routing
    try:
        audio_input_q.put_nowait(mono)
    except queue.Full:
        pass
    try:
        detection_q.put_nowait(mono)
    except queue.Full:
        pass
    # output
    try:
        frame = audio_output_q.get_nowait()
        outdata[:] = frame.reshape(outdata.shape)
    except queue.Empty:
        outdata.fill(0)

# --- Main ---
def main():
    global running
    load_model()
    stream_config = _stream_config()
    # start workers
    det_t = threading.Thread(target=detection_worker, daemon=True)
    rout_t = threading.Thread(target=routing_worker, daemon=True)
    det_t.start()
    rout_t.start()

    logging.info("Starting audio stream. Speak now...")
    with sd.Stream(
        samplerate=SAMPLE_RATE,
        blocksize=BLOCK_SAMPLES,
        channels=1,
        dtype='float32',
        callback=audio_callback,
        latency='low'
    ):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping...")
            running = False

if __name__ == "__main__":
    main()'''

import sounddevice as sd
import numpy as np
import logging
import threading
import queue
import time
import os
import platform
from collections import deque, Counter
from faster_whisper import WhisperModel

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

def _env_flag(name, default=False):
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")

# Pi / edge profile: lighter inference cadence, higher stream latency, native ALSA rates.
IS_PI_PROFILE = _env_flag("LANGFILTER_PI") or (
    platform.system() == "Linux"
    and os.path.exists("/proc/device-tree/model")
    and "raspberry pi" in open("/proc/device-tree/model", "rb").read().decode("utf-8", "ignore").lower()
)

TARGET_LANGUAGE = "en"
WHISPER_SAMPLE_RATE = 16000
# Full-duplex stream rate — negotiated with hardware (Pi headphone jack prefers 44100/48000).
STREAM_SAMPLE_RATE = int(os.environ.get("AUDIO_SAMPLE_RATE", "48000" if IS_PI_PROFILE else "16000"))
BLOCK_DURATION_MS = 30 if not IS_PI_PROFILE else 40
BLOCK_SAMPLES = int(STREAM_SAMPLE_RATE * BLOCK_DURATION_MS / 1000)
DETECTION_INTERVAL = 0.75 if IS_PI_PROFILE else 0.5
BUFFER_DURATION_S = 2.5 if IS_PI_PROFILE else 3.5
BUFFER_FRAMES = int(BUFFER_DURATION_S * 1000 / BLOCK_DURATION_MS)
CONFIDENCE_THRESHOLD = 0.45
VOTE_CONFIDENCE_MIN = 0.25
ENGLISH_VOTE_CONFIDENCE_MIN = 0.50 if IS_PI_PROFILE else 0.40
MIN_SPEECH_RMS = 0.003
MIN_LANGUAGE_MARGIN = 0.08 if IS_PI_PROFILE else 0.05
ENGLISH_MIN_MARGIN = 0.12
TARGET_SUPPORT_MIN = 0.35
CONTRADICT_CONFIDENCE = 0.65
HISTORY_WINDOW = 6
WHISPER_BEAM_SIZE = 1 if IS_PI_PROFILE else 3
WHISPER_BEST_OF = 1 if IS_PI_PROFILE else 3
USE_VAD = False
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "tiny" if IS_PI_PROFILE else "base")
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8" if IS_PI_PROFILE else "float32")
STREAM_LATENCY = os.environ.get("AUDIO_LATENCY", "high" if IS_PI_PROFILE else "low")
INPUT_DEVICE = os.environ.get("AUDIO_INPUT_DEVICE")
OUTPUT_DEVICE = os.environ.get("AUDIO_OUTPUT_DEVICE")
OUTPUT_QUEUE_SIZE = 200 if IS_PI_PROFILE else 100

# --- Queues and State ---
audio_input_q = queue.Queue(maxsize=100)
audio_output_q = queue.Queue(maxsize=OUTPUT_QUEUE_SIZE)
detection_q   = queue.Queue(maxsize=50)

state_lock = threading.Lock()
current_language = "unknown"
language_conf = 0.0
history = deque(maxlen=HISTORY_WINDOW)
running = True

latest_status = {
    "language": "unknown",
    "confidence": 0.0,
    "decision": "MUTE",
    "target_language": TARGET_LANGUAGE,
    "volume_rms": 0.0,
    "raw_language": "unknown",   # latest single detection (before voting)
    "raw_confidence": 0.0,
    "language_support": 0.0,
    "stream_sample_rate": STREAM_SAMPLE_RATE,
    "pi_profile": IS_PI_PROFILE,
}

# --- Model Initialization ---
model = None

def load_model():
    global model
    logging.info(
        "Loading faster-whisper model (%s, cpu, %s)...",
        WHISPER_MODEL,
        WHISPER_COMPUTE,
    )
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type=WHISPER_COMPUTE)
    logging.info("Model loaded.")

# --- Target Language Setter (called from server.py) ---
def set_target_language(lang_code):
    global TARGET_LANGUAGE
    with state_lock:
        TARGET_LANGUAGE = lang_code.lower()
        latest_status["target_language"] = TARGET_LANGUAGE
    logging.info(f"Target language changed to: {TARGET_LANGUAGE}")

def _language_margin(info, fallback_conf):
    probs = getattr(info, "all_language_probs", None)
    if not probs:
        return fallback_conf

    values = []
    for item in probs:
        if isinstance(item, tuple) and len(item) >= 2:
            values.append(float(item[1]))
        elif hasattr(item, "probability"):
            values.append(float(item.probability))

    if len(values) < 2:
        return fallback_conf

    values.sort(reverse=True)
    return values[0] - values[1]

def _top_language_codes(info, limit=5):
    probs = getattr(info, "all_language_probs", None)
    if not probs:
        return []

    ranked = []
    for item in probs:
        if isinstance(item, tuple) and len(item) >= 2:
            ranked.append((str(item[0]), float(item[1])))
        elif hasattr(item, "language") and hasattr(item, "probability"):
            ranked.append((str(item.language), float(item.probability)))

    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked[:limit]

def _english_ambiguity_reject(info, lang, conf, margin):
    """Whisper base/tiny often over-picks English on Hindi/Japanese chunks."""
    if lang != "en":
        return False

    required_margin = ENGLISH_MIN_MARGIN
    if margin < required_margin:
        return True

    ranked = _top_language_codes(info)
    if len(ranked) < 2:
        return conf < ENGLISH_VOTE_CONFIDENCE_MIN

    second_lang, second_conf = ranked[1]
    # If a non-English language is close behind, treat English as unreliable.
    if second_lang != "en" and (conf - second_conf) < required_margin:
        return True

    return conf < ENGLISH_VOTE_CONFIDENCE_MIN

def _resample_audio(audio, source_rate, target_rate):
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)

    if len(audio) == 0:
        return audio.astype(np.float32, copy=False)

    target_len = max(1, int(round(len(audio) * target_rate / source_rate)))
    source_idx = np.linspace(0, len(audio) - 1, num=target_len, dtype=np.float32)
    return np.interp(source_idx, np.arange(len(audio), dtype=np.float32), audio).astype(np.float32)

def _resolve_device(devices, override, want_input):
    if override:
        try:
            return int(override)
        except ValueError:
            wanted = override.lower()
            hints = [wanted]
            if "headphone" in wanted or "bcm2835" in wanted:
                hints.extend(["bcm2835", "headphones", "headphone"])

            for hint in hints:
                for idx, device in enumerate(devices):
                    name = device["name"].lower()
                    channel_ok = (
                        device["max_input_channels"] > 0
                        if want_input
                        else device["max_output_channels"] > 0
                    )
                    if hint in name and channel_ok:
                        return idx
            raise RuntimeError(f"Audio device not found: {override}")

    default_idx = sd.default.device[0 if want_input else 1]
    if default_idx is not None and default_idx >= 0:
        device = sd.query_devices(default_idx)
        channel_ok = (
            device["max_input_channels"] > 0
            if want_input
            else device["max_output_channels"] > 0
        )
        if channel_ok:
            return default_idx

    for idx, device in enumerate(devices):
        channel_ok = (
            device["max_input_channels"] > 0
            if want_input
            else device["max_output_channels"] > 0
        )
        if channel_ok:
            return idx

    direction = "input" if want_input else "output"
    raise RuntimeError(f"No {direction} audio device was found.")

def _negotiate_sample_rate(input_device, output_device, input_channels, output_channels):
    candidates = []
    preferred = STREAM_SAMPLE_RATE
    for rate in (preferred, 48000, 44100, 32000, 16000, 22050):
        if rate not in candidates:
            candidates.append(rate)

    for rate in candidates:
        try:
            sd.check_input_settings(
                device=input_device,
                channels=input_channels,
                samplerate=rate,
                dtype="float32",
            )
            sd.check_output_settings(
                device=output_device,
                channels=output_channels,
                samplerate=rate,
                dtype="float32",
            )
            return rate
        except Exception:
            continue

    raise RuntimeError(
        "Could not find a sample rate supported by both input and output devices."
    )

def _vote_language():
    scores = {}
    conf_sums = {}
    weight_sums = {}

    for index, (lang, conf) in enumerate(history):
        if lang == "unk":
            continue
        recency_weight = 1.0 + (index * 0.15)
        score = conf * recency_weight
        scores[lang] = scores.get(lang, 0.0) + score
        conf_sums[lang] = conf_sums.get(lang, 0.0) + score
        weight_sums[lang] = weight_sums.get(lang, 0.0) + recency_weight

    if not scores:
        return "unknown", 0.0, 0.0

    voted_lang = max(scores, key=scores.get)
    total_score = sum(scores.values())
    support = scores[voted_lang] / total_score if total_score else 0.0
    voted_conf = conf_sums[voted_lang] / weight_sums[voted_lang]
    return voted_lang, voted_conf, support

def _should_play(target_lang, voted_lang, voted_conf, support, raw_lang, raw_conf):
    if voted_lang != target_lang:
        return False
    if voted_conf < CONFIDENCE_THRESHOLD or support < TARGET_SUPPORT_MIN:
        return False
    if raw_lang != target_lang and raw_conf >= CONTRADICT_CONFIDENCE:
        return False
    return True

def _resolve_input_device(devices):
    return _resolve_device(devices, INPUT_DEVICE, want_input=True)

def _resolve_output_device(devices):
    # Prefer Pi headphone jack when no override is set.
    if IS_PI_PROFILE and not OUTPUT_DEVICE:
        for idx, device in enumerate(devices):
            name = device["name"].lower()
            if device["max_output_channels"] > 0 and (
                "bcm2835" in name or "headphones" in name or "headphone" in name
            ):
                return idx
    return _resolve_device(devices, OUTPUT_DEVICE, want_input=False)

def _stream_config():
    devices = sd.query_devices()
    input_device = _resolve_input_device(devices)
    output_device = _resolve_output_device(devices)
    input_info = sd.query_devices(input_device)
    output_info = sd.query_devices(output_device)

    input_channels = max(1, int(input_info["max_input_channels"]))
    output_channels = 1

    try:
        sd.check_output_settings(
            device=output_device,
            channels=output_channels,
            samplerate=STREAM_SAMPLE_RATE,
            dtype="float32",
        )
    except Exception as exc:
        output_channels = max(1, int(output_info["max_output_channels"]))
        logging.warning(
            "Output device rejected mono output; trying %d channels. Reason: %s",
            output_channels,
            exc,
        )

    try:
        sd.check_input_settings(
            device=input_device,
            channels=input_channels,
            samplerate=STREAM_SAMPLE_RATE,
            dtype="float32",
        )
    except Exception as exc:
        logging.warning(
            "Input device rejected %d channels; trying mono input. Reason: %s",
            input_channels,
            exc,
        )
        input_channels = 1

    stream_rate = _negotiate_sample_rate(
        input_device,
        output_device,
        input_channels,
        output_channels,
    )

    logging.info(
        "Audio devices: input=%s (%s, ch=%d), output=%s (%s, ch=%d), "
        "stream_rate=%d, whisper_rate=%d, pi_profile=%s",
        input_device,
        input_info["name"],
        input_channels,
        output_device,
        output_info["name"],
        output_channels,
        stream_rate,
        WHISPER_SAMPLE_RATE,
        IS_PI_PROFILE,
    )

    return {
        "device": (input_device, output_device),
        "channels": (input_channels, output_channels),
        "stream_sample_rate": stream_rate,
    }

# --- Detection Worker ---
def detection_worker():
    global current_language, language_conf, running
    buffer = deque(maxlen=BUFFER_FRAMES)
    last_detect = 0
    while running:
        try:
            frame = detection_q.get(timeout=0.1)
            buffer.append(frame)
        except queue.Empty:
            continue

        now = time.time()
        if len(buffer) >= BUFFER_FRAMES and (now - last_detect) >= DETECTION_INTERVAL:
            audio = np.concatenate(list(buffer))
            audio_rms = float(np.sqrt(np.mean(audio ** 2)))
            try:
                if audio_rms < MIN_SPEECH_RMS:
                    lang, conf, margin = "unknown", 0.0, 0.0
                    info = None
                else:
                    whisper_audio = _resample_audio(
                        audio.astype(np.float32, copy=False),
                        stream_sample_rate,
                        WHISPER_SAMPLE_RATE,
                    )
                    _, info = model.transcribe(
                        whisper_audio,
                        language=None,
                        beam_size=WHISPER_BEAM_SIZE,
                        best_of=WHISPER_BEST_OF,
                        condition_on_previous_text=False,
                        vad_filter=USE_VAD,
                    )
                    lang = info.language or "unknown"
                    conf = float(info.language_probability or 0.0)
                    margin = _language_margin(info, conf)
            except Exception as e:
                logging.warning(f"Detection error: {e}")
                lang, conf, margin, info = "unknown", 0.0, 0.0, None

            with state_lock:
                # Store raw detection (before voting)
                latest_status["raw_language"] = lang
                latest_status["raw_confidence"] = float(conf)

                vote_min = ENGLISH_VOTE_CONFIDENCE_MIN if lang == "en" else VOTE_CONFIDENCE_MIN
                reject = (
                    conf < vote_min
                    or margin < MIN_LANGUAGE_MARGIN
                    or (info is not None and _english_ambiguity_reject(info, lang, conf, margin))
                )
                if reject:
                    history.append(("unk", 0.0))
                else:
                    history.append((lang, conf))

                voted_lang, voted_conf, support = _vote_language()
                current_language = voted_lang
                language_conf = voted_conf

                # Update status
                latest_status["language"] = current_language
                latest_status["confidence"] = float(language_conf)
                latest_status["target_language"] = TARGET_LANGUAGE
                latest_status["language_support"] = float(support)
                latest_status["decision"] = "PLAY" if _should_play(
                    TARGET_LANGUAGE, current_language, language_conf, support, lang, conf
                ) else "MUTE"

            buffer.clear()
            last_detect = now
            logging.info(
                f"Raw: {lang} ({conf:.2f}, margin={margin:.2f}, rms={audio_rms:.3f}) | "
                f"Voted: {current_language} ({language_conf:.2f}) | "
                f"Support: {support:.2f} | Decision: {latest_status['decision']}"
            )

# --- Routing Worker ---
def routing_worker():
    global running
    current_vol = 0.0
    fade_rate = 0.1
    while running:
        try:
            frame = audio_input_q.get(timeout=0.05)
        except queue.Empty:
            continue
        # Determine forwarding
        with state_lock:
            ok = (latest_status["decision"] == "PLAY")
        target_vol = 1.0 if ok else 0.0
        # smooth fade
        current_vol += (target_vol - current_vol) * fade_rate
        out_frame = frame * current_vol
        try:
            audio_output_q.put_nowait(out_frame)
        except queue.Full:
            pass

# --- Audio Callback ---
def audio_callback(indata, outdata, frames, time_info, status):
    if status:
        logging.warning(f"Audio status: {status}")
    if indata.ndim == 1 or indata.shape[1] == 1:
        mono = indata.reshape(-1).astype(np.float32, copy=False)
    else:
        mono = np.mean(indata, axis=1, dtype=np.float32)

    # Compute RMS volume level and store in latest_status
    rms = float(np.sqrt(np.mean(mono ** 2)))
    # Normalize RMS to 0-1 range (clamp, typical speech RMS is 0.01-0.3)
    normalized_rms = min(1.0, rms / 0.15)
    with state_lock:
        # Fast rise, slower decay — so speech spikes are visible
        old = latest_status["volume_rms"]
        if normalized_rms > old:
            latest_status["volume_rms"] = old * 0.3 + normalized_rms * 0.7  # fast rise
        else:
            latest_status["volume_rms"] = old * 0.85 + normalized_rms * 0.15  # slow decay

    # enqueue for detection and routing
    try:
        audio_input_q.put_nowait(mono)
    except queue.Full:
        pass
    try:
        detection_q.put_nowait(mono)
    except queue.Full:
        pass
    # output
    try:
        frame = audio_output_q.get_nowait()
        mono_out = frame.reshape(-1, 1)
        if outdata.shape[1] == 1:
            outdata[:] = mono_out
        else:
            outdata[:] = np.repeat(mono_out, outdata.shape[1], axis=1)
    except queue.Empty:
        outdata.fill(0)

# --- Main ---
stream_sample_rate = STREAM_SAMPLE_RATE

def main():
    global running, stream_sample_rate
    load_model()
    stream_config = _stream_config()
    stream_sample_rate = stream_config["stream_sample_rate"]
    block_samples = int(stream_sample_rate * BLOCK_DURATION_MS / 1000)

    with state_lock:
        latest_status["stream_sample_rate"] = stream_sample_rate
        latest_status["pi_profile"] = IS_PI_PROFILE

    # start workers
    det_t = threading.Thread(target=detection_worker, daemon=True)
    rout_t = threading.Thread(target=routing_worker, daemon=True)
    det_t.start()
    rout_t.start()

    logging.info("Starting audio stream. Speak now...")
    with sd.Stream(
        samplerate=stream_sample_rate,
        blocksize=block_samples,
        channels=stream_config["channels"],
        dtype='float32',
        callback=audio_callback,
        latency=STREAM_LATENCY,
        device=stream_config["device"],
    ):
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Stopping...")
            running = False

if __name__ == "__main__":
    main()
