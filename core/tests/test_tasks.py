"""
Unit tests for Celery tasks.

Tests the functionality of:
- Task error handling
- generate_prompt_with_openrouter task
- generate_product_video task
- process_complete_video_generation orchestration task

TS-05: Implements unit tests with pytest following best practices
CP-01: Tests clean code structure and separation of concerns
NX-06: Validates error boundary handling
"""

import pytest
import uuid
from unittest.mock import patch, MagicMock, call

from django.test import TestCase
from celery.exceptions import Retry

from core.tasks import (
    generate_prompt_with_openrouter,
    generate_product_video,
    process_complete_video_generation
)
from core.models import ProductPrompt, VideoGeneration
from core.utils.error_handlers import task_error_handler, CeleryTaskError


@pytest.mark.django_db
class TestPromptGenerationTask:
    """Test suite for the generate_prompt_with_openrouter task."""
    
    @patch('core.tasks.get_prompt_service')
    def test_successful_prompt_generation(self, mock_get_service):
        """Test successful execution of prompt generation task."""
        # Setup
        mock_service = MagicMock()
        mock_prompt = MagicMock()
        mock_prompt.id = str(uuid.uuid4())
        mock_prompt.prompt_text = "This is a test prompt"
        mock_prompt.model_used = "test-model"
        
        # Configure the mock service to return our mock prompt
        mock_service.get_or_generate_prompt.return_value = (mock_prompt, True)
        mock_get_service.return_value = mock_service
        
        # Create a mock task object for self
        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        
        # Test data
        test_data = {
            'product_title': 'Test Product',
            'product_description': 'Test description',
            'email': 'test@example.com'
        }
        
        # Act
        result = generate_prompt_with_openrouter(mock_task, test_data)
        
        # Assert
        assert result['status'] == 'success'
        assert result['prompt'] == mock_prompt.prompt_text
        assert result['prompt_id'] == str(mock_prompt.id)
        assert result['created_new'] is True
        
        # Verify service was called with correct parameters
        mock_service.get_or_generate_prompt.assert_called_once_with(
            product_title='Test Product',
            product_description='Test description',
            email='test@example.com',
            category=None,
            force_new=False,
            model=None
        )
    
    @patch('core.tasks.get_prompt_service')
    def test_missing_required_fields(self, mock_get_service):
        """Test validation of required fields."""
        # Setup
        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        
        # Missing product_description
        test_data = {
            'product_title': 'Test Product',
            # Missing description
            'email': 'test@example.com'
        }
        
        # Act
        result = generate_prompt_with_openrouter(mock_task, test_data)
        
        # Assert
        assert result['status'] == 'failed'
        assert 'error' in result
        assert 'Missing required fields' in result['error']
        assert 'product_description' in result['error']
        
        # Service should not be called
        mock_get_service.assert_not_called()
    
    @patch('core.tasks.get_prompt_service')
    def test_error_handling(self, mock_get_service):
        """Test error handling in prompt generation task."""
        # Setup - make service raise an exception
        mock_service = MagicMock()
        mock_service.get_or_generate_prompt.side_effect = ValueError("Test error")
        mock_get_service.return_value = mock_service
        
        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        mock_task.retry = MagicMock(side_effect=Retry)
        
        test_data = {
            'product_title': 'Test Product',
            'product_description': 'Test description',
            'email': 'test@example.com'
        }
        
        # Act
        result = generate_prompt_with_openrouter(mock_task, test_data)
        
        # Assert
        assert result['status'] == 'failed'
        assert 'error' in result
        assert 'Test error' in result['error']


@pytest.mark.django_db
class TestVideoGenerationTask:
    """Test suite for the generate_product_video task."""
    
    @patch('core.tasks.ProductPrompt.objects.get')
    def test_successful_video_generation(self, mock_get_prompt):
        """Test successful execution of video generation task."""
        # Setup
        mock_prompt = MagicMock()
        mock_prompt.id = str(uuid.uuid4())
        mock_prompt.prompt_text = "This is a test prompt"
        mock_get_prompt.return_value = mock_prompt
        
        # Create a mock task object
        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        
        # Test data
        test_data = {
            'product_title': 'Test Product',
            'product_description': 'Test description',
            'email': 'test@example.com',
            'file_url': 'https://example.com/test.jpg',
            'prompt': 'Test prompt text',
            'prompt_id': str(mock_prompt.id)
        }
        
        # Act
        with patch('core.tasks.VideoGeneration.objects.get_or_create') as mock_create:
            mock_create.return_value = (MagicMock(), True)
            result = generate_product_video(test_data)
        
        # Assert
        assert result['status'] == 'success'
        assert result['email'] == 'test@example.com'
        assert result['product_title'] == 'Test Product'
        assert 'video_data' in result
        
        # Verify database interactions
        mock_get_prompt.assert_called_once_with(id=str(mock_prompt.id))
    
    def test_missing_required_fields(self):
        """Test validation of required fields."""
        # Test data with missing fields
        test_data = {
            'product_title': 'Test Product',
            'email': 'test@example.com',
            # Missing file_url, prompt, prompt_id
        }
        
        # Act
        result = generate_product_video(test_data)
        
        # Assert
        assert result['status'] == 'failed'
        assert 'error' in result
        assert any(field in result['error'] for field in ['file_url', 'prompt', 'prompt_id'])


@pytest.mark.django_db
class TestOrchestrationTask:
    """Test suite for the process_complete_video_generation orchestration task."""
    
    @patch('core.tasks.generate_prompt_with_openrouter')
    @patch('core.tasks.generate_product_video')
    def test_successful_orchestration(self, mock_video_task, mock_prompt_task):
        """Test successful orchestration of the entire pipeline."""
        # Setup
        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        
        # Configure prompt task mock
        prompt_result = {
            'status': 'success',
            'prompt': 'Test generated prompt',
            'prompt_id': str(uuid.uuid4()),
            'created_new': True
        }
        mock_prompt_result = MagicMock()
        mock_prompt_result.get.return_value = prompt_result
        mock_prompt_task.delay.return_value = mock_prompt_result
        
        # Configure video task mock
        video_result = {
            'status': 'success',
            'message': 'Video generated successfully',
            'video_data': {'url': 'https://example.com/video.mp4'}
        }
        mock_video_result = MagicMock()
        mock_video_result.get.return_value = video_result
        mock_video_task.delay.return_value = mock_video_result
        
        # Test data
        test_data = {
            'product_title': 'Test Orchestration Product',
            'product_description': 'Testing the complete pipeline',
            'email': 'orchestration@example.com',
            'file_url': 'https://example.com/test.jpg'
        }
        
        # Act
        result = process_complete_video_generation(mock_task, test_data)
        
        # Assert
        assert result['status'] == 'success'
        assert result['prompt_id'] == prompt_result['prompt_id']
        assert result['prompt'] == prompt_result['prompt']
        assert result['email'] == test_data['email']
        assert 'prompt_was_reused' in result
        assert result['prompt_was_reused'] is False
        
        # Verify tasks were called correctly
        mock_prompt_task.delay.assert_called_once_with(test_data)
        
        # Verify video task was called with updated data including prompt
        video_data_call = mock_video_task.delay.call_args[0][0]
        assert video_data_call['prompt'] == prompt_result['prompt']
        assert video_data_call['prompt_id'] == prompt_result['prompt_id']
    
    @patch('core.tasks.generate_prompt_with_openrouter')
    @patch('core.tasks.generate_product_video')
    def test_prompt_reuse_flag(self, mock_video_task, mock_prompt_task):
        """Test that prompt reuse is correctly tracked."""
        # Setup
        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        
        # Configure prompt task to return an existing prompt
        prompt_result = {
            'status': 'success',
            'prompt': 'Existing prompt text',
            'prompt_id': str(uuid.uuid4()),
            'created_new': False  # This indicates reuse
        }
        mock_prompt_result = MagicMock()
        mock_prompt_result.get.return_value = prompt_result
        mock_prompt_task.delay.return_value = mock_prompt_result
        
        # Configure video task
        video_result = {'status': 'success'}
        mock_video_result = MagicMock()
        mock_video_result.get.return_value = video_result
        mock_video_task.delay.return_value = mock_video_result
        
        # Test data
        test_data = {
            'product_title': 'Reused Prompt Product',
            'product_description': 'Testing prompt reuse',
            'email': 'reuse@example.com',
            'file_url': 'https://example.com/reuse.jpg'
        }
        
        # Act
        result = process_complete_video_generation(mock_task, test_data)
        
        # Assert
        assert result['prompt_was_reused'] is True
        
        # Verify video task was called with prompt_was_reused=True
        video_data_call = mock_video_task.delay.call_args[0][0]
        assert video_data_call['prompt_was_reused'] is True
    
    @patch('core.tasks.generate_prompt_with_openrouter')
    def test_error_handling_prompt_failure(self, mock_prompt_task):
        """Test error handling when prompt generation fails."""
        # Setup
        mock_task = MagicMock()
        mock_task.request.id = str(uuid.uuid4())
        
        # Configure prompt task to fail
        prompt_result = {
            'status': 'failed',
            'error': 'Prompt generation failed',
            'step': 'api_call'
        }
        mock_prompt_result = MagicMock()
        mock_prompt_result.get.return_value = prompt_result
        mock_prompt_task.delay.return_value = mock_prompt_result
        
        # Test data
        test_data = {
            'product_title': 'Failure Test Product',
            'product_description': 'Testing error handling',
            'email': 'failure@example.com',
            'file_url': 'https://example.com/failure.jpg'
        }
        
        # Act
        result = process_complete_video_generation(mock_task, test_data)
        
        # Assert
        assert result['status'] == 'failed'
        assert result['error'] == 'Prompt generation failed'
        assert result['step'] == 'prompt_generation'
        
        # Video generation should not be called if prompt fails
        assert 'video_result' not in result


@pytest.mark.django_db
class TestErrorHandlingDecorator:
    """Test the task_error_handler decorator."""
    
    def test_decorator_basic_functionality(self):
        """Test that the decorator correctly wraps functions."""
        # Define a test function with the decorator
        @task_error_handler()
        def test_function(self, arg1, arg2=None):
            return f"Success: {arg1}, {arg2}"
        
        # Create a mock task object
        mock_task = MagicMock()
        mock_task.request.id = "test-id"
        
        # Act
        result = test_function(mock_task, "value1", arg2="value2")
        
        # Assert
        assert result == "Success: value1, value2"
    
    def test_decorator_catches_exceptions(self):
        """Test that the decorator catches and formats exceptions."""
        # Define a test function that raises an exception
        @task_error_handler()
        def failing_function(self, should_fail=True):
            if should_fail:
                raise ValueError("Test exception")
            return "Success"
        
        # Create a mock task object
        mock_task = MagicMock()
        mock_task.request.id = "test-id"
        
        # Act
        result = failing_function(mock_task)
        
        # Assert
        assert result['status'] == 'failed'
        assert 'error' in result
        assert 'Test exception' in result['error']
        assert result['error_type'] == 'ValueError'
        assert result['task_id'] == 'test-id'
    
    def test_decorator_handles_retries(self):
        """Test that the decorator correctly handles retry exceptions."""
        # Define a function that raises a retriable exception
        @task_error_handler(retry_for=ValueError)
        def retrying_function(self):
            raise ValueError("Retriable error")
        
        # Create a mock task object that counts retries
        mock_task = MagicMock()
        mock_task.request.id = "test-id"
        mock_task.request.retries = 3  # Simulate max retries reached
        mock_task.max_retries = 3
        
        # Act
        result = retrying_function(mock_task)
        
        # Assert - should return failure since max retries is reached
        assert result['status'] == 'failed'
        assert 'retries_exhausted' in result
        assert result['retries_exhausted'] is True
