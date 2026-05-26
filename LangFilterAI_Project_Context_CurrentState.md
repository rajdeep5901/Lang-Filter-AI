# LangFilterAI — Current Project Context (Clean Handoff)

This document is meant to hand the project to another AI model without re-entering the long debugging loop.

## 1) What the project is

LangFilterAI is a **real-time language-aware selective audio routing system**.

The goal is simple:

- **English audio should pass through**
- **non-English audio should be muted**
- the system should work live from a microphone
- the UI should show the live state
- the output should go to earphones / speaker output

This is **not** classical denoising.  
It is a **language-gated audio firewall**.

---

## 2) Current project state

### What is currently working
- microphone capture
- live audio streaming
- Flask backend
- UI polling/status updates
- RMS waveform activity
- audio routing logic
- volume fade logic
- queues / worker threads
- Whisper integration
- English detection and audio playback on the laptop setup

### What is still not fully solved
- Whisper still **over-predicts English**
- Hindi and Japanese often get classified as English
- mixed-language speech is still a problem
- strict filters can make even valid English get muted
- Raspberry Pi deployment is **not finalized**

### Current status in one line
The pipeline is working, but the **language-ID accuracy is still the limiting factor**, not the audio plumbing.

---

## 3) The system architecture

Current pipeline:

```text
Mic input
  ↓
sounddevice callback
  ↓
audio queues
  ↓
sliding buffer
  ↓
Whisper language detection
  ↓
confidence / ambiguity filtering
  ↓
temporal voting
  ↓
volume gate
  ↓
earphone output
```

Important idea:
- the callback only captures and enqueues audio
- detection happens in a separate worker
- routing happens in another worker
- output is smoothed instead of hard-switched

---

## 4) Current working code design

### Main model used
```python
WhisperModel("base", device="cpu", compute_type="float32")
```

This is the current laptop-friendly setting.

### Current transcribe settings
```python
_, info = model.transcribe(
    audio_f32,
    language=None,
    beam_size=5,
    best_of=5,
    condition_on_previous_text=False,
    vad_filter=False
)
```

### Current context/buffering
```python
BUFFER_DURATION_S = 3.5
HISTORY_WINDOW = 6
DETECTION_INTERVAL = 0.5
```

This version was chosen because shorter buffers caused much worse language leakage.

---

## 5) What changed over time

### Early version
- 1 second-ish buffers
- weak thresholds
- English leakage
- noisy decisions
- unstable playback

### Then we tried
- stricter English thresholds
- history voting
- aggressive clearing of history
- VAD on Whisper

### What failed
- VAD removed too much speech
- short buffers made Hindi/Japanese collapse into English
- aggressive thresholds muted even valid English
- history clearing was too harsh and caused instability

### What finally stabilized the pipeline
- longer buffer
- float32 model
- beam search increased
- VAD disabled
- ambiguity rejection based on English vs non-English probability
- temporal voting
- smoother output fade

---

## 6) Current language-detection logic

Current logic in spirit:

1. accumulate a buffer
2. send it to Whisper
3. read:
   - `info.language`
   - `info.language_probability`
   - `info.all_language_probs`
4. reject weak or ambiguous detections
5. add accepted labels to history
6. vote over history
7. compute output volume
8. play or mute

Important observation from logs:
- Whisper still often returns **English** even for Hindi/Japanese speech
- the secondary language probabilities do show hints like `ja`, `ko`, `nn`, etc., but English still dominates
- the system therefore still needs tuning if the goal is strict non-English muting

---

## 7) What happened on the laptop setup

### Laptop output
The laptop setup is the stable working environment for development.

It successfully:
- runs the Flask server
- captures microphone audio
- shows live status
- updates waveform activity
- routes English audio to output
- mutes some non-English / ambiguous audio

### Remaining laptop issue
Even on the laptop:
- Whisper often classifies Hindi/Japanese as English
- this can cause non-English speech to pass through
- if thresholds are made too strict, valid English also gets muted

So the laptop setup is **functionally stable**, but **language discrimination is still imperfect**.

---

## 8) What happened on the Raspberry Pi setup

### What was tried
- USB microphone input
- 3.5mm headphone jack output
- device mapping using `sounddevice`
- ALSA testing with `speaker-test`, `arecord`, `amixer`, `alsactl`
- changing input/output device handling
- trying different sample rates
- trying different stream configurations

### What failed
The Pi debugging loop was dominated by:
- invalid sample-rate errors
- invalid channel errors
- output device matching issues
- playback routing problems
- underflow/overflow style audio behavior
- CPU latency limitations

### Key lesson from Pi testing
The Pi can be used for edge deployment, but the current Whisper-based pipeline is heavy enough that real-time multilingual language gating becomes hard on limited CPU resources.

### Important current status
The **last Raspberry Pi output-fix suggestions have not been fully applied**.  
So the Pi setup should be treated as **not finalized yet**.

---

## 9) The main technical limitation we found

The main limitation is **not audio routing** anymore.

The main limitation is:

# Whisper base model language-ID is too English-biased on short streaming chunks.

This is especially true for:
- Hindi
- Japanese
- code-switching
- accented English
- short phrases
- noisy mic input

So the system architecture is good, but the model itself is still the bottleneck.

---

## 10) Important concepts already used in the code

### Sliding buffer
Keeps only recent audio for detection.

### Temporal voting
Smooths unstable predictions over several detections.

### Confidence gating
Rejects weak predictions.

### Ambiguity rejection
If English is not clearly dominant, treat as unknown / mute.

### Fade smoothing
Avoids abrupt volume jumps.

### Queue-based producer/consumer design
Separates:
- capture
- detection
- routing
- output

---

## 11) Why delay exists

Delay comes mostly from:
- long buffer size
- Whisper inference time
- history voting
- smoothing

The current balance was chosen because:
- too low latency caused bad language detection
- too short buffers made English bias worse

So the current setup intentionally trades some delay for better stability.

---

## 12) Final current code philosophy

The current system is trying to behave like this:

- **Strong verified English** → allow audio
- **weak English / ambiguous audio** → reject or mute
- **strong non-English** → mute

This is the right philosophy for the project.

The remaining problem is that Whisper sometimes still gives English too much confidence on Hindi/Japanese speech.

---

## 13) What should NOT be re-opened

Do **not** restart the old loop of:
- device index confusion
- plughw vs hw confusion
- sounddevice device-string mistakes
- audio stream not starting at all
- sample-rate mismatch on the Pi
- repeated output worker rewrite attempts

Those were already debugged enough to move forward.

The real remaining work is **model/threshold tuning**, not basic audio plumbing.

---

## 14) Good future directions

### Best next improvement
Use a dedicated language-ID model instead of relying only on Whisper.

Possible options:
- SpeechBrain LID
- VoxLingua107-style classifiers
- ECAPA-TDNN based LID
- dedicated multilingual speech classifiers

### Another strong improvement
Source separation before language detection.

That would help when:
- multiple speakers talk together
- background speech exists
- music or TV audio is present

### Hardware improvement
A GPU would help a lot.

It would allow:
- larger Whisper models
- faster beam search
- lower latency
- better language discrimination

---

## 15) Short summary for another AI

The project is a real-time language-gated audio router.

Current stable parts:
- mic input
- Flask backend
- UI
- queues
- routing
- volume fade
- laptop playback

Current unresolved part:
- Whisper still confuses Hindi/Japanese with English on short buffers

Current Pi state:
- not finalized
- output/routing work was debugged heavily
- last Pi output fix suggestions were not completed

The next AI should focus on:
- language-ID robustness
- threshold tuning
- stronger non-English rejection
- maybe dedicated LID instead of Whisper-only detection
