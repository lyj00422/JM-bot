from pathlib import Path
import sys


project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root / "src"))

from jmbot.bot import main


if __name__ == "__main__":
    main()
