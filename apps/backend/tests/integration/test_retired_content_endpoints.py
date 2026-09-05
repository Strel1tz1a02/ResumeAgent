"""已退出的旧内容生成端点必须保持明确的 410 契约。"""

from httpx import ASGITransport, AsyncClient

from app.main import app


async def test_retired_content_generation_endpoints_return_gone() -> None:
    """旧内容生成不会调用模型或写入数据库。"""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in (
            "/api/v1/resumes/res-1/generate-cover-letter",
            "/api/v1/resumes/res-1/generate-outreach",
            "/api/v1/resumes/res-1/cover-letter/pdf",
        ):
            response = await client.post(path) if "generate-" in path else await client.get(path)
            assert response.status_code == 410
