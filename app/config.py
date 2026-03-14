from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    data_dir: str = "/data"
    jwt_secret: str = "change-me"
    jwt_expire_days: int = 30
    admin_username: str = "admin"
    admin_password: str = "admin123"

    @property
    def db_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.data_dir}/botc.db"

    class Config:
        env_file = ".env"


settings = Settings()
