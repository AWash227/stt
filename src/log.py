import json
from pathlib import Path
import tempfile

LOG_PATH = Path(tempfile.gettempdir()) / "llm_interactions_log.jsonl"


def log_interaction(input_text, output_text):
    entry = {"input": input_text, "output": output_text}
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[logging] Logging error: {e}")
