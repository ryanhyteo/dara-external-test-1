import time
from openai import AsyncOpenAI
from .config import ASSESSMENT, DISCUSSION

class Model:
    def __init__(self, settings):
        self.settings = settings
        self.client = AsyncOpenAI(api_key=settings.api_key, base_url=settings.openai_base_url,
                                  timeout=settings.timeout_seconds, max_retries=0)

    async def run(self, phase, text):
        start = time.monotonic()
        response = await self.client.chat.completions.create(
            model=self.settings.model_id, max_tokens=self.settings.max_tokens,
            messages=[{'role': 'system', 'content': ASSESSMENT if phase == 'assessment' else DISCUSSION},
                      {'role': 'user', 'content': text}])
        usage = response.usage.model_dump() if response.usage else None
        identity = {'requested_model': self.settings.model_id, 'returned_model': response.model or 'unknown',
                    'reported_model_version': getattr(response, 'model_version', None) or 'unknown',
                    'system_fingerprint': response.system_fingerprint or 'unknown',
                    'duration_ms': round((time.monotonic() - start) * 1000),
                    'token_usage': usage, 'api_cost': 'unknown', 'actual_model_call': True}
        if response.model != self.settings.model_id:
            return None, {**identity, 'failure_reason': 'model_identity_mismatch'}
        text = response.choices[0].message.content if response.choices else None
        if not text or response.choices[0].finish_reason != 'stop':
            return None, {**identity, 'failure_reason': 'incomplete_model_response'}
        return text, identity

    async def close(self):
        await self.client.close()
