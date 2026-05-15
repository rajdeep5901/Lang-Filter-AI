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
from collections import deque, Counter
from faster_whisper import WhisperModel

# --- Configuration ---
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
TARGET_LANGUAGE = "en"
SAMPLE_RATE = 16000
BLOCK_DURATION_MS = 30               # small frame for I/O
BLOCK_SAMPLES = int(SAMPLE_RATE * BLOCK_DURATION_MS / 1000)
DETECTION_INTERVAL = 0.5            # seconds between detection
BUFFER_DURATION_S = 1.5             # accumulate 1s of audio
BUFFER_FRAMES = int(BUFFER_DURATION_S * 1000 / BLOCK_DURATION_MS)
CONFIDENCE_THRESHOLD = 0.4       # for PLAY/MUTE decision
VOTE_CONFIDENCE_MIN  = 0.25      # minimum conf to count a vote (lower = more responsive to non-English)
ENGLISH_VOTE_CONFIDENCE_MIN = 0.35  # Whisper often over-picks English on weak/ambiguous speech
MIN_SPEECH_RMS = 0.003          # skip silence/noise so it cannot become a language vote
MIN_LANGUAGE_MARGIN = 0.05      # reject close language guesses as unstable
TARGET_SUPPORT_MIN = 0.30       # smoothed vote share required before audio is opened
CONTRADICT_CONFIDENCE = 0.70    # immediately close audio on strong non-target detections
HISTORY_WINDOW = 7               # last N detections for voting (~2.5s at 0.5s intervals)
INPUT_DEVICE = os.environ.get("AUDIO_INPUT_DEVICE")  # optional device index/name override

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
    "decision": "MUTE",
    "target_language": TARGET_LANGUAGE,
    "volume_rms": 0.0,
    "raw_language": "unknown",   # latest single detection (before voting)
    "raw_confidence": 0.0,
    "language_support": 0.0,
}

# --- Model Initialization ---
model = None

def load_model():
    global model
    logging.info("Loading faster-whisper model (tiny/base-int8)...")
    #model = WhisperModel("base", device="cpu", compute_type="int8")
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
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
    if INPUT_DEVICE:
        try:
            return int(INPUT_DEVICE)
        except ValueError:
            wanted = INPUT_DEVICE.lower()
            for idx, device in enumerate(devices):
                if wanted in device["name"].lower() and device["max_input_channels"] > 0:
                    return idx
            raise RuntimeError(f"Input device not found: {INPUT_DEVICE}")

    default_input = sd.default.device[0]
    if default_input is not None and default_input >= 0:
        device = sd.query_devices(default_input)
        if device["max_input_channels"] > 0:
            return default_input

    for idx, device in enumerate(devices):
        if device["max_input_channels"] > 0:
            return idx

    raise RuntimeError("No input audio device with input channels was found.")

def _resolve_output_device(devices):
    '''default_output = sd.default.device[1]
    if default_output is not None and default_output >= 0:
        device = sd.query_devices(default_output)
        if device["max_output_channels"] > 0:
            return default_output

    for idx, device in enumerate(devices):
        if device["max_output_channels"] > 0:
            return idx

    raise RuntimeError("No output audio device with output channels was found.")'''
    return 5

def _stream_config():
    devices = sd.query_devices()
    input_device = _resolve_input_device(devices)
    output_device = _resolve_output_device(devices)
    input_info = sd.query_devices(input_device)
    output_info = sd.query_devices(output_device)

    input_channels = max(1, int(input_info["max_input_channels"]))
    output_channels = 1

    try:
        sd.check_input_settings(
            device=input_device,
            channels=input_channels,
            samplerate=SAMPLE_RATE,
            dtype="float32",
        )
    except Exception as exc:
        logging.warning(
            f"Input device rejected {input_channels} channels; trying mono input. Reason: {exc}"
        )
        input_channels = 1
        sd.check_input_settings(
            device=input_device,
            channels=input_channels,
            samplerate=SAMPLE_RATE,
            dtype="float32",
        )

    try:
        sd.check_output_settings(
            device=output_device,
            channels=output_channels,
            samplerate=SAMPLE_RATE,
            dtype="float32",
        )
    except Exception as exc:
        output_channels = max(1, int(output_info["max_output_channels"]))
        logging.warning(
            f"Output device rejected mono output; trying {output_channels} channels. Reason: {exc}"
        )
        sd.check_output_settings(
            device=output_device,
            channels=output_channels,
            samplerate=SAMPLE_RATE,
            dtype="float32",
        )

    logging.info(
        "Audio devices: input=%s (%s, channels=%d), output=%s (%s, channels=%d), samplerate=%d",
        input_device,
        input_info["name"],
        input_channels,
        output_device,
        output_info["name"],
        output_channels,
        SAMPLE_RATE,
    )

    return {
        "device": (input_device, output_device),
        "channels": (input_channels, output_channels),
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
                else:
                    # Whisper expects float32 normalized
                    audio_f32 = audio.astype(np.float32)
                    _, info = model.transcribe(
                        audio_f32, language=None, beam_size=1,
                        condition_on_previous_text=False,
                        vad_filter=True,
                        vad_parameters=dict(min_silence_duration_ms=300)
                    )
                    lang = info.language or "unknown"
                    conf = float(info.language_probability or 0.0)
                    margin = _language_margin(info, conf)
            except Exception as e:
                logging.warning(f"Detection error: {e}")
                lang, conf, margin = "unknown", 0.0, 0.0

            with state_lock:
                # Store raw detection (before voting)
                latest_status["raw_language"] = lang
                latest_status["raw_confidence"] = float(conf)

                vote_min = ENGLISH_VOTE_CONFIDENCE_MIN if lang == "en" else VOTE_CONFIDENCE_MIN
                if conf < vote_min or margin < MIN_LANGUAGE_MARGIN:
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
        channels=stream_config["channels"],
        dtype='float32',
        callback=audio_callback,
        latency='low',
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
