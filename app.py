#!/usr/bin/env python3
import subprocess, sys, time, os, threading
from flask import Flask
app = Flask(__name__)
@app.route('/')
def home():
    cexs = os.getenv('ENABLED_CEXS','FULL 10 CEX DEFAULT')
    return f"Bot V10.1 FULL 10 CEX - Running! ENABLED_CEXS={cexs}"
@app.route('/health')
def health(): return "OK", 200
def run_bot():
    # bot_v7.py is now V10.1 FULL 10 CEX
    target = "bot_v7.py"
    print(f"[APP V10.1 FULL] Starting {target} with ENABLED_CEXS={os.getenv('ENABLED_CEXS','FULL DEFAULT')}")
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
