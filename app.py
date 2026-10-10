
import threading
import os
import subprocess
import time
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot SOL MEXC dang chay 24/7 FREE - OK! Bot dang tim token SOL moi list tren MEXC..."

@app.route('/health')
def health():
    return "OK", 200

def run_bot():
    print("Dang khoi dong bot SOL-MEXC...")
    print("Bot se thong bao khi co token mang SOL moi list tren MEXC")
    while True:
        try:
            # Chay file bot chinh
            result = subprocess.run(["python", "bot_auto_24_7.py"], capture_output=False)
            print(f"Bot ket thuc voi code {result.returncode}, se khoi dong lai sau 10s...")
        except Exception as e:
            print(f"Loi chay bot: {e}")
        time.sleep(10)

if __name__ == '__main__':
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    port = int(os.environ.get("PORT", 10000))
    print(f"Web server dang chay tren port {port}")
    app.run(host='0.0.0.0', port=port)
