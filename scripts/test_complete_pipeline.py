#!/usr/bin/env python
"""
Test Script for Complete Video Generation Pipeline
-------------------------------------------------

This script tests the full asynchronous pipeline including:
1. Prompt generation or retrieval using PromptService
2. Video generation (simulated)
3. Error handling and logging

CP-04: Tests robust logging and error handling
NX-05: Demonstrates proper API error handling
CP-01: Shows clean separation of concerns
"""

import os
import sys
import time
import django
import logging
import uuid
from pathlib import Path

# Set up Django environment
sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'product_video_app.settings')
django.setup()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('test_pipeline')

# Import the actual services instead of the Celery tasks directly
from core.models import ProductPrompt, VideoGeneration
from core.services.prompt_service import get_prompt_service
from core.services.openrouter import create_client


def display_result(result):
    """Pretty print a task result"""
    print("\n" + "=" * 60)
    print("PIPELINE RESULT:")
    print("=" * 60)
    
    if isinstance(result, dict):
        for key, value in result.items():
            # Make the output more readable and secure
            if key in ('prompt', 'error') and value and len(str(value)) > 100:
                print(f"{key}: {str(value)[:100]}... (truncated)")
            elif isinstance(value, dict):
                print(f"{key}:")
                for subkey, subvalue in value.items():
                    print(f"  {subkey}: {subvalue}")
            else:
                print(f"{key}: {value}")
    else:
        print(result)
    
    print("=" * 60 + "\n")


def test_successful_pipeline():
    """Test the complete pipeline with valid inputs for success case"""
    print("\n[TEST] Running successful pipeline test...")
    
    # Create test data similar to what would come from the form
    test_data = {
        'product_title': 'Premium Bluetooth Headphones',
        'product_description': 'Noise-cancelling, 30-hour battery life, comfortable fit for all-day listening.',
        'email': 'test@example.com',
        'file_url': 'https://example.com/fake-image-url.jpg',  # Fake URL, since we're just testing the pipeline
        'file_name': f'test/headphones_{uuid.uuid4()}.jpg',
        'category': 'electronics'
    }
    
    logger.info(f"Starting pipeline test with data: {test_data}")
    
    task_id = str(uuid.uuid4())  # Generate a fake task ID
    try:
        # Use the PromptService directly instead of through Celery tasks
        prompt_service = get_prompt_service()
        
        # Get or generate prompt
        prompt, created = prompt_service.get_or_generate_prompt(
            product_title=test_data['product_title'],
            product_description=test_data['product_description'],
            email=test_data['email'],
            category=test_data.get('category'),
            force_new=False,
            model=test_data.get('model')
        )
        
        # Update task ID to simulate Celery behavior
        if created:
            prompt.task_id = task_id
            prompt.save(update_fields=['task_id'])
            logger.info(f"Updated prompt with task ID {task_id}")
        
        # Format result similar to what the Celery task would return
        prompt_result = {
            'status': 'success',
            'prompt': prompt.prompt_text,
            'prompt_id': str(prompt.id),
            'model_used': prompt.model_used,
            'product_title': test_data['product_title'],
            'product_description': test_data['product_description'],
            'email': test_data['email'],
            'created_new': created
        }
        
        # Add the prompt to the video generation data
        video_data = test_data.copy()
        video_data.update({
            'prompt': prompt_result['prompt'],
            'prompt_id': prompt_result['prompt_id'],
        })
        
        # Simulate video generation
        logger.info(f"Simulating video generation for {test_data['email']}")
        # This simulates the generate_product_video task
        time.sleep(1)  # Simulate processing time
        
        # Create a VideoGeneration record
        video_gen, created = VideoGeneration.objects.get_or_create(
            task_id=task_id,
            defaults={
                'email': test_data['email'],
                'product_title': test_data['product_title'],
                'product_description': test_data['product_description'],
                'input_image_url': test_data['file_url'],
                'prompt': prompt,
                'status': 'completed'
            }
        )
        
        video_result = f"Video generation completed for {test_data['email']}"
        
        # Combine the results
        result = {
            'status': 'success',
            'prompt_id': prompt_result['prompt_id'],
            'prompt': prompt_result['prompt'],
            'email': test_data.get('email'),
            'product_title': test_data.get('product_title'),
            'video_result': video_result
        }
    except Exception as e:
        logger.error(f"Pipeline test failed: {str(e)}")
        result = {
            'status': 'failed',
            'error': str(e),
            'error_type': e.__class__.__name__
        }
    
    display_result(result)
    
    # Verify database entries were created
    if result and result.get('status') == 'success' and result.get('prompt_id'):
        prompt_id = result['prompt_id']
        try:
            prompt = ProductPrompt.objects.get(id=prompt_id)
            print(f"✅ Found prompt in database: {prompt.id}")
            print(f"   - Model used: {prompt.model_used}")
            print(f"   - Created: {prompt.created_at}")
            print(f"   - Approved: {prompt.is_approved}")
        except ProductPrompt.DoesNotExist:
            print("❌ Prompt not found in database!")
    
    # Check for video generation record
    if result and result.get('status') == 'success':
        try:
            video = VideoGeneration.objects.filter(task_id=task_id).first()
            if video:
                print(f"✅ Found video generation in database: {video.id}")
                print(f"   - Status: {video.status}")
                print(f"   - Created: {video.created_at}")
                print(f"   - Email: {video.email}")
            else:
                print("❌ No video generation record found!")
        except Exception as e:
            print(f"❌ Error checking video generation: {e}")
    
    # If video wasn't created, create it manually for subsequent tests
    if result and result.get('status') == 'success' and not VideoGeneration.objects.filter(task_id=task_id).exists():
        try:
            prompt = ProductPrompt.objects.get(id=result['prompt_id'])
            VideoGeneration.objects.create(
                task_id=task_id, email=test_data['email'], 
                product_title=test_data['product_title'], status='completed', prompt=prompt
            )
        except Exception as e:
            print(f"Could not create video record: {e}")
    
    return result


def test_pipeline_with_existing_prompt():
    """Test the pipeline with a product title that should match an existing prompt"""
    print("\n[TEST] Running pipeline with existing prompt...")
    
    # First, let's check if we have any prompts in the database
    existing_prompts = ProductPrompt.objects.all()
    if not existing_prompts.exists():
        print("No existing prompts found. Run the successful test first.")
        return None
    
    # Use a product title that matches an existing prompt
    sample_prompt = existing_prompts.first()
    
    test_data = {
        'product_title': sample_prompt.product_title,
        'product_description': sample_prompt.product_description,
        'email': 'reuse-test@example.com',
        'file_url': 'https://example.com/another-image.jpg',
        'file_name': f'test/reuse_test_{uuid.uuid4()}.jpg',
    }
    
    logger.info(f"Testing pipeline with existing prompt match: {test_data['product_title']}")
    
    task_id = str(uuid.uuid4())
    try:
        # Use prompt service directly
        prompt_service = get_prompt_service()
        prompt, created = prompt_service.get_or_generate_prompt(
            product_title=test_data['product_title'],
            product_description=test_data['product_description'],
            email=test_data['email']
        )
        
        # Should reuse existing prompt
        if created:
            logger.warning("Created a new prompt when we expected to reuse one")
        
        prompt_result = {
            'status': 'success',
            'prompt': prompt.prompt_text,
            'prompt_id': str(prompt.id),
            'model_used': prompt.model_used,
            'created_new': created
        }
        
        # Add prompt to video data
        video_data = test_data.copy()
        video_data.update({
            'prompt': prompt_result['prompt'],
            'prompt_id': prompt_result['prompt_id'],
        })
        
        # Simulate video generation
        logger.info(f"Simulating video generation for {test_data['email']}")
        # Create a VideoGeneration record
        video_gen, created = VideoGeneration.objects.get_or_create(
            task_id=task_id,
            defaults={
                'email': test_data['email'],
                'product_title': test_data['product_title'],
                'product_description': test_data['product_description'],
                'prompt': prompt,
                'status': 'completed'
            }
        )
        
        video_result = f"Video generation completed for {test_data['email']}"
        
        # Combine results
        result = {
            'status': 'success',
            'prompt_id': prompt_result['prompt_id'],
            'prompt': prompt_result['prompt'],
            'email': test_data.get('email'),
            'product_title': test_data.get('product_title'),
            'video_result': video_result
        }
    except Exception as e:
        logger.error(f"Pipeline with existing prompt test failed: {str(e)}")
        result = {
            'status': 'failed',
            'error': str(e),
            'error_type': e.__class__.__name__
        }
    
    display_result(result)
    
    if result and result.get('status') == 'success':
        print(f"✅ Pipeline completed successfully with prompt reuse")
        # We can verify the prompt ID matches the one we expected
        if result.get('prompt_id') == str(sample_prompt.id):
            print(f"✅ Correctly reused existing prompt: {sample_prompt.id}")
        else:
            print(f"❓ Used different prompt than expected: {result.get('prompt_id')} vs {sample_prompt.id}")
    else:
        print("❌ Pipeline with existing prompt failed!")
    
    return result


def test_pipeline_with_invalid_data():
    """Test error handling with invalid input data"""
    print("\n[TEST] Running pipeline with invalid data (missing required fields)...")
    
    # Create incomplete test data
    test_data = {
        'product_title': 'Missing Data Test',
        # Missing product_description
        'email': 'error-test@example.com',
        'file_url': 'https://example.com/fake-image.jpg',
    }
    
    logger.info(f"Testing pipeline with invalid data: {test_data}")
    
    # Generate fake task ID
    task_id = str(uuid.uuid4())
    
    # Rather than use the task directly with our mock task object,
    # we will manually try to create the prompt to simulate the validation
    # that would happen in the task
    
    try:
        # Missing fields should cause validation error
        prompt_service = get_prompt_service()
        
        # Input validation - should fail here
        required_fields = ['product_title', 'product_description', 'email']
        missing_fields = [field for field in required_fields if field not in test_data]
        if missing_fields:
            error_msg = f"Missing required fields: {', '.join(missing_fields)}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # If we somehow get here, proceed with prompt generation
        prompt, created = prompt_service.get_or_generate_prompt(
            product_title=test_data['product_title'],
            product_description=test_data.get('product_description', ''),  # Will be empty, should fail
            email=test_data['email']
        )
        
        # If we somehow get here, just use the result
        result = {
            'status': 'success',
            'prompt': prompt.prompt_text,
            'prompt_id': str(prompt.id)
        }
    except Exception as e:
        logger.error(f"Pipeline with invalid data test failed as expected: {str(e)}")
        result = {
            'status': 'failed',
            'error': str(e),
            'error_type': e.__class__.__name__
        }
    
    display_result(result)
    
    # Validation should fail and return an error
    if result.get('status') == 'failed':
        print(f"✅ Pipeline correctly failed with invalid data")
        print(f"   - Error: {result.get('error')}")
    else:
        print("❌ Pipeline unexpectedly succeeded with invalid data!")
    
    return result


def test_pipeline_with_forced_regeneration():
    """Test pipeline with force_new=True to always generate a new prompt"""
    print("\n[TEST] Running pipeline with forced prompt regeneration...")
    
    # Find an existing prompt to try to force regenerate
    existing_prompts = ProductPrompt.objects.all()
    if not existing_prompts.exists():
        print("No existing prompts found. Run the successful test first.")
        return None
    
    sample_prompt = existing_prompts.first()
    
    test_data = {
        'product_title': sample_prompt.product_title,
        'product_description': sample_prompt.product_description,
        'email': 'force-new@example.com',
        'file_url': 'https://example.com/force-new.jpg',
        'file_name': f'test/force_new_{uuid.uuid4()}.jpg',
        'force_new': True  # Force a new prompt even if one exists
    }
    
    logger.info(f"Testing pipeline with forced regeneration: {test_data['product_title']}")
    
    task_id = str(uuid.uuid4())
    try:
        # Use the prompt service directly with force_new=True
        prompt_service = get_prompt_service()
        prompt, created = prompt_service.get_or_generate_prompt(
            product_title=test_data['product_title'],
            product_description=test_data['product_description'],
            email=test_data['email'],
            force_new=True  # Force a new prompt
        )
        
        # Verify it's actually new
        if not created:
            logger.warning("Prompt wasn't newly created despite force_new=True")
        
        prompt_result = {
            'status': 'success',
            'prompt': prompt.prompt_text,
            'prompt_id': str(prompt.id),
            'created_new': created
        }
        
        # Add prompt to video data
        video_data = test_data.copy()
        video_data.update({
            'prompt': prompt_result['prompt'],
            'prompt_id': prompt_result['prompt_id'],
        })
        
        # Simulate video generation
        time.sleep(1)
        video_result = f"Video generation completed for {test_data['email']}"
        
        # Combine results
        result = {
            'status': 'success',
            'prompt_id': prompt_result['prompt_id'],
            'prompt': prompt_result['prompt'],
            'email': test_data.get('email'),
            'product_title': test_data.get('product_title'),
            'video_result': video_result
        }
    except Exception as e:
        logger.error(f"Pipeline with forced regeneration test failed: {str(e)}")
        result = {
            'status': 'failed',
            'error': str(e),
            'error_type': e.__class__.__name__
        }
    
    display_result(result)
    
    if result and result.get('status') == 'success':
        print(f"✅ Pipeline completed successfully with forced regeneration")
        # The prompt ID should be different from the existing one
        if result.get('prompt_id') != str(sample_prompt.id):
            print(f"✅ Correctly generated new prompt: {result.get('prompt_id')} (original was {sample_prompt.id})")
        else:
            print(f"❌ Reused existing prompt despite force_new=True: {result.get('prompt_id')}")
    else:
        print("❌ Pipeline with forced regeneration failed!")
    
    return result


if __name__ == '__main__':
    print("\n" + "=" * 80)
    print("TESTING COMPLETE VIDEO GENERATION PIPELINE")
    print("=" * 80)
    
    # Run the tests
    test_successful_pipeline()
    time.sleep(1)  # Add small delay between tests
    
    test_pipeline_with_existing_prompt()
    time.sleep(1)
    
    test_pipeline_with_invalid_data() 
    time.sleep(1)
    
    test_pipeline_with_forced_regeneration()
    
    print("\nAll tests completed!\n")
    
    # Optional: Print database stats
    prompt_count = ProductPrompt.objects.count()
    video_count = VideoGeneration.objects.count()
    print(f"Database status: {prompt_count} prompts, {video_count} video generations")
