import json
import os
from dotenv import load_dotenv
from openai import OpenAI
load_dotenv()
client = OpenAI(base_url=os.environ["VF_ENDPOINT_BASE_URL"], api_key=os.environ["VF_ENDPOINT_API_KEY"], max_retries=0, timeout=20)
result = client.chat.completions.create(
    model=os.environ["VF_ENDPOINT_MODEL"], temperature=0, max_tokens=64,
    messages=[{"role": "system", "content": "Return only executable SQLite SQL."}, {"role": "user", "content": "Schema: CREATE TABLE users (id INTEGER, name TEXT); List every name."}])
print(json.dumps({"model": result.model, "completion": result.choices[0].message.content, "usage": result.usage.model_dump()}, sort_keys=True))
