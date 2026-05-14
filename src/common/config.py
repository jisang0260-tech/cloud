import os


class Settings:
    def __init__(self) -> None:
        self.targets_table = os.environ["TARGETS_TABLE"]
        self.sessions_table = os.environ["SESSIONS_TABLE"]
        self.recordings_bucket = os.environ["RECORDINGS_BUCKET"]
        self.analysis_bucket = os.environ["ANALYSIS_BUCKET"]
        self.app_timezone = os.getenv("APP_TIMEZONE", "Asia/Seoul")
        self.admin_group = os.getenv("ADMIN_GROUP", "admin")
        self.presigned_url_expires = int(os.getenv("PRESIGNED_URL_EXPIRES", "3600"))


settings = Settings()
