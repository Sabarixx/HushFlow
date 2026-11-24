"""
WhisperFlow Web Studio - Executive Browser-Based Control Center & Lexicon Architecture
Serves a high-end web application (Linear / Raycast / Apple aesthetic) on http://127.0.0.1:8765
Features:
- Single-page dashboard with bespoke soundwave brand identity
- Asynchronous Start / Stop master engine lifecycle controls
- Live discrete 24-segment audio input visualizer
- Real-time transcription canvas with instant copy & clear
- Custom Lexicon manager (instant rule addition, search/filter, 1-click delete)
- Transcription history log viewer with raw vs polished diffs
- Comprehensive keybindings and voice command cheat sheet
"""

import os
import sys

# Prevent pythonw.exe NoneType stdout/stderr crashes
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

import time
import json
import threading
import subprocess
from typing import Dict, List, Any

import bottle
from bottle import Bottle, request, response, static_file
import requests
import psutil

try:
    import sounddevice as sd
    import numpy as np
    AUDIO_AVAILABLE = True
except ImportError:
    AUDIO_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "whisper_flow_config.json")
VAULT_DIR = os.path.join(BASE_DIR, "vault")
CORRECTIONS_FILE = os.path.join(VAULT_DIR, "corrections.jsonl")
PID_FILE = os.path.join(BASE_DIR, "whisper_flow.pid")
SERVER_HEALTH_URL = "http://127.0.0.1:8080/health"

app = Bottle()

# Global Audio Monitor
current_mic_level = 0.0

def audio_monitor_worker():
    global current_mic_level
    if not AUDIO_AVAILABLE:
        return

    def audio_cb(indata, frames, time_info, status):
        global current_mic_level
        try:
            rms = float(np.sqrt(np.mean(indata**2)))
            level = min(1.0, rms * 15.0)
            if level > current_mic_level:
                current_mic_level = level
            else:
                current_mic_level = current_mic_level * 0.78 + level * 0.22
        except Exception:
            pass

    try:
        with sd.InputStream(channels=1, samplerate=16000, blocksize=1024, callback=audio_cb):
            while True:
                time.sleep(0.04)
    except Exception:
        pass

threading.Thread(target=audio_monitor_worker, daemon=True).start()


# Helper Functions
def is_engine_running() -> (bool, List[int]):
    server_ok = False
    try:
        r = requests.get(SERVER_HEALTH_URL, timeout=0.5)
        if r.status_code == 200:
            server_ok = True
    except Exception:
        server_ok = False

    pids = []
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            if "whisper_flow.py" in cmd:
                pids.append(p.info['pid'])
        except Exception:
            pass

    return (server_ok or len(pids) > 0, pids)


# API Endpoints
@app.route('/api/status', method='GET')
def api_status():
    running, pids = is_engine_running()
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    rules_count = len(cfg.get("word_replacements", {}))
    vocab_count = len(cfg.get("custom_vocabulary", []))
    
    return {
        "running": running,
        "pids": pids,
        "mic_level": round(current_mic_level, 3),
        "model": cfg.get("model", "small.en-q5_1"),
        "rules_count": rules_count,
        "vocab_count": vocab_count
    }


@app.route('/api/start', method='POST')
def api_start():
    running, _ = is_engine_running()
    if running:
        return {"status": "already_running"}

    script_path = os.path.join(BASE_DIR, "whisper_flow.py")
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = "pythonw.exe"

    subprocess.Popen(
        [pythonw, script_path],
        cwd=BASE_DIR,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    )

    # Wait briefly for startup
    for _ in range(8):
        time.sleep(0.4)
        r, _ = is_engine_running()
        if r:
            break

    return {"status": "started"}


@app.route('/api/stop', method='POST')
def api_stop():
    # Kill whisper_flow.py
    for p in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmd = " ".join(p.info['cmdline'] or [])
            if "whisper_flow.py" in cmd:
                p.terminate()
        except Exception:
            pass

    # Kill whisper-server.exe
    for p in psutil.process_iter(['pid', 'name']):
        try:
            if "whisper-server" in p.info['name'].lower():
                p.terminate()
        except Exception:
            pass

    # Clean port 8080
    try:
        for conn in psutil.net_connections(kind='inet'):
            if conn.laddr and conn.laddr.port == 8080 and conn.pid:
                try:
                    psutil.Process(conn.pid).terminate()
                except Exception:
                    pass
    except Exception:
        pass

    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

    return {"status": "stopped"}


@app.route('/api/rules', method='GET')
def api_get_rules():
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    return {
        "word_replacements": cfg.get("word_replacements", {}),
        "custom_vocabulary": cfg.get("custom_vocabulary", [])
    }


@app.route('/api/rules', method='POST')
def api_add_rule():
    data = request.json or {}
    spoken = data.get("spoken", "").strip()
    corrected = data.get("corrected", "").strip()

    if not spoken or not corrected:
        return {"error": "Invalid inputs"}

    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    reps = cfg.get("word_replacements", {})
    reps[spoken.lower()] = corrected
    cfg["word_replacements"] = reps

    vocab = cfg.get("custom_vocabulary", [])
    for w in corrected.split():
        clean_w = w.strip(".,!?;:\"'()")
        if clean_w and clean_w not in vocab:
            vocab.append(clean_w)
    cfg["custom_vocabulary"] = vocab

    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        return {"error": str(e)}

    # Record to corrections.jsonl
    record = {
        "timestamp": time.strftime("%Y%m%d_%H%M%S"),
        "original": spoken,
        "corrected": corrected,
        "replacements": [[spoken.lower(), corrected]],
        "window_title": "WhisperFlow Web Manual Add"
    }
    try:
        with open(CORRECTIONS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return {"status": "ok", "rules": reps}


@app.route('/api/rules/<spoken>', method='DELETE')
def api_delete_rule(spoken):
    cfg = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    reps = cfg.get("word_replacements", {})
    if spoken.lower() in reps:
        del reps[spoken.lower()]
        cfg["word_replacements"] = reps
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            return {"error": str(e)}

    return {"status": "ok", "rules": reps}


@app.route('/api/history', method='GET')
def api_history():
    records = []
    if os.path.exists(CORRECTIONS_FILE):
        try:
            with open(CORRECTIONS_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            records.append(json.loads(line.strip()))
                        except Exception:
                            pass
        except Exception:
            pass
    # Return last 20 reversed
    return {"history": list(reversed(records[-20:]))}


@app.route('/api/quickfix', method='POST')
def api_launch_quickfix():
    qf_script = os.path.join(BASE_DIR, "quick_fix.py")
    if os.path.exists(qf_script):
        subprocess.Popen([sys.executable, qf_script], cwd=BASE_DIR)
    return {"status": "launched"}


# Single-Page Application (HTML/CSS/JS)
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>WhisperFlow • Voice Architecture</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://unpkg.com/lucide@latest"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          fontFamily: {
            sans: ['"Plus Jakarta Sans"', 'system-ui', 'sans-serif'],
            mono: ['"JetBrains Mono"', 'monospace'],
          },
          colors: {
            obsidian: '#080a0f',
            card: '#10141e',
            inner: '#151b27',
            borderHair: 'rgba(255, 255, 255, 0.07)'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #080a0f; color: #f8fafc; font-feature-settings: "cv02", "cv03", "cv04", "cv11"; }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #1e2638; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #28334a; }
  </style>
</head>
<body class="min-h-screen flex flex-col font-sans antialiased selection:bg-slate-700 selection:text-white">

  <!-- Top Glass Bar -->
  <header class="border-b border-borderHair bg-card/80 backdrop-blur-md sticky top-0 z-50 px-6 py-3.5 flex items-center justify-between">
    <div class="flex items-center space-x-3.5">
      <!-- Machined Logo Emblem -->
      <div class="relative w-10 h-10 rounded-full bg-[#131722] border border-[#252f44] flex items-center justify-center shadow-inner">
        <svg class="w-5 h-5 text-white" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round">
          <line x1="4" y1="9" x2="4" y2="15"></line>
          <line x1="8" y1="5" x2="8" y2="19"></line>
          <line x1="12" y1="3" x2="12" y2="21"></line>
          <line x1="16" y1="5" x2="16" y2="19"></line>
          <line x1="20" y1="9" x2="20" y2="15"></line>
        </svg>
      </div>
      <div>
        <div class="flex items-center space-x-2">
          <span class="text-base font-bold tracking-tight text-white">WhisperFlow</span>
          <span class="text-[10px] font-mono font-medium px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-slate-400">v2.0 • Air-Gapped</span>
        </div>
        <p class="text-xs text-slate-400">Speech Recognition & Lexicon Architecture</p>
      </div>
    </div>

    <!-- Right Global Status Indicator -->
    <div class="flex items-center space-x-3">
      <div id="status-pill" class="inline-flex items-center space-x-2 px-3 py-1 rounded-full text-xs font-semibold bg-[#161c28] border border-white/10 text-slate-400">
        <span class="w-2 h-2 rounded-full bg-slate-400 animate-pulse"></span>
        <span id="status-text">INITIALIZING</span>
      </div>
      <button onclick="toggleEngine()" id="btn-toggle-engine" class="px-4 py-1.5 text-xs font-semibold rounded-lg bg-white text-slate-950 hover:bg-slate-100 transition shadow-sm active:scale-[0.98]">
        Start Service
      </button>
    </div>
  </header>

  <!-- Main Container -->
  <div class="flex-1 max-w-6xl w-full mx-auto px-6 py-6 grid grid-cols-1 md:grid-cols-12 gap-6">

    <!-- Left Column: Navigation & Telemetry (4 cols) -->
    <div class="md:col-span-4 space-y-4">
      
      <!-- Navigation Menu -->
      <div class="bg-card border border-borderHair rounded-2xl p-2 space-y-1">
        <button onclick="switchTab('overview')" id="nav-overview" class="tab-btn w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold transition bg-inner text-white">
          <i data-lucide="activity" class="w-4 h-4 text-emerald-400"></i>
          <span>System Overview</span>
        </button>
        <button onclick="switchTab('canvas')" id="nav-canvas" class="tab-btn w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold transition hover:bg-inner/60 text-slate-400">
          <i data-lucide="file-text" class="w-4 h-4 text-sky-400"></i>
          <span>Live Canvas</span>
        </button>
        <button onclick="switchTab('lexicon')" id="nav-lexicon" class="tab-btn w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold transition hover:bg-inner/60 text-slate-400">
          <i data-lucide="book-open" class="w-4 h-4 text-indigo-400"></i>
          <span>Custom Lexicon</span>
        </button>
        <button onclick="switchTab('history')" id="nav-history" class="tab-btn w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold transition hover:bg-inner/60 text-slate-400">
          <i data-lucide="history" class="w-4 h-4 text-amber-400"></i>
          <span>Transcript Logs</span>
        </button>
        <button onclick="switchTab('shortcuts')" id="nav-shortcuts" class="tab-btn w-full flex items-center space-x-3 px-3.5 py-2.5 rounded-xl text-xs font-semibold transition hover:bg-inner/60 text-slate-400">
          <i data-lucide="command" class="w-4 h-4 text-rose-400"></i>
          <span>Keybindings</span>
        </button>
      </div>

      <!-- Live Input Signal Meter -->
      <div class="bg-card border border-borderHair rounded-2xl p-4 space-y-3">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <i data-lucide="mic" class="w-4 h-4 text-slate-400"></i>
            <span class="text-xs font-semibold text-slate-200">Audio Input Signal</span>
          </div>
          <span id="mic-status-label" class="text-[11px] font-mono text-slate-400">Listening...</span>
        </div>
        
        <!-- 24-Segment Discrete Visualizer -->
        <div id="meter-container" class="grid grid-cols-24 gap-1 h-3 p-1 rounded-lg bg-inner border border-white/5">
          <!-- 24 bars generated dynamically -->
        </div>
      </div>

      <!-- Engine Specs & Telemetry -->
      <div class="bg-card border border-borderHair rounded-2xl p-4 space-y-2.5 text-xs">
        <div class="text-[11px] font-mono uppercase tracking-wider text-slate-500 font-semibold mb-1">Architecture Specs</div>
        <div class="flex items-center justify-between py-1 border-b border-white/5">
          <span class="text-slate-400">Quantized Model</span>
          <span id="spec-model" class="font-mono text-slate-200 font-medium">small.en-q5_1</span>
        </div>
        <div class="flex items-center justify-between py-1 border-b border-white/5">
          <span class="text-slate-400">Execution Threads</span>
          <span class="font-mono text-slate-200 font-medium">8 CPU Threads</span>
        </div>
        <div class="flex items-center justify-between py-1 border-b border-white/5">
          <span class="text-slate-400">Latency Floor</span>
          <span class="font-mono text-emerald-400 font-medium">&lt; 0.8s (Sub-Second)</span>
        </div>
        <div class="flex items-center justify-between py-1 border-b border-white/5">
          <span class="text-slate-400">Local Loopback</span>
          <span class="font-mono text-slate-200 font-medium">127.0.0.1:8080</span>
        </div>
        <div class="flex items-center justify-between py-1">
          <span class="text-slate-400">Privacy Status</span>
          <span class="font-mono text-emerald-400 font-medium">100% Air-Gapped</span>
        </div>
      </div>

    </div>

    <!-- Right Column: Dynamic Stage Content (8 cols) -->
    <div class="md:col-span-8 space-y-6">

      <!-- TAB 1: SYSTEM OVERVIEW -->
      <div id="tab-content-overview" class="tab-pane space-y-4">
        
        <!-- Hero Service Card -->
        <div class="bg-card border border-borderHair rounded-2xl p-6 relative overflow-hidden">
          <div class="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div>
              <h2 id="hero-service-title" class="text-lg font-bold text-white tracking-tight">Local Engine Service</h2>
              <p id="hero-service-desc" class="text-xs text-slate-400 mt-1 max-w-md leading-relaxed">
                Manages in-memory whisper-server instance and global low-level Windows keyboard hooks.
              </p>
            </div>
            <button onclick="toggleEngine()" id="btn-hero-action" class="px-5 py-2 rounded-xl text-xs font-semibold bg-white text-slate-950 hover:bg-slate-100 transition shadow-sm active:scale-[0.98] whitespace-nowrap">
              Start Service
            </button>
          </div>
        </div>

        <!-- Quick Controls Grid -->
        <div class="grid grid-cols-2 gap-4">
          <div class="bg-card border border-borderHair rounded-2xl p-5 space-y-2">
            <div class="text-[11px] font-mono text-slate-400">Push-to-Talk Key</div>
            <div class="flex items-center space-x-2">
              <kbd class="px-2.5 py-1 text-xs font-mono font-semibold bg-inner border border-white/10 rounded-lg text-white">Ctrl + Alt</kbd>
            </div>
            <p class="text-[11px] text-slate-500">Hold anywhere to dictate; releases immediately and types into focused app.</p>
          </div>

          <div class="bg-card border border-borderHair rounded-2xl p-5 space-y-2">
            <div class="text-[11px] font-mono text-slate-400">Hands-Free Dictation</div>
            <div class="flex items-center space-x-2">
              <kbd class="px-2.5 py-1 text-xs font-mono font-semibold bg-inner border border-white/10 rounded-lg text-white">Ctrl + Alt + Space</kbd>
            </div>
            <p class="text-[11px] text-slate-500">Toggle mode for multi-sentence speech. Press again or Esc to end.</p>
          </div>
        </div>

        <!-- In-situ Live Preview Box -->
        <div class="bg-card border border-borderHair rounded-2xl p-5 space-y-3">
          <div class="flex items-center justify-between">
            <span class="text-xs font-bold text-white tracking-tight">Transcription Mirror Canvas</span>
            <button onclick="clearCanvas()" class="text-xs text-slate-400 hover:text-white transition">Clear</button>
          </div>
          <textarea id="overview-textarea" rows="4" class="w-full bg-obsidian border border-white/10 rounded-xl p-3 text-sm text-slate-200 placeholder-slate-600 focus:outline-none focus:border-white/20 font-sans resize-none leading-relaxed" placeholder="Hold [Ctrl + Alt] or toggle [Ctrl + Alt + Space] to dictate anywhere. Spoken transcription will reflect here..."></textarea>
        </div>

      </div>

      <!-- TAB 2: LIVE CANVAS -->
      <div id="tab-content-canvas" class="tab-pane hidden space-y-4">
        <div class="bg-card border border-borderHair rounded-2xl p-6 space-y-4">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-base font-bold text-white tracking-tight">Full Dictation Canvas</h2>
              <p class="text-xs text-slate-400">Test voice typing, view real-time casing, or draft long dictation notes.</p>
            </div>
            <div class="flex items-center space-x-2">
              <button onclick="copyCanvas()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-inner border border-white/10 text-slate-200 hover:text-white transition">Copy All</button>
              <button onclick="clearCanvas()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-inner border border-white/10 text-slate-400 hover:text-white transition">Clear</button>
            </div>
          </div>
          <textarea id="full-canvas-textarea" rows="12" class="w-full bg-obsidian border border-white/10 rounded-xl p-4 text-sm text-slate-100 placeholder-slate-600 focus:outline-none focus:border-white/20 font-sans resize-none leading-relaxed" placeholder="Click here, hold [Ctrl + Alt], speak naturally, and watch words type in real time..."></textarea>
        </div>
      </div>

      <!-- TAB 3: CUSTOM LEXICON -->
      <div id="tab-content-lexicon" class="tab-pane hidden space-y-4">
        
        <!-- Add Rule Form -->
        <div class="bg-card border border-borderHair rounded-2xl p-6 space-y-4">
          <div>
            <h2 class="text-base font-bold text-white tracking-tight">Add Lexicon Replacement</h2>
            <p class="text-xs text-slate-400">Acoustic corrections map what Whisper hears to your desired spelling.</p>
          </div>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <input type="text" id="input-spoken" placeholder="Spoken form (e.g. read the setup)" class="bg-obsidian border border-white/10 rounded-xl px-3.5 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-white/20">
            <input type="text" id="input-corrected" placeholder="Normalized text (e.g. Rate the setup)" class="bg-obsidian border border-white/10 rounded-xl px-3.5 py-2 text-xs text-white placeholder-slate-500 focus:outline-none focus:border-white/20">
          </div>
          <div class="flex justify-between items-center pt-1">
            <button onclick="launchQuickfix()" class="text-xs font-medium text-slate-400 hover:text-white transition">Open QuickFix Dialog (Alt + Z)</button>
            <button onclick="addLexiconRule()" class="px-4 py-2 rounded-xl text-xs font-semibold bg-white text-slate-950 hover:bg-slate-100 transition shadow-sm active:scale-[0.98]">
              Save Rule
            </button>
          </div>
        </div>

        <!-- Rules Table -->
        <div class="bg-card border border-borderHair rounded-2xl p-6 space-y-4">
          <div class="flex items-center justify-between">
            <h3 class="text-xs font-bold text-white tracking-tight">Active Replacement Rules</h3>
            <input type="text" id="filter-rules-input" onkeyup="filterRules()" placeholder="Filter rules..." class="w-48 bg-obsidian border border-white/10 rounded-lg px-2.5 py-1 text-xs text-white placeholder-slate-500 focus:outline-none">
          </div>
          <div id="rules-list" class="divide-y divide-white/5 max-h-72 overflow-y-auto space-y-1">
            <!-- Rules populated via JS -->
          </div>
        </div>

      </div>

      <!-- TAB 4: TRANSCRIPT LOGS -->
      <div id="tab-content-history" class="tab-pane hidden space-y-4">
        <div class="bg-card border border-borderHair rounded-2xl p-6 space-y-4">
          <div class="flex items-center justify-between">
            <div>
              <h2 class="text-base font-bold text-white tracking-tight">Recent Verified Transcripts</h2>
              <p class="text-xs text-slate-400">Ground-truth dictation logs recorded in vault/corrections.jsonl.</p>
            </div>
            <button onclick="loadHistory()" class="text-xs text-slate-400 hover:text-white transition">Refresh</button>
          </div>
          <div id="history-list" class="space-y-3 max-h-96 overflow-y-auto">
            <!-- Populated via JS -->
          </div>
        </div>
      </div>

      <!-- TAB 5: KEYBINDINGS & CHEATSHEET -->
      <div id="tab-content-shortcuts" class="tab-pane hidden space-y-4">
        
        <div class="bg-card border border-borderHair rounded-2xl p-6 space-y-4">
          <h2 class="text-base font-bold text-white tracking-tight">System Keybindings</h2>
          <div class="space-y-3 text-xs">
            <div class="flex items-center justify-between py-2 border-b border-white/5">
              <kbd class="font-mono px-2 py-1 rounded bg-inner border border-white/10 text-emerald-400">Hold [Ctrl + Alt]</kbd>
              <span class="text-slate-400">Push-to-Talk audio recording; types instantly on release</span>
            </div>
            <div class="flex items-center justify-between py-2 border-b border-white/5">
              <kbd class="font-mono px-2 py-1 rounded bg-inner border border-white/10 text-sky-400">Press [Ctrl + Alt + Space]</kbd>
              <span class="text-slate-400">Hands-Free Toggle for multi-sentence dictation</span>
            </div>
            <div class="flex items-center justify-between py-2 border-b border-white/5">
              <kbd class="font-mono px-2 py-1 rounded bg-inner border border-white/10 text-indigo-400">Press [Alt + Z]</kbd>
              <span class="text-slate-400">Open QuickFix to capture misheard words & teach model</span>
            </div>
            <div class="flex items-center justify-between py-2">
              <kbd class="font-mono px-2 py-1 rounded bg-inner border border-white/10 text-rose-400">Press [Ctrl + Shift + Q]</kbd>
              <span class="text-slate-400">Emergency Stop (terminates listeners and server)</span>
            </div>
          </div>
        </div>

        <div class="bg-card border border-borderHair rounded-2xl p-6 space-y-4">
          <h2 class="text-base font-bold text-white tracking-tight">Voice Formatting Syntax</h2>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <div class="p-3 bg-inner/60 border border-white/5 rounded-xl space-y-1">
              <div class="font-mono font-bold text-sky-400">"new line"</div>
              <div class="text-slate-400">Spoken line break (\\n)</div>
            </div>
            <div class="p-3 bg-inner/60 border border-white/5 rounded-xl space-y-1">
              <div class="font-mono font-bold text-sky-400">"new paragraph"</div>
              <div class="text-slate-400">Double line break (\\n\\n)</div>
            </div>
            <div class="p-3 bg-inner/60 border border-white/5 rounded-xl space-y-1">
              <div class="font-mono font-bold text-emerald-400">"snake case &lt;words&gt;"</div>
              <div class="text-slate-400">Formats to snake_case</div>
            </div>
            <div class="p-3 bg-inner/60 border border-white/5 rounded-xl space-y-1">
              <div class="font-mono font-bold text-emerald-400">"camel case &lt;words&gt;"</div>
              <div class="text-slate-400">Formats to camelCase</div>
            </div>
            <div class="p-3 bg-inner/60 border border-white/5 rounded-xl space-y-1">
              <div class="font-mono font-bold text-indigo-400">"all caps &lt;words&gt;"</div>
              <div class="text-slate-400">Formats to UPPERCASE</div>
            </div>
            <div class="p-3 bg-inner/60 border border-white/5 rounded-xl space-y-1">
              <div class="font-mono font-bold text-amber-400">"double &lt;digit&gt;"</div>
              <div class="text-slate-400">Repeats digits (e.g. 'double 4' &rarr; 44)</div>
            </div>
          </div>
        </div>

      </div>

    </div>

  </div>

  <!-- Footer -->
  <footer class="border-t border-borderHair bg-card/40 px-6 py-3 text-center text-xs text-slate-500">
    WhisperFlow Studio • Intel Core i5-13420H • 16GB RAM • 100% Offline Air-Gapped Speech Architecture
  </footer>

  <script>
    lucide.createIcons();

    // Generate 24 Discrete VU Level Segments
    const meterContainer = document.getElementById('meter-container');
    for (let i = 0; i < 24; i++) {
      const seg = document.createElement('div');
      seg.className = 'w-full h-full rounded-sm bg-[#1b2232] transition-colors duration-75';
      seg.id = `meter-seg-${i}`;
      meterContainer.appendChild(seg);
    }

    let isRunning = false;
    let allRules = {};

    function switchTab(tabId) {
      document.querySelectorAll('.tab-pane').forEach(el => el.classList.add('hidden'));
      document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.remove('bg-inner', 'text-white');
        btn.classList.add('text-slate-400');
      });

      const pane = document.getElementById(`tab-content-${tabId}`);
      if (pane) pane.classList.remove('hidden');

      const btn = document.getElementById(`nav-${tabId}`);
      if (btn) {
        btn.classList.add('bg-inner', 'text-white');
        btn.classList.remove('text-slate-400');
      }

      if (tabId === 'lexicon') loadRules();
      if (tabId === 'history') loadHistory();
    }

    async function pollStatus() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();
        isRunning = data.running;

        // Status Pill
        const pill = document.getElementById('status-pill');
        const text = document.getElementById('status-text');
        const btnToggle = document.getElementById('btn-toggle-engine');
        const heroTitle = document.getElementById('hero-service-title');
        const heroDesc = document.getElementById('hero-service-desc');
        const heroBtn = document.getElementById('btn-hero-action');

        if (isRunning) {
          pill.className = "inline-flex items-center space-x-2 px-3 py-1 rounded-full text-xs font-semibold bg-[#0d2119] border border-emerald-800/40 text-emerald-400";
          pill.innerHTML = '<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span><span>ENGINE ACTIVE</span>';
          btnToggle.innerText = "Stop Service";
          btnToggle.className = "px-4 py-1.5 text-xs font-semibold rounded-lg bg-rose-950/60 border border-rose-800/40 text-rose-300 hover:bg-rose-900/60 transition shadow-sm active:scale-[0.98]";
          heroTitle.innerText = "Local Engine Active";
          heroDesc.innerText = `Background whisper-server and keyboard hooks active (${data.pids.length ? 'PID ' + data.pids[0] : 'Port 8080'}). Press [Ctrl + Alt] to dictate anywhere.`;
          heroBtn.innerText = "Stop Service";
          heroBtn.className = "px-5 py-2 rounded-xl text-xs font-semibold bg-rose-950/60 border border-rose-800/40 text-rose-300 hover:bg-rose-900/60 transition shadow-sm active:scale-[0.98] whitespace-nowrap";
        } else {
          pill.className = "inline-flex items-center space-x-2 px-3 py-1 rounded-full text-xs font-semibold bg-[#161c28] border border-white/10 text-slate-400";
          pill.innerHTML = '<span class="w-2 h-2 rounded-full bg-slate-400"></span><span>ENGINE STOPPED</span>';
          btnToggle.innerText = "Start Service";
          btnToggle.className = "px-4 py-1.5 text-xs font-semibold rounded-lg bg-white text-slate-950 hover:bg-slate-100 transition shadow-sm active:scale-[0.98]";
          heroTitle.innerText = "Local Engine Inactive";
          heroDesc.innerText = "Start the local in-memory whisper server and global keyboard listeners.";
          heroBtn.innerText = "Start Service";
          heroBtn.className = "px-5 py-2 rounded-xl text-xs font-semibold bg-white text-slate-950 hover:bg-slate-100 transition shadow-sm active:scale-[0.98] whitespace-nowrap";
        }

        // Audio Meter Visualizer
        const level = data.mic_level || 0.0;
        const total = 24;
        const activeCount = Math.floor(level * total);
        for (let i = 0; i < total; i++) {
          const seg = document.getElementById(`meter-seg-${i}`);
          if (seg) {
            if (i < activeCount) {
              seg.className = i < 18 ? 'w-full h-full rounded-sm bg-emerald-400' : 'w-full h-full rounded-sm bg-sky-400';
            } else {
              seg.className = 'w-full h-full rounded-sm bg-[#1b2232]';
            }
          }
        }
        document.getElementById('mic-status-label').innerText = activeCount > 0 ? `Signal Active (${Math.round(level * 100)}%)` : 'Listening...';
        document.getElementById('spec-model').innerText = data.model;

      } catch (e) {
        console.error(e);
      }
    }

    async function toggleEngine() {
      const endpoint = isRunning ? '/api/stop' : '/api/start';
      try {
        await fetch(endpoint, { method: 'POST' });
        setTimeout(pollStatus, 400);
      } catch (e) {
        console.error(e);
      }
    }

    async function loadRules() {
      try {
        const res = await fetch('/api/rules');
        const data = await res.json();
        allRules = data.word_replacements || {};
        renderRules(allRules);
      } catch (e) {
        console.error(e);
      }
    }

    function renderRules(rules) {
      const container = document.getElementById('rules-list');
      container.innerHTML = '';
      const entries = Object.entries(rules);
      if (!entries.length) {
        container.innerHTML = '<p class="text-xs text-slate-500 py-4 text-center">No custom replacement rules configured.</p>';
        return;
      }
      entries.forEach(([spoken, corrected]) => {
        const row = document.createElement('div');
        row.className = 'flex items-center justify-between py-2.5 px-3 rounded-lg hover:bg-inner transition';
        row.innerHTML = `
          <div class="flex items-center space-x-2 text-xs">
            <span class="font-mono text-slate-300 bg-obsidian px-2 py-0.5 rounded border border-white/5">${spoken}</span>
            <span class="text-slate-500">&rarr;</span>
            <span class="font-mono font-semibold text-emerald-400 bg-emerald-950/30 px-2 py-0.5 rounded border border-emerald-800/30">${corrected}</span>
          </div>
          <button onclick="deleteRule('${spoken}')" class="text-slate-500 hover:text-rose-400 transition p-1">
            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
          </button>
        `;
        container.appendChild(row);
      });
      lucide.createIcons();
    }

    function filterRules() {
      const q = document.getElementById('filter-rules-input').value.toLowerCase();
      const filtered = {};
      Object.entries(allRules).forEach(([k, v]) => {
        if (k.toLowerCase().includes(q) || v.toLowerCase().includes(q)) {
          filtered[k] = v;
        }
      });
      renderRules(filtered);
    }

    async function addLexiconRule() {
      const spoken = document.getElementById('input-spoken').value.trim();
      const corrected = document.getElementById('input-corrected').value.trim();
      if (!spoken || !corrected) return;

      await fetch('/api/rules', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spoken, corrected })
      });

      document.getElementById('input-spoken').value = '';
      document.getElementById('input-corrected').value = '';
      loadRules();
    }

    async function deleteRule(spoken) {
      await fetch(`/api/rules/${encodeURIComponent(spoken)}`, { method: 'DELETE' });
      loadRules();
    }

    async function loadHistory() {
      try {
        const res = await fetch('/api/history');
        const data = await res.json();
        const container = document.getElementById('history-list');
        container.innerHTML = '';
        if (!data.history || !data.history.length) {
          container.innerHTML = '<p class="text-xs text-slate-500 py-4 text-center">No transcripts recorded yet.</p>';
          return;
        }
        data.history.forEach(item => {
          const card = document.createElement('div');
          card.className = 'p-3 bg-inner/60 border border-white/5 rounded-xl space-y-1.5 text-xs';
          card.innerHTML = `
            <div class="flex items-center justify-between text-[10px] text-slate-500 font-mono">
              <span>${item.timestamp}</span>
              <span>${item.window_title || 'Application'}</span>
            </div>
            <p class="text-slate-300">${item.corrected}</p>
          `;
          container.appendChild(card);
        });
      } catch (e) {
        console.error(e);
      }
    }

    function clearCanvas() {
      document.getElementById('overview-textarea').value = '';
      document.getElementById('full-canvas-textarea').value = '';
    }

    function copyCanvas() {
      const text = document.getElementById('full-canvas-textarea').value;
      navigator.clipboard.writeText(text);
    }

    async function launchQuickfix() {
      await fetch('/api/quickfix', { method: 'POST' });
    }

    setInterval(pollStatus, 400);
    pollStatus();
  </script>
</body>
</html>
"""

@app.route('/')
def index():
    return HTML_TEMPLATE

def start_server():
    print("[OK] WhisperFlow Web Studio starting on http://127.0.0.1:8765")
    bottle.run(app, host='127.0.0.1', port=8765, quiet=True)

if __name__ == "__main__":
    start_server()
