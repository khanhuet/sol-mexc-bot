#!/usr/bin/env python3
import subprocess, sys, time, os, threading
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot V9.0 9 CEX + Deposit Alert + WS Live - Running!"

@app.route('/health')
def health():
    return "OK", 200

def run_bot():
    target = "bot_v7.py"
    print(f"[APP V9.0] Starting {target} - Full 9 CEX Deposit Monitor")
    while True:
        try:
            result = subprocess.run([sys.executable, target])
            print(f"[APP] {target} exit {result.returncode}, restart in 10s")
        except Exception as e:
            print(f"[APP ERR] {e}")
            import traceback; traceback.print_exc()
        time.sleep(10)

if __name__ == '__main__':
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
