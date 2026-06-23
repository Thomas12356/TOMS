from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    token_encryption_key: str
    jwt_secret_key: str
    starling_base_url: str

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()