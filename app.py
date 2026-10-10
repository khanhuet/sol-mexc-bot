
import threading, os, time, subprocess
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "9 Bots: SOL + RH + BNB + Base + ETH + Arc + Polygon + NEAR + Arbitrum - 24/7 OK"

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
    bots = [
        ("bot_auto_24_7.py", "SOL"),
        ("bot_robinhood_chain.py", "ROBINHOOD"),
        ("bot_bnb_chain.py", "BNB"),
        ("bot_base_chain.py", "BASE"),
        ("bot_eth_chain.py", "ETH"),
        ("bot_arc_chain.py", "ARC"),
        ("bot_polygon_chain.py", "POLYGON"),
        ("bot_near_chain.py", "NEAR"),
        ("bot_arbitrum_chain.py", "ARBITRUM"),
    ]
    for f, n in bots:
        threading.Thread(target=run_bot, args=(f, n), daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    print(f"Web server chay tren port {port} - 9 bot")
    app.run(host='0.0.0.0', port=port)
