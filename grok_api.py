import os
import openai
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

XAI_API_KEY = os.getenv('XAI_API_KEY')
client = openai.OpenAI(
    api_key=XAI_API_KEY,
    base_url="https://api.x.ai/v1"
)

# Model configuration
DEFAULT_MODEL = 'grok-4-1-fast-reasoning'

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_grok_response_with_retry(prompt, max_retries=None):
    """
    Get Grok response with automatic retry every 10 seconds on failure.
    Will retry forever if max_retries is None.
    """
    retry_count = 0
    current_model = DEFAULT_MODEL
    fallback_used = False
    
    while True:
        try:
            logger.info(f"Attempting Grok API call (attempt {retry_count + 1}) using model '{current_model}'")
            response = client.chat.completions.create(
                model=current_model,
                messages=[{"role": "user", "content": prompt}],
                timeout=30  # 30 second timeout
            )
            logger.info("Grok API call successful")
            return response.choices[0].message.content
            
        except Exception as e:
            retry_count += 1
            logger.error(f"Grok API error (attempt {retry_count}): {e}")

            # If the specified model is unavailable/invalid, attempt a one-time fallback to grok-2-latest
            try:
                invalid_model_error = isinstance(e, (openai.BadRequestError, getattr(openai, 'NotFoundError', Exception))) and 'model' in str(e).lower()
            except Exception:
                invalid_model_error = False

            if invalid_model_error and not fallback_used and current_model != 'grok-4-1-fast-reasoning':
                logger.warning(f"Model '{current_model}' appears unavailable. Falling back to 'grok-4-1-fast-reasoning' once.")
                current_model = 'grok-4-1-fast-reasoning'
                fallback_used = True
                # Immediate retry without additional delay
                continue
            
            if max_retries is not None and retry_count >= max_retries:
                logger.error(f"Max retries ({max_retries}) reached for Grok API")
                raise e
            
            logger.info("Retrying Grok API call in 10 seconds...")
            await asyncio.sleep(10)

def get_grok_response(prompt):
    """
    Synchronous wrapper for backward compatibility.
    For new code, prefer using get_grok_response_with_retry directly.
    """
    return asyncio.run(get_grok_response_with_retry(prompt))

# Example usage:
# print(get_grok_response("hello, how are you"))
