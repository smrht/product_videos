from django.shortcuts import render, redirect
from django.urls import reverse
from .forms import ProductVideoForm
from .tasks import process_complete_video_generation
from storages.backends.s3boto3 import S3Boto3Storage
from django.http import HttpResponse
from celery.result import AsyncResult
import uuid
import os
import logging

logger = logging.getLogger(__name__)

# Create your views here.
def index_view(request):
    if request.method == 'POST':
        form = ProductVideoForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded_file = form.cleaned_data['product_photo']
            email = form.cleaned_data['email']
            title = form.cleaned_data['product_title']
            description = form.cleaned_data['product_description']

            # Generate a unique filename
            file_extension = os.path.splitext(uploaded_file.name)[1]
            unique_filename = f"uploads/{uuid.uuid4()}{file_extension}"

            try:
                logger.info(f"Processing upload for {email} with title: {title}")
                # Explicitly instantiate and use S3Boto3Storage
                storage = S3Boto3Storage()
                
                # Save the file using the specific storage backend (R2)
                file_name = storage.save(unique_filename, uploaded_file)
                
                file_url = storage.url(file_name)

                print(f"File uploaded successfully for {email}!")
                print(f"Title: {title}")
                print(f"Description: {description}")
                print(f"File saved as: {file_name}")
                print(f"Accessible at URL: {file_url}")
                logger.info(f"File uploaded successfully to {file_name}")

                # Temporary store upload data in session
                # Ensure session key is unique if multiple requests can happen
                session_key = f'video_request_{uuid.uuid4()}'
                # Let's simplify what we store for now, just pass necessary data to task
                task_data = {
                    'file_url': file_url, # Store the generated R2 URL
                    'file_name': file_name, # Store the R2 object name
                    'email': email,
                    'product_title': title,  # Use the same key names expected by prompt service
                    'product_description': description,
                    'category': form.cleaned_data.get('category', None),
                }
                # request.session[session_key] = task_data # Storing in session might not be needed if task handles all

                # Trigger Celery task asynchronously
                # Use the new orchestrator task that handles the entire pipeline
                task = process_complete_video_generation.delay(task_data)
                logger.info(f"Triggered complete video generation pipeline task {task.id} for {email}")

                # Store task_id in session to potentially check status later
                request.session['last_task_id'] = task.id 

                # Redirect to a success page or back to the form
                # For now, redirect back to the index page
                return redirect(reverse('core:index'))

            except Exception as e:
                logger.error(f"Error uploading file: {e}", exc_info=True)
                # Add error handling, maybe add form error
                form.add_error(None, f"An error occurred during file upload: {e}")

    else: # GET request
        form = ProductVideoForm()

    last_task_id = request.session.get('last_task_id')
    context = {'form': form, 'last_task_id': last_task_id}
    return render(request, 'core/index.html', context)

def task_status_view(request, task_id):
    """Return HTMX snippet with Celery task status."""
    # Convert UUID or other types to string as Celery expects a string task ID
    task_id_str = str(task_id)
    result = AsyncResult(task_id_str)
    state = result.state
    
    # Get the task result if available
    task_result = None
    result_info = ""
    prompt_was_reused = False
    if state == 'SUCCESS':
        try:
            task_result = result.get()
            # Handle different types of task_result (dict, string, etc.)
            if isinstance(task_result, dict) and task_result.get('status') == 'failed':
                # For failed tasks with dict response
                state = 'FAILURE'  # Override the state for better UI feedback
                result_info = f": {task_result.get('error', 'Unknown error')}"
            elif isinstance(task_result, dict) and task_result.get('status') == 'success':
                # For successful tasks with dict response and status field
                # Add some basic info about the successful task
                product_title = task_result.get('product_title', '')
                # Check if prompt was reused for UI feedback
                prompt_was_reused = task_result.get('prompt_was_reused', False)
                
                if product_title:
                    result_info = f": Video for '{product_title}' is ready!"
            elif isinstance(task_result, dict):
                # For dict responses without status field
                # Just use generic success message
                product_title = task_result.get('product_title', '')
                if product_title:
                    result_info = f": Task for '{product_title}' completed!"
            elif isinstance(task_result, str):
                # For string responses, use the result as info
                result_info = f": {task_result}"
            else:
                # For other response types
                logger.info(f"Task {task_id} completed with result type: {type(task_result)}")
                result_info = ": Task completed successfully"
                
            # If there's a product_title directly in task_result
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

    # Continue polling until task reaches a terminal state
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

    # Add badge for prompt reuse status if task is successful
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
