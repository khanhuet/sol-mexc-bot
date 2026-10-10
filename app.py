
import threading
import os
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot SOL MEXC dang chay 24/7 FREE - OK!"

@app.route('/health')
def health():
    return "OK", 200

def run_bot():
    print("Dang khoi dong bot...")
    # import bot chinh cua ban
    try:
        import bot_auto_24_7
    except Exception as e:
        print(f"Loi chay bot: {e}")
        import time
        time.sleep(5)

if __name__ == '__main__':
    # Chay bot o background thread
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Chay web server de Render Free giu alive
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
