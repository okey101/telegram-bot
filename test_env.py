import os
from dotenv import load_dotenv

load_dotenv()

print("BOT_TOKEN:", os.getenv("BOT_TOKEN"))
print("DUNE_API_KEY:", os.getenv("DUNE_API_KEY"))
print("TOP_TRADERS_QUERY:", os.getenv("TOP_TRADERS_QUERY"))
print("TRADES_QUERY:", os.getenv("TRADES_QUERY"))
