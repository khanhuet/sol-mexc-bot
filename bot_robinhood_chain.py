"""
BOT ROBINHOOD CHAIN vs MEXC - V1 PRO
Tuong tu bot SOL nhung cho mang Robinhood Chain (chainId 4663)
- Lay token mang ROBINHOOD tren MEXC
- So sanh bid/ask MEXC vs DEX Robinhood Chain
- Loc liquidity, check depth, cooldown
"""

import requests, time, os
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8942050944:AAF6tftI7lTYErDCSnLhQSG1BXkUeU7-m3E")
CHAT_ID = os.getenv("CHAT_ID", "469374705")

THRESHOLD = 0.2
MIN_LIQUIDITY_USD = 2000
MIN_ORDERBOOK_DEPTH_USDT = 100
COOLDOWN_MINUTES = 30
CHECK_INTERVAL = 60
SCAN_LIMIT_PER_ROUND = 80

alert_history = {}

def send_telegram(msg):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML", "disable_web_page_preview": True}
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print(f"[TG RH ERR] {e}")

def get_mexc_robinhood_tokens():
    """Lay token co network ROBINHOOD/HOOD tren MEXC"""
    try:
        url = "https://api.mexc.com/api/v3/capital/config/getall"
        r = requests.get(url, timeout=20)
        data = r.json()
        tokens = []
        possible_networks = ["ROBINHOOD", "HOOD", "ROBINHOODCHAIN", "RH", "ROBINHOOD CHAIN"]
        for coin in data:
            coin_name = coin.get("coin")
            for net in coin.get("networkList", []):
                net_name = net.get("network","").upper().replace(" ","").replace("-","").replace("_","")
                # Check neu network trung
                if any(p in net_name for p in possible_networks) or net_name=="ROBINHOOD":
                    contract = net.get("contract","").strip()
                    if net.get("depositEnable"):
                        tokens.append({"symbol": coin_name, "contract": contract, "raw_network": net.get("network")})
        print(f"[MEXC RH] Tim thay {len(tokens)} token khai bao mang ROBINHOOD")

        # Neu MEXC chua list mang ROBINHOOD (vi chain moi), thi lay token tu DEX Robinhood Chain roi check MEXC
        if len(tokens) < 5:
            print("[MEXC RH] MEXC chua co nhieu token RH, se quet tu DEX RH Chain...")
            # Lay token list tu DexScreener chain robinhood
            try:
                # Search top pairs robinhood
                url = "https://api.dexscreener.com/latest/dex/search/?q=chainId:robinhood"
                r = requests.get(url, timeout=15)
                j = r.json()
                pairs = j.get("pairs", [])[:100]
                seen = set()
                for p in pairs:
                    if p.get("chainId")!="robinhood": continue
                    sym = p.get("baseToken",{}).get("symbol","").upper()
                    if not sym or sym in seen: continue
                    if float(p.get("liquidity",{}).get("usd",0) or 0) < MIN_LIQUIDITY_USD: continue
                    seen.add(sym)
                    # Check xem MEXC co pair USDT khong
                    tokens.append({"symbol": sym, "contract": p.get("baseToken",{}).get("address",""), "raw_network": "ROBINHOOD_DEX"})
                print(f"[MEXC RH] Bo sung tu DEX: total {len(tokens)} token")
            except Exception as e:
                print(f"[DEX RH SEARCH ERR] {e}")

        return tokens
    except Exception as e:
        print(f"[MEXC RH ERROR] {e}")
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

def get_robinhood_dex_pool(symbol_or_contract):
    """Lay pool tren Robinhood Chain"""
    # Thu theo contract truoc neu co
    if symbol_or_contract and len(symbol_or_contract)>20 and symbol_or_contract.startswith("0x"):
        try:
            url = f"https://api.dexscreener.com/latest/dex/tokens/{symbol_or_contract}"
            r = requests.get(url, timeout=12)
            data = r.json()
            pairs = [p for p in data.get("pairs",[]) if p.get("chainId")=="robinhood" and float(p.get("liquidity",{}).get("usd",0) or 0)>=MIN_LIQUIDITY_USD]
            if pairs:
                best = max(pairs, key=lambda x: float(x.get("liquidity",{}).get("usd",0)))
                return {"price": float(best.get("priceUsd")), "dex": best.get("dexId"), "liquidity": float(best.get("liquidity",{}).get("usd",0)), "url": best.get("url"), "pair_name": f"{best.get('baseToken',{}).get('symbol')}/{best.get('quoteToken',{}).get('symbol')}", "volume": best.get("volume",{}).get("h24")}
        except Exception as e:
            print(f"[RH DEX contract ERR] {e}")
    # Fallback search by symbol
    try:
        url = f"https://api.dexscreener.com/latest/dex/search/?q={symbol_or_contract} chain:robinhood"
        r = requests.get(url, timeout=12)
        data = r.json()
        pairs = [p for p in data.get("pairs",[]) if p.get("chainId")=="robinhood" and float(p.get("liquidity",{}).get("usd",0) or 0)>=MIN_LIQUIDITY_USD]
        if pairs:
            # Uu tien symbol khop
            exact = [p for p in pairs if p.get("baseToken",{}).get("symbol","").upper()==symbol_or_contract.upper()]
            pool_list = exact if exact else pairs
            best = max(pool_list, key=lambda x: float(x.get("liquidity",{}).get("usd",0)))
            return {"price": float(best.get("priceUsd")), "dex": best.get("dexId"), "liquidity": float(best.get("liquidity",{}).get("usd",0)), "url": best.get("url"), "pair_name": f"{best.get('baseToken',{}).get('symbol')}/{best.get('quoteToken',{}).get('symbol')}", "volume": best.get("volume",{}).get("h24"), "contract": best.get("baseToken",{}).get("address")}
    except Exception as e:
        print(f"[RH DEX search ERR {symbol_or_contract}] {e}")
    return None

def should_alert(symbol):
    now = datetime.now()
    last = alert_history.get(symbol)
    if last and now - last < timedelta(minutes=COOLDOWN_MINUTES):
        return False
    return True

def main():
    print("="*70)
    print("BOT ROBINHOOD CHAIN vs MEXC - BAT DAU")
    print(f"ChainId: 4663 | RPC: https://rpc.mainnet.chain.robinhood.com")
    print(f"Threshold {THRESHOLD}%")
    print("="*70)
    send_telegram(f"🔷 <b>BOT ROBINHOOD CHAIN DA BAT DAU</b>\n\nChainId: 4663\nRPC: mainnet.chain.robinhood.com\nNguong: {THRESHOLD}%\nQuet moi {CHECK_INTERVAL}s")

    tokens = get_mexc_robinhood_tokens()
    last_refresh = datetime.now()
    idx=0

    while True:
        try:
            if datetime.now() - last_refresh > timedelta(hours=1):
                tokens = get_mexc_robinhood_tokens()
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

            print(f"\n[RH VONG] {datetime.now().strftime('%H:%M:%S')} - {len(batch)} token")

            for tok in batch:
                time.sleep(0.4) # rate limit
                symbol = tok["symbol"]
                contract = tok.get("contract","")

                mexc = get_mexc_bid_ask(symbol)
                if not mexc: continue

                dex = get_robinhood_dex_pool(contract if contract and contract.startswith("0x") else symbol)
                if not dex: continue

                dex_price = dex["price"]
                spread_buy_mexc = (dex_price - mexc["ask"]) / mexc["ask"] * 100
                spread_buy_dex = (mexc["bid"] - dex_price) / dex_price * 100

                print(f"{symbol}: MEXC {mexc['bid']}/{mexc['ask']} | RH DEX {dex_price:.6f} ({dex['dex']} liq${dex['liquidity']:.0f}) | S1={spread_buy_mexc:.2f}% S2={spread_buy_dex:.2f}%")

                best=None; direction=""
                if spread_buy_mexc>=THRESHOLD:
                    best=spread_buy_mexc; direction=f"MUA {mexc['pair']} ASK -> BAN ROBINHOOD CHAIN"
                elif spread_buy_dex>=THRESHOLD:
                    best=spread_buy_dex; direction=f"MUA ROBINHOOD CHAIN -> BAN {mexc['pair']} BID"

                if best and should_alert(symbol):
                    alert_history[symbol]=datetime.now()
                    msg = (
                        f"🔷 <b>ARBITRAGE ROBINHOOD CHAIN - {symbol}</b>\n\n"
                        f"Chain: Robinhood Chain (4663) | DEX: {dex['dex']} | Liq: ${dex['liquidity']:,.0f}\n\n"
                        f"📊 <b>MEXC {mexc['pair']}</b>\n"
                        f"  Bid: ${mexc['bid']:.6f} (depth ${mexc['bid_depth']:.0f})\n"
                        f"  Ask: ${mexc['ask']:.6f} (depth ${mexc['ask_depth']:.0f})\n\n"
                        f"📊 <b>Robinhood Chain DEX</b> ({dex['pair_name']})\n"
                        f"  Price: ${dex_price:.6f}\n"
                        f"  Vol24h: ${dex.get('volume',0) or 0}\n\n"
                        f"📈 <b>Chenh lech: {best:.3f}%</b>\n"
                        f"🎯 {direction}\n\n"
                        f"🔗 MEXC: https://www.mexc.com/exchange/{mexc['pair']}\n"
                        f"🔗 DEX: {dex['url']}\n"
                        f"🔗 Explorer: https://robinhoodchain.blockscout.com/address/{dex.get('contract',contract)}\n\n"
                        f"⏰ {datetime.now().strftime('%H:%M:%S %d/%m')} | Cooldown {COOLDOWN_MINUTES}p"
                    )
                    send_telegram(msg)
                    time.sleep(1.5)

            print(f"--- RH Nghi {CHECK_INTERVAL}s ---")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print(f"[RH MAIN ERR] {e}")
            import traceback; traceback.print_exc()
            time.sleep(20)

if __name__=="__main__":
    main()
