"""Single source of configuration. Values come from environment variables (or a local `.env`).

Nothing here is a real secret: the demo defaults are for a disposable local stack only, and
`APP_ENV=production` refuses to seed or run with the demo accounts (app.runtime.bootstrap).
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_PACKAGE = Path(__file__).resolve().parent
_REPO = _PACKAGE.parents[1]


def _resource(packaged: str, checkout: str) -> Path:
    """Wheels carry data under app/ (pyproject force-include); a checkout reads the repo."""
    inside = _PACKAGE / packaged
    return inside if inside.exists() else _REPO / checkout


FIXTURES_DIR = _resource("fixtures", "data/fixtures")
BUNDLED_GRAPH = _resource("demo.rl", "data/demo.rl")
BRAND_DIR = _resource("brand", "brand")


class Settings(BaseSettings):
    # env_ignore_empty: docker compose passes unset variables as "", which means "use default".
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    app_env: Literal["test", "production"] = Field(
        "test", description="`production` disables demo seeding and demands real secrets."
    )
    data_dir: Path = Field(
        Path("var"),
        description="Runtime state: the SQLite database and the writable Relate store.",
    )
    org_names: dict[str, str] = Field(
        {},
        description=(
            'JSON map from organisation key to display name, e.g. {"datakind-uk": "Org A"}. '
            "Applied when the demo is seeded."
        ),
    )
    session_ttl_hours: int = Field(12, description="How long a sign-in lasts.")
    cookie_secure: bool | None = Field(
        None,
        description=(
            "Force the Secure cookie flag. Unset: Secure whenever the request arrived over "
            "HTTPS (directly or via X-Forwarded-Proto from a trusted proxy)."
        ),
    )
    trust_proxy_headers: bool = Field(
        False, description="Honour X-Forwarded-Proto (set when behind a TLS-terminating proxy)."
    )
    api_url: str = Field(
        "http://127.0.0.1:8080",
        description="Where the Streamlit admin console reaches the JSON API (its only data path).",
    )
    projector_interval_seconds: float = Field(
        5.0, description="How often the background projector retries pending graph updates."
    )
    seed_demo_on_start: bool = Field(
        True,
        description="Seed the disposable demo when the data directory is empty (test env only).",
    )
    graph_max_rows: int = Field(500, description="Upper bound on rows read from Relate.")

    aura_uri: str = Field("", description="Optional neo4j+s:// URI of a designated dev AuraDB.")
    aura_username: str = Field("neo4j", description="AuraDB user.")
    aura_password: SecretStr = Field(SecretStr(""), description="AuraDB password.")
    aura_database: str = Field("neo4j", description="AuraDB database name.")

    @property
    def sqlite_path(self) -> Path:
        return self.data_dir / "dvn.sqlite3"

    @property
    def graph_path(self) -> Path:
        return self.data_dir / "graph.rl"

    @property
    def demo_allowed(self) -> bool:
        return self.app_env != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
