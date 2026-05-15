'''from flask import Flask, jsonify
from flask_cors import CORS
import threading
import ASR_plus_denoising_test as backend

app = Flask(__name__)
CORS(app)   # 🔥 THIS IS MANDATORY

@app.route("/status")
def status():
    #return jsonify(backend.latest_status)
    with backend.state_lock:
        return jsonify(dict(backend.latest_status))

if __name__ == "__main__":
    t = threading.Thread(target=backend.main, daemon=True)
    t.start()

    app.run(host="0.0.0.0", port=5000)'''
    
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import threading
import os
import ASR_plus_denoising_test as backend

# Serve frontend from the same directory as this script
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder=STATIC_DIR)
CORS(app)   # 🔥 THIS IS MANDATORY

# --- API Endpoints ---

@app.route("/status")
def status():
    """Returns current detection status as JSON."""
    with backend.state_lock:
        return jsonify(dict(backend.latest_status))

@app.route("/set_target", methods=["POST"])
def set_target():
    """Change the target language at runtime.
    Accepts JSON: {"language": "en"} or query param: ?language=en
    """
    lang = None

    # Try JSON body first
    if request.is_json:
        lang = request.json.get("language")

    # Fallback to query param
    if not lang:
        lang = request.args.get("language")

    if not lang:
        return jsonify({"error": "Missing 'language' parameter"}), 400

    lang = lang.strip().lower()
    backend.set_target_language(lang)

    return jsonify({
        "status": "ok",
        "target_language": lang
    })

@app.route("/config")
def config():
    """Returns current configuration."""
    with backend.state_lock:
        return jsonify({
            "target_language": backend.TARGET_LANGUAGE,
            "confidence_threshold": backend.CONFIDENCE_THRESHOLD,
            "sample_rate": backend.SAMPLE_RATE,
            "detection_interval": backend.DETECTION_INTERVAL,
        })

# --- Serve Frontend ---

@app.route("/")
def serve_index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.route("/<path:filename>")
def serve_static(filename):
    return send_from_directory(STATIC_DIR, filename)

# --- Main ---

if __name__ == "__main__":
    # Start the audio backend in a background thread
    t = threading.Thread(target=backend.main, daemon=True)
    t.start()

    print("\n" + "=" * 50)
    print("  LangFilterAI Server")
    print("  Dashboard:  http://localhost:5000")
    print("  API:        http://localhost:5000/status")
    print("=" * 50 + "\n")

    app.run(host="0.0.0.0", port=5000)
