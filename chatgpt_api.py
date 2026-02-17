import os
import openai
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv('SECRET_KEY')
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Model configuration: override with env var OPENAI_MODEL; default per user request
DEFAULT_MODEL = os.getenv('OPENAI_MODEL', 'gpt5')

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def get_chatgpt_response_with_retry(prompt, max_retries=None):
    """
    Get ChatGPT response with automatic retry every 10 seconds on failure.
    Will retry forever if max_retries is None.
    """
    retry_count = 0
    current_model = "gpt-5.2"
    fallback_used = False
    
    while True:
        try:
            logger.info(f"Attempting ChatGPT API call (attempt {retry_count + 1}) using model '{current_model}'")
            response = client.chat.completions.create(
                model=current_model,
                messages=[{"role": "user", "content": prompt}],
                timeout=30  # 30 second timeout
            )
            logger.info("ChatGPT API call successful")
            return response.choices[0].message.content
            
        except Exception as e:
            retry_count += 1
            logger.error(f"ChatGPT API error (attempt {retry_count}): {e}")

            # If the specified model is unavailable/invalid, attempt a one-time fallback to gpt-4
            try:
                invalid_model_error = isinstance(e, (openai.BadRequestError, getattr(openai, 'NotFoundError', Exception))) and 'model' in str(e).lower()
            except Exception:
                invalid_model_error = False

            if invalid_model_error and not fallback_used and current_model != 'gpt-5.1':
                logger.warning(f"Model '{current_model}' appears unavailable. Falling back to 'gpt-5.1' once.")
                current_model = 'gpt-5.1'
                fallback_used = True
                # Immediate retry without additional delay
                continue
            
            if max_retries is not None and retry_count >= max_retries:
                logger.error(f"Max retries ({max_retries}) reached for ChatGPT API")
                raise e
            
            logger.info("Retrying ChatGPT API call in 10 seconds...")
            await asyncio.sleep(10)

def get_chatgpt_response(prompt):
    """
    Synchronous wrapper for backward compatibility.
    For new code, prefer using get_chatgpt_response_with_retry directly.
    """
    return asyncio.run(get_chatgpt_response_with_retry(prompt))

# Example usage:
# print(get_chatgpt_response("hello, how are you"))
