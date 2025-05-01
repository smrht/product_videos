from celery import shared_task, chain
from product_video_app.celery import app  # Import the Celery app instance
import time
import json
import logging
from .services.openrouter import create_client, OpenRouterError
from .services.prompt_service import get_prompt_service # Corrected import path
from .services.image_editing_service import image_editing_service # Import the new service
from .models import ProductPrompt, VideoGeneration
from .utils.error_handlers import task_error_handler, log_task_start, log_task_success, log_task_error, CeleryTaskError # Added log_task_error

logger = logging.getLogger(__name__)

# Configure Celery app instance
# Ensure your Celery app is configured correctly, e.g., in product_video_app/celery.py
# This assumes 'app' is the configured Celery instance

# --- Task Definitions ---

@shared_task(bind=True)
@task_error_handler(max_retries=3)
def generate_prompt_with_openrouter(self, product_data):
    """
    Celery task to generate or retrieve a prompt for product video generation.
    This is the first step in the async pipeline after initial data validation.
    Links to the image editing step upon successful completion.
    """
    task_id = self.request.id
    log_task_start("generate_prompt_with_openrouter", task_id, product_data)

    prompt_service = get_prompt_service()
    try:
        logger.info(f"[{task_id}] Attempting to call prompt_service.get_or_generate_prompt...")
        prompt, created = prompt_service.get_or_generate_prompt(
            email=product_data['email'], # Email is needed for the prompt service
            product_title=product_data['product_title'],
            product_description=product_data['product_description']
            # Add any other relevant fields for prompt generation/retrieval
        )
        logger.info(f"[{task_id}] Successfully returned from prompt_service.get_or_generate_prompt. Created: {created}")
    except Exception as e:
        error_payload = {
            'status': 'failed',
            'error': f"Prompt generation/retrieval failed: {e}",
            'error_type': e.__class__.__name__,
            'task_id': task_id
        }
        log_task_error("generate_prompt_with_openrouter", task_id, e, msg="Failed during prompt service interaction")
        # Return the error payload so the callback can handle it gracefully
        return error_payload

    prompt_text = prompt.prompt_text # Assuming the service returns a Prompt object or similar
    prompt_id = str(prompt.id) # Assuming the prompt object has an 'id' attribute

    # Return a dictionary as expected by the callback
    result = {
        'status': 'success',
        'prompt_text': prompt_text,
        'prompt_id': prompt_id, 
        'original_data': product_data, # Pass original data along if needed
        'task_id': task_id
    }
    log_task_success("generate_prompt_with_openrouter", task_id, result=result)
    return result

@shared_task(bind=True)
@task_error_handler(max_retries=2) # Fewer retries for potentially expensive API calls
def edit_product_image(self, data):
    """
    Celery task to edit the product image using an AI provider.
    Takes the original image URL and the generated prompt.
    Links to the video generation step upon successful completion.
    """
    task_id = self.request.id
    log_task_start("edit_product_image", task_id, data)

    required_fields = ['file_url', 'prompt', 'prompt_id']
    missing_fields = [field for field in required_fields if field not in data or not data[field]]
    if missing_fields:
        error_msg = f"Missing required fields for image editing: {', '.join(missing_fields)}"
        log_task_error("edit_product_image", task_id, ValueError(error_msg))
        raise CeleryTaskError(error_msg)

    try:
        edited_image_url = image_editing_service.edit_image(
            provider_name='openai', # Make this configurable later
            image_url=data['file_url'],
            prompt=data['prompt']
        )
        log_task_success("edit_product_image", task_id)
        logger.info(f"Image editing successful for task {task_id}. Edited URL: {edited_image_url}")

        # Prepare result for the next step (video generation)
        result = {
            **data, # Pass existing data along
            'status': 'success',
            'edited_image_url': edited_image_url,
        }
        return result # This result goes to the callback linking to video generation

    except Exception as e:
        log_task_error("edit_product_image", task_id, e, msg="Image editing service failed")
        # Optionally update VideoGeneration record status to failed here
        raise CeleryTaskError(f"Image editing failed: {e}") from e


@shared_task(bind=True)
@task_error_handler(max_retries=3)
def generate_product_video(self, data=None):
    """
    Celery task to generate a product video from an EDITED image and prompt.
    This task is executed after image editing.

    Args:
        data: Dictionary containing all required data including the prompt and edited_image_url

    Returns:
        Dict containing status and result information (e.g., final video URL)
    """
    task_id = self.request.id
    log_task_start("generate_product_video", task_id, data)

    if data is None:
        error_msg = 'Input data missing for video generation.'
        log_task_error("generate_product_video", task_id, ValueError(error_msg))
        raise CeleryTaskError(error_msg)

    logger.info(f"Starting video generation for {data.get('product_title', 'unknown product')}")

    # Input validation (CP-02: Security first)
    required_fields = ['product_title', 'email', 'edited_image_url', 'prompt', 'prompt_id']
    missing_fields = [field for field in required_fields if field not in data or not data[field]]
    if missing_fields:
        error_msg = f"Missing required fields for video generation: {', '.join(missing_fields)}"
        log_task_error("generate_product_video", task_id, ValueError(error_msg))
        raise CeleryTaskError(error_msg)

    # Update VideoGeneration record status (optional, can also be done in callbacks)
    try:
        # TODO: Implement robust way to get/update the VideoGeneration record
        # video_gen_id = data.get('video_generation_id') # Needs to be passed through chain
        # VideoGeneration.objects.filter(id=video_gen_id).update(status='processing_video')
        logger.info(f"Updating status to 'processing_video' for task {task_id}") # Placeholder log
        pass # Placeholder
    except Exception as e:
        log_task_error("generate_product_video", task_id, e, msg="Failed to update VideoGeneration status before processing")
        # Decide policy: stop or continue?
        # raise CeleryTaskError(f"DB update failed before video gen: {e}") from e

    # Implement ECHTE video generatie met Fal.ai of andere provider
    # Gebruik data['edited_image_url'] en data['prompt']
    logger.info(f"Simulating video generation using edited image: {data.get('edited_image_url')} and prompt: {data.get('prompt', '')[:50]}...")
    time.sleep(10)  # Simulate processing time for video generation

    # Voorbeeld resultaat
    # video_url = call_fal_ai_video_api(data['edited_image_url'], data['prompt'])

    # ** Tijdelijke MOCK response **
    video_url = f"https://mock-fal-ai.com/video/{task_id}.mp4"
    thumbnail_url = f"https://mock-fal-ai.com/thumbnail/{task_id}.jpg"
    duration = 15 # seconden

    # Log successful task completion
    log_task_success("generate_product_video", task_id)

    # Final result of the video generation step
    result = {
        'status': 'success',
        'email': data.get('email'),
        'product_title': data.get('product_title'),
        'prompt_id': data.get('prompt_id'),
        'video_data': {
            'video_url': video_url,
            'thumbnail_url': thumbnail_url,
            'duration': duration,
            'prompt_used': data['prompt'][:100] + "..." if len(data['prompt']) > 100 else data['prompt'],
            'created_from_prompt_id': data['prompt_id'],
            'edited_image_url_used': data['edited_image_url'] # Include for clarity
        },
        'message': f"Video generation completed for {data.get('email')}"
    }

    # TODO: Update VideoGeneration record with final status and video URL
    # VideoGeneration.objects.filter(id=video_gen_id).update(status='completed', output_video_url=video_url, ...)
    logger.info(f"Video generation task {task_id} finished successfully.")

    return result

# --- Callback Tasks (Internal Implementation Detail) ---

# NB: Callbacks zijn expliciet geregistreerd zodat Celery ze kan vinden.
# Ze halen de originele data op uit de backend en verrijken deze met resultaten
# van de vorige stap voordat ze de volgende taak in de keten aanroepen.

@shared_task(name="core.tasks.continue_with_image_edit")
def _continue_with_image_edit_callback(prompt_result, parent_task_id):
    """
    Callback taak die wordt uitgevoerd na prompt generatie.
    Start de image editing taak.
    """
    log_task_start("_continue_with_image_edit_callback", parent_task_id, {"prompt_result_status": prompt_result.get('status') if isinstance(prompt_result, dict) else 'unknown'})
    logger.info(f"Callback: Continue with image edit. Parent task: {parent_task_id}")
    logger.info(f"Received prompt_result type: {type(prompt_result)}, value: {prompt_result}")

    # Check if the previous task (prompt generation) failed
    if not isinstance(prompt_result, dict) or prompt_result.get('status') != 'success':
        error_msg = f"Cannot continue image editing, previous step (prompt generation) failed or returned unexpected result: {prompt_result}"
        log_task_error("_continue_with_image_edit_callback", parent_task_id, None, msg=error_msg)
        # TODO: Update VideoGeneration status to 'failed'
        return {'status': 'FAILURE', 'error': error_msg, 'step': 'callback_image_edit_validation'}

    # Retrieve original data stored by the orchestrator
    state_key = f"video_gen_data_{parent_task_id}"
    try:
        stored_data_raw = app.backend.client.get(state_key)
        if not stored_data_raw:
            raise ValueError(f"Original data not found in backend for key {state_key}")
        original_data = json.loads(stored_data_raw)
        logger.info(f"Retrieved original data from backend for parent {parent_task_id}")
    except Exception as e:
        error_msg = f"Failed to retrieve original data from backend: {e}"
        log_task_error("_continue_with_image_edit_callback", parent_task_id, e)
        # TODO: Update VideoGeneration status to 'failed'
        return {'status': 'FAILURE', 'error': error_msg, 'step': 'callback_retrieve_data'}

    # Prepare data for the image editing task
    try:
        prompt_text = prompt_result.get('prompt_text')
        prompt_id = prompt_result.get('prompt_id') 
        file_url = original_data.get('file_url')

        if not all([prompt_text, prompt_id, file_url]):
            missing = [f for f, v in [('prompt_text', prompt_text), ('prompt_id', prompt_id), ('file_url', file_url)] if not v]
            raise ValueError(f"Missing required data to start image editing: {', '.join(missing)}")

        data_for_edit = {
            **original_data, # Pass through original data
            'prompt': prompt_text, 
            'prompt_id': prompt_id, 
            'parent_task_id': parent_task_id # Pass parent ID for context
        }

        # Chain the next step: edit_product_image -> _continue_with_video_generation_callback
        image_edit_signature = edit_product_image.s(data=data_for_edit)
        video_gen_callback_signature = _continue_with_video_generation_callback.s(parent_task_id=parent_task_id)

        # Execute the image edit task and link the video generation callback
        chain(image_edit_signature | video_gen_callback_signature).apply_async()

        logger.info(f"Chained image edit task -> video generation callback for parent {parent_task_id}")
        # Optional: Update VideoGeneration status
        # VideoGeneration.objects.filter(id=original_data.get('video_generation_id')).update(status='queued_image_edit')

        # Callback is successful if the next task is queued
        return {'status': 'SUCCESS', 'message': 'Image editing task queued.'}

    except Exception as e:
        error_msg = f"Error preparing or starting image editing task: {e}"
        log_task_error("_continue_with_image_edit_callback", parent_task_id, e)
        # TODO: Update VideoGeneration status to 'failed'
        return {'status': 'FAILURE', 'error': error_msg, 'step': 'callback_start_image_edit'}


@shared_task(name="core.tasks.continue_with_video_generation")
def _continue_with_video_generation_callback(image_edit_result, parent_task_id):
    """
    Callback taak die wordt uitgevoerd na image editing.
    Start de video generation taak.
    """
    logger.info(f"Callback: Continue with video generation. Parent task: {parent_task_id}")
    state_key = f"video_gen_data_{parent_task_id}" # Key to retrieve original data
    original_data_json = app.backend.get(state_key)

    if not original_data_json:
        error_msg = f"Original data not found for parent task {parent_task_id} in video generation callback."
        logger.error(error_msg)
        # TODO: Update VideoGeneration record status to 'failed'
        return {'status': 'FAILURE', 'error': error_msg, 'step': 'callback_video_gen_data_retrieval'}

    original_data = json.loads(original_data_json)

    # Validate image edit result
    if image_edit_result.get('status') != 'success':
        error_msg = image_edit_result.get('error', 'Unknown error during image editing')
        logger.error(f"Cannot continue video generation, previous step (image edit) failed: {error_msg}")
        # TODO: Update VideoGeneration record status to 'failed'
        return {'status': 'FAILURE', 'error': f'Previous step failed: {error_msg}', 'step': 'callback_video_gen_validation'}

    # Combine original data with results needed for video generation
    data_for_video_gen = {
        **original_data,
        'prompt_id': image_edit_result.get('prompt_id'),
        'prompt': image_edit_result.get('prompt'),
        'edited_image_url': image_edit_result.get('edited_image_url'),
        'prompt_generation_source': image_edit_result.get('prompt_generation_source'), # Komt nu uit image edit result
        # Voeg hier eventueel video_generation_id toe
    }

    required_fields = ['prompt_id', 'prompt', 'edited_image_url', 'email', 'product_title']
    missing_fields = [field for field in required_fields if field not in data_for_video_gen or not data_for_video_gen[field]]
    if missing_fields:
        error_msg = f"Missing required data ({', '.join(missing_fields)}) for video generation callback."
        logger.error(f"{error_msg} Parent task: {parent_task_id}")
        # TODO: Update VideoGeneration record status to 'failed'
        return {'status': 'FAILURE', 'error': error_msg, 'step': 'callback_video_gen_validation'}

    # Start the final video generation task
    try:
        video_task = generate_product_video.apply_async(
            kwargs={'data': data_for_video_gen}
        )
        logger.info(f"Started product video generation task: {video_task.id} for parent {parent_task_id}")

        # Update VideoGeneration record status (optional)
        # VideoGeneration.objects.filter(id=data_for_video_gen.get('video_generation_id')).update(status='queued_video')

        # Callback is successful if the task is queued
        return {'status': 'SUCCESS', 'message': f'Video generation task {video_task.id} queued.'}

    except Exception as e:
        error_msg = f"Error starting video generation task: {e}"
        log_task_error("_continue_with_video_generation_callback", parent_task_id, e)
        # TODO: Update VideoGeneration record status to 'failed'
        return {'status': 'FAILURE', 'error': error_msg, 'step': 'start_video_generation'}

# --- Hoofd Orchestrator Taak ---

@shared_task(bind=True)
@task_error_handler(max_retries=2) # Minder retries voor de orchestrator zelf
def process_complete_video_generation(self, data):
    """
    Orchestrator task that starts the entire video generation pipeline.
    1. Creates a VideoGeneration record.
    2. Stores initial data in Celery backend.
    3. Starts the prompt generation task, linking the image edit callback.
    """
    task_id = self.request.id
    log_task_start("process_complete_video_generation", task_id, data)

    # 1. Create initial record (optional, but good for tracking)
    try:
        # video_gen = VideoGeneration.objects.create(
        #     task_id=task_id,
        #     email=data.get('email'),
        #     product_title=data.get('product_title'),
        #     product_description=data.get('product_description'),
        #     input_image_url=data.get('file_url'),
        #     status='pending'
        # )
        # video_gen_id = str(video_gen.id)
        # data['video_generation_id'] = video_gen_id # Add ID for later updates
        # logger.info(f"Created VideoGeneration record {video_gen_id} for task {task_id}")
        logger.info(f"Creating placeholder VideoGeneration record tracking for task {task_id}") # Placeholder log
        pass # Placeholder
    except Exception as e:
        log_task_error("process_complete_video_generation", task_id, e, msg="Failed to create initial VideoGeneration record")
        # Decide if this is fatal
        # raise CeleryTaskError(f"DB error: {e}") from e
        pass # Continue for now

    # 2. Store initial data in backend for callbacks
    state_key = f"video_gen_data_{task_id}"
    try:
        # Gebruik setex direct op de redis client voor TTL
        app.backend.client.setex(state_key, 3600, json.dumps(data)) # Store for 1 hour
        # app.backend.set(state_key, json.dumps(data), expires=3600) # Store for 1 hour <-- Oude methode
        logger.info(f"Stored initial data in backend with key {state_key}")
    except Exception as e:
        log_task_error("process_complete_video_generation", task_id, e, msg="Failed to store initial data in backend")
        # This is likely fatal for the chain
        # TODO: Update VideoGeneration record status to 'failed'
        raise CeleryTaskError(f"Backend error: {e}") from e

    # 3. Start the first task in the chain: prompt generation
    # Link the callback that will start the image editing task
    try:
        prompt_task_signature = generate_prompt_with_openrouter.s(product_data=data)
        image_edit_callback_signature = _continue_with_image_edit_callback.s(parent_task_id=task_id)
        # Chain: generate_prompt -> _continue_with_image_edit_callback
        chain(prompt_task_signature | image_edit_callback_signature).apply_async()

        logger.info(f"Started pipeline chain: prompt_task -> image_edit_callback for parent {task_id}")
    except Exception as e:
        log_task_error("process_complete_video_generation", task_id, e, msg="Failed to start the Celery chain")
        # TODO: Update VideoGeneration record status to 'failed'
        raise CeleryTaskError(f"Celery chain initiation failed: {e}") from e

    # The orchestrator returns immediately with a pending status
    log_task_success("process_complete_video_generation", task_id)
    return {'status': 'PENDING', 'message': 'Video generation pipeline initiated', 'task_id': task_id}
