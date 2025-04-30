from django.shortcuts import render, redirect
from django.urls import reverse
from .forms import ProductVideoForm
from .tasks import generate_product_video
from storages.backends.s3boto3 import S3Boto3Storage
from django.http import HttpResponse
from celery.result import AsyncResult
import uuid
import os

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

                # Temporary store upload data in session
                # Ensure session key is unique if multiple requests can happen
                session_key = f'video_request_{uuid.uuid4()}'
                # Let's simplify what we store for now, just pass necessary data to task
                task_data = {
                    'file_url': file_url, # Store the generated R2 URL
                    'file_name': file_name, # Store the R2 object name
                    'email': email,
                    'title': title,
                    'description': description,
                }
                # request.session[session_key] = task_data # Storing in session might not be needed if task handles all

                # Trigger Celery task asynchronously
                task = generate_product_video.delay(task_data)
                print(f"Triggered Celery task {task.id} for {email}")

                # Store task_id in session to potentially check status later
                request.session['last_task_id'] = task.id 

                # Redirect to a success page or back to the form
                # For now, redirect back to the index page
                return redirect(reverse('core:index'))

            except Exception as e:
                print(f"Error uploading file: {e}")
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
    status_map = {
        'PENDING': 'Processing...',
        'RECEIVED': 'Processing...',
        'STARTED': 'Generating...',
        'SUCCESS': 'Done!',
        'FAILURE': 'Failed!',
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

    html = (
        f'<div id="generation-status" class="mt-4 {css_class} font-semibold" {hx_attrs}>'
        f'{message}'
        '</div>'
    )
    return HttpResponse(html)
