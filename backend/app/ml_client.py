import httpx


class MLUnavailable(Exception):
    pass


class MLInvalidResponse(Exception):
    pass


class MLClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def evaluate(self, request: dict) -> dict:
        try:
            response = await self.client.post("/v1/evaluate", json=request)
            response.raise_for_status()
        except (httpx.RequestError, httpx.HTTPStatusError) as error:
            raise MLUnavailable("Local ML service is unavailable.") from error
        try:
            result = response.json()
        except ValueError as error:
            raise MLInvalidResponse("ML did not return valid JSON.") from error
        if not isinstance(result, dict):
            raise MLInvalidResponse("ML response must be an object.")
        return result

