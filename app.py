
import threading, os, time, subprocess
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Dual Bot: SOL + Robinhood Chain - Dang chay 24/7 - OK"

@app.route('/health')
def health():
    return "OK", 200

def run_bot(file_name, name):
    print(f"[{name}] Dang khoi dong {file_name}...")
    while True:
        try:
            result = subprocess.run(["python", file_name])
            print(f"[{name}] Ket thuc code {result.returncode}, khoi dong lai sau 10s...")
        except Exception as e:
            print(f"[{name}] Loi: {e}")
        time.sleep(10)

if __name__ == '__main__':
    # Chay 2 bot song song
    t1 = threading.Thread(target=run_bot, args=("bot_auto_24_7.py", "SOL"), daemon=True)
    t2 = threading.Thread(target=run_bot, args=("bot_robinhood_chain.py", "ROBINHOOD"), daemon=True)
    t1.start()
    t2.start()
    port = int(os.environ.get("PORT", 10000))
    print(f"Web server chay tren port {port} - 2 bot SOL + RH")
    app.run(host='0.0.0.0', port=port)
