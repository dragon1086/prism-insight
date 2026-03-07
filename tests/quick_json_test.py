#!/usr/bin/env python3
"""Simple JSON parsing test"""

import json
import re

def fix_json_syntax(json_str):
    """Fix JSON syntax errors"""
    # 1. Remove trailing commas
    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

    # 2. Add comma when array is followed by object property
    json_str = re.sub(r'(\])\s*(\n\s*")', r'\1,\2', json_str)

    # 3. Add comma when object is followed by object property
    json_str = re.sub(r'(})\s*(\n\s*")', r'\1,\2', json_str)

    # 4. Add comma when number or string is followed by property
    json_str = re.sub(r'([0-9]|")\s*(\n\s*")', r'\1,\2', json_str)

    # 5. Remove duplicate commas
    json_str = re.sub(r',\s*,', ',', json_str)

    return json_str

# JSON that actually caused an error (missing comma between sell_triggers and hold_conditions)
broken_json = """{
  "sell_triggers": [
    "Take profit condition 1: Sell all when approaching 41,000 KRW",
    "Take profit condition 2: Sell when momentum exhausted after breaking through 37,000 KRW - volume drop and consecutive bearish candles (2 days) within 3 trading days"
  ]
  "hold_conditions": [
    "Price stays above 20/60-day moving average at 33,000 KRW or higher",
    "Institutional/foreign net buying trend continues (cumulative +1M shares/week or more per stock)"
  ]
}"""

print("=" * 50)
print("JSON Parsing Error Fix Test")
print("=" * 50)

# 1. Try parsing original
print("\n1. Direct parsing of error JSON:")
try:
    json.loads(broken_json)
    print("   ❌ Unexpectedly parsed successfully")
except json.JSONDecodeError as e:
    print("   ✅ Failed to parse as expected")
    print(f"      Error: {e}")

# 2. Parse after fixing
print("\n2. After applying fix_json_syntax:")
fixed_json = fix_json_syntax(broken_json)
print("   Fixed JSON:")
print("   " + "-" * 40)
# Print only the fixed part
for line in fixed_json.split('\n')[2:5]:
    print(f"   {line}")
print("   " + "-" * 40)

try:
    parsed = json.loads(fixed_json)
    print("   ✅ Parsing successful!")
    print(f"      - sell_triggers: {len(parsed['sell_triggers'])}")
    print(f"      - hold_conditions: {len(parsed['hold_conditions'])}")
except Exception as e:
    print(f"   ❌ Parsing failed: {e}")

# 3. json-repair test
print("\n3. json-repair library test:")
try:
    import json_repair
    repaired = json_repair.repair_json(broken_json)
    parsed = json.loads(repaired)
    print("   ✅ Recovered successfully with json-repair!")
    print(f"      - sell_triggers: {len(parsed['sell_triggers'])}")
    print(f"      - hold_conditions: {len(parsed['hold_conditions'])}")
except ImportError:
    print("   ⚠️ json-repair not installed (pip install json-repair)")
except Exception as e:
    print(f"   ❌ Recovery failed: {e}")

print("\n" + "=" * 50)
print("Test complete!")
