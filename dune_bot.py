import logging
import requests
import asyncio
import time
import io
import csv
import re
import os
from dotenv import load_dotenv
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# -------- Load environment variables --------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
DUNE_API_KEY = os.getenv("DUNE_API_KEY")
TOP_TRADERS_QUERY = int(os.getenv("TOP_TRADERS_QUERY"))
TRADES_QUERY = int(os.getenv("TRADES_QUERY"))

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Conversation states
ASK_CA_TOP, ASK_CA_TRADES, ASK_START, ASK_END = range(4)

# Regex for datetime validation
DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# Dune API setup
BASE_URL = "https://api.dune.com/api/v1"
HEADERS = {"x-dune-api-key": DUNE_API_KEY, "Content-Type": "application/json"}


# -------- DUNE HELPERS --------
def execute_query(query_id: int, params: dict):
    url = f"{BASE_URL}/query/{query_id}/execute"
    r = requests.post(url, headers=HEADERS, json={"query_parameters": params})
    r.raise_for_status()
    return r.json()


def get_status(execution_id: str):
    url = f"{BASE_URL}/execution/{execution_id}/status"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def get_results(execution_id: str):
    url = f"{BASE_URL}/execution/{execution_id}/results"
    r = requests.get(url, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def run_query(query_id: int, params: dict, max_wait_minutes=30, poll_interval=5):
    exec_response = execute_query(query_id, params)
    execution_id = exec_response["execution_id"]
    logger.info(f"Execution started: {execution_id}")

    waited = 0
    while waited < max_wait_minutes * 60:
        status = get_status(execution_id)
        state = status["state"]

        if state == "QUERY_STATE_COMPLETED":
            return get_results(execution_id)
        elif state in ["QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"]:
            raise Exception(f"Query failed: {status}")

        time.sleep(poll_interval)
        waited += poll_interval

    raise TimeoutError("Query did not finish within allowed time")


# -------- TOKEN NAME LOOKUP --------
def get_token_name(ca: str) -> str:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{ca}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "pairs" in data and len(data["pairs"]) > 0:
                return data["pairs"][0]["baseToken"]["name"]
        return "Unknown Token"
    except Exception as e:
        logger.error(f"Token lookup failed: {e}")
        return "Unknown Token"


# -------- TELEGRAM HANDLERS --------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Top Traders", callback_data="top_traders")],
        [InlineKeyboardButton("💹 Trades", callback_data="trades")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("👋 Welcome! Please choose an option:", reply_markup=reply_markup)


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "top_traders":
        await query.message.reply_text("✍️ Please enter the Solana CA (Contract Address):")
        return ASK_CA_TOP

    elif query.data == "trades":
        await query.message.reply_text("✍️ Please enter the Solana CA (Contract Address):")
        return ASK_CA_TRADES

    elif query.data == "help":
        await query.message.reply_text(
            "ℹ️ *How to Use:*\n\n"
            "📊 *Top Traders*: Enter a Solana contract address and get the top traders.\n\n"
            "💹 *Trades*: Enter CA, then start and end datetime.\n"
            "Format: `YYYY-MM-DD HH:MM:SS`\n\n"
            "👉 Example:\n"
            "`2025-09-01 00:00:00`\n"
            "`2025-09-05 23:59:59`\n",
            parse_mode="Markdown"
        )
        return ConversationHandler.END


# --- Top Traders Flow ---
async def handle_ca_top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ca = update.message.text.strip()
    if not (32 <= len(ca) <= 44):
        await update.message.reply_text("⚠️ Invalid Solana contract address format.")
        return ConversationHandler.END

    token_name = get_token_name(ca)
    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    status_msg = await update.message.reply_text(
        f"✅ Token confirmed: *{token_name}*\n\n⏳ Running query...",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    try:
        results = await asyncio.to_thread(run_query, TOP_TRADERS_QUERY, {"CA": ca})
        rows = results.get("result", {}).get("rows", [])

        if not rows:
            await status_msg.edit_text("⚠️ No results found.")
            return ConversationHandler.END

        msg = f"📊 *Top Traders for {token_name}*\n\n"
        for row in rows:
            msg += (
                f"👤 `{row['trader_id']}`\n"
                f"💰 Profit: {row['profit_usd']:.2f} USD\n"
                f"📈 ROI: {row['roi']:.2f}\n"
                f"---\n"
            )

        await status_msg.edit_text(msg, parse_mode="Markdown")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")

    return ConversationHandler.END


# --- Trades Flow ---
async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ca = update.message.text.strip()
    if not (32 <= len(ca) <= 44):
        await update.message.reply_text("⚠️ Invalid Solana contract address format.")
        return ConversationHandler.END

    token_name = get_token_name(ca)
    context.user_data["contract_address"] = ca

    await update.message.reply_text(
        f"✅ Token confirmed: *{token_name}*\n\nNow enter *start date* (YYYY-MM-DD HH:MM:SS):",
        parse_mode="Markdown"
    )
    return ASK_START


async def ask_end(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_time = update.message.text.strip()
    if not DATETIME_PATTERN.match(start_time):
        await update.message.reply_text("⚠️ Invalid format! Try again: YYYY-MM-DD HH:MM:SS")
        return ASK_START

    context.user_data["start_time"] = start_time
    await update.message.reply_text("✅ Got start time.\nNow enter *end date* (YYYY-MM-DD HH:MM:SS):")
    return ASK_END


async def fetch_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    end_time = update.message.text.strip()
    if not DATETIME_PATTERN.match(end_time):
        await update.message.reply_text("⚠️ Invalid format! Try again: YYYY-MM-DD HH:MM:SS")
        return ASK_END

    context.user_data["end_time"] = end_time
    ca = context.user_data["contract_address"]
    start_time = context.user_data["start_time"]

    keyboard = [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    status_msg = await update.message.reply_text(
        f"📡 Running query...\n\n"
        f"CA: `{ca}`\n"
        f"From: {start_time}\nTo: {end_time}",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

    try:
        results = await asyncio.to_thread(run_query, TRADES_QUERY, {
            "contract address": ca,
            "start time": start_time,
            "end time": end_time
        })
        rows = results.get("result", {}).get("rows", [])

        if not rows:
            await status_msg.edit_text("⚠️ No results found.")
            return ConversationHandler.END

        message = "📊 Query Results:\n\n"
        for row in rows[:20]:
            message += (
                f"⏰ {row.get('trade_date')}\n"
                f"👤 {row.get('trader_id')}\n"
                f"💠 {row.get('token_bought_symbol')} — ${row.get('amount_usd')}\n\n"
            )

        if len(message) <= 4096:
            await status_msg.edit_text(message)
        else:
            await status_msg.edit_text("📂 Results too large, sending CSV...")

        # Always send CSV
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
        output.seek(0)
        await update.message.reply_document(
            document=io.BytesIO(output.getvalue().encode("utf-8")),
            filename=f"trades_{ca}.csv",
            caption="📊 Full query results"
        )

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}")

    return ConversationHandler.END


# --- Cancel ---
async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.message.edit_text("❌ Query cancelled by user.")
    return ConversationHandler.END


# -------- MAIN --------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler)],
        states={
            ASK_CA_TOP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ca_top)],
            ASK_CA_TRADES: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_start)],
            ASK_START: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_end)],
            ASK_END: [MessageHandler(filters.TEXT & ~filters.COMMAND, fetch_trades)]
        },
        fallbacks=[CallbackQueryHandler(cancel_callback, pattern="^cancel$")],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    logger.info("✅ Bot running... use /start")
    app.run_polling()


if __name__ == "__main__":
    main()
