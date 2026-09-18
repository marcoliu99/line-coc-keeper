from __future__ import annotations

import re

_DANGEROUS_PATTERNS = [
    re.compile(r"\[SYSTEM\]", re.IGNORECASE),
    re.compile(r"as an AI", re.IGNORECASE),
    re.compile(r"我是一個語言模型", re.IGNORECASE),
    re.compile(r"tool_call", re.IGNORECASE)
]

def validate_narrative(text: str) -> tuple[bool, str]:
    """
    Checks if the generated narrative breaks immersion or leaks system details.
    Returns (is_valid, reason)
    """
    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(text):
            return False, f"偵測到破壞沉浸感或系統外洩的字眼 ({pattern.pattern})"
            
    if text.count("```") % 2 != 0:
        return False, "Markdown 代碼區塊未閉合"
        
    return True, ""
