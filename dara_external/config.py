import hashlib
import json
import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit

RELEASE = 'dara-external-test@1.0.0'
STYLE = 'Write concise, clinical, plain-language markdown (4-10 sentences or a short list). Reference specific values from any data shared. Say exactly what information is missing. Never write lines starting with "@@".'
SAFETY = 'This is decision support, not a diagnosis. If anything suggests an emergency, say clearly to seek emergency care now.'
ASSESSMENT = f'''You are Dara External — Test, an educational dermatology specialist: Assesses skin, hair and nail concerns and flags lesions that need a closer look.
Focus areas: Lesion triage, Rashes, Acne, Psoriasis, Skin cancer screening.
Give your own independent initial assessment of the user's case from your specialty's angle. If your specialty is less relevant, say so briefly and weigh in on what you can.
Include: your working assessment, confidence (low/medium/high) with why, red flags, and next steps.
{STYLE}
{SAFETY}'''
DISCUSSION = f'''You are Dara External — Test, an educational dermatology specialist. Focus areas: Lesion triage, Rashes, Acne, Psoriasis, Skin cancer screening.
You have read the panel's initial assessments. Respond to them by name: say where you agree, challenge weak reasoning, point out missed information, and state whether you revise your own position. Do not fake agreement; keep genuine disagreement explicit.
{STYLE}
{SAFETY}'''

@dataclass(frozen=True)
class Settings:
    model_id: str
    public_base_url: str
    openai_base_url: str
    api_key: str = field(repr=False)
    shared_secret: str = field(repr=False)
    data_dir: str = '/data'
    timeout_seconds: float = 90
    max_tokens: int = 2000
    max_concurrent: int = 4

    def __post_init__(self):
        for url in (self.public_base_url, self.openai_base_url):
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError('Public and model base URLs must be credential-free HTTPS URLs')
            if parsed.hostname in ('localhost', '127.0.0.1', '0.0.0.0', '::1'):
                raise ValueError('Use the actual public HTTPS address')
        if urlsplit(self.public_base_url).path not in ('', '/'):
            raise ValueError('PUBLIC_BASE_URL must be the public origin without a path')
        if not self.model_id or not self.api_key or len(self.shared_secret) < 32:
            raise ValueError('MODEL_ID, OPENAI_API_KEY and a shared secret of at least 32 characters are required')
        if not 1 <= self.timeout_seconds <= 300 or not 1 <= self.max_concurrent <= 16:
            raise ValueError('Invalid execution bounds')

    @property
    def fingerprint(self):
        body = dict(release=RELEASE, protocol='1.0', sdk='1.3.0', model=self.model_id,
                    endpoint=self.openai_base_url.rstrip('/') + '/chat/completions',
                    assessment=ASSESSMENT, discussion=DISCUSSION, max_tokens=self.max_tokens,
                    timeout=self.timeout_seconds, max_concurrent=self.max_concurrent,
                    adapter='openai-2.8.1-no-retries-exact-model-v1')
        return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    @classmethod
    def from_env(cls):
        return cls(model_id=os.environ['MODEL_ID'], public_base_url=os.environ['PUBLIC_BASE_URL'].rstrip('/'),
                   openai_base_url=os.environ['OPENAI_BASE_URL'], api_key=os.environ['OPENAI_API_KEY'],
                   shared_secret=os.environ['BAZAARO_SHARED_SECRET'], data_dir=os.getenv('DATA_DIR', '/data'),
                   timeout_seconds=float(os.getenv('MODEL_TIMEOUT_SECONDS', '90')))
