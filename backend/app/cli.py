"""Command line: npm run py -- -m app.cli seed | clear-samples | google-auth"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from .google_service import SCOPES
from .seed import clear_samples, seed

BACKEND_DIR = Path(__file__).resolve().parent.parent


def main() -> int:
    load_dotenv(BACKEND_DIR.parent / ".env")
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    db_path = os.environ.get("DATABASE_PATH", str(BACKEND_DIR / "data" / "app.db"))
    if command == "seed":
        added = seed(db_path)
        print(f"Added {added} sample campaigns." if added else "Sample campaigns already exist.")
    elif command == "clear-samples":
        clear_samples(db_path)
        print("Removed sample data.")
    elif command == "google-auth":
        from google_auth_oauthlib.flow import InstalledAppFlow

        secrets = os.environ.get("GOOGLE_CLIENT_SECRETS_PATH", str(BACKEND_DIR / "credentials.json"))
        token = Path(os.environ.get("GOOGLE_TOKEN_PATH", str(BACKEND_DIR / "token.json")))
        if not Path(secrets).exists():
            print(f"Download an OAuth desktop client from Google Cloud and save it as {secrets}")
            return 1
        creds = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES).run_local_server(port=0)
        token.write_text(creds.to_json())
        token.chmod(0o600)
        print(f"Saved Google authorization to {token}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
