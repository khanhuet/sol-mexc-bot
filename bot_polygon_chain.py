"""
BOT POLYGON vs MEXC - V1 PRO
Mang Polygon (chainId 137)
"""
import requests, time, os
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8942050944:AAF6tftI7lTYErDCSnLhQSG1BXkUeU7-m3E")
CHAT_ID = os.getenv("CHAT_ID", "469374705")

THRESHOLD = 0.2
MIN_LIQUIDITY_USD = 3000
MIN_ORDERBOOK_DEPTH_USDT = 100
COOLDOWN_MINUTES = 30
CHECK_INTERVAL = 60
SCAN_LIMIT_PER_ROUND = 100

alert_history = {}

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"[TG POLY ERR] {e}")

def get_mexc_polygon_tokens():
    try:
        url = "https://api.mexc.com/api/v3/capital/config/getall"
        r = requests.get(url, timeout=20)
        data = r.json()
        tokens = []
        for coin in data:
            coin_name = coin.get("coin")
            for net in coin.get("networkList", []):
                net_upper = net.get("network","").upper()
                if net_upper in ["MATIC","POLYGON","POL","MATIC(POLYGON)","POLYGON (MATIC)"] and net.get("depositEnable"):
                    contract = net.get("contract","").strip()
                    if contract and contract.startswith("0x") and len(contract)==42:
                        tokens.append({"symbol": coin_name, "contract": contract, "raw_network": net.get("network")})
        print(f"[MEXC POLYGON] {len(tokens)} token POLYGON")
        uniq = {t["symbol"]: t for t in tokens}
        if len(uniq) < 15:
            try:
                url = "https://api.dexscreener.com/latest/dex/search/?q=chainId:polygon"
                r = requests.get(url, timeout=15)
                j = r.json()
                for p in j.get("pairs", [])[:120]:
                    if p.get("chainId")!="polygon": continue
                    if float(p.get("liquidity",{}).get("usd",0) or 0) < MIN_LIQUIDITY_USD: continue
                    sym = p.get("baseToken",{}).get("symbol","").upper()
                    if not sym or sym in uniq: continue
                    uniq[sym] = {"symbol": sym, "contract": p.get("baseToken",{}).get("address",""), "raw_network": "POLYGON_DEX"}
            except Exception as e:
                print(f"[POLY DEX SEARCH ERR] {e}")
        return list(uniq.values())
    except Exception as e:
        print(f"[MEXC POLY ERR] {e}")
        return []

def get_mexc_bid_ask(symbol):
    for quote in ["USDT","USDC"]:
        pair = f"{symbol}{quote}"
        try:
            url = f"https://api.mexc.com/api/v3/depth?symbol={pair}&limit=5"
            r = requests.get(url, timeout=10)
            if r.status_code!=200: continue
            ob = r.json()
            bids = ob.get("bids", []); asks = ob.get("asks", [])
            if not bids or not asks: continue
            best_bid = float(bids[0][0]); best_ask = float(asks[0][0])
            bid_depth = sum(float(p)*float(q) for p,q in bids[:5])
            ask_depth = sum(float(p)*float(q) for p,q in asks[:5])
            if bid_depth < MIN_ORDERBOOK_DEPTH_USDT or ask_depth < MIN_ORDERBOOK_DEPTH_USDT:
                continue
            return {"pair": pair, "bid": best_bid, "ask": best_ask, "bid_depth": bid_depth, "ask_depth": ask_depth}
        except:
            continue
    return None

def get_polygon_dex_pool(symbol_or_contract):
    if symbol_or_contract and symbol_or_contract.startswith("0x") and len(symbol_or_contract)==42:
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{symbol_or_contract}"
            r = requests.get(url, timeout=12)
            data = r.json()
            pairs = [p for p in data.get("pairs",[]) if p.get("chainId")=="polygon" and float(p.get("liquidity",{}).get("usd",0) or 0)>=MIN_LIQUIDITY_USD]
            if pairs:
                best = max(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)))
                return {"price": float(best.get("priceUsd")), "dex": best.get("dexId"), "liquidity": float(best.get("liquidity",{}).get("usd",0)), "url": best.get("url"), "pair_name": f"{best.get('baseToken',{}).get('symbol')}/{best.get('quoteToken',{}).get('symbol')}", "volume": best.get("volume",{}).get("h24"), "contract": best.get("baseToken",{}).get("address")}
        except Exception as e:
            print(f"[POLY DEX contract ERR] {e}")
    try:
        url = f"https://api.dexscreener.com/latest/dex/search/?q={symbol_or_contract} chain:polygon"
        r = requests.get(url, timeout=12)
        data = r.json()
        pairs = [p for p in data.get("pairs",[]) if p.get("chainId")=="polygon" and float(p.get("liquidity",{}).get("usd",0) or 0)>=MIN_LIQUIDITY_USD]
        if pairs:
            exact = [p for p in pairs if p.get("baseToken",{}).get("symbol","").upper()==symbol_or_contract.upper()]
            pool_list = exact if exact else pairs
            best = max(pool_list, key=lambda x: float(x.get("liquidity",{}).get("usd",0)))
            return {"price": float(best.get("priceUsd")), "dex": best.get("dexId"), "liquidity": float(best.get("liquidity",{}).get("usd",0)), "url": best.get("url"), "pair_name": f"{best.get('baseToken',{}).get('symbol')}/{best.get('quoteToken',{}).get('symbol')}", "volume": best.get("volume",{}).get("h24"), "contract": best.get("baseToken",{}).get("address")}
    except Exception as e:
        print(f"[POLY DEX search ERR {symbol_or_contract}] {e}")
    return None

def should_alert(symbol):
    now = datetime.now()
    last = alert_history.get(symbol)
    if last and now - last < timedelta(minutes=COOLDOWN_MINUTES):
        return False
    return True

def main():
    print("="*70)
    print("BOT POLYGON vs MEXC - ChainId 137")
    print("="*70)
    send_telegram(f"💜 <b>BOT POLYGON DA BAT DAU</b>\n\nChainId: 137 (Polygon PoS)\nDEX: QuickSwap / Uniswap\nNguong: {THRESHOLD}%\nQuet moi {CHECK_INTERVAL}s")
    tokens = get_mexc_polygon_tokens()
    last_refresh = datetime.now()
    idx=0
    while True:
        try:
            if datetime.now() - last_refresh > timedelta(hours=1):
                tokens = get_mexc_polygon_tokens()
                last_refresh = datetime.now()
                idx=0
            if not tokens:
                time.sleep(30)
                continue
            batch = tokens[idx:idx+SCAN_LIMIT_PER_ROUND]
            if len(batch)<SCAN_LIMIT_PER_ROUND:
                batch+= tokens[:SCAN_LIMIT_PER_ROUND-len(batch)]
                idx=0
            else:
                idx+=SCAN_LIMIT_PER_ROUND
                if idx>=len(tokens): idx=0
            print(f"\n[POLY VONG] {datetime.now().strftime('%H:%M:%S')} - {len(batch)} token")
            for tok in batch:
                time.sleep(0.3)
                symbol = tok["symbol"]
                contract = tok.get("contract","")
                mexc = get_mexc_bid_ask(symbol)
                if not mexc: continue
                dex = get_polygon_dex_pool(contract if contract else symbol)
                if not dex: continue
                dex_price = dex["price"]
                spread_buy_mexc = (dex_price - mexc["ask"]) / mexc["ask"] * 100
                spread_buy_dex = (mexc["bid"] - dex_price) / dex_price * 100
                print(f"{symbol}: MEXC {mexc['bid']}/{mexc['ask']} | POLY {dex_price:.6f} ({dex['dex']} liq${dex['liquidity']:.0f}) | S1={spread_buy_mexc:.2f}% S2={spread_buy_dex:.2f}%")
                best=None; direction=""
                if spread_buy_mexc>=THRESHOLD:
                    best=spread_buy_mexc; direction=f"MUA {mexc['pair']} ASK -> BAN POLYGON"
                elif spread_buy_dex>=THRESHOLD:
                    best=spread_buy_dex; direction=f"MUA POLYGON -> BAN {mexc['pair']} BID"
                if best and should_alert(symbol):
                    alert_history[symbol]=datetime.now()
                    msg = (
                        f"💜 <b>ARBITRAGE POLYGON - {symbol}</b>\n\n"
                        f"Chain: Polygon (137) | DEX: {dex['dex']} | Liq: ${dex['liquidity']:,.0f}\n\n"
                        f"📊 <b>MEXC {mexc['pair']}</b>\n"
                        f"  Bid: ${mexc['bid']:.6f} (depth ${mexc['bid_depth']:.0f})\n"
                        f"  Ask: ${mexc['ask']:.6f} (depth ${mexc['ask_depth']:.0f})\n\n"
                        f"📊 <b>Polygon DEX</b> ({dex['pair_name']})\n"
                        f"  Price: ${dex_price:.6f}\n"
                        f"  Vol24h: ${dex.get('volume',0) or 0}\n\n"
                        f"📈 <b>Chenh lech: {best:.3f}%</b>\n"
                        f"🎯 {direction}\n\n"
                        f"🔗 MEXC: https://www.mexc.com/exchange/{mexc['pair']}\n"
                        f"🔗 DEX: {dex['url']}\n"
                        f"🔗 PolygonScan: https://polygonscan.com/address/{dex.get('contract',contract)}\n\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m')} | Cooldown {COOLDOWN_MINUTES}p"
                    )
                    send_telegram(msg)
                    time.sleep(1.5)
            print(f"--- POLY Nghi {CHECK_INTERVAL}s ---")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"[POLY MAIN ERR] {e}")
            import traceback; traceback.print_exc()
            time.sleep(20)

if __name__=="__main__":
    main()
