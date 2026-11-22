import re
import sys
from pathlib import Path

files = [
    'aerodynamics_glossary.md',
    'flight_dynamics_glossary.md',
    'physics_glossary.md',
    'optimization_geometry_guide.md',
    'docs/HIGH_SCHOOL_GLOSSARY_MASTER.md'
]

has_error = False
for f in files:
    content = Path(f).read_text(encoding='utf-8')
    dd_count = content.count('$$')
    if dd_count % 2 != 0:
        print(f"ERROR: {f} has unbalanced $$ count: {dd_count}")
        has_error = True
    
    content_no_dd = re.sub(r'\$\$.*?\$\$', '', content, flags=re.DOTALL)
    content_no_code = re.sub(r'```.*?```', '', content_no_dd, flags=re.DOTALL)
    dollar_count = content_no_code.count('$')
    if dollar_count % 2 != 0:
        print(f"ERROR: {f} has unbalanced inline $ count: {dollar_count}")
        has_error = True
    else:
        print(f"OK: {f} (inline $: {dollar_count}, block $$: {dd_count})")

if has_error:
    sys.exit(1)
print("All LaTeX math blocks perfectly balanced!")
