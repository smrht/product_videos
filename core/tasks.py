from celery import shared_task, chain
from product_video_app.celery import app  # Import the Celery app instance
import time
import json
import uuid
import logging
from .services.openrouter import create_client, OpenRouterError
from .services.prompt_service import get_prompt_service # Corrected import path
from .services.image_editing_service import image_editing_service # Import the new service
from .services.fal_service import fal_service, FalServiceError # Import Fal service and specific error
from .models import ProductPrompt, VideoGeneration
from .utils.error_handlers import task_error_handler, log_task_start, log_task_success, log_task_error, CeleryTaskError # Added log_task_error
from django.core.mail import send_mail, EmailMultiAlternatives 
from django.template.loader import render_to_string 
from django.core.files.base import ContentFile # Add ContentFile import
from django.core.files.storage import default_storage # Add default_storage import
import requests # Add requests import
import io # Add io import
import uuid # Add uuid import
from django.conf import settings
from django.utils import timezone
from smtplib import SMTPException # Add SMTPException import

logger = logging.getLogger(__name__)

# Configure Celery app instance
# Ensure your Celery app is configured correctly, e.g., in product_video_app/celery.py
# This assumes 'app' is the configured Celery instance

# --- Task Definitions ---

@shared_task(bind=True)
@task_error_handler(max_retries=3)
def generate_prompt_with_openrouter(self, product_data, orchestrator_task_id):
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
        'task_id': task_id,
        'orchestrator_task_id': orchestrator_task_id # Pass orchestrator ID
    }
    log_task_success("generate_prompt_with_openrouter", task_id, result=result)
    return result

@shared_task(bind=True)
@task_error_handler(max_retries=2) # Fewer retries for potentially expensive API calls
def edit_product_image(self, data, orchestrator_task_id):
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

    # Validate uploaded file is an image we support
    supported_ext = ('.png', '.jpg', '.jpeg', '.webp')
    lower_url = data['file_url'].lower()
    if not lower_url.endswith(supported_ext):
        raise CeleryTaskError(
            f"Unsupported input file type for image editing: '{lower_url}'. "
            "Please upload a PNG/JPG image instead of a video or other format."
        )

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
            'orchestrator_task_id': orchestrator_task_id # Pass orchestrator ID
        }
        return result # This result goes to the callback linking to video generation

    except Exception as e:
        log_task_error("edit_product_image", task_id, e, msg="Image editing service failed")
        # Optionally update VideoGeneration record status to failed here
        raise CeleryTaskError(f"Image editing failed: {e}") from e


@shared_task(bind=True)
@task_error_handler(max_retries=1) # Reduce retries for potentially long/expensive video generation
def generate_product_video(self, data):
    """
    Celery task to generate a product video from an EDITED image (S3 URL) and prompt.
    Uses Fal AI service.

    Args:
        data (dict): Dictionary containing required data including 'edited_image_url' (S3),
                     'prompt', 'email', 'product_title', and 'video_generation_id'.

    Returns:
        Dict containing status and result information (e.g., final video URL).
    """
    task_id = self.request.id
    log_task_start("generate_product_video", task_id, data)

    video_generation_id = data.get('video_generation_id')
    if not video_generation_id:
        # This case should ideally not happen if the callback passes it correctly
        error_msg = 'video_generation_id missing from input data for video generation.'
        log_task_error("generate_product_video", task_id, ValueError(error_msg))
        raise CeleryTaskError(error_msg)

    logger.info(f"Starting video generation for VideoGeneration ID: {video_generation_id}")

    # Input validation (CP-02: Security first)
    required_fields = ['edited_image_url', 'prompt', 'email', 'product_title']
    missing_fields = [field for field in required_fields if field not in data or not data[field]]
    if missing_fields:
        error_msg = f"Missing required fields for video generation: {', '.join(missing_fields)}"
        log_task_error("generate_product_video", task_id, ValueError(error_msg), video_generation_id=video_generation_id)
        try:
            VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=error_msg[:255])
        except Exception as db_err:
            logger.error(f"[{task_id}] DB Error updating failed status due to missing fields for {video_generation_id}: {db_err}")
        raise CeleryTaskError(error_msg)

    # Status is already set to 'processing_video' by the callback
    # logger.info(f"[{task_id}] Status confirmed as 'processing_video' for VideoGeneration ID {video_generation_id}")

    try:
        # === ECHTE Video Generatie met Fal AI ===
        edited_s3_url = data['edited_image_url']
        logger.info(f"[{task_id}] Calling Fal AI service with edited image URL (S3): {edited_s3_url}")

        # Get the video duration from the data (default to 5 seconds if not provided)
        video_duration = data.get('video_duration', '5')
        
        # Call the FalService to generate the video
        video_url = fal_service.generate_svd_video(
            image_url=edited_s3_url,
            duration=video_duration  # Pass the user-selected duration
        )
        # Note: Currently FalService uses Kling, but method name is still generate_svd_video

        # --- Verwijderde Simulatie --- 
        # logger.info(f"[{task_id}] Simulating video generation using edited image: {data.get('edited_image_url')} and prompt: {data.get('prompt', '')[:50]}...")
        # time.sleep(10)  # Simulate processing time for video generation
        # video_url = f"https://mock-fal-ai.com/video/{task_id}.mp4"
        # thumbnail_url = f"https://mock-fal-ai.com/thumbnail/{task_id}.jpg" # Thumbnail might come from Fal or need separate generation
        # duration = 15 # Duration might come from Fal
        # --- Einde Simulatie --- 

        log_task_success("generate_product_video", task_id)
        logger.info(f"[{task_id}] Fal AI Video generated successfully. URL: {video_url}")

        # Final result structure
        result = {
            'status': 'success',
            'video_url': video_url, # The actual video URL
            'video_generation_id': video_generation_id,
            'message': f"Video generation completed for {data.get('email')}"
            # Include other relevant data if needed by potential final callbacks
        }

        # Update VideoGeneration record with final status and ACTUAL video URL
        VideoGeneration.objects.filter(id=video_generation_id).update(
            status='completed',
            output_video_url=video_url, # Save the actual URL
            error_message=None # Clear any previous errors if retried
            # No finished_at field in the model
        )
        logger.info(f"[{task_id}] Video generation task finished successfully for VideoGeneration {video_generation_id}. Updated status to completed.")

        # Trigger email notification task
        try:
            if data.get('email'): # Only send if email exists
                 logger.info(f"[{task_id}] Triggering email notification task for {video_generation_id} to {data.get('email')}")
                 send_video_ready_email_task.delay(video_generation_id)
            else:
                 logger.info(f"[{task_id}] Skipping email notification for {video_generation_id} as no email address is associated.")
        except Exception as email_err:
            # Log the error but don't fail the main task because of email trigger failure
            log_task_error("generate_product_video", task_id, email_err, msg=f"Failed to trigger email notification task for {video_generation_id}")
        # --- End Email Notification Trigger ---

        # Return success information
        result = {
            'status': 'success',
            'video_url': video_url, # The actual video URL
            'video_generation_id': video_generation_id,
            'message': f"Video generation completed for {data.get('email')}"
            # Include other relevant data if needed by potential final callbacks
        }

        return result

    except FalServiceError as e:
        # Handle specific errors from the Fal service
        error_msg = f"Fal AI video generation failed for {video_generation_id}: {e}"
        log_task_error("generate_product_video", task_id, e, msg=error_msg)
        try:
            VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=f"Fal AI Error: {str(e)[:200]}") # Truncate
        except Exception as db_err:
            logger.error(f"[{task_id}] DB Error updating failed status after FalServiceError for {video_generation_id}: {db_err}")
        # Let the task_error_handler manage retries/failure based on the raised exception
        raise # Reraise FalServiceError

    except Exception as e:
        # Catch any other unexpected errors during video generation
        error_msg = f"Unexpected error during video generation process for {video_generation_id}: {e}"
        log_task_error("generate_product_video", task_id, e, msg=error_msg)
        try:
            VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=f"Unexpected Error: {str(e)[:200]}") # Truncate error message
        except Exception as db_err:
            logger.error(f"[{task_id}] DB Error updating failed status after unexpected video gen exception for {video_generation_id}: {db_err}")
        # Let the task_error_handler manage retries/failure
        raise # Reraise the original exception

# --- Callback Tasks (Internal Implementation Detail) ---

# NB: Callbacks zijn expliciet geregistreerd zodat Celery ze kan vinden.
# Ze halen de originele data op uit de backend en verrijken deze met resultaten
# van de vorige stap voordat ze de volgende taak in de keten aanroepen.

@shared_task(name="core.tasks.continue_with_image_edit", bind=True)
def _continue_with_image_edit_callback(self, prompt_result, parent_task_id):
    """
    Callback taak die wordt uitgevoerd na prompt generatie.
    Start de image editing taak.
    """
    task_id = self.request.id
    log_task_start("_continue_with_image_edit_callback", task_id, {'parent_task_id': parent_task_id})
    logger.info(f"Callback: Continue with image edit. Parent task: {parent_task_id}")
    logger.info(f"Received prompt_result type: {type(prompt_result)}, value: {str(prompt_result)[:200]}...")

    # Retrieve video_generation_id from backend state
    state_key = f"video_gen_data_{parent_task_id}"
    original_data_json = app.backend.client.get(state_key)
    if not original_data_json:
        log_task_error("_continue_with_image_edit_callback", task_id, ValueError("Original data not found in backend"), parent_task_id)
        # Cannot proceed without original data or video_generation_id
        # Try to update status to failed if we have the ID
        raise CeleryTaskError("Critical Error: Original data missing from backend state.")

    try:
        original_data = json.loads(original_data_json)
        video_generation_id = original_data.get('video_generation_id')
        logger.info(f"Retrieved original data from backend for parent {parent_task_id}")
        if not video_generation_id:
             raise ValueError("video_generation_id missing from backend state")

    except (json.JSONDecodeError, ValueError, KeyError) as e:
        log_task_error("_continue_with_image_edit_callback", task_id, e, parent_task_id, msg="Failed to load or parse original data/video_generation_id from backend")
        # Attempt to update status without ID, raise
        raise CeleryTaskError(f"Critical Error: Could not parse backend state - {e}") from e

    # Check prompt generation result
    if not isinstance(prompt_result, dict) or prompt_result.get('status') != 'success':
        error_msg = f"Prompt generation failed: {prompt_result.get('error', 'Unknown error')}"
        log_task_error("_continue_with_image_edit_callback", task_id, ValueError(error_msg), parent_task_id)
        try:
            VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=error_msg)
        except Exception as db_err:
            logger.error(f"[Callback:{task_id}] DB Error updating status to failed after prompt error: {db_err}")
        # Stop the chain here by not calling the next task
        return {'status': 'FAILURE', 'error': error_msg, 'step': 'callback_image_edit_validation'}
        
    # Check if we should skip image editing
    skip_image_editing = original_data.get('skip_image_editing', False)
    if skip_image_editing:
        # Check if the file is a valid image format for direct video generation
        file_url = original_data.get('file_url', '')
        supported_img_ext = ('.png', '.jpg', '.jpeg', '.webp')
        
        if not any(file_url.lower().endswith(ext) for ext in supported_img_ext):
            error_msg = f"Cannot skip image editing for non-image file format: {file_url}. Only PNG, JPG, and WEBP formats are supported for direct video generation."
            logger.error(f"[{task_id}] {error_msg}")
            try:
                VideoGeneration.objects.filter(id=video_generation_id).update(
                    status='failed', 
                    error_message=error_msg[:255]
                )
            except Exception as db_err:
                logger.error(f"[{task_id}] DB Error updating failed status for invalid file type: {db_err}")
            return {'status': 'FAILURE', 'error': error_msg, 'step': 'skip_image_edit_validation'}
            
        logger.info(f"[{task_id}] Skipping image editing as requested by user")
        
        # Prepare data for video generation - using the original image directly
        video_gen_data = {
            **original_data,
            'status': 'success',
            'edited_image_url': original_data.get('file_url'),  # Use the original image URL
            'prompt': prompt_result.get('prompt_text'),  # Still use the prompt for video generation
            'prompt_id': prompt_result.get('prompt_id'),
            'video_generation_id': video_generation_id
        }
        
        try:
            # Update status to video processing directly
            VideoGeneration.objects.filter(id=video_generation_id).update(status='processing_video')
            
            # Skip directly to video generation
            video_task_signature = generate_product_video.s(data=video_gen_data)
            video_task_result = video_task_signature.apply_async()
            
            logger.info(f"[{task_id}] Skip editing: Started video generation task: {video_task_result.id} for orchestrator {parent_task_id}")
            return {'status': 'SUCCESS', 'message': f'Skipped editing and started video generation task {video_task_result.id}'}
        except Exception as e:
            error_msg = f"Failed to start video generation task (skipped editing): {e}"
            log_task_error("_continue_with_image_edit_callback", task_id, e, parent_task_id, msg=error_msg)
            try:
                VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=error_msg)
            except Exception as db_err:
                logger.error(f"[Callback:{task_id}] DB Error updating status to failed after skip error: {db_err}")
            raise CeleryTaskError(error_msg) from e

    # Update status to indicate image editing is next
    try:
        VideoGeneration.objects.filter(id=video_generation_id).update(status='processing') # Or a more specific status like 'processing_image_queue'
    except Exception as e:
        log_task_error("_continue_with_image_edit_callback", task_id, e, parent_task_id, msg="Failed to update VideoGeneration status before image edit")
        # Log it but continue for now
        pass

    # Prepare data for image editing task
    image_edit_data = {
        **original_data, # Pass original data along
        'prompt': prompt_result.get('prompt_text'),
        'prompt_id': prompt_result.get('prompt_id'),
        'video_generation_id': video_generation_id # Pass ID explicitly
    }

    # Trigger the image editing task, linking the video generation callback
    try:
        image_edit_signature = edit_product_image.s(data=image_edit_data, orchestrator_task_id=parent_task_id)
        video_gen_callback_signature = _continue_with_video_generation_callback.s(parent_task_id) # Pass orchestrator ID as positional arg
        # Chain: edit_image -> _continue_with_video_generation_callback
        chain(image_edit_signature | video_gen_callback_signature).apply_async()
        logger.info(f"Chained image edit task -> video generation callback for parent {parent_task_id}")
        log_task_success("_continue_with_image_edit_callback", task_id)
        return {'status': 'SUCCESS', 'message': 'Image editing task queued.'}
    except Exception as e:
        error_msg = f"Failed to chain image editing task: {e}"
        log_task_error("_continue_with_image_edit_callback", task_id, e, parent_task_id)
        try:
            VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=error_msg)
        except Exception as db_err:
            logger.error(f"[Callback:{task_id}] DB Error updating status to failed after chain error: {db_err}")
        raise CeleryTaskError(error_msg) from e


@shared_task(name="core.tasks.continue_with_video_generation", bind=True)
def _continue_with_video_generation_callback(self, image_result, parent_task_id):
    """
    Callback executed after image editing.
    Downloads the edited image, uploads it to S3, updates status,
    and triggers the video generation task with the S3 URL.
    """
    task_id = self.request.id
    log_task_start("_continue_with_video_generation_callback", task_id, {'parent_task_id': parent_task_id})
    logger.info(f"Callback: Continue with video generation. Parent task: {parent_task_id}")
    logger.info(f"Received image_result type: {type(image_result)}, value: {str(image_result)[:200]}...")

    video_generation_id = None
    s3_edited_image_url = None

    try:
        # 1. Retrieve original data and video_generation_id from backend state
        state_key = f'video_gen_data_{parent_task_id}'
        original_data_json = app.backend.client.get(state_key)
        if not original_data_json:
            raise CeleryTaskError(f"Could not retrieve original data from backend key {state_key}")
        
        try:
            original_data = json.loads(original_data_json)
            video_generation_id = original_data.get('video_generation_id')
            if not video_generation_id:
                raise CeleryTaskError(f"video_generation_id not found in backend data for {state_key}")
        except json.JSONDecodeError as e:
            raise CeleryTaskError(f"Failed to parse JSON data from backend: {e}")
            
        logger.info(f"Retrieved original data and video_generation_id {video_generation_id} from backend for parent {parent_task_id}")

        # Ensure image_result is a dictionary
        if not isinstance(image_result, dict):
             # Attempt to handle potential result wrapping (e.g., if coming from a chain)
             if isinstance(image_result, (list, tuple)) and len(image_result) == 1 and isinstance(image_result[0], dict):
                 logger.warning(f"image_result was wrapped in a {type(image_result)}, unwrapping.")
                 image_result = image_result[0]
             else:
                 raise CeleryTaskError(f"Expected image_result to be a dict, but got {type(image_result)}")

        # Check if previous task failed (check within the dict)
        if image_result.get('status') == 'error':
            error_msg = image_result.get('message', 'Image editing failed.')
            logger.error(f"Image editing task failed: {error_msg}. Aborting video generation for {video_generation_id}.")
            VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=f"Image Editing Error: {error_msg[:250]}") # Truncate
            return {'status': 'error', 'message': 'Image editing failed, video generation aborted.'}

        # 2. Get the edited image URL from the result
        openai_edited_image_url = image_result.get('edited_image_url')
        if not openai_edited_image_url:
            raise CeleryTaskError(f"Edited image URL not found in image_result for {video_generation_id}")

        # 3. Download the image from the URL
        logger.info(f"[{task_id}] Downloading edited image for {video_generation_id} from: {openai_edited_image_url}")
        try:
            response = requests.get(openai_edited_image_url, stream=True, timeout=60) # Added timeout
            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            image_content = response.content
            # Attempt to get content type, default to png if not available or invalid
            content_type = response.headers.get('content-type', 'image/png').lower()
            if 'jpeg' in content_type or 'jpg' in content_type:
                extension = 'jpg'
            elif 'png' in content_type:
                extension = 'png'
            elif 'webp' in content_type:
                extension = 'webp'
            else:
                 logger.warning(f"Unknown content type '{content_type}' received from {openai_edited_image_url}, defaulting to .png")
                 extension = 'png' # Default extension

        except requests.exceptions.RequestException as e:
            raise CeleryTaskError(f"Failed to download edited image for {video_generation_id} from {openai_edited_image_url}: {e}") from e

        # 4. Upload the image content to S3/R2 with public access
        # =====================================================
        # SKIP THE UPLOAD AND JUST USE THE OPENAI URL DIRECTLY
        # =====================================================
        logger.info(f"[{task_id}] Using OpenAI image URL directly instead of uploading to R2: {openai_edited_image_url}")
        s3_edited_image_url = openai_edited_image_url
        
        # Add detailed logging
        logger.info(f"[{task_id}] Image URL for Fal.ai: {s3_edited_image_url}")
        
        # Check if OpenAI URL is accessible
        try:
            img_check = requests.head(s3_edited_image_url, timeout=10)
            logger.info(f"[{task_id}] Image URL check status: {img_check.status_code}, headers: {img_check.headers}")
            if img_check.status_code != 200:
                raise CeleryTaskError(f"OpenAI image is not accessible: status {img_check.status_code}")
        except requests.RequestException as e:
            logger.warning(f"[{task_id}] Could not verify OpenAI image URL: {e}")
            # Continue anyway as this is just a check
        except Exception as e:
            # Catch potential S3/storage exceptions
            raise CeleryTaskError(f"Failed to upload edited image for {video_generation_id} to S3: {e}") from e

        # 5. Prepare data for the next task (generate_product_video)
        # Merge original data with results from previous steps, ensuring S3 URL is used
        data_for_next_task = original_data.copy()
        data_for_next_task.update(image_result) # Add results from image editing (like prompt_id if needed)
        data_for_next_task['edited_image_url'] = s3_edited_image_url # IMPORTANT: OVERWRITE with S3 URL
        # Ensure video_generation_id is explicitly passed if not already in original_data
        data_for_next_task['video_generation_id'] = str(video_generation_id) # Ensure it's a string
        data_for_next_task['orchestrator_task_id'] = parent_task_id # Pass along orchestrator ID

        # 6. Update status before queueing next task
        VideoGeneration.objects.filter(id=video_generation_id).update(status='processing_video')
        logger.info(f"Updated VideoGeneration {video_generation_id} status to 'processing_video'")

        # 7. Trigger the final video generation task
        # Pass the enriched data and the original orchestrator ID
        video_task_signature = generate_product_video.s(data=data_for_next_task)
        video_task_result = video_task_signature.apply_async()

        logger.info(f"Started product video generation task: {video_task_result.id} for orchestrator {parent_task_id} (VG_ID: {video_generation_id})")
        log_task_success("_continue_with_video_generation_callback", task_id)
        return {'status': 'SUCCESS', 'message': f'Video generation task {video_task_result.id} queued.'}

    except Exception as e:
        # General exception handler for the callback itself
        error_msg = f"Error in _continue_with_video_generation_callback (Parent task: {parent_task_id}, VG_ID: {video_generation_id}): {e}"
        log_task_error("_continue_with_video_generation_callback", task_id, e, msg=error_msg)
        if video_generation_id:
            try:
                VideoGeneration.objects.filter(id=video_generation_id).update(status='failed', error_message=f"Callback Error: {str(e)[:200]}") # Truncate error message
                logger.info(f"Updated VideoGeneration {video_generation_id} status to 'failed' due to callback exception.")
            except Exception as db_err:
                 logger.error(f"DB Error updating failed status in callback exception handler for VG_ID {video_generation_id}: {db_err}")
        # Reraise the exception to mark the callback task itself as failed in Celery
        raise

# --- Email Notification Task ---

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
@task_error_handler(max_retries=3)
def send_video_ready_email_task(self, video_generation_id):
    """
    Asynchronous task to send an email notification when a video is ready.
    """
    task_id = self.request.id
    log_task_start("send_video_ready_email_task", task_id, {'video_generation_id': video_generation_id})

    try:
        video_gen = VideoGeneration.objects.get(id=video_generation_id)
        
        if not video_gen.email:
            logger.warning(f"[{task_id}] No email address found for VideoGeneration ID {video_generation_id}. Skipping email.")
            log_task_success("send_video_ready_email_task", task_id, result={'status': 'skipped', 'reason': 'No email address'})
            return {'status': 'SKIPPED', 'message': 'No email address provided.'}

        if video_gen.status != 'completed':
            logger.warning(f"[{task_id}] VideoGeneration ID {video_generation_id} status is '{video_gen.status}', not 'completed'. Skipping email.")
            log_task_success("send_video_ready_email_task", task_id, result={'status': 'skipped', 'reason': f'Video not completed (status: {video_gen.status})'})
            return {'status': 'SKIPPED', 'message': 'Video not in completed state.'}
        
        if not video_gen.output_video_url:
             logger.warning(f"[{task_id}] VideoGeneration ID {video_generation_id} is completed but has no output_video_url. Skipping email.")
             log_task_success("send_video_ready_email_task", task_id, result={'status': 'skipped', 'reason': 'Missing output video URL'})
             return {'status': 'SKIPPED', 'message': 'Output video URL missing.'}

        context = {
            'product_title': video_gen.product_title,
            'video_url': video_gen.output_video_url,
        }

        # Render email templates
        text_content = render_to_string('core/emails/video_ready_body.txt', context)
        html_content = render_to_string('core/emails/video_ready_body.html', context)

        # Create email message
        subject = f'Your video for "{video_gen.product_title}" is ready!'
        from_email = settings.DEFAULT_FROM_EMAIL
        to_email = video_gen.email

        msg = EmailMultiAlternatives(subject, text_content, from_email, [to_email])
        msg.attach_alternative(html_content, "text/html")

        # Send the email
        msg.send()
        
        log_task_success("send_video_ready_email_task", task_id)
        return {
            'status': 'success',
            'message': f'Email sent successfully to {to_email}',
            'task_id': task_id
        }

    except VideoGeneration.DoesNotExist as e:
        error_msg = f"Video generation record not found: {video_generation_id}"
        log_task_error("send_video_ready_email_task", task_id, e, msg=error_msg)
        raise CeleryTaskError(error_msg) from e
            
    except SMTPException as e:
        error_msg = f"SMTP error sending email for ID {video_generation_id}: {e}"
        log_task_error("send_video_ready_email_task", task_id, e, msg=error_msg)
        raise CeleryTaskError(error_msg) from e
            
    except Exception as e:
        error_msg = f"Unexpected error sending email for ID {video_generation_id}: {e}"
        log_task_error("send_video_ready_email_task", task_id, e, msg=error_msg)
        raise CeleryTaskError(error_msg) from e

# --- Hoofd Orchestrator Taak ---

@shared_task(bind=True)
@task_error_handler(max_retries=2) # Minder retries voor de orchestrator zelf
def process_complete_video_generation(self, data):
    """
    Orchestrator task that starts the entire video generation pipeline.
    1. Creates a VideoGeneration record.
    2. Stores initial data including video_generation_id in Celery backend.
    3. Starts the prompt generation task, linking the image edit callback.
    """
    orchestrator_task_id = self.request.id
    log_task_start("process_complete_video_generation", orchestrator_task_id, data)

    # 1. Create initial VideoGeneration record
    video_gen = None
    video_gen_id = None
    try:
        video_gen = VideoGeneration.objects.create(
            task_id=orchestrator_task_id, # Store the orchestrator task ID
            email=data.get('email'),
            product_title=data.get('product_title'),
            product_description=data.get('product_description'),
            input_image_url=data.get('file_url'),
            status='pending' # Initial status
        )
        video_gen_id = str(video_gen.id)
        data['video_generation_id'] = video_gen_id # Add real ID to data for backend state
        logger.info(f"Created VideoGeneration record {video_gen_id} for task {orchestrator_task_id}")
    except Exception as e:
        log_task_error("process_complete_video_generation", orchestrator_task_id, e, msg="Failed to create initial VideoGeneration record")
        # This is fatal, cannot proceed without a record ID
        raise CeleryTaskError(f"DB error creating VideoGeneration record: {e}") from e

    # 2. Store initial data (including video_gen_id) in backend for callbacks
    state_key = f"video_gen_data_{orchestrator_task_id}"
    try:
        # Gebruik setex direct op de redis client voor TTL
        app.backend.client.setex(state_key, 3600, json.dumps(data)) # Store for 1 hour
        logger.info(f"Stored initial data in backend with key {state_key}")
    except Exception as e:
        log_task_error("process_complete_video_generation", orchestrator_task_id, e, msg="Failed to store initial data in backend")
        # Attempt to mark the record as failed before raising
        if video_gen_id:
            try:
                 VideoGeneration.objects.filter(id=video_gen_id).update(status='failed', error_message=f"Backend Error: {e}")
            except Exception as db_err:
                 logger.error(f"[{orchestrator_task_id}] DB Error trying to update failed status after backend error: {db_err}")
        raise CeleryTaskError(f"Backend error storing state: {e}") from e

    # 3. Start the first task in the chain: prompt generation
    try:
        prompt_task_signature = generate_prompt_with_openrouter.s(product_data=data, orchestrator_task_id=orchestrator_task_id)
        image_edit_callback_signature = _continue_with_image_edit_callback.s(parent_task_id=orchestrator_task_id)
        # Chain: generate_prompt -> _continue_with_image_edit_callback -> (triggers image_edit -> video_gen_callback -> video_gen -> final_callback)
        chain(prompt_task_signature | image_edit_callback_signature).apply_async()

        logger.info(f"Started pipeline chain: prompt_task -> image_edit_callback for parent {orchestrator_task_id}")
    except Exception as e:
        error_msg = f"Failed to start the Celery chain: {e}"
        log_task_error("process_complete_video_generation", orchestrator_task_id, e, msg=error_msg)
        # Mark record as failed
        if video_gen_id:
            try:
                 VideoGeneration.objects.filter(id=video_gen_id).update(status='failed', error_message=error_msg)
            except Exception as db_err:
                 logger.error(f"[{orchestrator_task_id}] DB Error trying to update failed status after chain start error: {db_err}")
        raise CeleryTaskError(error_msg) from e

    # The orchestrator returns immediately with a pending status
    log_task_success("process_complete_video_generation", orchestrator_task_id)
    return {'status': 'PENDING', 'message': 'Video generation pipeline initiated', 'task_id': orchestrator_task_id, 'video_generation_id': video_gen_id} # Return ID for immediate use by frontend if needed
