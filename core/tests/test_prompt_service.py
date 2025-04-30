"""
Unit tests for the prompt service module.

Tests the functionality of:
- PromptService for getting/generating prompts
- OpenRouterClient for API interaction
- Error handling and retry logic

TS-05: Follows unit test best practices with pytest
CP-01: Ensures clean code and separation of concerns
CP-02: Validates security and input validation
"""

import pytest
import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase
from core.services.prompt_service import PromptService, get_prompt_service
from core.services.openrouter import OpenRouterClient, OpenRouterError, create_client
from core.models import ProductPrompt


@pytest.mark.django_db
class TestPromptService:
    """Test suite for the PromptService class."""
    
    @patch('core.services.openrouter.create_client')
    def test_get_new_prompt(self, mock_create_client):
        """Test generating a new prompt when one doesn't exist."""
        # Setup mock OpenRouter client to return a test prompt
        mock_client = MagicMock()
        mock_client.generate_prompt.return_value = "This is a test product video prompt."
        mock_create_client.return_value = mock_client
        
        service = PromptService()
        product_title = f"Test Product {uuid.uuid4()}"
        product_description = "A test product description for testing purposes."
        email = "test@example.com"
        
        # Act - get a prompt that doesn't exist yet
        prompt, created = service.get_or_generate_prompt(
            product_title=product_title,
            product_description=product_description,
            email=email
        )
        
        # Assert
        assert created is True
        assert prompt.product_title == product_title
        assert prompt.product_description == product_description
        assert prompt.email == email
        assert prompt.prompt_text == "This is a test product video prompt."
        assert prompt.is_approved is True  # Default value
        
        # Verify the OpenRouter client was called with correct parameters
        mock_client.generate_prompt.assert_called_once_with(
            product_title, 
            product_description,
            model=None,
            max_retries=None
        )
    
    @patch('core.services.openrouter.create_client')
    def test_get_existing_prompt(self, mock_create_client):
        """Test retrieving an existing prompt."""
        # Setup - create a prompt first
        mock_client = MagicMock()
        mock_client.generate_prompt.return_value = "This is a test product video prompt."
        mock_create_client.return_value = mock_client
        
        service = PromptService()
        product_title = f"Existing Product {uuid.uuid4()}"
        product_description = "An existing product description."
        email = "existing@example.com"
        
        # Create a prompt first
        prompt1, created1 = service.get_or_generate_prompt(
            product_title=product_title,
            product_description=product_description,
            email=email
        )
        
        # Reset the mock to verify it's not called in the second request
        mock_client.generate_prompt.reset_mock()
        
        # Act - try to get the same prompt again
        prompt2, created2 = service.get_or_generate_prompt(
            product_title=product_title,
            product_description=product_description,
            email="another@example.com"  # Different email shouldn't matter
        )
        
        # Assert
        assert created1 is True  # First one was created
        assert created2 is False  # Second one was retrieved, not created
        assert prompt1.id == prompt2.id  # Same prompt is returned
        assert prompt2.prompt_text == "This is a test product video prompt."
        
        # Verify the API wasn't called the second time
        mock_client.generate_prompt.assert_not_called()
    
    @patch('core.services.openrouter.create_client')
    def test_force_new_prompt(self, mock_create_client):
        """Test forcing a new prompt even when one exists."""
        # Setup
        mock_client = MagicMock()
        mock_client.generate_prompt.return_value = "This is a test product video prompt."
        mock_create_client.return_value = mock_client
        
        service = PromptService()
        product_title = f"Force New Product {uuid.uuid4()}"
        product_description = "A force new product description."
        email = "force@example.com"
        
        # Create initial prompt
        prompt1, created1 = service.get_or_generate_prompt(
            product_title=product_title,
            product_description=product_description,
            email=email
        )
        
        # Now have the mock return a different prompt for the second call
        mock_client.generate_prompt.return_value = "This is a NEW and DIFFERENT prompt."
        
        # Act - force a new prompt for the same product
        prompt2, created2 = service.get_or_generate_prompt(
            product_title=product_title,
            product_description=product_description,
            email=email,
            force_new=True
        )
        
        # Assert
        assert created1 is True
        assert created2 is True  # New one was created
        assert prompt1.id != prompt2.id  # Different prompts
        assert prompt2.prompt_text == "This is a NEW and DIFFERENT prompt."
        
        # Verify API was called both times
        assert mock_client.generate_prompt.call_count == 2
    
    @patch('core.services.openrouter.create_client')
    def test_prompt_with_category(self, mock_create_client):
        """Test that category is used in prompt generation."""
        # Setup
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        
        service = PromptService()
        product_title = f"Category Product {uuid.uuid4()}"
        product_description = "A product with category for testing."
        email = "category@example.com"
        category = "electronics"
        
        # Act
        service.get_or_generate_prompt(
            product_title=product_title,
            product_description=product_description,
            email=email,
            category=category
        )
        
        # Verify the category was applied to optimize the prompt template
        # by checking what was passed to the client
        prompt_description = mock_client.generate_prompt.call_args[0][1]
        assert category.lower() in prompt_description.lower()


@pytest.mark.django_db
class TestOpenRouterClient:
    """Test suite for the OpenRouterClient class."""
    
    @patch('core.services.openrouter.requests.post')
    def test_generate_prompt_success(self, mock_post):
        """Test successful prompt generation."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [
                {
                    'message': {
                        'content': 'This is a mock prompt from OpenRouter API.'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response
        
        # Create client with mock API key
        client = OpenRouterClient(api_key="test_key")
        
        # Act
        prompt = client.generate_prompt(
            "Test Product", 
            "A test product description."
        )
        
        # Assert
        assert prompt == 'This is a mock prompt from OpenRouter API.'
        mock_post.assert_called_once()
    
    @patch('core.services.openrouter.requests.post')
    def test_generate_prompt_api_error(self, mock_post):
        """Test error handling in prompt generation."""
        # Setup mock to raise an API error
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {'error': 'API Error Message'}
        mock_post.return_value = mock_response
        
        # Create client
        client = OpenRouterClient(api_key="test_key")
        
        # Act & Assert
        with pytest.raises(OpenRouterError) as excinfo:
            client.generate_prompt("Test Product", "A test product description.")
        
        assert "API Error Message" in str(excinfo.value)
    
    @patch('core.services.openrouter.requests.post')
    def test_generate_prompt_network_error(self, mock_post):
        """Test network error handling."""
        # Setup mock to raise a network error
        mock_post.side_effect = ConnectionError("Network error")
        
        # Create client
        client = OpenRouterClient(api_key="test_key", max_retries=0)
        
        # Act & Assert - should raise the error since max_retries=0
        with pytest.raises(OpenRouterError) as excinfo:
            client.generate_prompt("Test Product", "A test product description.")
        
        assert "Network error" in str(excinfo.value)
    
    @patch('core.services.openrouter.requests.post')
    def test_model_selection(self, mock_post):
        """Test that model selection is passed correctly."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [
                {
                    'message': {
                        'content': 'This is a mock prompt from a specific model.'
                    }
                }
            ]
        }
        mock_post.return_value = mock_response
        
        # Create client
        client = OpenRouterClient(api_key="test_key")
        
        # Act
        client.generate_prompt(
            "Test Product", 
            "A test product description.",
            model="anthropic/claude-3-opus"
        )
        
        # Assert the correct model was used
        request_json = mock_post.call_args.kwargs['json']
        assert request_json['model'] == "anthropic/claude-3-opus"


@pytest.mark.django_db
class TestErrorHandling:
    """Tests for error handling and validation."""
    
    @patch('core.services.openrouter.create_client')
    def test_validation_missing_fields(self, mock_create_client):
        """Test validation of required fields."""
        service = PromptService()
        
        # Missing product_description
        with pytest.raises(ValueError) as excinfo:
            service.get_or_generate_prompt(
                product_title="Test Product",
                product_description="",  # Empty description
                email="test@example.com"
            )
        
        assert "product_description" in str(excinfo.value).lower()
        
        # Missing product_title
        with pytest.raises(ValueError) as excinfo:
            service.get_or_generate_prompt(
                product_title="",  # Empty title
                product_description="A test description",
                email="test@example.com"
            )
        
        assert "product_title" in str(excinfo.value).lower()
    
    @patch('core.services.openrouter.create_client')
    def test_get_prompt_service_factory(self, mock_create_client):
        """Test the factory function for getting a PromptService."""
        # Setup
        mock_client = MagicMock()
        mock_create_client.return_value = mock_client
        
        # Act - should get the same instance when called multiple times
        service1 = get_prompt_service()
        service2 = get_prompt_service()
        
        # Assert
        assert service1 is service2  # Singleton pattern check
        assert isinstance(service1, PromptService)
