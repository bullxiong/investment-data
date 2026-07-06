"""
temporal_engine — 时序信号引擎
在 stock-blogger-tracker 之上增加时序维度的能力层
"""
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "temporal.db"
