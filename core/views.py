from django.shortcuts import render, redirect
from django.urls import reverse
from .forms import ProductVideoForm
from .tasks import process_complete_video_generation
from .models import VideoGeneration, IPUsage
from storages.backends.s3boto3 import S3Boto3Storage
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from celery.result import AsyncResult
import uuid
import os
import logging
from PIL import Image  # Import Pillow
from django.conf import settings # Import settings

logger = logging.getLogger(__name__)

# --- Constants for Image Validation ---
MIN_IMAGE_WIDTH = 300
MIN_IMAGE_HEIGHT = 300
MAX_IMAGE_SIZE_MB = 10 # Example limit: 10MB

# Create your views here.
def index_view(request):
    context = {}
    last_video_generation_id = request.session.get('last_video_generation_id') # Get from session first

    if request.method == 'POST':
        form = ProductVideoForm(request.POST, request.FILES)
        if form.is_valid():
            # --- Rate Limiting Check ---
            ip_address = request.client_ip
            if not ip_address:
                 logger.warning("Could not determine client IP address for rate limiting.")
                 # Decide how to handle this - maybe allow it, maybe block it?
                 # For now, let's add an error and block.
                 form.add_error(None, "Could not verify your request origin. Please try again.")
                 context['form'] = form
                 return render(request, 'core/index.html', context)

            usage_count = IPUsage.get_usage_count(ip_address)
            if usage_count >= 1: # Limit is 1 free video
                logger.info(f"Rate limit exceeded for IP: {ip_address} (Usage: {usage_count})")
                form.add_error(None, "You have already used your free video generation trial.")
                context['form'] = form
                # Render the full page with the error, not just a partial
                return render(request, 'core/index.html', context)
            # --- End Rate Limiting Check ---

            # --- Image Validation ---
            uploaded_file = request.FILES['product_photo']

            # 1. Check file size
            if uploaded_file.size > MAX_IMAGE_SIZE_MB * 1024 * 1024:
                 logger.warning(f"Uploaded file size {uploaded_file.size} bytes exceeds limit of {MAX_IMAGE_SIZE_MB}MB.")
                 form.add_error('product_photo', f"Image file size cannot exceed {MAX_IMAGE_SIZE_MB}MB.")
                 return render(request, 'core/index.html', {'form': form})

            # 2. Check image dimensions using Pillow
            try:
                 img = Image.open(uploaded_file)
                 width, height = img.size
                 img.verify() # Verify image integrity
                 # Re-open after verify: https://pillow.readthedocs.io/en/stable/reference/Image.html#PIL.Image.Image.verify
                 img = Image.open(uploaded_file)
                 width, height = img.size # Get dimensions again

                 if width < MIN_IMAGE_WIDTH or height < MIN_IMAGE_HEIGHT:
                     logger.warning(f"Uploaded image dimensions {width}x{height} are below minimum requirement {MIN_IMAGE_WIDTH}x{MIN_IMAGE_HEIGHT}.")
                     form.add_error('product_photo', f"Image dimensions must be at least {MIN_IMAGE_WIDTH}x{MIN_IMAGE_HEIGHT} pixels.")
                     return render(request, 'core/index.html', {'form': form})

            except Exception as e: # Catch potential Pillow errors (corrupt file, etc.)
                 logger.error(f"Error validating image: {e}", exc_info=True)
                 form.add_error('product_photo', "Could not process the image file. Please ensure it's a valid image (JPG, PNG, WEBP).")
                 return render(request, 'core/index.html', {'form': form})
            finally:
                # Ensure the file pointer is reset if Pillow moved it
                uploaded_file.seek(0)

            uploaded_file = form.cleaned_data['product_photo']
            email = form.cleaned_data['email']
            title = form.cleaned_data['product_title']
            description = form.cleaned_data['product_description']
            video_duration = form.cleaned_data['video_duration']  # Get the selected duration
            skip_image_editing = form.cleaned_data['skip_image_editing']  # Get skip image editing preference

            # Get file extension and validate format
            file_extension = os.path.splitext(uploaded_file.name)[1].lower()
            
            # If user selected skip_image_editing, validate file type is supported by Fal.ai
            if skip_image_editing and file_extension not in ('.png', '.jpg', '.jpeg', '.webp'):
                form.add_error('skip_image_editing', 
                               f"Cannot skip image editing for {file_extension} files. "
                               "Only PNG, JPG, and WEBP formats are supported for direct video generation. "
                               "Uncheck 'Skip Image Editing' to process this file type.")
                context['form'] = form
                return render(request, 'core/index.html', context)
                
            unique_filename = f"uploads/{uuid.uuid4()}{file_extension}"

            try:
                storage = S3Boto3Storage()
                file_url = storage.save(unique_filename, uploaded_file)
                file_access_url = storage.url(file_url)
                logger.info(f"File uploaded to {file_access_url}")

                task_data = {
                    'email': email,
                    'product_title': title,
                    'product_description': description,
                    'file_url': file_access_url,
                    'video_duration': video_duration, # Pass duration to task
                    'skip_image_editing': skip_image_editing, # Pass skip editing preference
                    'requesting_ip': ip_address # Pass IP for potential future use/logging in task
                }

                task_result = process_complete_video_generation.apply_async(args=[task_data])
                orchestrator_task_id = task_result.id

                # --- Record Usage AFTER successful task dispatch ---
                try:
                    IPUsage.record_usage(ip_address)
                    logger.info(f"Recorded usage for IP: {ip_address}. New count: {usage_count + 1}")
                except Exception as e:
                    # Log error, but maybe don't fail the whole request?
                    # If recording fails, the user might get another free try, which isn't ideal but not catastrophic.
                    logger.error(f"Failed to record IP usage for {ip_address}: {e}", exc_info=True)
                # --- End Record Usage ---

                try:
                    result_meta = task_result.get(timeout=5)
                    last_video_generation_id = result_meta.get('video_generation_id')
                    logger.info(f"Orchestrator task {orchestrator_task_id} started. Video Generation ID: {last_video_generation_id}")

                    # Store in session *after* confirming the task started and usage recorded
                    request.session['last_video_generation_id'] = last_video_generation_id

                    context = {
                        'form': form, 
                        'video_generation_id': last_video_generation_id,
                        'message': 'Video generation started successfully!'
                    }
                    html_response = render_to_string('core/partials/video_status_wrapper.html', context)
                    return HttpResponse(html_response)

                except TimeoutError:
                    logger.error(f"Timeout waiting for orchestrator task {orchestrator_task_id} result to get video_generation_id.")
                    form.add_error(None, "Failed to initiate video generation process properly.")
                except Exception as e:
                     logger.error(f"Error getting result from orchestrator task {orchestrator_task_id}: {e}", exc_info=True)
                     form.add_error(None, f"An unexpected error occurred: {e}")

            except Exception as e:
                logger.error(f"Error during file upload or task start: {e}", exc_info=True)
                form.add_error(None, f"An error occurred: {e}")

        context['form'] = form
        return render(request, 'core/index.html', context)

    else:
        form = ProductVideoForm()
        last_video_generation_id = request.session.get('last_video_generation_id')
        
        # Get recent successful video generations to display as showcase examples
        try:
            recent_videos = VideoGeneration.objects.filter(
                status='completed',
                output_video_url__isnull=False
            ).order_by('-id')[:5]  # Get 5 most recent completed videos
        except Exception as e:
            logger.error(f"Error fetching recent videos: {e}")
            recent_videos = []

    context = {
        'form': form, 
        'last_video_generation_id': last_video_generation_id,
        'recent_videos': recent_videos
    }
    return render(request, 'core/index.html', context)


def check_video_status(request, video_generation_id):
    try:
        video_gen = VideoGeneration.objects.get(id=video_generation_id)
        
        context = {
            'video_gen': video_gen,
            'stop_polling': False  # Default to continue polling
        }

        # Check for terminal states
        if video_gen.status in ['completed', 'failed']:
            context['stop_polling'] = True

        html = render_to_string('core/partials/video_status.html', context)
        response = HttpResponse(html)

        # --- Optional: Add HX-Stop-Polling header --- 
        # This is a more direct way to tell HTMX to stop.
        # The template logic will handle the visual update and removal of polling attributes.
        # Uncomment the following lines if you want to use this header approach
        # in addition to (or instead of) the template logic.
        # if context['stop_polling']:
        #    response['HX-Reswap'] = 'none' # Prevents swapping content if already done
        #    response['HX-Retarget'] = 'none' # Prevents retargeting if already done
        #    # Setting HX-Trigger directly to 'none' might be better
        #    response['HX-Trigger'] = 'none'
        # --- End Optional Header --- 
        
        return response

    except VideoGeneration.DoesNotExist:
        logger.warning(f"VideoGeneration record not found for ID: {video_generation_id}")
        return HttpResponse(f"<p>Error: Could not find video generation request {video_generation_id}.</p>", status=404)
    except Exception as e:
        logger.error(f"Error checking video status for ID {video_generation_id}: {e}", exc_info=True)
        return HttpResponse("<p>An error occurred while checking the status.</p>", status=500)


def task_status_view(request, task_id):
    task_id_str = str(task_id)
    result = AsyncResult(task_id_str)
    state = result.state
    
    task_result = None
    result_info = ""
    prompt_was_reused = False
    if state == 'SUCCESS':
        try:
            task_result = result.get()
            if isinstance(task_result, dict) and task_result.get('status') == 'failed':
                state = 'FAILURE'
                result_info = f": {task_result.get('error', 'Unknown error')}"
            elif isinstance(task_result, dict) and task_result.get('status') == 'success':
                product_title = task_result.get('product_title', '')
                prompt_was_reused = task_result.get('prompt_was_reused', False)
                
                if product_title:
                    result_info = f": Video for '{product_title}' is ready!"
            elif isinstance(task_result, dict):
                product_title = task_result.get('product_title', '')
                if product_title:
                    result_info = f": Task for '{product_title}' completed!"
            elif isinstance(task_result, str):
                result_info = f": {task_result}"
            else:
                logger.info(f"Task {task_id} completed with result type: {type(task_result)}")
                result_info = ": Task completed successfully"
                
            if isinstance(task_result, dict) and 'product_title' in task_result:
                product_title = task_result.get('product_title', '')
                if product_title:
                    result_info = f": Video for '{product_title}' is ready!"
        except Exception as e:
            logger.error(f"Error retrieving task result: {e}", exc_info=True)
            state = 'FAILURE'
            result_info = ": Error retrieving result"
    
    status_map = {
        'PENDING': 'Processing...',
        'RECEIVED': 'Processing...',
        'STARTED': 'Generating...',
        'SUCCESS': f'Done{result_info}',
        'FAILURE': f'Failed{result_info}',
    }
    css_map = {
        'PENDING': 'text-blue-600',
        'RECEIVED': 'text-blue-600',
        'STARTED': 'text-blue-600',
        'SUCCESS': 'text-green-600',
        'FAILURE': 'text-red-600',
    }
    message = status_map.get(state, state)
    css_class = css_map.get(state, 'text-gray-600')

    terminal_states = {'SUCCESS', 'FAILURE'}
    if state in terminal_states:
        hx_attrs = ''
    else:
        poll_url = reverse('core:task_status', args=[task_id])
        hx_attrs = (
            f'hx-get="{poll_url}" ' \
            'hx-trigger="load, every 2s" ' \
            'hx-swap="outerHTML"'
        )

    prompt_status_badge = ""
    if state == 'SUCCESS' and isinstance(task_result, dict) and prompt_was_reused is not None:
        if prompt_was_reused:
            prompt_status_badge = '<span class="ml-2 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">Existing Prompt Reused</span>'
        else:
            prompt_status_badge = '<span class="ml-2 inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">New Prompt Generated</span>'

    html = (
        f'<div id="generation-status" class="mt-4 {css_class} font-semibold" {hx_attrs}>'
        f'{message}{prompt_status_badge}'
        '</div>'
    )
    return HttpResponse(html)
