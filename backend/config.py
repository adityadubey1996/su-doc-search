from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gemini_api_key: str = "placeholder"
    opensearch_url: str = "http://localhost:9200"
    pinecone_host: str = "http://localhost:5081"
    pinecone_index: str = "su-docs"
    sitemap_url: str = "https://docs.searchunify.com/Sitemap.xml"
    log_level: str = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton Settings instance, creating it on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
