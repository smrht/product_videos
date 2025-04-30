from celery import shared_task
import time
import json
import logging
from .services.openrouter import create_client, OpenRouterError
from .services.prompt_service import get_prompt_service
from .models import ProductPrompt, VideoGeneration

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
def generate_prompt_with_openrouter(self, product_data, force_new=False):
    """
    Celery task to generate an optimized prompt using OpenRouter API.
    
    Args:
        product_data: Dictionary containing product information
            - product_title: Title of the product
            - product_description: Description of the product
            - email: User email for notification
        force_new: If True, always generate a new prompt even if a similar one exists
    
    Returns:
        Dict containing the generated prompt and metadata
        
    NX-06: Implements standardized error handling with retries
    """
    try:
        logger.info(f"Starting prompt generation for product: {product_data.get('product_title', 'Unknown')}")
        
        # Input validation (CP-02: Security first)
        required_fields = ['product_title', 'product_description', 'email']
        missing_fields = [field for field in required_fields if field not in product_data]
        if missing_fields:
            error_msg = f"Missing required fields: {', '.join(missing_fields)}"
            logger.error(error_msg)
            return {'error': error_msg, 'status': 'failed'}
        
        # Get or create a prompt using the prompt service
        try:
            prompt_service = get_prompt_service()
            
            # Get prompt from database or generate a new one
            prompt, created = prompt_service.get_or_generate_prompt(
                product_title=product_data['product_title'],
                product_description=product_data['product_description'],
                email=product_data['email'],
                category=product_data.get('category'),
                force_new=force_new,
                model=product_data.get('model')
            )
            
            # Update task ID in the prompt record if it was created
            if created and self.request.id:
                prompt.task_id = self.request.id
                prompt.save(update_fields=['task_id'])
                logger.info(f"Updated prompt record with task ID: {self.request.id}")
            
            generation_source = "newly generated" if created else "retrieved from database"
            logger.info(f"Successfully {generation_source} prompt for {product_data.get('email')}")
            
            # Return the prompt data
            return {
                'status': 'success',
                'prompt': prompt.prompt_text,
                'prompt_id': str(prompt.id),
                'model_used': prompt.model_used,
                'product_title': product_data['product_title'],
                'product_description': product_data['product_description'],
                'email': product_data['email'],
                'created_new': created
            }
            
        except ValueError as e:
            # Handle validation errors
            logger.error(f"Validation error: {str(e)}")
            return {
                'status': 'failed',
                'error': str(e),
                'email': product_data.get('email', 'Unknown')
            }
        
    except OpenRouterError as e:
        logger.error(f"OpenRouter API error: {str(e)}")
        # Retry with exponential backoff (CP-04: Proper error handling)
        retry_count = self.request.retries
        retry_delay = 2 ** retry_count  # Exponential backoff: 2, 4, 8 seconds
        
        # Only retry for potentially transient errors
        if "Request error" in str(e) and retry_count < self.max_retries:
            logger.warning(f"Retrying task in {retry_delay} seconds (attempt {retry_count + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=retry_delay)
            
        # Return error information for terminal errors
        return {
            'status': 'failed',
            'error': str(e),
            'email': product_data.get('email', 'Unknown')
        }
    except Exception as e:
        logger.error(f"Unexpected error in prompt generation: {str(e)}")
        return {
            'status': 'failed',
            'error': f"Unexpected error: {str(e)}",
            'email': product_data.get('email', 'Unknown')
        }

@shared_task
def generate_product_video(data):
    """Celery task to generate product video asynchronously."""
    print(f"Received video generation task with data: {data}")
    # Simulate video generation time
    time.sleep(10) # Simulate a 10-second process
    print(f"Finished processing task for email: {data.get('email')}")
    # TODO: Implement actual video generation logic using AI models
    # - Download image from R2 URL (data['file_url'])
    # - Call OpenRouter for script
    # - Call OpenAI for voiceover
    # - Call Fal AI for video generation
    # - Upload final video to R2
    # - Notify user (e.g., via email)
    return f"Video generation started for {data.get('email')}"
