from celery import shared_task
from product_video_app.celery import app  # Import the Celery app instance
import time
import json
import logging
from .services.openrouter import create_client, OpenRouterError
from .services.prompt_service import get_prompt_service
from .models import ProductPrompt, VideoGeneration
from .utils.error_handlers import task_error_handler, log_task_start, log_task_success, CeleryTaskError
import uuid

logger = logging.getLogger(__name__)

@shared_task(bind=True, max_retries=3)
@task_error_handler(max_retries=3, retry_for=(OpenRouterError, ConnectionError, TimeoutError))
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
    logger.info(f"Starting prompt generation for product: {product_data.get('product_title', 'Unknown')}")
    
    # Input validation (CP-02: Security first)
    required_fields = ['product_title', 'product_description', 'email']
    missing_fields = [field for field in required_fields if field not in product_data]
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        logger.error(error_msg)
        return {'error': error_msg, 'status': 'failed'}
    
    # Get or create a prompt using the prompt service
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
    task_id = self.request.id
    if created and task_id:
        prompt.task_id = task_id
        prompt.save(update_fields=['task_id'])
        logger.info(f"Updated prompt record with task ID: {task_id}")
    
    generation_source = "newly generated" if created else "retrieved from database"
    
    # Log successful task completion
    log_task_success("generate_prompt_with_openrouter", task_id)
    
    # Return the prompt data
    result = {
        'status': 'success',
        'prompt': prompt.prompt_text,
        'prompt_id': str(prompt.id),
        'model_used': prompt.model_used,
        'product_title': product_data['product_title'],
        'product_description': product_data['product_description'],
        'email': product_data['email'],
        'created_new': created
    }
    
    logger.info(f"Successfully {generation_source} prompt for {product_data.get('email')}")
    return result

@shared_task(bind=True)
@task_error_handler(max_retries=3)
def generate_product_video(self, data=None):
    """Celery task to generate a product video from a prompt.
    This task is executed after prompt generation.
    
    Args:
        data: Dictionary containing all required data including the prompt
        
    Returns:
        Dict containing status and result information
    """
    task_id = self.request.id
    log_task_start("generate_product_video", task_id, data)
    
    # CP-01: Handle different call patterns with clean parameter validation
    if data is None:
        logger.error(f"No data provided to video generation task {task_id}")
        return {
            'status': 'failed',
            'error': 'No data provided to video generation task',
            'step': 'input_validation'
        }
        
    logger.info(f"Starting video generation for {data.get('product_title', 'unknown product')}")
    
    # Input validation (CP-02: Security first)
    required_fields = ['product_title', 'email', 'file_url', 'prompt', 'prompt_id']
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        error_msg = f"Missing required fields: {', '.join(missing_fields)}"
        logger.error(error_msg)
        return {'error': error_msg, 'status': 'failed'}
    
    # Update status in database to 'processing'
    try:
        prompt_id = data.get('prompt_id')
        prompt = ProductPrompt.objects.get(id=prompt_id)
        
        # Create or get video generation record to track progress
        video_gen, created = VideoGeneration.objects.get_or_create(
            task_id=task_id,
            defaults={
                'email': data.get('email', ''),
                'product_title': data.get('product_title', ''),
                'product_description': data.get('product_description', ''),
                'input_image_url': data.get('file_url', ''),
                'prompt': prompt,
                'status': 'processing'
            }
        )
        
        if not created:
            video_gen.status = 'processing'
            video_gen.save(update_fields=['status', 'updated_at'])
        
        logger.info(f"Updated video generation record for task {task_id} - status: processing")
    except Exception as e:
        logger.error(f"Error updating video generation record: {e}")
        return {
            'status': 'failed',
            'error': f"Database error: {str(e)}",
            'step': 'database_update'
        }
    
    # For MVPv1, we'll simulate the video generation process
    # In a future version, implement the actual AI pipeline:
    # 1. Use the provided prompt from OpenRouter
    # 2. Call OpenAI for image transformation
    # 3. Call Fal AI for video generation
    # 4. Upload the video to S3/R2
    
    try:
        # Current implementation: simulate video generation time
        logger.info(f"Processing video with prompt: {data['prompt'][:100]}...")
        
        # Simulate AI processing time
        time.sleep(10)
        
        # Mock result data that would come from the video generation
        result_data = {
            'video_url': f"https://example.com/videos/{uuid.uuid4()}.mp4",
            'thumbnail_url': f"https://example.com/thumbnails/{uuid.uuid4()}.jpg",
            'duration': 15,  # seconds
            'prompt_used': data['prompt'][:100] + "..." if len(data['prompt']) > 100 else data['prompt'],
            'created_from_prompt_id': data['prompt_id'],
        }
        
        # Update the database record with the result
        if video_gen:
            video_gen.status = 'completed'
            video_gen.output_video_url = result_data['video_url']
            video_gen.save(update_fields=['status', 'output_video_url', 'updated_at'])
            
        logger.info(f"Completed video generation for {data.get('email')}")
    except Exception as e:
        logger.error(f"Error in video generation process: {e}", exc_info=True)
        
        # Update the database record with the error
        if video_gen:
            video_gen.status = 'failed'
            video_gen.error_message = str(e)
            video_gen.save(update_fields=['status', 'error_message', 'updated_at'])
        
        return {
            'status': 'failed',
            'error': str(e),
            'step': 'video_generation'
        }
    
    # Log successful task completion
    log_task_success("generate_product_video", task_id)
    
    # Return detailed result information
    return {
        'status': 'success',
        'email': data.get('email'),
        'product_title': data.get('product_title'),
        'prompt_id': data.get('prompt_id'),
        'video_data': result_data,
        'message': f"Video generation completed for {data.get('email')}"
    }

@shared_task(bind=True)
@task_error_handler(max_retries=2)
def process_complete_video_generation(self, data):
    """
    Celery task that orchestrates the entire video generation pipeline.
    
    Args:
        data: Dictionary containing all required data for video generation
    
    Returns:
        Dict containing status and result information
    """
    task_id = self.request.id
    log_task_start("process_complete_video_generation", task_id, data)
    
    logger.info(f"Starting complete video pipeline for {data.get('email')}")
    
    # Create and store a VideoGeneration record first
    try:
        from .models import VideoGeneration
        
        # Create video generation record
        video_gen = VideoGeneration.objects.create(
            email=data.get('email'),
            product_title=data.get('product_title'),
            status='processing',
            task_id=task_id
        )
        logger.info(f"Created video generation record {video_gen.id}")
    except Exception as e:
        logger.error(f"Error creating video generation record: {e}")
    
    # Use chord/group to avoid .get() calls
    # Start with prompt generation
    try:
        # Store task data in Celery's task state (CP-01: Clean code, NX-05: API Errors)
        state_key = f"video_gen_data_{task_id}"
        app.backend.set(state_key, json.dumps(data))
        
        # Gebruik de expliciet geregistreerde callback taak (CP-01: Clean code)
        prompt_task = generate_prompt_with_openrouter.apply_async(
            args=[data],
            link=_continue_with_video_generation_callback.s(task_id)
        )
        logger.info(f"Started prompt generation task: {prompt_task.id}")
    except Exception as e:
        logger.error(f"Error starting prompt generation: {e}")
        return {
            'status': 'failed',
            'error': str(e),
            'step': 'prompt_generation_startup'
        }
    
    # Return immediately to avoid blocking
    return {
        'status': 'processing',
        'message': 'Video generation pipeline started',
        'task_id': task_id
    }

# Expliciet geregistreerde callback task die Celery kan vinden (CP-01: Clean code)
@shared_task(name="core.tasks.continue_video_callback")
def _continue_with_video_generation_callback(prompt_data, parent_task_id):
    """
    Callback versie van de video generation pipeline voortzetting.
    """
    return _continue_with_video_generation_raw(prompt_data, parent_task_id)

def _continue_with_video_generation_raw(prompt_data, parent_task_id):
    """
    Non-public second step in the video generation pipeline.
    Takes the output from prompt generation and continues the video creation process.
    """
    logger.info(f"Continuing video generation after prompt. Parent task: {parent_task_id}")
    
    # Retrieve original data from Celery's backend
    state_key = f"video_gen_data_{parent_task_id}"
    original_data_json = app.backend.get(state_key)
    
    if not original_data_json:
        logger.error(f"Could not retrieve original data for task {parent_task_id}")
        return {
            'status': 'failed',
            'error': 'Original request data not found',
            'step': 'data_retrieval'
        }
    
    try:
        original_data = json.loads(original_data_json)
    except Exception as e:
        logger.error(f"Error parsing original data: {e}")
        return {
            'status': 'failed',
            'error': f'Error parsing request data: {str(e)}',
            'step': 'data_parsing'
        }
    
    if prompt_data.get('status') != 'success':
        logger.error(f"Prompt generation failed: {prompt_data.get('error')}")
        return {
            'status': 'failed',
            'error': prompt_data.get('error', 'Prompt generation failed'),
            'step': 'prompt_generation'
        }
    
    # Track if prompt was reused for UI feedback
    prompt_was_reused = prompt_data.get('reused', False)
    logger.info(f"Prompt {'was reused' if prompt_was_reused else 'was newly generated'}")
    
    # Add the generated prompt to our data and proceed with video generation
    enriched_data = original_data.copy()
    enriched_data['prompt'] = prompt_data.get('prompt')
    enriched_data['prompt_id'] = prompt_data.get('prompt_id')
    enriched_data['prompt_was_reused'] = prompt_was_reused
    
    # Now generate the video in non-blocking way
    try:
        # First log that we're continuing the process
        logger.info(f"Continuing with video generation for {enriched_data.get('product_title')}")
        
        # CP-01: Zorg ervoor dat enriched_data als keyword argument wordt doorgegeven
        # Dit voorkomt dat het als eerste parameter (self) wordt geïnterpreteerd
        video_task = generate_product_video.apply_async(
            kwargs={'data': enriched_data}
        )
        logger.info(f"Started product video generation task: {video_task.id}")
        
        # For now, we still want to return a useful response to show in the UI
        # So we'll construct a result with what we know now
        return {
            'status': 'processing',
            'prompt_id': prompt_data.get('prompt_id'),
            'prompt': prompt_data.get('prompt'),
            'prompt_was_reused': prompt_was_reused,
            'message': f"Video generation in progress for {enriched_data.get('product_title')}",
            'email': original_data.get('email'),
            'video_task_id': video_task.id
        }
    except Exception as e:
        logger.error(f"Error starting video generation: {e}")
        return {
            'status': 'failed',
            'error': str(e),
            'step': 'video_generation_startup',
            'prompt_was_reused': prompt_was_reused
        }
