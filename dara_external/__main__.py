import logging
import os
import uvicorn
from .config import Settings
from .service import create_app

# Ordinary logs never include provider bodies, case text or credentials.
logging.getLogger('a2a').setLevel(logging.CRITICAL)
logging.getLogger('httpx').setLevel(logging.CRITICAL)
logging.getLogger('openai').setLevel(logging.CRITICAL)
app = create_app(Settings.from_env())
uvicorn.run(app, host='0.0.0.0', port=int(os.environ['PORT']), workers=1, access_log=False)
