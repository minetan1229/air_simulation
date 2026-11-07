"""Proxy CLI to call current Kilo model: python tools/ai_cli.py --task <name> --prompt-file <file>
Falls back to local heuristic (git/pytest) when no proxy configured."""
import sys, subprocess
def main():
    import argparse, pathlib
    p = argparse.ArgumentParser()
    p.add_argument("--task", default="")
    p.add_argument("--prompt-file", default="")
    p.add_argument("--prompt", default="")
    a = p.parse_args()
    proxy = pathlib.Path("tools/ai_proxy.json")
    if not proxy.exists():
        print("[ai_cli] no proxy configured; running local checks (pytest) as fallback")
        subprocess.run([sys.executable,"-m","pytest","-q"], check=False)
        return
    print("[ai_cli] proxy found; invoke configured command")
if __name__ == "__main__":
    main()
