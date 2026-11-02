"""Update selected dotenv entries without displaying or replacing unrelated secrets."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def read_env():
    return dict(line.split('=', 1) for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines() if line and not line.startswith('#') and '=' in line)

def update(values):
    path = ROOT / '.env'
    lines = path.read_text(encoding='utf-8').splitlines()
    seen = set()
    for index, line in enumerate(lines):
        key = line.split('=', 1)[0]
        if key in values:
            lines[index] = f'{key}={values[key]}'
            seen.add(key)
    lines += [f'{key}={value}' for key, value in values.items() if key not in seen]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')

