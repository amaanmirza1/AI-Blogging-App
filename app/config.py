from **future** import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(**file**).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings:
def **init**(self) -> None:
self.base_dir = BASE_DIR
self.app_name = os.getenv("APP_NAME", "AI Blogging Platform")
self.secret_key = os.getenv("SECRET_KEY", "dev-secret-key")
self.database_url = os.getenv("DATABASE_URL", "sqlite:///blog.db")
self.host = os.getenv("HOST", "0.0.0.0")   # 🔥 important for Render
self.port = int(os.getenv("PORT", "10000"))  # 🔥 Render port

```
    # 🔥 FIX: allow all hosts (no Invalid host error)
    allowed = os.getenv("ALLOWED_HOSTS", "*")
    if allowed == "*":
        self.allowed_hosts = ["*"]
    else:
        self.allowed_hosts = [item.strip() for item in allowed.split(",") if item.strip()]

    self.session_https_only = os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true"
    self.access_token_expire_minutes = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "120"))
    self.max_upload_size_mb = int(os.getenv("MAX_UPLOAD_SIZE_MB", "3"))
    self.upload_dir = self.base_dir / "uploads"
    self.upload_dir.mkdir(parents=True, exist_ok=True)

@property
def db_path(self) -> Path:
    prefix = "sqlite:///"
    if self.database_url.startswith(prefix):
        raw = self.database_url.removeprefix(prefix)
        path = Path(raw)
        if not path.is_absolute():
            path = self.base_dir / path
        return path
    return self.base_dir / "blog.db"
```

settings = Settings()

settings = Settings()

