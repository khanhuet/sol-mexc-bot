# sol-mexc-bot · BOT V11.1 CEX ↔ DEX

Bot Telegram **chỉ cảnh báo** (không tự giao dịch) chênh lệch giá giữa sàn CEX và các DEX.

- **CEX:** MEXC, GATE, KUCOIN, BITGET, BYBIT, BINANCE, OKX, BINGX, HTX, LBANK
- **Chain:** BNB, SOL, BASE, ETH, POLYGON, ARBITRUM, AVAX, HYPEREVM, ROBINHOOD, ARC, NEAR
- Gộp giá **tất cả DEX** trên mỗi chain (DexScreener), quote thật qua aggregator (KyberSwap + ParaSwap / Jupiter / Ref)
- Kiểm tra **đúng contract**, **trạng thái nạp/rút**, **tax mua/bán/chuyển**, honeypot
- Size động $200–2000, chọn size **% lãi cao nhất**; chỉ báo khi **lãi ròng ≥ 2%** sau mọi chi phí
- Thông báo **đóng/mở nạp rút**; **Daily Report 21:00 (UTC+7)**

## Deploy trên Render
1. **Region:** Singapore hoặc Frankfurt (server ở Mỹ bị Binance/Bybit chặn).
2. **Build command:** `pip install -r requirements.txt`
3. **Start command:** `python app.py`
4. **Environment:** xem `env.example`. Bắt buộc: `TELEGRAM_TOKEN`, `CHAT_ID`.
   API key **chỉ đọc** cho MEXC, BINANCE, BYBIT, OKX, BINGX (thiếu key → sàn đó không có contract/nạp-rút → không ra kèo).
   KuCoin, Bitget, HTX, LBank dùng API công khai, không cần key.
5. Gói Free: bot tự ping chính nó mỗi 10 phút (`RENDER_EXTERNAL_URL` do Render tự cấp) để không bị ngủ.

⚠️ **Không bao giờ commit file `.env` hay API key lên repo này** (repo đang public).

## Lệnh Telegram
`/status` · `/check SYMBOL` (vì sao có/không có kèo) · `/deposit SYMBOL` · `/top` · `/report` · `/networks` · `/config` · `/pause CHAIN` · `/resume CHAIN` · `/blacklist SYMBOL` · `/unblacklist SYMBOL`
