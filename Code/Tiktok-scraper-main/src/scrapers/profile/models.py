from pydantic import BaseModel


class ProfileData(BaseModel):
    username: str
    profile_url: str
    nickname: str = ""
    signature: str = ""
    avatar_url: str = ""
    followers_count: int = 0
    following_count: int = 0
    total_likes: int = 0
    video_count: int = 0
    is_verified: bool = False
