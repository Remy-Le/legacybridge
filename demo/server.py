"""Billflow demo backend (LegacyBridge hackathon prop).

A tiny Flask app that (a) serves the single-file frontend same-origin (no CORS),
(b) proxies three hackathon-partner APIs (Gemini / Tavily / Fastino-Pioneer), and
(c) launches the on-device LegacyBridge agent against the legacy Tryton ERP.

Design rules:
- Partner calls are ALWAYS non-blocking: any failure (missing key, network, bad
  shape) falls back to a canned response so the on-stage pipeline never breaks.
- Everything else crashes fast (no defensive nets) for easy debugging.

Run:
    GEMINI_API_KEY=... TAVILY_API_KEY=... PIONEER_API_KEY=... python demo/server.py
    FAKE_RUN=1 python demo/server.py      # rehearse the UI without the 19GB model
"""

import json
import os
import re
import subprocess
import sys
import threading
import time

import requests
from flask import Flask, jsonify, request, send_from_directory

# --- Paths -------------------------------------------------------------------
DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(DEMO_DIR)
TRACE_LOG = os.path.join(REPO_ROOT, "runs", "trace.log")
INVOICE_PATH = os.path.join(REPO_ROOT, "invoice_request.json")


# --- Load demo/.env (no extra dependency: parse it ourselves) ----------------
def _load_env():
    env_path = os.path.join(DEMO_DIR, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

app = Flask(__name__)

# Background agent subprocess (or fake-run thread) lives here.
_proc = None          # subprocess.Popen handle for the real agent
_started = False       # has /push ever been called this session?
_fake_done = False     # set True by the fake-run thread when it finishes


# --- Partner integrations ----------------------------------------------------
# Verified against current docs (June 2026):
#   Gemini:  POST .../v1beta/models/<model>:generateContent, header x-goog-api-key,
#            body {"contents":[{"parts":[{"text":...}]}]}, text at
#            candidates[0].content.parts[0].text.  Model: gemini-flash-latest.
#   Tavily:  POST https://api.tavily.com/search, Bearer auth, body {"query":...,
#            "include_answer":true}, summary in response["answer"].
# Verified base (api.pioneer.ai, X-API-Key, OpenAI-compatible at /v1):
#   Pioneer: POST https://api.pioneer.ai/v1/chat/completions with X-API-Key, asking
#   the model to emit the invoice JSON. NOTE: Pioneer returns 403
#   `card_verification_required` until you subscribe to a Hobby/Pro plan and verify a
#   card at https://agent.pioneer.ai/billing — until then /structure uses the local
#   fallback (which builds the exact contract anyway, so the demo never breaks).
#   Model id is configurable via PIONEER_MODEL.

def _gemini(message):
    key = os.environ["GEMINI_API_KEY"]  # KeyError -> caught by caller
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent"
    prompt = (
        "You are Billflow's friendly billing assistant. The user wants to create an "
        "invoice. Reply in ONE short, warm sentence confirming you understood and will "
        f"prepare the invoice. Do not ask questions.\n\nUser: {message}"
    )
    r = requests.post(
        url,
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"maxOutputTokens": 120, "temperature": 0.7}},
        timeout=8,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()


def _tavily(product):
    key = os.environ["TAVILY_API_KEY"]
    r = requests.post(
        "https://api.tavily.com/search",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"query": f"What is {product}? One concise sentence.",
              "include_answer": True, "max_results": 3},
        timeout=8,
    )
    r.raise_for_status()
    answer = r.json().get("answer")
    if not answer:
        raise ValueError("no answer in Tavily response")
    return answer.strip()


def _pioneer(party, product, quantity, unit_price):
    """Pioneer (Fastino) call via its OpenAI-compatible endpoint (api.pioneer.ai/v1,
    X-API-Key). Pioneer extracts the four invoice fields and we build the contract
    from ITS output. On any failure the caller builds locally."""
    key = os.environ["PIONEER_API_KEY"]
    model = os.environ.get("PIONEER_MODEL", "pioneer/auto")
    r = requests.post(
        "https://api.pioneer.ai/v1/chat/completions",
        headers={"X-API-Key": key, "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "user", "content": (
                  "Return ONLY a JSON object with keys party (string), product "
                  "(string), quantity (number), unit_price (number) for this invoice "
                  f"request: Invoice {party} for {quantity}x {product} "
                  f"at {unit_price} each.")}]},
        timeout=25,  # pioneer/auto reasons before answering
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    # Strip optional ```json fences, then parse Pioneer's structured output.
    text = re.sub(r"^```(?:json)?|```$", "", content.strip(), flags=re.MULTILINE).strip()
    data = json.loads(text)
    return _build_invoice(data["party"], data["product"],
                          data["quantity"], data["unit_price"])


def _build_invoice(party, product, quantity, unit_price):
    return {
        "action": "create_and_post_customer_invoice",
        "erp": "tryton",
        "party": party,
        "lines": [{"product": product, "quantity": quantity, "unit_price": unit_price}],
    }


# --- Routes ------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(DEMO_DIR, "app.html")


@app.route("/chat", methods=["POST"])
def chat():
    message = request.json["message"]
    try:
        return jsonify({"reply": _gemini(message)})
    except Exception as e:
        print(f"[chat fallback] {e}", file=sys.stderr)
        return jsonify({"reply": f"Got it — I'll prepare an invoice for {message}."})


@app.route("/research", methods=["POST"])
def research():
    product = request.json["product"]
    try:
        return jsonify({"snippet": _tavily(product)})
    except Exception as e:
        print(f"[research fallback] {e}", file=sys.stderr)
        return jsonify({"snippet": "A4 Paper 500 — standard 500-sheet ream of A4 office paper."})


@app.route("/structure", methods=["POST"])
def structure():
    b = request.json
    party, product = b["party"], b["product"]
    quantity, unit_price = b["quantity"], b["unit_price"]
    try:
        invoice = _pioneer(party, product, quantity, unit_price)
    except Exception as e:
        print(f"[structure fallback] {e}", file=sys.stderr)
        invoice = _build_invoice(party, product, quantity, unit_price)
    return jsonify({"invoice": invoice})


def _fake_run():
    """Rehearsal: emit 17 fake STEP lines into trace.log, one per second."""
    global _fake_done
    os.makedirs(os.path.dirname(TRACE_LOG), exist_ok=True)
    with open(TRACE_LOG, "w") as f:
        f.write("WORKFLOW: FAKE_RUN\nSTEPS: 17\n")
    for n in range(1, 18):
        with open(TRACE_LOG, "a") as f:
            f.write(f"\n===== STEP {n}/17: fake =====\n")
        time.sleep(1)
    _fake_done = True


@app.route("/push", methods=["POST"])
def push():
    global _proc, _started, _fake_done
    invoice = request.json
    with open(INVOICE_PATH, "w") as f:
        json.dump(invoice, f, indent=2)

    _started = True
    if os.environ.get("FAKE_RUN") == "1":
        _fake_done = False
        _proc = None
        threading.Thread(target=_fake_run, daemon=True).start()
    else:
        _proc = subprocess.Popen(
            [sys.executable or "python", "agent.py",
             "--workflow", "workflows/invoice_template.yaml",
             "--input", "invoice_request.json",
             "--config", "config.yaml"],
            cwd=REPO_ROOT,
        )
    return jsonify({"status": "started"})


def _last_step():
    """Return (step, total) from the last '===== STEP N/M =====' line, or (0, 17)."""
    if not os.path.exists(TRACE_LOG):
        return 0, 17
    step, total = 0, 17
    with open(TRACE_LOG) as f:
        for line in f:
            m = re.search(r"=====\s*STEP\s+(\d+)/(\d+)", line)
            if m:
                step, total = int(m.group(1)), int(m.group(2))
    return step, total


@app.route("/status")
def status():
    if not _started:
        return jsonify({"running": False, "step": 0, "total": 17, "done": False,
                        "error": False, "invoice_id": None, "message": "idle"})

    step, total = _last_step()

    # Fake-run mode: no subprocess; the thread sets _fake_done.
    if os.environ.get("FAKE_RUN") == "1":
        done = _fake_done
        running = not done
        return jsonify({"running": running, "step": step, "total": total, "done": done,
                        "error": False, "invoice_id": "INV-0042" if done else None,
                        "message": "Workflow complete." if done else f"Step {step}/{total}"})

    code = _proc.poll() if _proc else None
    if code is None:
        return jsonify({"running": True, "step": step, "total": total, "done": False,
                        "error": False, "invoice_id": None, "message": f"Step {step}/{total}"})
    if code == 0:
        return jsonify({"running": False, "step": step, "total": total, "done": True,
                        "error": False, "invoice_id": "INV-0042", "message": "Workflow complete."})
    return jsonify({"running": False, "step": step, "total": total, "done": False,
                    "error": True, "invoice_id": None, "message": f"Agent exited with code {code}."})


if __name__ == "__main__":
    # Default 5050, not 5000: macOS hands port 5000 to the AirPlay Receiver,
    # which silently returns 403 and hijacks the demo. Override with PORT=.
    port = int(os.environ.get("PORT", "5050"))
    print(f"Billflow demo on http://localhost:{port}  (FAKE_RUN={os.environ.get('FAKE_RUN', '0')})")
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)
